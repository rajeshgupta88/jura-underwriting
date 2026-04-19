from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, Undefined

from jura.models import DOIFlag, ESResult, SubmissionEvent

_ROOT = Path(__file__).parent.parent
_DATA = _ROOT / "data"
_HOLD_NOTICES_DIR = _DATA / "hold_notices"
_DISCLOSURES_DIR = _DATA / "disclosures"
_ES_NOTICES_DIR = _DATA / "es_notices"
_DISCLOSURE_TEMPLATES_DIR = _DATA / "disclosure_templates"


def _env(template_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
        undefined=Undefined,
    )


def write_hold_notice(
    submission_id: str,
    event: SubmissionEvent,
    block_reason: str,
    statutory_ref: str,
) -> str:
    _HOLD_NOTICES_DIR.mkdir(parents=True, exist_ok=True)
    template = _env(_DATA).get_template("hold_notice_template.txt")
    content = template.render(
        submission_id=submission_id,
        named_insured=event.named_insured,
        writing_state=event.writing_state,
        block_reason=block_reason,
        statutory_ref=statutory_ref,
        date=date.today().isoformat(),
    )
    out_path = _HOLD_NOTICES_DIR / f"hold_{submission_id}.txt"
    out_path.write_text(content)
    return str(out_path)


def write_disclosure(
    submission_id: str,
    flag: DOIFlag,
    event: SubmissionEvent,
) -> str:
    if not flag.disclosure_template:
        raise ValueError(f"Flag {flag.rule_id!r} has no disclosure_template")
    _DISCLOSURES_DIR.mkdir(parents=True, exist_ok=True)
    template = _env(_DISCLOSURE_TEMPLATES_DIR).get_template(flag.disclosure_template)
    content = template.render(
        named_insured=event.named_insured,
        date=date.today().isoformat(),
        state=event.writing_state,
    )
    out_path = _DISCLOSURES_DIR / f"disclosure_{submission_id}_{flag.rule_id}.txt"
    out_path.write_text(content)
    return str(out_path)


def write_es_notice(
    submission_id: str,
    es_result: ESResult,
    event: SubmissionEvent,
) -> str:
    _ES_NOTICES_DIR.mkdir(parents=True, exist_ok=True)
    n = len(es_result.mock_declinations)
    declination_lines = "\n".join(
        f"  {i+1}. {d['carrier']} ({d['date']}): {d['reason']}"
        for i, d in enumerate(es_result.mock_declinations)
    )
    content = (
        f"E&S PLACEMENT NOTICE\n"
        f"{'=' * 60}\n"
        f"Named Insured: {event.named_insured}\n"
        f"State:         {es_result.state}\n"
        f"Date:          {date.today().isoformat()}\n\n"
        f"E&S placement notice: {event.named_insured} — "
        f"{es_result.state} surplus lines eligible.\n\n"
        f"Diligent Search: {n} admitted carrier(s) declined:\n"
        f"{declination_lines}\n\n"
        f"Carrier: Example Mutual Insurance Co E&S division.\n"
        f"Diligent search requirement met: {es_result.diligent_search_met}\n"
    )
    out_path = _ES_NOTICES_DIR / f"es_notice_{submission_id}.txt"
    out_path.write_text(content)
    return str(out_path)
