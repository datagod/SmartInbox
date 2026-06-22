"""SQLite persistence for emails, OAuth tokens, and UI settings."""

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
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            email TEXT,
            token_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS emails (
            id TEXT PRIMARY KEY,
            thread_id TEXT,
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

        CREATE TABLE IF NOT EXISTS oauth_state (
            state TEXT PRIMARY KEY,
            created_at REAL NOT NULL
        );
        """
    )
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


def save_oauth_token(conn: sqlite3.Connection, email: str, token_json: str) -> None:
    conn.execute(
        "INSERT INTO oauth_tokens (id, email, token_json, updated_at) VALUES (1, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET email = excluded.email, "
        "token_json = excluded.token_json, updated_at = excluded.updated_at",
        (email, token_json, time.time()),
    )
    conn.commit()


def load_oauth_token(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT email, token_json FROM oauth_tokens WHERE id = 1").fetchone()
    if row is None:
        return None
    try:
        token = json.loads(row["token_json"])
    except json.JSONDecodeError:
        return None
    return {"email": row["email"], "token": token}


def clear_oauth_token(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM oauth_tokens WHERE id = 1")
    conn.commit()


def save_oauth_state(conn: sqlite3.Connection, state: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO oauth_state (state, created_at) VALUES (?, ?)",
        (state, time.time()),
    )
    conn.commit()


def pop_oauth_state(conn: sqlite3.Connection, state: str) -> bool:
    row = conn.execute(
        "SELECT created_at FROM oauth_state WHERE state = ?", (state,)
    ).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM oauth_state WHERE state = ?", (state,))
    conn.commit()
    return time.time() - float(row["created_at"]) < 600.0


def upsert_email(conn: sqlite3.Connection, email: dict[str, Any]) -> bool:
    existing = conn.execute("SELECT id FROM emails WHERE id = ?", (email["id"],)).fetchone()
    if existing is not None:
        return False
    conn.execute(
        """
        INSERT INTO emails (
            id, thread_id, sender, subject, snippet, body_text,
            received_at, summary_short, summary_detailed, alerted_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            email["id"],
            email.get("thread_id"),
            email.get("sender"),
            email.get("subject"),
            email.get("snippet"),
            email.get("body_text"),
            email.get("received_at"),
            email.get("summary_short"),
            email.get("summary_detailed"),
            email.get("alerted_at"),
            time.time(),
        ),
    )
    conn.commit()
    return True


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


def mark_email_alerted(conn: sqlite3.Connection, email_id: str) -> None:
    conn.execute(
        "UPDATE emails SET alerted_at = ? WHERE id = ?",
        (time.time(), email_id),
    )
    conn.commit()


def list_emails(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM emails ORDER BY received_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_email(conn: sqlite3.Connection, email_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
    return dict(row) if row else None