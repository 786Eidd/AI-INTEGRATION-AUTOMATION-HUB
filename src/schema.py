"""
Database connectivity — real SQLite via Python's standard library
`sqlite3`, not a JSON file standing in for a database. No extra dependency
(no SQLAlchemy) because a single-file relational store with real SQL
(foreign keys, a UNIQUE constraint used for idempotency — see
`insert_lead`'s ON CONFLICT handling) is exactly what `sqlite3` is for; an
ORM would add a layer without adding a capability this project needs.
Swapping in Postgres/MySQL later is a connection-string change plus
whatever ORM you prefer, not a redesign — every write here goes through
parameterized SQL, not string formatting.

`HUB_DB_PATH` (env var) lets `src/evaluate.py` point this at an isolated,
throwaway database file so repeated evaluation runs don't accumulate stale
leads in the same file you'd use for a real run of `src/app.py`.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "hub.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id),
    email TEXT NOT NULL UNIQUE,
    company TEXT,
    employee_count INTEGER,
    urgency TEXT NOT NULL,
    deal_size_tier TEXT NOT NULL,
    status TEXT NOT NULL,
    crm_contact_id TEXT,
    crm_deal_id TEXT,
    classification_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id),
    channel TEXT NOT NULL,
    priority TEXT NOT NULL,
    message TEXT NOT NULL,
    delivered INTEGER NOT NULL,
    sent_at TEXT NOT NULL
);
"""


def get_db_path() -> str:
    return os.environ.get("HUB_DB_PATH", str(_DEFAULT_DB_PATH))


@contextmanager
def get_connection():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)


def insert_event(source: str, raw_payload: Dict[str, Any], received_at: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO events (received_at, source, raw_payload_json) VALUES (?, ?, ?)",
            (received_at, source, json.dumps(raw_payload)),
        )
        return cur.lastrowid


def upsert_lead(
    event_id: int, email: str, company: Optional[str], employee_count: Optional[int],
    urgency: str, deal_size_tier: str, status: str, classification_path: str, created_at: str,
) -> tuple[int, bool]:
    """Returns (lead_id, was_new). Idempotent on email via the UNIQUE
    constraint — re-processing the same lead's email updates the existing
    row instead of creating a duplicate, the same way a real CRM sync
    would need to behave."""
    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM leads WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.execute(
                """UPDATE leads SET event_id=?, company=?, employee_count=?, urgency=?,
                   deal_size_tier=?, status=?, classification_path=?, created_at=? WHERE email=?""",
                (event_id, company, employee_count, urgency, deal_size_tier, status,
                 classification_path, created_at, email),
            )
            return existing["id"], False
        cur = conn.execute(
            """INSERT INTO leads (event_id, email, company, employee_count, urgency,
               deal_size_tier, status, classification_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, email, company, employee_count, urgency, deal_size_tier, status,
             classification_path, created_at),
        )
        return cur.lastrowid, True


def update_lead_crm_ids(lead_id: int, crm_contact_id: Optional[str], crm_deal_id: Optional[str], status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE leads SET crm_contact_id=?, crm_deal_id=?, status=? WHERE id=?",
            (crm_contact_id, crm_deal_id, status, lead_id),
        )


def insert_notification(lead_id: Optional[int], channel: str, priority: str, message: str, delivered: bool, sent_at: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO notifications (lead_id, channel, priority, message, delivered, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
            (lead_id, channel, priority, message, int(delivered), sent_at),
        )
        return cur.lastrowid


def list_leads() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM leads ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def get_lead_by_email(email: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM leads WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None
