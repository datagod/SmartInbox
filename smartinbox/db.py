"""SQLite persistence for emails, IMAP credentials, and UI settings."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS imap_account (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            email TEXT NOT NULL,
            app_password TEXT NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS emails (
            id TEXT PRIMARY KEY,
            thread_id TEXT,
            account_id TEXT,
            account_email TEXT,
            provider TEXT,
            sender TEXT,
            subject TEXT,
            snippet TEXT,
            body_text TEXT,
            received_at REAL,
            summary_short TEXT,
            summary_detailed TEXT,
            alerted_at REAL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS important_senders (
            sender_key TEXT PRIMARY KEY,
            display TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        """
    )
    conn.commit()
    _ensure_email_account_columns(conn)
    from smartinbox.imap_mail import init_imap_accounts_table
    from smartinbox.calendar_events import init_calendar_tables
    from smartinbox.sender_interest import init_sender_interest_table

    init_imap_accounts_table(conn)
    init_sender_interest_table(conn)
    init_calendar_tables(conn)


def _ensure_email_account_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(emails)").fetchall()}
    for name, ddl in (
        ("account_id", "ALTER TABLE emails ADD COLUMN account_id TEXT"),
        ("account_email", "ALTER TABLE emails ADD COLUMN account_email TEXT"),
        ("provider", "ALTER TABLE emails ADD COLUMN provider TEXT"),
        ("starred", "ALTER TABLE emails ADD COLUMN starred INTEGER NOT NULL DEFAULT 0"),
        ("calendar_ics", "ALTER TABLE emails ADD COLUMN calendar_ics TEXT"),
    ):
        if name not in cols:
            conn.execute(ddl)
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
    conn.commit()


def _normalize_calendar_ics(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text else None


def upsert_email(conn: sqlite3.Connection, email: dict[str, Any]) -> str:
    """Insert or touch an email. Returns 'new', 'ics_updated', or 'existing'."""
    ics = _normalize_calendar_ics(email.get("calendar_ics"))
    existing = conn.execute(
        "SELECT calendar_ics FROM emails WHERE id = ?",
        (email["id"],),
    ).fetchone()
    if existing is not None:
        if ics:
            old_ics = _normalize_calendar_ics(existing["calendar_ics"])
            if old_ics != ics:
                conn.execute(
                    "UPDATE emails SET calendar_ics = ? WHERE id = ?",
                    (ics, email["id"]),
                )
                conn.commit()
                return "ics_updated"
        return "existing"
    conn.execute(
        """
        INSERT INTO emails (
            id, thread_id, account_id, account_email, provider,
            sender, subject, snippet, body_text, calendar_ics,
            received_at, summary_short, summary_detailed, alerted_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            email["id"],
            email.get("thread_id"),
            email.get("account_id"),
            email.get("account_email"),
            email.get("provider"),
            email.get("sender"),
            email.get("subject"),
            email.get("snippet"),
            email.get("body_text"),
            ics,
            email.get("received_at"),
            email.get("summary_short"),
            email.get("summary_detailed"),
            email.get("alerted_at"),
            time.time(),
        ),
    )
    conn.commit()
    return "new"


def update_email_summary(
    conn: sqlite3.Connection,
    email_id: str,
    *,
    summary_short: str | None = None,
    summary_detailed: str | None = None,
) -> None:
    conn.execute(
        "UPDATE emails SET summary_short = ?, summary_detailed = ? WHERE id = ?",
        (summary_short, summary_detailed, email_id),
    )
    conn.commit()


def set_email_starred(
    conn: sqlite3.Connection, email_id: str, *, starred: bool
) -> bool:
    cur = conn.execute(
        "UPDATE emails SET starred = ? WHERE id = ?",
        (1 if starred else 0, email_id),
    )
    conn.commit()
    return cur.rowcount > 0


def mark_email_alerted(conn: sqlite3.Connection, email_id: str) -> None:
    conn.execute(
        "UPDATE emails SET alerted_at = ? WHERE id = ?",
        (time.time(), email_id),
    )
    conn.commit()


def list_emails(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM emails ORDER BY COALESCE(received_at, created_at) DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_email(conn: sqlite3.Connection, email_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    return dict(row) if row else None


def clear_all_emails(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM emails")
    conn.commit()
    return int(cur.rowcount)