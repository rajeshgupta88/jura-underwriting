from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from jura.models import (
    DOIFlag,
    JurisdictionBlock,
    JurisdictionResult,
    MultiStateConflict,
    SubmissionEvent,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent.parent
_CONFIG = _ROOT / "config"
_TEMPLATES_DIR = _ROOT / "data" / "disclosure_templates"
_DISCLOSURES_DIR = _ROOT / "data" / "disclosures"

# ---------------------------------------------------------------------------
# Config loaded once at import
# ---------------------------------------------------------------------------

with open(_CONFIG / "admitted_states.yaml") as _f:
    ADMITTED_STATES: dict = yaml.safe_load(_f)

with open(_CONFIG / "doi_rules.yaml") as _f:
    DOI_RULES: dict = yaml.safe_load(_f)

with open(_CONFIG / "surplus_lines.yaml") as _f:
    SURPLUS_LINES: dict = yaml.safe_load(_f)

with open(_CONFIG / "fl_moratorium_zips.yaml") as _f:
    FL_MORATORIUM_ZIPS: dict = yaml.safe_load(_f)

with open(_CONFIG / "ca_fair_plan_zips.yaml") as _f:
    CA_FAIR_PLAN_ZIPS: dict = yaml.safe_load(_f)

# ---------------------------------------------------------------------------
# ZIP proxy objects for eval() context
# ---------------------------------------------------------------------------

class _MoratoriumZipProxy:
    _zips: set[str] = set(FL_MORATORIUM_ZIPS["moratorium_zips"])

    def __contains__(self, zip5: str) -> bool:
        return is_moratorium_zip(zip5)


class _FairPlanZipProxy:
    def __contains__(self, zip5: str) -> bool:
        return is_fair_plan_zip(zip5)


# ---------------------------------------------------------------------------
# Rule-topic grouping for conflict detection
# ---------------------------------------------------------------------------

_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "credit_score":  ["credit"],
    "sinkhole":      ["sinkhole"],
    "moratorium":    ["moratorium"],
    "surplus_lines": ["surplus"],
    "free_look":     ["free_look", "free look"],
    "fair_plan":     ["fair_plan", "fair plan"],
}


def _rule_topic(flag: DOIFlag) -> str:
    haystack = (flag.rule_name + " " + flag.rule_id).lower()
    for topic, keywords in _TOPIC_KEYWORDS.items():
        if any(kw in haystack for kw in keywords):
            return topic
    return flag.rule_id


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def detect_jurisdiction(event: SubmissionEvent) -> dict:
    state = event.writing_state
    admitted = state in ADMITTED_STATES["admitted"]
    multi_state = event.writing_state != event.mailing_state
    return {"state": state, "admitted": admitted, "multi_state": multi_state}


def is_fair_plan_zip(zip5: str) -> bool:
    prefixes = CA_FAIR_PLAN_ZIPS["fair_plan_zip_prefixes"]
    return zip5[:3] in prefixes


def is_moratorium_zip(zip5: str) -> bool:
    return zip5 in FL_MORATORIUM_ZIPS["moratorium_zips"]


def evaluate_doi_rules(state: str, event: SubmissionEvent) -> list[DOIFlag]:
    rules = DOI_RULES.get(state) or []
    admitted = state in ADMITTED_STATES["admitted"]

    ctx: dict = {
        # event fields
        "credit_score_used": event.credit_score_used,
        "new_business": event.new_business,
        "property_coverage": event.property_coverage,
        "writing_state": event.writing_state,
        "mailing_state": event.mailing_state,
        "premises_zip": event.premises_zip,
        "tiv": event.tiv or 0.0,
        "sic_code": event.sic_code,
        # derived
        "admitted_market": admitted,
        # YAML boolean literals
        "true": True,
        "false": False,
        # state-code constants (trigger strings use bare identifiers like `FL`)
        "FL": "FL", "CA": "CA", "NY": "NY", "TX": "TX", "IL": "IL",
        # ZIP set proxies
        "CA_FAIR_PLAN_ZIPS": _FairPlanZipProxy(),
        "FL_MORATORIUM_ZIPS": _MoratoriumZipProxy(),
    }

    flags: list[DOIFlag] = []
    for rule in rules:
        try:
            triggered = bool(eval(rule["trigger_condition"], {"__builtins__": {}}, ctx))  # noqa: S307
        except Exception:
            triggered = False

        level: str = rule["type"] if triggered else "clear"
        flags.append(
            DOIFlag(
                rule_id=rule["id"],
                rule_name=rule["name"],
                level=level,
                state=state,
                statutory_ref=rule["statutory_ref"],
                disclosure_template=rule.get("disclosure_template"),
                description=(
                    f"{rule['name']}: rule triggered ({rule['statutory_ref']})"
                    if triggered
                    else f"{rule['name']}: not triggered"
                ),
            )
        )

    return flags


def check_surplus_eligible(state: str, event: SubmissionEvent) -> bool:
    if state not in ADMITTED_STATES["surplus_lines_licensed"]:
        return False

    state_cfg = (SURPLUS_LINES.get("thresholds") or {}).get(state)
    if not state_cfg:
        return True  # licensed, no threshold defined

    tiv_ok = (event.tiv or 0.0) > state_cfg["tiv_threshold"]
    sic_ok = event.sic_code in [str(s) for s in state_cfg.get("eligible_sics", [])]
    return tiv_ok or sic_ok


def generate_es_mock_declinations(state: str, sic: str) -> list[dict]:
    carriers: list[str] = SURPLUS_LINES["mock_admitted_carriers"]
    today = date.today()
    return [
        {
            "carrier": carrier,
            "date": str(today - timedelta(days=random.randint(5, 30))),
            "reason": f"Outside appetite for SIC {sic}",
        }
        for carrier in carriers[:3]
    ]


def detect_multi_state_conflict(states: list[str], flags: list[DOIFlag]) -> list[str]:
    active = [f for f in flags if f.level != "clear"]
    topic_levels: dict[str, set[str]] = defaultdict(set)
    for flag in active:
        topic_levels[_rule_topic(flag)].add(flag.level)
    return [topic for topic, levels in topic_levels.items() if len(levels) > 1]


# ---------------------------------------------------------------------------
# Disclosure doc writer
# ---------------------------------------------------------------------------

def _write_disclosure_doc(event: SubmissionEvent, flag: DOIFlag) -> str:
    if not flag.disclosure_template:
        return ""
    template_path = _TEMPLATES_DIR / flag.disclosure_template
    if not template_path.exists():
        return ""
    _DISCLOSURES_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    filename = f"{event.submission_id}_{flag.rule_id}_{ts}.txt"
    out_path = _DISCLOSURES_DIR / filename
    out_path.write_text(template_path.read_text())
    return str(out_path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_jurisdiction_check(event: SubmissionEvent) -> JurisdictionResult:
    # 1. Detect jurisdiction
    jd = detect_jurisdiction(event)
    state = jd["state"]
    admitted = jd["admitted"]
    multi_state = jd["multi_state"]

    # 2. Hard geo-block checks — raise immediately
    if state == "FL" and is_moratorium_zip(event.premises_zip):
        raise JurisdictionBlock(
            reason=f"FL coastal moratorium applies to ZIP {event.premises_zip}",
            statutory_ref="FL Ins Code §627.351",
            broker_notice_template="fl_moratorium_notice.txt",
        )
    if state == "CA" and is_fair_plan_zip(event.premises_zip):
        raise JurisdictionBlock(
            reason=f"CA FAIR Plan wildfire zone: ZIP {event.premises_zip} (prefix {event.premises_zip[:3]})",
            statutory_ref="CA Ins Code §10091",
            broker_notice_template="ca_fair_plan_notice.txt",
        )

    # 3. Evaluate DOI rules for writing state
    doi_flags = evaluate_doi_rules(state, event)

    # 4. Multi-state: also evaluate mailing state rules
    conflicts: list[str] = []
    if multi_state:
        mailing_flags = evaluate_doi_rules(event.mailing_state, event)
        doi_flags = doi_flags + mailing_flags
        conflicts = detect_multi_state_conflict([state, event.mailing_state], doi_flags)

    # 5. Surplus lines eligibility
    es_eligible = check_surplus_eligible(state, event)

    # 6. Determine market
    has_block = any(f.level == "block" for f in doi_flags)
    if has_block:
        market = "restricted"
    elif conflicts:
        market = "multi_state_conflict"
    elif admitted:
        market = "admitted"
    else:
        market = "surplus_lines"

    # 7. Generate disclosure docs for triggered DISCLOSE flags
    disclosure_docs: list[str] = []
    for flag in doi_flags:
        if flag.level == "disclose" and flag.disclosure_template:
            path = _write_disclosure_doc(event, flag)
            if path:
                disclosure_docs.append(path)

    # 8. Build result
    block_flags = [f for f in doi_flags if f.level == "block"]
    return JurisdictionResult(
        submission_id=event.submission_id,
        writing_state=state,
        market=market,
        admitted=admitted,
        multi_state=multi_state,
        es_eligible=es_eligible,
        doi_flags=doi_flags,
        block_reason=block_flags[0].description if block_flags else None,
        statutory_ref=block_flags[0].statutory_ref if block_flags else None,
        disclosure_docs=disclosure_docs,
        rationale="",
        checked_at=datetime.utcnow(),
    )
