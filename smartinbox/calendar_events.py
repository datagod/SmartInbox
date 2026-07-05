"""Calendar events extracted from email bodies."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from typing import Any

from smartinbox.important_senders import display_sender, normalize_sender


def init_calendar_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_events (
            id TEXT PRIMARY KEY,
            email_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            location TEXT,
            event_start REAL NOT NULL,
            event_end REAL,
            source_text TEXT,
            sender TEXT,
            subject TEXT,
            upvotes INTEGER NOT NULL DEFAULT 0,
            downvotes INTEGER NOT NULL DEFAULT 0,
            last_vote TEXT,
            hidden INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_extraction_log (
            email_id TEXT PRIMARY KEY,
            extracted_at REAL NOT NULL,
            event_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_calendar_events_start ON calendar_events(event_start)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_ignored_senders (
            sender_key TEXT PRIMARY KEY,
            display TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    _ensure_calendar_sender_key_column(conn)
    conn.commit()


def _ensure_calendar_sender_key_column(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(calendar_events)").fetchall()}
    if "sender_key" not in cols:
        conn.execute("ALTER TABLE calendar_events ADD COLUMN sender_key TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_calendar_events_sender_key "
        "ON calendar_events(sender_key)"
    )
    rows = conn.execute(
        """
        SELECT id, sender FROM calendar_events
        WHERE sender_key IS NULL OR TRIM(sender_key) = ''
        """
    ).fetchall()
    for row in rows:
        key = normalize_sender(str(row["sender"] or ""))
        if not key:
            continue
        conn.execute(
            "UPDATE calendar_events SET sender_key = ? WHERE id = ?",
            (key, row["id"]),
        )


def add_calendar_ignored_sender(
    conn: sqlite3.Connection, sender: str | None
) -> dict[str, Any]:
    key = normalize_sender(sender)
    if not key:
        raise ValueError("Could not parse sender address.")
    now = time.time()
    display = display_sender(sender)
    conn.execute(
        """
        INSERT INTO calendar_ignored_senders (sender_key, display, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(sender_key) DO UPDATE SET
            display = excluded.display
        """,
        (key, display, now),
    )
    conn.commit()
    return {"sender_key": key, "display": display, "created_at": now}


def remove_calendar_ignored_sender(
    conn: sqlite3.Connection, sender: str | None
) -> bool:
    key = normalize_sender(sender)
    if not key:
        return False
    cur = conn.execute(
        "DELETE FROM calendar_ignored_senders WHERE sender_key = ?",
        (key,),
    )
    conn.commit()
    return int(cur.rowcount) > 0


def is_calendar_sender_ignored(conn: sqlite3.Connection, sender: str | None) -> bool:
    key = normalize_sender(sender)
    if not key:
        return False
    row = conn.execute(
        "SELECT 1 FROM calendar_ignored_senders WHERE sender_key = ?",
        (key,),
    ).fetchone()
    return row is not None


def list_calendar_ignored_senders(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT sender_key, display, created_at
        FROM calendar_ignored_senders
        ORDER BY display COLLATE NOCASE ASC
        """
    ).fetchall()
    return [
        {
            "sender_key": str(r["sender_key"]),
            "display": str(r["display"]),
            "created_at": float(r["created_at"]),
        }
        for r in rows
    ]


def email_ids_for_sender_key(conn: sqlite3.Connection, sender_key: str) -> list[str]:
    key = str(sender_key or "").strip().lower()
    if not key:
        return []
    rows = conn.execute(
        "SELECT id FROM emails WHERE sender_key = ?",
        (key,),
    ).fetchall()
    return [str(r["id"]) for r in rows]


def hide_calendar_events_for_sender(conn: sqlite3.Connection, sender: str | None) -> int:
    key = normalize_sender(sender)
    if not key:
        return 0
    now = time.time()
    cur = conn.execute(
        """
        UPDATE calendar_events
        SET hidden = 1, last_vote = 'down', updated_at = ?
        WHERE sender_key = ?
        """,
        (now, key),
    )
    conn.commit()
    return int(cur.rowcount)


def unhide_calendar_events_for_sender(conn: sqlite3.Connection, sender: str | None) -> int:
    key = normalize_sender(sender)
    if not key:
        return 0
    now = time.time()
    cur = conn.execute(
        """
        UPDATE calendar_events
        SET hidden = 0, updated_at = ?
        WHERE sender_key = ? AND hidden = 1
        """,
        (now, key),
    )
    conn.commit()
    return int(cur.rowcount)


def skip_calendar_extraction_for_sender(conn: sqlite3.Connection, sender: str | None) -> int:
    """Mark all mail from this sender as calendar-processed (no future extraction)."""
    ids = email_ids_for_sender_key(conn, normalize_sender(sender))
    if not ids:
        return 0
    skip_calendar_backlog_for_emails(conn, ids)
    return len(ids)


def make_event_id(email_id: str, title: str, event_start: float) -> str:
    raw = f"{email_id}|{title.strip().lower()}|{int(event_start)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _row_to_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    up = int(data.get("upvotes") or 0)
    down = int(data.get("downvotes") or 0)
    return {
        "id": data["id"],
        "email_id": data.get("email_id"),
        "title": data.get("title") or "",
        "description": data.get("description"),
        "location": data.get("location"),
        "event_start": float(data["event_start"]),
        "event_end": float(data["event_end"]) if data.get("event_end") else None,
        "source_text": data.get("source_text"),
        "sender": data.get("sender"),
        "subject": data.get("subject"),
        "upvotes": up,
        "downvotes": down,
        "score": up - down,
        "last_vote": data.get("last_vote"),
        "hidden": bool(int(data.get("hidden") or 0)),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def calendar_extraction_event_count(conn: sqlite3.Connection, email_id: str) -> int | None:
    row = conn.execute(
        "SELECT event_count FROM calendar_extraction_log WHERE email_id = ?",
        (email_id,),
    ).fetchone()
    if row is None:
        return None
    return int(row["event_count"])


def calendar_extraction_event_counts(
    conn: sqlite3.Connection, email_ids: list[str]
) -> dict[str, int]:
    ids = [str(email_id).strip() for email_id in email_ids if str(email_id).strip()]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT email_id, event_count
        FROM calendar_extraction_log
        WHERE email_id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    return {str(row["email_id"]): int(row["event_count"]) for row in rows}


def is_email_extracted(conn: sqlite3.Connection, email_id: str) -> bool:
    return calendar_extraction_event_count(conn, email_id) is not None


def email_calendar_needs_extraction(conn: sqlite3.Connection, email_id: str) -> bool:
    """True when mail was never scanned, or a prior scan found zero events."""
    count = calendar_extraction_event_count(conn, email_id)
    if count is None:
        return True
    if count == CALENDAR_QUEUE_SKIP_EVENT_COUNT:
        return False
    return count == 0


CALENDAR_QUEUE_SKIP_EVENT_COUNT = -1


def skip_calendar_backlog_for_emails(
    conn: sqlite3.Connection, email_ids: list[str]
) -> None:
    """Mark pending mail as calendar-processed so it leaves the extraction queue."""
    for email_id in email_ids:
        mark_email_extracted(
            conn,
            str(email_id),
            event_count=CALENDAR_QUEUE_SKIP_EVENT_COUNT,
        )


def mark_email_extracted(
    conn: sqlite3.Connection, email_id: str, *, event_count: int
) -> None:
    conn.execute(
        """
        INSERT INTO calendar_extraction_log (email_id, extracted_at, event_count)
        VALUES (?, ?, ?)
        ON CONFLICT(email_id) DO UPDATE SET
            extracted_at = excluded.extracted_at,
            event_count = excluded.event_count
        """,
        (email_id, time.time(), int(event_count)),
    )
    conn.commit()


def count_emails_pending_calendar_extraction(
    conn: sqlite3.Connection, *, since_ts: float
) -> int:
    """Mail in the window that has never been calendar-scanned."""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM emails e
        LEFT JOIN calendar_extraction_log l ON l.email_id = e.id
        WHERE COALESCE(e.received_at, e.created_at) >= ?
          AND l.email_id IS NULL
        """,
        (since_ts,),
    ).fetchone()
    return int(row[0]) if row else 0


def count_calendar_scanned_no_events(
    conn: sqlite3.Connection, *, since_ts: float
) -> int:
    """Mail scanned with zero events — not in the queue, but eligible for re-process."""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM emails e
        INNER JOIN calendar_extraction_log l ON l.email_id = e.id
        WHERE COALESCE(e.received_at, e.created_at) >= ?
          AND l.event_count = 0
        """,
        (since_ts,),
    ).fetchone()
    return int(row[0]) if row else 0


def list_emails_pending_extraction(
    conn: sqlite3.Connection, *, since_ts: float
) -> list[dict[str, Any]]:
    return list_emails_for_calendar_scan(conn, since_ts=since_ts, pending_only=True)


def list_emails_for_calendar_scan(
    conn: sqlite3.Connection,
    *,
    since_ts: float,
    pending_only: bool = False,
) -> list[dict[str, Any]]:
    if pending_only:
        rows = conn.execute(
            """
            SELECT e.*
            FROM emails e
            LEFT JOIN calendar_extraction_log l ON l.email_id = e.id
            WHERE COALESCE(e.received_at, e.created_at) >= ?
              AND l.email_id IS NULL
            ORDER BY COALESCE(e.received_at, e.created_at) ASC
            """,
            (since_ts,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT e.*
            FROM emails e
            WHERE COALESCE(e.received_at, e.created_at) >= ?
            ORDER BY COALESCE(e.received_at, e.created_at) ASC
            """,
            (since_ts,),
        ).fetchall()
    return [dict(r) for r in rows]


def count_emails_by_provider(emails: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in emails:
        provider = str(row.get("provider") or "mail").strip().lower() or "mail"
        counts[provider] = counts.get(provider, 0) + 1
    return counts


def format_provider_counts(counts: dict[str, int]) -> str:
    if not counts:
        return ""
    labels = {
        "gmail": "Gmail",
        "proton": "Proton",
        "mail": "Mail",
    }
    parts = [
        f"{labels.get(key, key.title())} {value}"
        for key, value in sorted(counts.items(), key=lambda item: item[0])
    ]
    return ", ".join(parts)


def reset_calendar_data_for_emails(
    conn: sqlite3.Connection, email_ids: list[str]
) -> None:
    if not email_ids:
        return
    placeholders = ",".join("?" for _ in email_ids)
    conn.execute(
        f"DELETE FROM calendar_events WHERE email_id IN ({placeholders})",
        email_ids,
    )
    conn.execute(
        f"DELETE FROM calendar_extraction_log WHERE email_id IN ({placeholders})",
        email_ids,
    )
    conn.commit()


def upsert_calendar_event(conn: sqlite3.Connection, event: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    event_id = str(event["id"])
    conn.execute(
        """
        INSERT INTO calendar_events (
            id, email_id, title, description, location,
            event_start, event_end, source_text, sender, sender_key, subject,
            upvotes, downvotes, last_vote, hidden, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, 0, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            description = excluded.description,
            location = excluded.location,
            event_start = excluded.event_start,
            event_end = excluded.event_end,
            source_text = excluded.source_text,
            sender = excluded.sender,
            sender_key = excluded.sender_key,
            subject = excluded.subject,
            updated_at = excluded.updated_at
        """,
        (
            event_id,
            event["email_id"],
            event["title"],
            event.get("description"),
            event.get("location"),
            float(event["event_start"]),
            event.get("event_end"),
            event.get("source_text"),
            event.get("sender"),
            normalize_sender(event.get("sender")) or None,
            event.get("subject"),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM calendar_events WHERE id = ?", (event_id,)
    ).fetchone()
    assert row is not None
    return _row_to_public(row)


def list_calendar_events(
    conn: sqlite3.Connection,
    *,
    start_ts: float,
    end_ts: float,
    include_hidden: bool = True,
) -> list[dict[str, Any]]:
    hidden_clause = "" if include_hidden else " AND hidden = 0"
    rows = conn.execute(
        f"""
        SELECT * FROM calendar_events
        WHERE event_start >= ? AND event_start < ?
        {hidden_clause}
        ORDER BY event_start ASC, title COLLATE NOCASE ASC
        """,
        (start_ts, end_ts),
    ).fetchall()
    return [_row_to_public(r) for r in rows]


def get_calendar_event(conn: sqlite3.Connection, event_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM calendar_events WHERE id = ?", (event_id,)
    ).fetchone()
    return _row_to_public(row) if row else None


def delete_calendar_event(conn: sqlite3.Connection, event_id: str) -> bool:
    """Delete a calendar event without changing vote counts elsewhere."""
    cur = conn.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
    conn.commit()
    return int(cur.rowcount) > 0


def record_event_vote(
    conn: sqlite3.Connection, event_id: str, *, vote: str
) -> dict[str, Any]:
    direction = (vote or "").strip().lower()
    if direction not in ("up", "down"):
        raise ValueError("vote must be 'up' or 'down'")
    existing = get_calendar_event(conn, event_id)
    if existing is None:
        raise ValueError("event not found")
    col = "upvotes" if direction == "up" else "downvotes"
    hidden = 1 if direction == "down" else 0
    conn.execute(
        f"""
        UPDATE calendar_events SET
            {col} = {col} + 1,
            last_vote = ?,
            hidden = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (direction, hidden, time.time(), event_id),
    )
    conn.commit()
    updated = get_calendar_event(conn, event_id)
    assert updated is not None
    return updated