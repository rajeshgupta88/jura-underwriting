from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, computed_field


class SubmissionEvent(BaseModel):
    submission_id: str
    named_insured: str
    pc_account_id: str
    sic_code: str
    sic_description: str
    writing_state: str
    mailing_state: str
    premises_zip: str
    mailing_zip: str
    tiv: float | None = None
    credit_score_used: bool = False
    new_business: bool = True
    property_coverage: bool = True
    created_at: datetime


class DOIFlag(BaseModel):
    rule_id: str
    rule_name: str
    level: Literal["block", "disclose", "warn", "clear"]
    state: str
    statutory_ref: str
    disclosure_template: str | None = None
    description: str


class JurisdictionResult(BaseModel):
    submission_id: str
    writing_state: str
    market: Literal["admitted", "surplus_lines", "restricted", "multi_state_conflict"]
    admitted: bool
    multi_state: bool
    es_eligible: bool
    doi_flags: list[DOIFlag]
    block_reason: str | None
    statutory_ref: str | None
    disclosure_docs: list[str]
    rationale: str
    checked_at: datetime

    @computed_field
    @property
    def has_block(self) -> bool:
        return any(f.level == "block" for f in self.doi_flags)

    @computed_field
    @property
    def has_disclose(self) -> bool:
        return any(f.level == "disclose" for f in self.doi_flags)

    @computed_field
    @property
    def eligible(self) -> bool:
        return self.market != "restricted" and not self.has_block


class ESResult(BaseModel):
    licensed: bool
    state: str
    mock_declinations: list[dict]
    notice_path: str | None = None
    diligent_search_met: bool


class JurisdictionBlock(Exception):
    def __init__(self, reason: str, statutory_ref: str, broker_notice_template: str):
        super().__init__(reason)
        self.reason = reason
        self.statutory_ref = statutory_ref
        self.broker_notice_template = broker_notice_template


class MultiStateConflict(Exception):
    def __init__(self, states: list[str], conflicting_rules: list[str], conflict_summary: str):
        super().__init__(conflict_summary)
        self.states = states
        self.conflicting_rules = conflicting_rules
        self.conflict_summary = conflict_summary
