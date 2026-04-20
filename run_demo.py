#!/usr/bin/env python3
"""Demo launcher — seeds submissions and optionally opens the exec UI."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx
from datetime import datetime, timezone
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

BASE_URL = "http://localhost:8003"
JURA_PORT = 8003
console = Console()

# ---------------------------------------------------------------------------
# Sample payloads (mirrors _SEED in jura/db.py)
# ---------------------------------------------------------------------------

_SAMPLES = [
    {
        "submission_id": "SUB-2024-001",
        "named_insured": "Rossi's Italian Kitchen LLC",
        "pc_account_id": "PC-1001",
        "sic_code": "5812",
        "sic_description": "Eating places",
        "writing_state": "CA",
        "mailing_state": "CA",
        "premises_zip": "90001",
        "mailing_zip": "90001",
        "tiv": 800_000.0,
        "credit_score_used": True,
        "new_business": True,
        "property_coverage": True,
    },
    {
        "submission_id": "SUB-2024-002",
        "named_insured": "Patel Food Markets Inc",
        "pc_account_id": "PC-1002",
        "sic_code": "5411",
        "sic_description": "Grocery stores",
        "writing_state": "TX",
        "mailing_state": "TX",
        "premises_zip": "78701",
        "mailing_zip": "78701",
        "tiv": 300_000.0,
        "credit_score_used": False,
        "new_business": True,
        "property_coverage": True,
    },
    {
        "submission_id": "SUB-2024-003",
        "named_insured": "Harbor View Lounge LLC",
        "pc_account_id": "PC-1003",
        "sic_code": "5813",
        "sic_description": "Drinking places (alcoholic beverages)",
        "writing_state": "FL",
        "mailing_state": "FL",
        "premises_zip": "33139",
        "mailing_zip": "33139",
        "tiv": 1_500_000.0,
        "credit_score_used": False,
        "new_business": True,
        "property_coverage": True,
    },
    {
        "submission_id": "SUB-2024-004",
        "named_insured": "Gulf Coast Marine Supply Inc",
        "pc_account_id": "PC-1004",
        "sic_code": "5551",
        "sic_description": "Boat dealers",
        "writing_state": "TX",
        "mailing_state": "TX",
        "premises_zip": "77002",
        "mailing_zip": "77002",
        "tiv": 6_200_000.0,
        "credit_score_used": False,
        "new_business": True,
        "property_coverage": True,
    },
    {
        "submission_id": "SUB-2024-005",
        "named_insured": "Meridian Coastal Properties LLC",
        "pc_account_id": "PC-1005",
        "sic_code": "6512",
        "sic_description": "Operators of apartment buildings",
        "writing_state": "CA",
        "mailing_state": "NV",
        "premises_zip": "90025",
        "mailing_zip": "89501",
        "tiv": 2_200_000.0,
        "credit_score_used": True,
        "new_business": True,
        "property_coverage": True,
    },
]


# ---------------------------------------------------------------------------
# Health check / server management
# ---------------------------------------------------------------------------

def _is_running() -> bool:
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _wait_for_server(timeout: int = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_running():
            return True
        time.sleep(0.5)
    return False


def _start_server() -> subprocess.Popen | None:
    """Start Jura in browser HITL mode (non-blocking)."""
    env = os.environ.copy()
    env["HITL_MODE"] = "browser"
    python = sys.executable
    proc = subprocess.Popen(
        [python, "-m", "uvicorn", "jura.server:app", "--port", str(JURA_PORT), "--log-level", "warning"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


# ---------------------------------------------------------------------------
# Data reset
# ---------------------------------------------------------------------------

def _reset_data() -> None:
    """Clear DB submissions and audit logs for a clean demo run."""
    root = Path(__file__).parent
    data_dir = root / "data"
    data_dir.mkdir(exist_ok=True)

    # Clear JSONL logs
    for fname in ("jurisdiction_log.jsonl", "compliance_decisions.jsonl"):
        p = data_dir / fname
        p.write_text("")

    # Clear DB via API
    try:
        httpx.post(f"{BASE_URL}/test/reset", timeout=5)
    except Exception:
        pass

    # Also clear via direct DB if available
    try:
        from jura.db import SubmissionDB
        db = SubmissionDB()
        db.clear_submissions()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Submit samples
# ---------------------------------------------------------------------------

def _submit_samples() -> list[dict]:
    results = []
    now = datetime.now(timezone.utc).isoformat()
    for sample in _SAMPLES:
        payload = {**sample, "created_at": now}
        try:
            r = httpx.post(f"{BASE_URL}/jurisdiction", json=payload, timeout=10)
            outcome = r.json() if r.status_code == 200 else {"error": r.text[:80]}
        except Exception as exc:
            outcome = {"error": str(exc)}
        results.append({"sample": sample, "outcome": outcome})
    return results


# ---------------------------------------------------------------------------
# Summary panel
# ---------------------------------------------------------------------------

def _print_summary(results: list[dict]) -> None:
    console.print()

    table = Table(title="Jura Demo — Submission Results", show_lines=True)
    table.add_column("Named insured", style="bold", min_width=28)
    table.add_column("State", justify="center", width=6)
    table.add_column("Market", width=14)
    table.add_column("Outcome", min_width=22)

    outcome_style = {
        "jurisdiction_blocked":     "[red]",
        "admitted_disclose_pending": "[yellow]",
        "forwarded_to_aria":        "[green]",
        "admitted_clear":           "[green]",
        "surplus_confirmed":        "[magenta]",
        "surplus_pending":          "[magenta]",
        "multi_state_conflict":     "[yellow]",
    }

    for item in results:
        s = item["sample"]
        o = item["outcome"]
        if "error" in o:
            outcome_str = f"[red]ERROR: {o['error']}"
            market_str  = "—"
        else:
            outcome = o.get("outcome", "—")
            market  = o.get("market", "—")
            style   = outcome_style.get(outcome, "")
            outcome_str = f"{style}{outcome}"
            market_str  = market
        table.add_row(
            s["named_insured"],
            s["writing_state"],
            market_str,
            outcome_str,
        )

    console.print(table)
    console.print()

    # Screen index
    screens = Table(title="Exec Demo Screens", show_header=True)
    screens.add_column("Screen", style="bold cyan", min_width=22)
    screens.add_column("URL", style="dim")
    screens.add_column("Description", min_width=36)
    rows = [
        ("Submission queue", f"{BASE_URL}/",          "All submissions · filter by status"),
        ("Jurisdiction blocks", f"{BASE_URL}/blocks",    "Blocked subs + pattern detection"),
        ("Audit log",        f"{BASE_URL}/audit",     "SHA-256 tamper-evident log + decisions"),
        ("Insights",         f"{BASE_URL}/insights",  "Clear rate · SLA · governance health"),
        ("Compliance review", f"{BASE_URL}/compliance", "Disclosure review portal"),
    ]
    for name, url, desc in rows:
        screens.add_row(name, url, desc)
    console.print(screens)
    console.print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Jura exec demo launcher")
    parser.add_argument("--demo", action="store_true", help="Open browser after seeding")
    parser.add_argument("--no-reset", action="store_true", help="Skip data reset")
    args = parser.parse_args()

    managed_proc: subprocess.Popen | None = None

    # 1. Health check / auto-start
    if _is_running():
        console.print(f"[green]✓[/green] Jura already running on port {JURA_PORT}")
    else:
        console.print(f"[yellow]⟳[/yellow] Starting Jura on port {JURA_PORT} …")
        managed_proc = _start_server()
        if not _wait_for_server(timeout=20):
            console.print("[red]✗ Server failed to start within 20s. Run manually:[/red]")
            console.print(f"  [dim]HITL_MODE=browser uvicorn jura.server:app --port {JURA_PORT}[/dim]")
            sys.exit(1)
        console.print(f"[green]✓[/green] Server started (pid {managed_proc.pid})")

    # 2. Reset data
    if not args.no_reset:
        console.print("[dim]Resetting demo data …[/dim]")
        _reset_data()

    # 3. Submit samples
    console.print("[dim]Submitting 5 sample submissions …[/dim]")
    results = _submit_samples()

    # 4. Summary
    _print_summary(results)

    # 5. Optionally open browser
    if args.demo:
        url = f"{BASE_URL}/"
        console.print(f"[cyan]Opening browser → {url}[/cyan]")
        webbrowser.open(url)
        console.print()
        console.print(Panel(
            f"[bold]Jura exec demo running[/bold]\n\n"
            f"  Queue:      {BASE_URL}/\n"
            f"  Blocks:     {BASE_URL}/blocks\n"
            f"  Audit:      {BASE_URL}/audit\n"
            f"  Insights:   {BASE_URL}/insights\n"
            f"  Compliance: {BASE_URL}/compliance\n\n"
            "[dim]Press Ctrl-C to stop.[/dim]",
            title="Jura",
            border_style="cyan",
        ))
        try:
            if managed_proc:
                managed_proc.wait()
            else:
                while True:
                    time.sleep(60)
        except KeyboardInterrupt:
            console.print("\n[dim]Shutting down.[/dim]")
            if managed_proc:
                managed_proc.terminate()


if __name__ == "__main__":
    main()
