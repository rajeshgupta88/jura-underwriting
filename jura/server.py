from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from rich.console import Console

from compliance.review import router as compliance_router
from jura.audit import JurisdictionAuditLogger
from jura.checker import ADMITTED_STATES
from jura.db import SubmissionDB, _SEED
from jura.llm import get_client, get_provider, get_provider_config, llm_status
from jura.models import SubmissionEvent
from jura.router import JurisdictionRouter

console = Console()

_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()

    db = SubmissionDB()
    audit = JurisdictionAuditLogger()
    llm_client = get_client()
    hitl_mode = os.environ.get("HITL_MODE", "terminal")

    import jura.notices as notices_module
    router = JurisdictionRouter(
        db=db,
        audit=audit,
        notices=notices_module,
        llm_client=llm_client,
        hitl_mode=hitl_mode,
    )

    app.state.db = db
    app.state.audit = audit
    app.state.router = router

    provider = get_provider()
    pcfg = get_provider_config()
    aria_endpoint = os.environ.get("ARIA_ENDPOINT", "http://localhost:8001/score")

    console.print()
    console.print("[bold cyan]Jura — Jurisdiction & Regulatory Authority agent[/bold cyan]")
    console.print(f"  Provider: [green]{provider}[/green] · Model: [green]{pcfg['model']}[/green]")
    console.print(f"  Aria endpoint: [dim]{aria_endpoint}[/dim]")
    console.print(f"  HITL mode: [yellow]{hitl_mode}[/yellow]")
    console.print(f"  Port: [dim]8003[/dim]")
    console.print()

    yield
    # shutdown — nothing to tear down


app = FastAPI(title="Jura", version=_VERSION, lifespan=lifespan)
app.include_router(compliance_router)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_row_to_event(row: dict) -> SubmissionEvent:
    return SubmissionEvent(
        submission_id=row["id"],
        named_insured=row["named_insured"],
        pc_account_id=row["pc_account_id"],
        sic_code=row["sic_code"],
        sic_description=row["sic_description"],
        writing_state=row["writing_state"],
        mailing_state=row["mailing_state"],
        premises_zip=row["premises_zip"],
        mailing_zip=row.get("mailing_zip", row["premises_zip"]),
        tiv=row.get("tiv"),
        credit_score_used=bool(row.get("credit_score_used", False)),
        new_business=True,
        property_coverage=True,
        created_at=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/jurisdiction")
async def post_jurisdiction(event: SubmissionEvent):
    router: JurisdictionRouter = app.state.router
    return await router.route(event)


@app.get("/submissions")
def get_submissions(
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
):
    db: SubmissionDB = app.state.db
    return db.list_submissions(status=status, limit=limit)


@app.get("/submissions/{submission_id}")
def get_submission(submission_id: str):
    db: SubmissionDB = app.state.db
    row = db.get_submission(submission_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Submission {submission_id!r} not found")
    return row


@app.get("/health")
def get_health():
    return {
        "status": "ok",
        "version": _VERSION,
        "hitl_mode": os.environ.get("HITL_MODE", "terminal"),
        "llm": llm_status(),
        "aria_endpoint": os.environ.get("ARIA_ENDPOINT", "http://localhost:8001/score"),
    }


@app.post("/test/submit/{n}")
async def test_submit(n: int):
    if not (0 <= n <= 4):
        raise HTTPException(status_code=400, detail="n must be 0–4")
    router: JurisdictionRouter = app.state.router
    row = _SEED[n]
    event = _seed_row_to_event(row)
    return await router.route(event)
