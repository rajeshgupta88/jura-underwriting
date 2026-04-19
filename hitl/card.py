"""Rich terminal cards for Jura HITL review flows."""
from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from jura.models import JurisdictionBlock, JurisdictionResult, MultiStateConflict, SubmissionEvent

console = Console()


# ---------------------------------------------------------------------------
# Block card — display only, no override allowed
# ---------------------------------------------------------------------------

def render_block_card(event: "SubmissionEvent", block_info) -> None:
    from jura.models import JurisdictionBlock, JurisdictionResult

    if isinstance(block_info, JurisdictionBlock):
        reason = block_info.reason
        statutory_ref = block_info.statutory_ref
    else:  # JurisdictionResult with has_block=True
        reason = block_info.block_reason or "Jurisdiction block — see DOI flags"
        statutory_ref = block_info.statutory_ref or "N/A"

    content = Table.grid(padding=(0, 2))
    content.add_column(style="bold dim", no_wrap=True)
    content.add_column()
    content.add_row("Named Insured", event.named_insured)
    content.add_row("Writing State", event.writing_state)
    content.add_row("ZIP Code", event.premises_zip)
    content.add_row("SIC", f"{event.sic_code} — {event.sic_description}")
    content.add_row("TIV", f"${event.tiv:,.0f}" if event.tiv else "N/A")
    content.add_row("", "")
    content.add_row("Block Reason", Text(reason, style="bold red"))
    content.add_row("Statutory Ref", Text(statutory_ref, style="yellow"))

    console.print()
    console.print(Panel(
        content,
        title="[bold red]Jura — Jurisdiction Block[/bold red]",
        subtitle="[dim]Hold notice written to data/hold_notices/[/dim]",
        border_style="red",
        padding=(1, 2),
    ))
    console.print(
        "[dim]No human override available — block is automatic per statutory requirement.[/dim]\n"
    )


# ---------------------------------------------------------------------------
# Disclose card — prompt reviewer to approve or request changes
# ---------------------------------------------------------------------------

def render_disclose_card(
    event: "SubmissionEvent",
    result: "JurisdictionResult",
) -> tuple[str, str, str]:
    """Returns (choice: 'A'|'R', reviewer_id: str, notes: str)."""
    flags_table = Table(
        "Rule ID", "Rule Name", "Statutory Ref", "Template",
        show_header=True,
        header_style="bold cyan",
        show_lines=True,
    )
    for f in result.doi_flags:
        if f.level == "disclose":
            flags_table.add_row(
                f.rule_id,
                f.rule_name,
                f.statutory_ref,
                f.disclosure_template or "—",
            )

    docs_text = "\n".join(f"  • {p}" for p in result.disclosure_docs) or "  (none yet)"

    console.print()
    console.print(Panel(
        flags_table,
        title="[bold yellow]Jura — Disclosure Required[/bold yellow]",
        subtitle=f"[dim]Submission: {event.submission_id} · {event.named_insured}[/dim]",
        border_style="yellow",
        padding=(1, 2),
    ))
    console.print(f"[dim]Disclosure docs written to data/disclosures/:[/dim]")
    console.print(docs_text)
    console.print()
    console.print("[bold]Review actions:[/bold]  [green][A][/green] Approve & forward to Aria"
                  "   [red][R][/red] Request changes")
    console.print()

    choice = ""
    while choice not in ("A", "R"):
        choice = Prompt.ask("Choice", choices=["A", "R"]).upper()

    reviewer_id = Prompt.ask("Reviewer ID")
    notes = Prompt.ask("Notes (optional)", default="")

    return choice, reviewer_id, notes


# ---------------------------------------------------------------------------
# Conflict card — escalate or override
# ---------------------------------------------------------------------------

def render_conflict_card(
    event: "SubmissionEvent",
    exc: "MultiStateConflict",
) -> tuple[str, str]:
    """Returns (choice: 'E'|'O', reason: str)."""
    states_str = ", ".join(exc.states)
    rules_str = "\n".join(f"  • {r}" for r in exc.conflicting_rules) or "  (unknown)"

    content = Table.grid(padding=(0, 2))
    content.add_column(style="bold dim", no_wrap=True)
    content.add_column()
    content.add_row("Named Insured", event.named_insured)
    content.add_row("States", Text(states_str, style="bold"))
    content.add_row("SIC", f"{event.sic_code} — {event.sic_description}")
    content.add_row("", "")
    content.add_row("Conflicting Topics", Text(rules_str, style="red"))
    content.add_row("Summary", exc.conflict_summary)

    console.print()
    console.print(Panel(
        content,
        title="[bold magenta]Jura — Multi-State Conflict[/bold magenta]",
        subtitle=f"[dim]Submission: {event.submission_id}[/dim]",
        border_style="magenta",
        padding=(1, 2),
    ))
    console.print("[bold]Actions:[/bold]  [yellow][E][/yellow] Escalate to compliance team"
                  "   [red][O][/red] Override (requires reason)")
    console.print()

    choice = ""
    while choice not in ("E", "O"):
        choice = Prompt.ask("Choice", choices=["E", "O"]).upper()

    reason = ""
    if choice == "O":
        reason = Prompt.ask("Override reason (required)")

    return choice, reason
