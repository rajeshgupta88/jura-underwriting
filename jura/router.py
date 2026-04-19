from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx

from jura.audit import JurisdictionAuditLogger
from jura.checker import generate_es_mock_declinations, run_jurisdiction_check
from jura.db import SubmissionDB
from jura.models import (
    ESResult,
    JurisdictionBlock,
    JurisdictionResult,
    MultiStateConflict,
    SubmissionEvent,
)
from jura.notices import write_disclosure, write_es_notice, write_hold_notice

_ROOT = Path(__file__).parent.parent
_ARIA_PENDING_DIR = _ROOT / "data" / "aria_pending"


class JurisdictionRouter:
    def __init__(
        self,
        db: SubmissionDB,
        audit: JurisdictionAuditLogger,
        notices: Any,          # module ref (write_* functions imported directly)
        llm_client: Any,
        hitl_mode: str = "terminal",
    ) -> None:
        self.db = db
        self.audit = audit
        self.notices = notices
        self.llm_client = llm_client
        self.hitl_mode = hitl_mode
        self.aria_endpoint = os.environ.get("ARIA_ENDPOINT", "http://localhost:8001/score")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def route(self, event: SubmissionEvent) -> dict:
        self.db.insert_submission({
            "id": event.submission_id,
            "named_insured": event.named_insured,
            "sic_code": event.sic_code,
            "sic_description": event.sic_description,
            "writing_state": event.writing_state,
            "mailing_state": event.mailing_state,
            "premises_zip": event.premises_zip,
            "tiv": event.tiv,
            "credit_score_used": int(event.credit_score_used),
            "pc_account_id": event.pc_account_id,
            "status": "jura_pending",
            "market": None,
            "jurisdiction_outcome": None,
        })
        self.db.update_status(event.submission_id, "jura_checking")

        try:
            result = run_jurisdiction_check(event)
        except JurisdictionBlock as exc:
            return await self._handle_block(event, exc)
        except MultiStateConflict as exc:
            return await self._handle_conflict(event, exc)

        self.audit.log_jurisdiction(result, named_insured=event.named_insured)

        if result.has_block:
            return await self._handle_block(event, result)
        elif result.has_disclose:
            return await self._handle_disclose(event, result)
        elif result.es_eligible:
            return await self._handle_es(event, result)
        else:
            return await self._forward_to_aria(event, result)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_block(
        self,
        event: SubmissionEvent,
        block_info,          # JurisdictionBlock exc OR JurisdictionResult
    ) -> dict:
        if isinstance(block_info, JurisdictionBlock):
            reason = block_info.reason
            statutory_ref = block_info.statutory_ref
        else:
            reason = block_info.block_reason or "Jurisdiction block"
            statutory_ref = block_info.statutory_ref or ""

        self.db.update_status(event.submission_id, "jurisdiction_blocked")
        notice_path = write_hold_notice(event.submission_id, event, reason, statutory_ref)

        if self.hitl_mode == "terminal":
            from hitl.card import render_block_card
            await asyncio.to_thread(render_block_card, event, block_info)

        return {
            "outcome": "blocked",
            "submission_id": event.submission_id,
            "reason": reason,
            "statutory_ref": statutory_ref,
            "notice": notice_path,
        }

    async def _handle_disclose(
        self,
        event: SubmissionEvent,
        result: JurisdictionResult,
    ) -> dict:
        self.db.update_status(event.submission_id, "admitted_disclose_pending")
        self.db.update_market(event.submission_id, "admitted")

        for flag in [f for f in result.doi_flags if f.level == "disclose"]:
            path = write_disclosure(event.submission_id, flag, event)
            result.disclosure_docs.append(path)

        if self.hitl_mode == "terminal":
            from hitl.card import render_disclose_card
            choice, reviewer_id, notes = await asyncio.to_thread(
                render_disclose_card, event, result
            )
            self.audit.log_compliance_decision(
                event.submission_id, reviewer_id, choice, notes
            )
            if choice == "A":
                self.db.update_status(event.submission_id, "admitted_disclose_approved")
                return await self._forward_to_aria(event, result)

        return {
            "outcome": "disclose_pending",
            "submission_id": event.submission_id,
            "flags": [
                {"rule_id": f.rule_id, "rule_name": f.rule_name, "statutory_ref": f.statutory_ref}
                for f in result.doi_flags
                if f.level == "disclose"
            ],
            "docs": result.disclosure_docs,
        }

    async def _handle_es(
        self,
        event: SubmissionEvent,
        result: JurisdictionResult,
    ) -> dict:
        self.db.update_status(event.submission_id, "surplus_pending")
        self.db.update_market(event.submission_id, "surplus_lines")

        declinations = generate_es_mock_declinations(event.writing_state, event.sic_code)
        surplus_cfg = {}
        try:
            from jura.checker import SURPLUS_LINES
            surplus_cfg = (SURPLUS_LINES.get("diligent_search_requirements") or {}).get(
                event.writing_state, {}
            )
        except Exception:
            pass
        min_decl = surplus_cfg.get("min_declinations", 3)

        es = ESResult(
            licensed=True,
            state=event.writing_state,
            mock_declinations=declinations,
            diligent_search_met=len(declinations) >= min_decl,
        )
        notice_path = write_es_notice(event.submission_id, es, event)

        return {
            "outcome": "es_pending",
            "submission_id": event.submission_id,
            "es_result": es.model_dump(),
            "notice": notice_path,
        }

    async def _handle_conflict(
        self,
        event: SubmissionEvent,
        exc: MultiStateConflict,
    ) -> dict:
        self.db.update_status(event.submission_id, "multi_state_conflict")

        if self.hitl_mode == "terminal":
            from hitl.card import render_conflict_card
            choice, reason = await asyncio.to_thread(render_conflict_card, event, exc)

        return {
            "outcome": "multi_state_conflict",
            "submission_id": event.submission_id,
            "states": exc.states,
            "conflicts": exc.conflicting_rules,
            "summary": exc.conflict_summary,
        }

    async def _forward_to_aria(
        self,
        event: SubmissionEvent,
        result: JurisdictionResult,
    ) -> dict:
        self.db.update_status(event.submission_id, "forwarded_to_aria")
        self.db.update_market(event.submission_id, "admitted")

        payload = {
            "event": event.model_dump(mode="json"),
            "jurisdiction_context": result.model_dump(mode="json"),
        }

        try:
            async with httpx.AsyncClient() as client:
                await client.post(self.aria_endpoint, json=payload, timeout=5.0)
        except Exception:
            self.db.update_status(event.submission_id, "aria_pending_retry")
            _ARIA_PENDING_DIR.mkdir(parents=True, exist_ok=True)
            stub_path = _ARIA_PENDING_DIR / f"{event.submission_id}.json"
            stub_path.write_text(json.dumps(payload, default=str, indent=2))

        return {
            "outcome": "forwarded_to_aria",
            "submission_id": event.submission_id,
            "market": "admitted",
        }
