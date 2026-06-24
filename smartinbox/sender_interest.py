"""Per-sender interest scores learned from inbox up/down votes."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from smartinbox.important_senders import display_sender, normalize_sender

MUTED_SCORE_THRESHOLD = -3


def init_sender_interest_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sender_interest (
            sender_key TEXT PRIMARY KEY,
            display TEXT NOT NULL,
            upvotes INTEGER NOT NULL DEFAULT 0,
            downvotes INTEGER NOT NULL DEFAULT 0,
            last_vote TEXT,
            updated_at REAL NOT NULL
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sender_interest)").fetchall()}
    if "last_vote" not in cols:
        conn.execute("ALTER TABLE sender_interest ADD COLUMN last_vote TEXT")
    conn.commit()


def _row_to_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    up = int(data["upvotes"])
    down = int(data["downvotes"])
    return {
        "sender_key": data["sender_key"],
        "display": data["display"],
        "upvotes": up,
        "downvotes": down,
        "score": up - down,
        "last_vote": data.get("last_vote"),
        "updated_at": data.get("updated_at"),
    }


def is_sender_muted(conn: sqlite3.Connection, sender: str | None) -> bool:
    entry = get_sender_interest(conn, sender)
    return entry is not None and int(entry["score"]) <= MUTED_SCORE_THRESHOLD


def get_sender_interest(conn: sqlite3.Connection, sender: str | None) -> dict[str, Any] | None:
    key = normalize_sender(sender)
    if not key:
        return None
    row = conn.execute(
        "SELECT sender_key, display, upvotes, downvotes, last_vote, updated_at FROM sender_interest WHERE sender_key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_public(row)


def sender_interest_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT sender_key, display, upvotes, downvotes, last_vote, updated_at FROM sender_interest"
    ).fetchall()
    return {str(r["sender_key"]): _row_to_public(r) for r in rows}


def list_ranked_sender_interest(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Return upvoted senders (by upvotes desc) then downvoted (by downvotes desc)."""
    up_rows = conn.execute(
        """
        SELECT sender_key, display, upvotes, downvotes, last_vote, updated_at
        FROM sender_interest
        WHERE upvotes > 0
        ORDER BY upvotes DESC, downvotes ASC, display COLLATE NOCASE ASC
        """
    ).fetchall()
    down_rows = conn.execute(
        """
        SELECT sender_key, display, upvotes, downvotes, last_vote, updated_at
        FROM sender_interest
        WHERE downvotes > 0
        ORDER BY downvotes DESC, upvotes ASC, display COLLATE NOCASE ASC
        """
    ).fetchall()
    return {
        "upvoted": [_row_to_public(r) for r in up_rows],
        "downvoted": [_row_to_public(r) for r in down_rows],
    }


def record_sender_vote(
    conn: sqlite3.Connection, sender: str | None, *, vote: str
) -> dict[str, Any]:
    key = normalize_sender(sender)
    if not key:
        raise ValueError("Could not parse sender address.")
    direction = (vote or "").strip().lower()
    if direction not in ("up", "down"):
        raise ValueError("vote must be 'up' or 'down'")
    display = display_sender(sender)
    col = "upvotes" if direction == "up" else "downvotes"
    conn.execute(
        f"""
        INSERT INTO sender_interest (sender_key, display, upvotes, downvotes, last_vote, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(sender_key) DO UPDATE SET
            display = excluded.display,
            {col} = {col} + 1,
            last_vote = excluded.last_vote,
            updated_at = excluded.updated_at
        """,
        (
            key,
            display,
            1 if direction == "up" else 0,
            1 if direction == "down" else 0,
            direction,
            time.time(),
        ),
    )
    conn.commit()
    entry = get_sender_interest(conn, key)
    assert entry is not None
    return entry