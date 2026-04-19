"""Verification script for DB, audit, and notices layers."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from jura.audit import JurisdictionAuditLogger
from jura.checker import run_jurisdiction_check
from jura.db import SubmissionDB
from jura.models import JurisdictionBlock, SubmissionEvent
from jura.notices import write_disclosure, write_hold_notice

console = Console()
ok = "[green]OK[/green]"
fail = "[red]FAIL[/red]"


# ---------------------------------------------------------------------------
# Step 1: Seed DB and print Rich table
# ---------------------------------------------------------------------------
console.rule("[bold cyan]Step 1 — Seed DB & list submissions")

db = SubmissionDB()
db.seed_sample_data()
rows = db.list_submissions(limit=20)

table = Table(title="Jura Submission DB — All Records", show_lines=True)
table.add_column("ID", style="cyan", no_wrap=True)
table.add_column("Named Insured", style="white")
table.add_column("State", justify="center")
table.add_column("ZIP", justify="center")
table.add_column("TIV ($)", justify="right", style="yellow")
table.add_column("Status", style="magenta")
table.add_column("Market", style="blue")

for r in rows:
    table.add_row(
        r["id"],
        r["named_insured"],
        r["writing_state"],
        r["premises_zip"],
        f"{r['tiv']:,.0f}",
        r["status"],
        r["market"],
    )

console.print(table)
console.print(f"  [dim]{len(rows)} submissions seeded[/dim]\n")


# ---------------------------------------------------------------------------
# Step 2: run_jurisdiction_check on Rossi's (SUB-2024-001)
# ---------------------------------------------------------------------------
console.rule("[bold cyan]Step 2 — run_jurisdiction_check: Rossi's Italian Kitchen")

rossi_row = db.get_submission("SUB-2024-001")
rossi_event = SubmissionEvent(
    submission_id=rossi_row["id"],
    named_insured=rossi_row["named_insured"],
    pc_account_id=rossi_row["pc_account_id"],
    sic_code=rossi_row["sic_code"],
    sic_description=rossi_row["sic_description"],
    writing_state=rossi_row["writing_state"],
    mailing_state=rossi_row["mailing_state"],
    premises_zip=rossi_row["premises_zip"],
    mailing_zip=rossi_row["premises_zip"],
    tiv=rossi_row["tiv"],
    credit_score_used=bool(rossi_row["credit_score_used"]),
    created_at=datetime.utcnow(),
)

rossi_result = run_jurisdiction_check(rossi_event)
console.print(f"  market      : [bold]{rossi_result.market}[/bold]")
console.print(f"  admitted    : {rossi_result.admitted}")
console.print(f"  eligible    : {rossi_result.eligible}")
console.print(f"  has_disclose: {rossi_result.has_disclose}")
console.print(f"  has_block   : {rossi_result.has_block}")
console.print(f"  doi_flags   :")
for f in rossi_result.doi_flags:
    style = "red" if f.level == "block" else "yellow" if f.level == "disclose" else "dim"
    console.print(f"    [{style}]{f.level:10}[/{style}]  {f.rule_id}  ({f.statutory_ref})")

# Log to audit
audit = JurisdictionAuditLogger()
audit.log_jurisdiction(rossi_result, named_insured=rossi_event.named_insured)
console.print(f"\n  Logged to audit: {ok}\n")


# ---------------------------------------------------------------------------
# Step 3: Disclosure doc for Rossi's CA AB 2414
# ---------------------------------------------------------------------------
console.rule("[bold cyan]Step 3 — Disclosure doc: Rossi's ca_ab2414")

disclose_flags = [f for f in rossi_result.doi_flags if f.level == "disclose" and f.disclosure_template]
assert disclose_flags, "Expected at least one disclose flag with template"

disc_path = write_disclosure("SUB-2024-001", disclose_flags[0], rossi_event)
exists = Path(disc_path).exists()
console.print(f"  disclosure_template : {disclose_flags[0].disclosure_template}")
console.print(f"  written to          : {disc_path}")
console.print(f"  file exists         : {ok if exists else fail}")
assert exists, f"Disclosure file not found: {disc_path}"
console.print()


# ---------------------------------------------------------------------------
# Step 4: Hold notice for Harbor View (FL moratorium)
# ---------------------------------------------------------------------------
console.rule("[bold cyan]Step 4 — Hold notice: Harbor View Lounge (FL moratorium)")

harbor_row = db.get_submission("SUB-2024-003")
harbor_event = SubmissionEvent(
    submission_id=harbor_row["id"],
    named_insured=harbor_row["named_insured"],
    pc_account_id=harbor_row["pc_account_id"],
    sic_code=harbor_row["sic_code"],
    sic_description=harbor_row["sic_description"],
    writing_state=harbor_row["writing_state"],
    mailing_state=harbor_row["mailing_state"],
    premises_zip=harbor_row["premises_zip"],
    mailing_zip=harbor_row["premises_zip"],
    tiv=harbor_row["tiv"],
    credit_score_used=bool(harbor_row["credit_score_used"]),
    created_at=datetime.utcnow(),
)

try:
    run_jurisdiction_check(harbor_event)
    console.print(f"  [red]Expected JurisdictionBlock — none raised[/red]")
    sys.exit(1)
except JurisdictionBlock as exc:
    console.print(f"  JurisdictionBlock caught: [bold red]{exc.reason}[/bold red]")
    hold_path = write_hold_notice(
        "SUB-2024-003",
        harbor_event,
        exc.reason,
        exc.statutory_ref,
    )
    hold_exists = Path(hold_path).exists()
    console.print(f"  hold notice written : {hold_path}")
    console.print(f"  file exists         : {ok if hold_exists else fail}")
    assert hold_exists, f"Hold notice file not found: {hold_path}"
    audit.log_jurisdiction(
        __import__("jura.models", fromlist=["JurisdictionResult"])  # skip logging block — no result
        if False else None or True and True,  # no-op placeholder
    ) if False else None

console.print()


# ---------------------------------------------------------------------------
# Step 5: Verify jurisdiction_log.jsonl integrity
# ---------------------------------------------------------------------------
console.rule("[bold cyan]Step 5 — Verify jurisdiction_log.jsonl integrity")

integrity = audit.verify_integrity()
entries = audit.read_jurisdiction_log()

console.print(f"  total entries : {integrity['total']}")
console.print(f"  valid hashes  : {integrity['valid']}")
console.print(f"  invalid       : {integrity['invalid']}")
console.print(f"  status        : [bold]{'[green]' + integrity['status'] + '[/green]' if integrity['status'] == 'ok' else '[red]' + integrity['status'] + '[/red]'}[/bold]")

if entries:
    console.print(f"\n  Sample log entry (submission_id={entries[0]['submission_id']}):")
    for k in ("submission_id", "named_insured", "market", "eligible", "has_block", "log_hash"):
        console.print(f"    {k:20}: {entries[0].get(k)}")

assert integrity["status"] == "ok", f"Integrity check failed: {integrity['errors']}"
assert integrity["total"] >= 1, "Expected at least one log entry"
console.print(f"\n  Integrity: {ok}\n")

console.rule("[bold green]All 5 verification steps passed")
