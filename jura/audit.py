from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from jura.models import JurisdictionResult

_ROOT = Path(__file__).parent.parent
_LOG_PATH = _ROOT / "data" / "jurisdiction_log.jsonl"
_COMPLIANCE_LOG_PATH = _ROOT / "data" / "compliance_decisions.jsonl"


class JurisdictionAuditLogger:
    def __init__(
        self,
        log_path: str | Path = _LOG_PATH,
        compliance_log_path: str | Path = _COMPLIANCE_LOG_PATH,
    ) -> None:
        self.log_path = Path(log_path)
        self.compliance_log_path = Path(compliance_log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log_jurisdiction(
        self,
        result: JurisdictionResult,
        named_insured: str = "",
    ) -> None:
        entry: dict = {
            "submission_id": result.submission_id,
            "named_insured": named_insured,
            "writing_state": result.writing_state,
            "market": result.market,
            "eligible": result.eligible,
            "doi_flags": [
                {
                    "rule_id": f.rule_id,
                    "level": f.level,
                    "statutory_ref": f.statutory_ref,
                }
                for f in result.doi_flags
            ],
            "has_block": result.has_block,
            "has_disclose": result.has_disclose,
            "checked_at": result.checked_at.isoformat(),
        }
        # Hash computed before adding log_hash field
        canonical = json.dumps(entry, sort_keys=True)
        entry["log_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
        with open(self.log_path, "a") as fh:
            fh.write(json.dumps(entry) + "\n")

    def log_compliance_decision(
        self,
        submission_id: str,
        reviewer_id: str,
        choice: str,
        notes: str,
    ) -> None:
        entry = {
            "submission_id": submission_id,
            "reviewer_id": reviewer_id,
            "choice": choice,
            "notes": notes,
            "decided_at": datetime.utcnow().isoformat(),
        }
        with open(self.compliance_log_path, "a") as fh:
            fh.write(json.dumps(entry) + "\n")

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_jurisdiction_log(
        self,
        submission_id: str | None = None,
    ) -> list[dict]:
        if not self.log_path.exists():
            return []
        entries: list[dict] = []
        with open(self.log_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if submission_id is None or entry.get("submission_id") == submission_id:
                    entries.append(entry)
        return entries

    def verify_integrity(self) -> dict:
        if not self.log_path.exists():
            return {
                "status": "no_log",
                "total": 0,
                "valid": 0,
                "invalid": 0,
                "errors": [],
            }

        total = valid = invalid = 0
        errors: list[dict] = []

        with open(self.log_path) as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    entry = json.loads(line)
                    stored_hash = entry.pop("log_hash", None)
                    recomputed = hashlib.sha256(
                        json.dumps(entry, sort_keys=True).encode()
                    ).hexdigest()
                    if recomputed == stored_hash:
                        valid += 1
                    else:
                        invalid += 1
                        errors.append({
                            "line": lineno,
                            "submission_id": entry.get("submission_id"),
                            "error": "hash_mismatch",
                        })
                except Exception as exc:
                    invalid += 1
                    errors.append({"line": lineno, "error": str(exc)})

        return {
            "status": "ok" if invalid == 0 else "integrity_errors",
            "total": total,
            "valid": valid,
            "invalid": invalid,
            "errors": errors,
        }
