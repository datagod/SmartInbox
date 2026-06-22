"""Important sender list and per-tier alert policies."""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Any

IMPORTANT_ALERT_MODES = ("always", "cooldown", "silent")
OTHER_ALERT_MODES = ("cooldown", "silent")

DEFAULT_IMPORTANT_ALERT_MODE = "always"
DEFAULT_OTHER_ALERT_MODE = "cooldown"


def normalize_sender(sender: str | None) -> str:
    """Extract and normalize an email address from a From header."""
    text = (sender or "").strip()
    if not text:
        return ""
    match = re.search(r"<([^>]+)>", text)
    if match:
        return match.group(1).strip().lower()
    if "@" in text:
        return text.lower()
    return text.lower()


def display_sender(sender: str | None) -> str:
    text = (sender or "").strip()
    return text or normalize_sender(text)


def normalize_important_alert_mode(value: Any) -> str:
    mode = str(value or DEFAULT_IMPORTANT_ALERT_MODE).strip().lower()
    return mode if mode in IMPORTANT_ALERT_MODES else DEFAULT_IMPORTANT_ALERT_MODE


def normalize_other_alert_mode(value: Any) -> str:
    mode = str(value or DEFAULT_OTHER_ALERT_MODE).strip().lower()
    return mode if mode in OTHER_ALERT_MODES else DEFAULT_OTHER_ALERT_MODE


def init_important_senders_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS important_senders (
            sender_key TEXT PRIMARY KEY,
            display TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.commit()


def list_important_senders(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT sender_key, display, created_at FROM important_senders ORDER BY display COLLATE NOCASE"
    ).fetchall()
    return [dict(r) for r in rows]


def important_sender_keys(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT sender_key FROM important_senders").fetchall()
    return {str(r["sender_key"]) for r in rows}


def is_important_sender(conn: sqlite3.Connection, sender: str | None) -> bool:
    key = normalize_sender(sender)
    if not key:
        return False
    row = conn.execute(
        "SELECT 1 FROM important_senders WHERE sender_key = ?", (key,)
    ).fetchone()
    return row is not None


def add_important_sender(conn: sqlite3.Connection, sender: str | None) -> dict[str, Any]:
    key = normalize_sender(sender)
    if not key:
        raise ValueError("Could not parse sender address.")
    display = display_sender(sender)
    conn.execute(
        """
        INSERT INTO important_senders (sender_key, display, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(sender_key) DO UPDATE SET display = excluded.display
        """,
        (key, display, time.time()),
    )
    conn.commit()
    return {"sender_key": key, "display": display}


def remove_important_sender(conn: sqlite3.Connection, sender_key: str) -> bool:
    key = normalize_sender(sender_key) or sender_key.strip().lower()
    cur = conn.execute("DELETE FROM important_senders WHERE sender_key = ?", (key,))
    conn.commit()
    return cur.rowcount > 0