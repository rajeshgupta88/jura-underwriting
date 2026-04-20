"""Exec-demo UI route handlers — /, /blocks, /audit, /insights."""
from __future__ import annotations

import json
import os
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates

from jura.audit import JurisdictionAuditLogger
from jura.db import SubmissionDB
from jura.llm import llm_status

_ROOT = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(_ROOT / "templates"))

router = APIRouter(tags=["ui"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _llm() -> dict:
    return llm_status()


def _nav_counts(db: SubmissionDB) -> dict:
    all_subs = db.list_submissions(limit=500)
    disclose = sum(1 for s in all_subs if s["status"] == "admitted_disclose_pending")
    blocks   = sum(1 for s in all_subs if s["status"] == "jurisdiction_blocked")
    queue    = disclose + sum(1 for s in all_subs if s["status"] in ("jura_pending", "jura_checking", "multi_state_conflict", "aria_pending_retry"))
    return {"queue": queue, "disclose": disclose, "blocks": blocks}


def _rel_time(dt_str: str | None) -> str:
    if not dt_str:
        return "—"
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
        if secs < 60:   return f"{secs}s ago"
        if secs < 3600: return f"{secs // 60}m ago"
        if secs < 86400: return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return "—"


def _today_prefix() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _sla_seconds(checked_at_str: str) -> int:
    sla_hours = int(os.environ.get("COMPLIANCE_SLA_HOURS", "24"))
    try:
        dt = datetime.fromisoformat(checked_at_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        deadline = dt + timedelta(hours=sla_hours)
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(remaining))
    except Exception:
        return 999999


def _base_ctx(request: Request, active: str) -> dict:
    db: SubmissionDB = request.app.state.db
    return {
        "request": request,
        "active": active,
        "counts": _nav_counts(db),
        "llm": _llm(),
    }


def _enrich_with_log(submissions: list[dict], audit: JurisdictionAuditLogger) -> list[dict]:
    """Join submission rows with latest audit log entry."""
    log_map: dict[str, dict] = {}
    for entry in audit.read_jurisdiction_log():
        log_map[entry["submission_id"]] = entry   # last write wins

    result = []
    for sub in submissions:
        sub = dict(sub)
        log = log_map.get(sub["id"], {})
        sub["doi_flags"] = log.get("doi_flags", [])
        sub["statutory_ref"] = log.get("statutory_ref")
        sub["rel_time"] = _rel_time(log.get("checked_at") or sub.get("created_at"))
        sub["_log"] = log
        result.append(sub)
    return result


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

_FILTER_STATUS: dict[str, list[str]] = {
    "cleared":     ["forwarded_to_aria", "admitted_disclose_approved", "admitted_clear"],
    "disclosure":  ["admitted_disclose_pending"],
    "es":          ["surplus_pending", "surplus_confirmed"],
    "blocked":     ["jurisdiction_blocked"],
}


@router.get("/")
def get_queue(request: Request, filter: str = Query(default="all")):
    db: SubmissionDB = request.app.state.db
    audit: JurisdictionAuditLogger = request.app.state.audit
    ctx = _base_ctx(request, "queue")

    all_subs = db.list_submissions(limit=200)
    today = _today_prefix()

    metrics = {
        "total":   sum(1 for s in all_subs if (s.get("created_at") or "").startswith(today)),
        "cleared": sum(1 for s in all_subs if s["status"] in ("forwarded_to_aria", "admitted_disclose_approved", "admitted_clear")),
        "disclose": sum(1 for s in all_subs if s["status"] == "admitted_disclose_pending"),
        "blocked":  sum(1 for s in all_subs if s["status"] == "jurisdiction_blocked"),
    }

    # Apply filter
    if filter in _FILTER_STATUS:
        subs = [s for s in all_subs if s["status"] in _FILTER_STATUS[filter]]
    else:
        subs = all_subs
        filter = "all"

    ctx.update({
        "submissions": _enrich_with_log(subs, audit),
        "metrics": metrics,
        "filter": filter,
    })
    return templates.TemplateResponse("queue.html", ctx)


# ---------------------------------------------------------------------------
# GET /blocks
# ---------------------------------------------------------------------------

@router.get("/blocks")
def get_blocks(request: Request):
    db: SubmissionDB = request.app.state.db
    audit: JurisdictionAuditLogger = request.app.state.audit
    ctx = _base_ctx(request, "blocks")

    blocks = _enrich_with_log(db.get_blocks(), audit)

    # Pattern detection: 2+ blocks sharing same writing_state in past 7 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = []
    for sub in blocks:
        try:
            dt_str = sub.get("created_at") or ""
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                recent.append(sub)
        except Exception:
            recent.append(sub)

    state_counts = Counter(s["writing_state"] for s in recent)
    patterns = [
        {
            "count": cnt,
            "state": state,
            "reason": f"{state} moratorium/restriction",
        }
        for state, cnt in state_counts.items() if cnt >= 2
    ]

    ctx.update({"submissions": blocks, "patterns": patterns})
    return templates.TemplateResponse("blocks.html", ctx)


# ---------------------------------------------------------------------------
# GET /audit
# ---------------------------------------------------------------------------

@router.get("/audit")
def get_audit(request: Request):
    db: SubmissionDB = request.app.state.db
    audit: JurisdictionAuditLogger = request.app.state.audit
    ctx = _base_ctx(request, "audit")

    integrity = audit.verify_integrity()

    # Jurisdiction entries — newest first, enrich with outcome tag
    j_entries = audit.read_jurisdiction_log()
    for e in j_entries:
        if e.get("has_block"):
            e["_outcome"] = "block"
        elif e.get("has_disclose"):
            e["_outcome"] = "disclose"
        elif e.get("market") in ("surplus_lines",):
            e["_outcome"] = "es"
        else:
            e["_outcome"] = "clear"
    j_entries.reverse()

    # Compliance decisions
    d_entries: list[dict] = []
    if audit.compliance_log_path.exists():
        with open(audit.compliance_log_path) as fh:
            for line in fh:
                try:
                    d_entries.append(json.loads(line.strip()))
                except Exception:
                    pass
        d_entries.reverse()

    ctx.update({
        "integrity": integrity,
        "jurisdiction_entries": j_entries,
        "decision_entries": d_entries,
        "decision_count": len(d_entries),
    })
    return templates.TemplateResponse("audit_log.html", ctx)


@router.get("/audit/export")
def get_audit_export():
    path = _ROOT / "data" / "jurisdiction_log.jsonl"
    if not path.exists():
        path.touch()
    return FileResponse(
        str(path),
        filename="jurisdiction_log.jsonl",
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=jurisdiction_log.jsonl"},
    )


# ---------------------------------------------------------------------------
# GET /insights
# ---------------------------------------------------------------------------

@router.get("/insights")
def get_insights(request: Request):
    db: SubmissionDB = request.app.state.db
    audit: JurisdictionAuditLogger = request.app.state.audit
    ctx = _base_ctx(request, "insights")

    all_subs = db.list_submissions(limit=500)
    log_entries = audit.read_jurisdiction_log()

    total = len(all_subs)
    cleared = sum(1 for s in all_subs if s["status"] in ("forwarded_to_aria", "admitted_disclose_approved", "admitted_clear"))
    clear_rate = round(100 * cleared / total) if total else 0

    disclosures = sum(1 for e in log_entries if e.get("has_disclose"))
    violations  = sum(1 for s in all_subs if s["status"] == "jurisdiction_blocked")

    # Mock avg check time — deterministic based on count
    seed_val = (total * 17 + 3) % 10
    avg_ms = 300 + seed_val * 50   # 300–800ms
    avg_check = f"~{avg_ms}ms"

    metrics = {
        "clear_rate": clear_rate,
        "avg_check_time": avg_check,
        "disclosures": disclosures,
        "violations_prevented": violations,
    }

    # Block reason bar chart
    blocks = [s for s in all_subs if s["status"] == "jurisdiction_blocked"]
    reason_counts: Counter = Counter()
    for s in blocks:
        outcome = s.get("jurisdiction_outcome") or "Unknown"
        # Shorten to first ~40 chars
        label = outcome[:40] + ("…" if len(outcome) > 40 else "")
        # Extract key phrase
        if "moratorium" in outcome.lower():
            label = f"{s['writing_state']} coastal moratorium"
        elif "fair plan" in outcome.lower():
            label = "CA FAIR Plan zone"
        elif "credit" in outcome.lower():
            label = f"{s['writing_state']} credit rule block"
        reason_counts[label] += 1

    max_b = max(reason_counts.values(), default=1)
    block_reasons = [
        {"label": lbl, "count": cnt, "pct": round(100 * cnt / max_b)}
        for lbl, cnt in reason_counts.most_common()
    ]

    # Disclosure type bar chart
    disc_counts: Counter = Counter()
    for e in log_entries:
        for f in e.get("doi_flags", []):
            if f.get("level") == "disclose":
                disc_counts[f.get("rule_id", "unknown")] += 1
    max_d = max(disc_counts.values(), default=1)
    disclosure_types = [
        {"label": rid, "count": cnt, "pct": round(100 * cnt / max_d)}
        for rid, cnt in disc_counts.most_common()
    ]

    # SLA items — disclose pending
    log_map = {e["submission_id"]: e for e in log_entries}
    sla_items = []
    for sub in all_subs:
        if sub["status"] != "admitted_disclose_pending":
            continue
        log = log_map.get(sub["id"], {})
        checked_at = log.get("checked_at", sub.get("created_at", ""))
        sla_sec = _sla_seconds(checked_at)
        h, m = sla_sec // 3600, (sla_sec % 3600) // 60
        dot_color = "#1D9E75" if sla_sec > 28800 else ("#EF9F27" if sla_sec > 7200 else "#E24B4A")
        sla_items.append({
            "id": sub["id"],
            "named_insured": sub["named_insured"],
            "sla_label": f"{h}h {m}m remaining",
            "dot_color": dot_color,
        })

    # Governance health
    integrity = audit.verify_integrity()
    llm = _llm()

    decisions: list[dict] = []
    if audit.compliance_log_path.exists():
        with open(audit.compliance_log_path) as fh:
            for line in fh:
                try:
                    decisions.append(json.loads(line.strip()))
                except Exception:
                    pass
    approvals = sum(1 for d in decisions if d.get("choice") == "approve")
    total_dec = len(decisions)
    approval_rate = round(100 * approvals / total_dec) if total_dec else 0

    try:
        with open(_ROOT / "config" / "admitted_states.yaml") as f:
            cfg = yaml.safe_load(f)
        doi_version = cfg.get("metadata", {}).get("version", "—")
    except Exception:
        doi_version = "—"

    try:
        with open(_ROOT / "config" / "fl_moratorium_zips.yaml") as f:
            mc = yaml.safe_load(f)
        moratorium_updated = mc.get("metadata", {}).get("last_updated", "—")
    except Exception:
        moratorium_updated = "—"

    governance = {
        "integrity_ok": integrity["status"] == "ok",
        "approval_rate": approval_rate,
        "doi_version": doi_version,
        "moratorium_updated": moratorium_updated,
        "llm_label": f"{llm['provider'].title()} · {llm['model']}",
        "hitl_mode": os.environ.get("HITL_MODE", "terminal"),
    }

    ctx.update({
        "metrics": metrics,
        "block_reasons": block_reasons,
        "disclosure_types": disclosure_types,
        "sla_items": sla_items,
        "governance": governance,
    })
    return templates.TemplateResponse("insights.html", ctx)


# ---------------------------------------------------------------------------
# GET /w2c/jurisdiction — Aria handoff alias
# ---------------------------------------------------------------------------

@router.get("/w2c/jurisdiction")
def w2c_info():
    return {
        "endpoint": "POST /jurisdiction",
        "description": "W2-C jurisdiction check — submit SubmissionEvent JSON body",
        "schema": "See POST /jurisdiction · OpenAPI at /docs",
    }
