from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_DB_PATH = _ROOT / "data" / "jura.db"

VALID_STATUSES: set[str] = {
    "jura_pending",
    "jura_checking",
    "admitted_clear",
    "admitted_disclose_pending",
    "admitted_disclose_approved",
    "surplus_pending",
    "surplus_confirmed",
    "multi_state_conflict",
    "jurisdiction_blocked",
    "forwarded_to_aria",
}

_SEED: list[dict] = [
    {
        "id": "SUB-2024-001",
        "named_insured": "Rossi's Italian Kitchen LLC",
        "sic_code": "5812",
        "sic_description": "Eating places",
        "writing_state": "CA",
        "mailing_state": "CA",
        # 90001 (South LA, prefix 900) — not a FAIR Plan wildfire zone; used so
        # run_jurisdiction_check returns a disclose result rather than a geo-block.
        "premises_zip": "90001",
        "tiv": 800_000.0,
        "credit_score_used": 1,
        "pc_account_id": "PC-1001",
        "status": "admitted_disclose_pending",
        "market": "surplus_lines",
        "jurisdiction_outcome": "CA credit score disclosure required (CA Ins Code §1861.05 / AB 2414)",
    },
    {
        "id": "SUB-2024-002",
        "named_insured": "Patel Food Markets Inc",
        "sic_code": "5411",
        "sic_description": "Grocery stores",
        "writing_state": "TX",
        "mailing_state": "TX",
        "premises_zip": "78701",
        "tiv": 300_000.0,
        "credit_score_used": 0,
        "pc_account_id": "PC-1002",
        "status": "forwarded_to_aria",
        "market": "admitted",
        "jurisdiction_outcome": "Admitted — all clear. Forwarded to Aria for scoring.",
    },
    {
        "id": "SUB-2024-003",
        "named_insured": "Harbor View Lounge LLC",
        "sic_code": "5813",
        "sic_description": "Drinking places (alcoholic beverages)",
        "writing_state": "FL",
        "mailing_state": "FL",
        "premises_zip": "33139",
        "tiv": 1_500_000.0,
        "credit_score_used": 0,
        "pc_account_id": "PC-1003",
        "status": "jurisdiction_blocked",
        "market": "restricted",
        "jurisdiction_outcome": (
            "FL coastal moratorium applies to ZIP 33139 (Miami Beach). "
            "New business prohibited per FL Ins Code §627.351."
        ),
    },
    {
        "id": "SUB-2024-004",
        "named_insured": "Gulf Coast Marine Supply Inc",
        "sic_code": "5551",
        "sic_description": "Boat dealers",
        "writing_state": "TX",
        "mailing_state": "TX",
        "premises_zip": "77002",
        "tiv": 6_200_000.0,
        "credit_score_used": 0,
        "pc_account_id": "PC-1004",
        "status": "surplus_confirmed",
        "market": "surplus_lines",
        "jurisdiction_outcome": (
            "TIV $6.2M exceeds TX E&S threshold ($5M). "
            "Diligent search required (min 2 declinations) per TX Ins Code §981.004."
        ),
    },
    {
        "id": "SUB-2024-005",
        "named_insured": "Meridian Coastal Properties LLC",
        "sic_code": "6512",
        "sic_description": "Operators of apartment buildings",
        "writing_state": "CA",
        "mailing_state": "NV",
        "premises_zip": "90025",
        "tiv": 2_200_000.0,
        "credit_score_used": 1,
        "pc_account_id": "PC-1005",
        "status": "multi_state_conflict",
        "market": "multi_state_conflict",
        "jurisdiction_outcome": (
            "Multi-state conflict: CA requires credit score disclosure (CA Ins Code §1861.05), "
            "NV has no equivalent rule. Compliance review required before binding."
        ),
    },
]


class SubmissionDB:
    def __init__(self, db_path: str | Path = _DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS submissions (
                    id                   TEXT PRIMARY KEY,
                    named_insured        TEXT,
                    sic_code             TEXT,
                    sic_description      TEXT,
                    writing_state        TEXT,
                    mailing_state        TEXT,
                    premises_zip         TEXT,
                    tiv                  REAL,
                    credit_score_used    INTEGER,
                    pc_account_id        TEXT,
                    status               TEXT,
                    market               TEXT,
                    jurisdiction_outcome TEXT,
                    raw_payload          TEXT,
                    created_at           TEXT
                )
            """)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def insert_submission(self, data: dict) -> None:
        now = datetime.utcnow().isoformat()
        payload = data.get("raw_payload") or json.dumps(
            {k: v for k, v in data.items() if k != "raw_payload"}
        )
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO submissions
                    (id, named_insured, sic_code, sic_description,
                     writing_state, mailing_state, premises_zip,
                     tiv, credit_score_used, pc_account_id,
                     status, market, jurisdiction_outcome,
                     raw_payload, created_at)
                VALUES
                    (:id, :named_insured, :sic_code, :sic_description,
                     :writing_state, :mailing_state, :premises_zip,
                     :tiv, :credit_score_used, :pc_account_id,
                     :status, :market, :jurisdiction_outcome,
                     :raw_payload, :created_at)
                """,
                {
                    **data,
                    "raw_payload": payload,
                    "created_at": now,
                },
            )

    def update_status(self, submission_id: str, status: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status {status!r}. Must be one of {sorted(VALID_STATUSES)}")
        with self._conn() as conn:
            conn.execute(
                "UPDATE submissions SET status = ? WHERE id = ?",
                (status, submission_id),
            )

    def update_market(self, submission_id: str, market: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE submissions SET market = ? WHERE id = ?",
                (market, submission_id),
            )

    def get_submission(self, submission_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM submissions WHERE id = ?", (submission_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_submissions(self, status: str | None = None, limit: int = 20) -> list[dict]:
        sql = "SELECT * FROM submissions"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_compliance_queue(self) -> list[dict]:
        return self.list_submissions(status="admitted_disclose_pending", limit=100)

    def get_blocks(self) -> list[dict]:
        return self.list_submissions(status="jurisdiction_blocked", limit=100)

    def seed_sample_data(self) -> None:
        for sub in _SEED:
            self.insert_submission(sub)
