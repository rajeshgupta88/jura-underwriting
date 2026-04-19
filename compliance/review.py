from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from jura.audit import JurisdictionAuditLogger
from jura.db import SubmissionDB
from jura.llm import get_provider
from jura.models import DOIFlag, JurisdictionResult, SubmissionEvent

_ROOT = Path(__file__).parent.parent
_TEMPLATES_DIR = _ROOT / "templates"
_DISCLOSURES_DIR = _ROOT / "data" / "disclosures"
_HOLD_NOTICES_DIR = _ROOT / "data" / "hold_notices"
_ES_NOTICES_DIR = _ROOT / "data" / "es_notices"
_ARIA_PENDING_DIR = _ROOT / "data" / "aria_pending"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/compliance", tags=["compliance"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sla(checked_at_str: str) -> dict:
    sla_hours = int(os.environ.get("COMPLIANCE_SLA_HOURS", "24"))
    try:
        checked_at = datetime.fromisoformat(checked_at_str)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        deadline = checked_at + timedelta(hours=sla_hours)
        now = datetime.now(timezone.utc)
        remaining = deadline - now
        total_sec = max(0, int(remaining.total_seconds()))
        hours, mins = total_sec // 3600, (total_sec % 3600) // 60
        urgency = "red" if total_sec < 7_200 else "amber" if total_sec < 28_800 else "ok"
        return {
            "hours": hours, "minutes": mins, "total_seconds": total_sec,
            "label": f"{hours}h {mins}m remaining", "urgency": urgency,
        }
    except Exception:
        return {"label": "N/A", "urgency": "ok", "total_seconds": 999999}


def _counts(db: SubmissionDB) -> dict:
    return {
        "pending":   len(db.get_compliance_queue()),
        "blocks":    len(db.get_blocks()),
        "es":        len(db.list_submissions(status="surplus_pending", limit=200))
                   + len(db.list_submissions(status="surplus_confirmed", limit=200)),
        "conflicts": len(db.list_submissions(status="multi_state_conflict", limit=200)),
    }


def _base(request: Request, active: str) -> dict:
    db: SubmissionDB = request.app.state.db
    return {
        "request": request,
        "active": active,
        "counts": _counts(db),
        "llm_provider": get_provider(),
    }


def _latest_log(audit: JurisdictionAuditLogger, submission_id: str) -> dict:
    entries = audit.read_jurisdiction_log(submission_id=submission_id)
    return entries[-1] if entries else {}


def _approved_today(audit: JurisdictionAuditLogger) -> int:
    if not audit.compliance_log_path.exists():
        return 0
    today = date.today().isoformat()
    count = 0
    with open(audit.compliance_log_path) as fh:
        for line in fh:
            try:
                d = json.loads(line)
                if d.get("choice") == "approve" and d.get("decided_at", "").startswith(today):
                    count += 1
            except Exception:
                pass
    return count


def _avg_review_time(audit: JurisdictionAuditLogger) -> str:
    """Crude avg: time between log entry checked_at and compliance decision decided_at."""
    if not audit.compliance_log_path.exists():
        return "N/A"
    decisions = []
    with open(audit.compliance_log_path) as fh:
        for line in fh:
            try:
                decisions.append(json.loads(line))
            except Exception:
                pass
    if not decisions:
        return "N/A"
    deltas = []
    for d in decisions:
        entries = audit.read_jurisdiction_log(submission_id=d.get("submission_id", ""))
        if entries:
            try:
                t0 = datetime.fromisoformat(entries[0]["checked_at"])
                t1 = datetime.fromisoformat(d["decided_at"])
                deltas.append(abs((t1 - t0).total_seconds()))
            except Exception:
                pass
    if not deltas:
        return "N/A"
    avg_sec = int(sum(deltas) / len(deltas))
    h, m = avg_sec // 3600, (avg_sec % 3600) // 60
    return f"{h}h {m}m" if h else f"{m}m"


def _find_disclosure_file(submission_id: str, rule_id: str) -> Path | None:
    candidate = _DISCLOSURES_DIR / f"disclosure_{submission_id}_{rule_id}.txt"
    if candidate.exists():
        return candidate
    # fallback: newest timestamped file matching pattern
    matches = sorted(_DISCLOSURES_DIR.glob(f"{submission_id}_{rule_id}_*.txt"), reverse=True)
    return matches[0] if matches else None


def _reconstruct_event(row: dict) -> SubmissionEvent:
    return SubmissionEvent(
        submission_id=row["id"],
        named_insured=row["named_insured"],
        pc_account_id=row.get("pc_account_id", ""),
        sic_code=row["sic_code"],
        sic_description=row.get("sic_description", ""),
        writing_state=row["writing_state"],
        mailing_state=row.get("mailing_state", row["writing_state"]),
        premises_zip=row["premises_zip"],
        mailing_zip=row.get("mailing_zip", row["premises_zip"]),
        tiv=row.get("tiv"),
        credit_score_used=bool(row.get("credit_score_used", False)),
        new_business=True,
        property_coverage=True,
        created_at=datetime.utcnow(),
    )


def _reconstruct_result(log: dict, submission_id: str) -> JurisdictionResult:
    doi_flags = []
    for f in log.get("doi_flags", []):
        doi_flags.append(DOIFlag(
            rule_id=f["rule_id"],
            rule_name=f.get("rule_name", f["rule_id"]),
            level=f["level"],
            state=log.get("writing_state", ""),
            statutory_ref=f.get("statutory_ref", ""),
            description=f.get("description", ""),
        ))
    checked_at_str = log.get("checked_at", datetime.utcnow().isoformat())
    try:
        checked_at = datetime.fromisoformat(checked_at_str)
    except Exception:
        checked_at = datetime.utcnow()

    return JurisdictionResult(
        submission_id=submission_id,
        writing_state=log.get("writing_state", ""),
        market=log.get("market", "admitted"),
        admitted=log.get("market") == "admitted",
        multi_state=False,
        es_eligible=False,
        doi_flags=doi_flags,
        block_reason=None,
        statutory_ref=None,
        disclosure_docs=[],
        rationale="",
        checked_at=checked_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
def get_queue(request: Request):
    db: SubmissionDB = request.app.state.db
    audit: JurisdictionAuditLogger = request.app.state.audit
    ctx = _base(request, "queue")

    raw = db.get_compliance_queue()
    submissions = []
    for sub in raw:
        log = _latest_log(audit, sub["id"])
        sub = dict(sub)
        sub["doi_flags"] = log.get("doi_flags", [])
        sub["sla"] = _sla(log["checked_at"]) if log.get("checked_at") else None
        # find any disclosure docs
        docs = []
        for f in sub["doi_flags"]:
            if f["level"] == "disclose":
                p = _find_disclosure_file(sub["id"], f["rule_id"])
                if p:
                    docs.append(str(p))
        sub["disclosure_docs"] = docs
        submissions.append(sub)

    ctx.update({
        "submissions": submissions,
        "approved_today": _approved_today(audit),
        "avg_review_time": _avg_review_time(audit),
    })
    return templates.TemplateResponse("compliance_queue.html", ctx)


@router.get("/review/{submission_id}")
def get_review(request: Request, submission_id: str):
    db: SubmissionDB = request.app.state.db
    audit: JurisdictionAuditLogger = request.app.state.audit
    ctx = _base(request, "queue")

    row = db.get_submission(submission_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Submission {submission_id!r} not found")

    log = _latest_log(audit, submission_id)
    doi_flags = log.get("doi_flags", [])
    checked_at = log.get("checked_at")
    sla_info = _sla(checked_at) if checked_at else None

    # Read disclosure file contents for each disclose flag
    disclosure_contents: dict[str, str] = {}
    for f in doi_flags:
        if f["level"] == "disclose":
            p = _find_disclosure_file(submission_id, f["rule_id"])
            if p:
                disclosure_contents[f["rule_id"]] = p.read_text()

    ctx.update({
        "sub": row,
        "doi_flags": doi_flags,
        "checked_at": checked_at,
        "sla": sla_info,
        "disclosure_contents": disclosure_contents,
    })
    return templates.TemplateResponse("disclosure_review.html", ctx)


@router.post("/review/{submission_id}/decide")
async def post_decide(
    request: Request,
    submission_id: str,
    choice: str = Form(...),
    reviewer_id: str = Form(...),
    notes: str = Form(default=""),
):
    db: SubmissionDB = request.app.state.db
    audit: JurisdictionAuditLogger = request.app.state.audit
    jura_router = request.app.state.router

    row = db.get_submission(submission_id)
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")

    if choice == "approve":
        audit.log_compliance_decision(submission_id, reviewer_id, "approve", notes)
        db.update_status(submission_id, "admitted_disclose_approved")

        # Forward to Aria
        log = _latest_log(audit, submission_id)
        event = _reconstruct_event(row)
        result = _reconstruct_result(log, submission_id)
        try:
            await jura_router._forward_to_aria(event, result)
        except Exception:
            pass

        msg = f"Disclosure+approved+and+forwarded+to+Aria+%28{submission_id}%29"
        return RedirectResponse(f"/compliance?success=1&msg={msg}", status_code=302)

    elif choice == "request_changes":
        audit.log_compliance_decision(submission_id, reviewer_id, "request_changes", notes)
        db.update_status(submission_id, "admitted_disclose_pending")
        note = "Changes+requested+—+please+revise+and+resubmit."
        return RedirectResponse(
            f"/compliance/review/{submission_id}?note={note}", status_code=302
        )

    raise HTTPException(status_code=400, detail=f"Unknown choice: {choice!r}")


@router.get("/blocks")
def get_blocks(request: Request):
    db: SubmissionDB = request.app.state.db
    ctx = _base(request, "blocks")
    ctx["submissions"] = db.get_blocks()
    return templates.TemplateResponse("compliance_blocks.html", ctx)


@router.get("/notice/{submission_id}")
def get_notice(submission_id: str):
    path = _HOLD_NOTICES_DIR / f"hold_{submission_id}.txt"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Hold notice not found")
    return PlainTextResponse(path.read_text())


@router.get("/disclosure/{submission_id}/{rule_id}")
def get_disclosure(submission_id: str, rule_id: str):
    path = _find_disclosure_file(submission_id, rule_id)
    if not path:
        raise HTTPException(status_code=404, detail="Disclosure document not found")
    return PlainTextResponse(path.read_text())


@router.get("/es")
def get_es(request: Request):
    db: SubmissionDB = request.app.state.db
    ctx = _base(request, "es")
    pending = db.list_submissions(status="surplus_pending", limit=100)
    confirmed = db.list_submissions(status="surplus_confirmed", limit=100)
    submissions = []
    for sub in pending + confirmed:
        sub = dict(sub)
        # Try to read declinations from ES notice stub
        notice_path = _ES_NOTICES_DIR / f"es_notice_{sub['id']}.txt"
        sub["declinations"] = []
        # Check aria_pending for mock declinations
        aria_stub = _ARIA_PENDING_DIR / f"{sub['id']}.json"
        if aria_stub.exists():
            try:
                data = json.loads(aria_stub.read_text())
                es = data.get("jurisdiction_context", {}).get("es_result", {})
                sub["declinations"] = es.get("mock_declinations", [])
            except Exception:
                pass
        submissions.append(sub)
    ctx["submissions"] = submissions
    return templates.TemplateResponse("compliance_es.html", ctx)


@router.get("/es-notice/{submission_id}")
def get_es_notice(submission_id: str):
    path = _ES_NOTICES_DIR / f"es_notice_{submission_id}.txt"
    if not path.exists():
        raise HTTPException(status_code=404, detail="E&S notice not found")
    return PlainTextResponse(path.read_text())


@router.get("/conflicts")
def get_conflicts(request: Request):
    db: SubmissionDB = request.app.state.db
    ctx = _base(request, "conflicts")
    ctx["submissions"] = db.list_submissions(status="multi_state_conflict", limit=100)
    return templates.TemplateResponse("compliance_conflicts.html", ctx)


@router.post("/conflicts/{submission_id}/escalate")
def post_escalate(request: Request, submission_id: str):
    db: SubmissionDB = request.app.state.db
    audit: JurisdictionAuditLogger = request.app.state.audit
    db.update_status(submission_id, "compliance_escalated")
    audit.log_compliance_decision(
        submission_id, "system", "escalate",
        "Escalated to senior compliance team via browser UI"
    )
    return RedirectResponse(
        f"/compliance/conflicts?note=Submission+{submission_id}+escalated.", status_code=302
    )


@router.get("/audit")
def get_audit(request: Request):
    audit: JurisdictionAuditLogger = request.app.state.audit
    db: SubmissionDB = request.app.state.db
    ctx = _base(request, "audit")

    decisions: list[dict] = []
    if audit.compliance_log_path.exists():
        with open(audit.compliance_log_path) as fh:
            for line in fh:
                try:
                    d = json.loads(line.strip())
                    # enrich with named_insured from DB
                    row = db.get_submission(d.get("submission_id", ""))
                    if row:
                        d["named_insured"] = row["named_insured"]
                    decisions.append(d)
                except Exception:
                    pass
    decisions.reverse()  # newest first
    ctx["decisions"] = decisions
    return templates.TemplateResponse("compliance_audit.html", ctx)
