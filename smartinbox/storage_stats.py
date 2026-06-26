"""Statistics about locally stored mail and database usage."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.exists() else 0
    except OSError:
        return 0


def get_storage_stats(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    total = int(conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0])

    by_provider_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(provider), ''), 'unknown') AS provider,
               COUNT(*) AS count
        FROM emails
        GROUP BY provider
        ORDER BY count DESC, provider ASC
        """
    ).fetchall()
    by_provider = [
        {"provider": str(r["provider"]), "count": int(r["count"])}
        for r in by_provider_rows
    ]

    by_account_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(provider), ''), 'unknown') AS provider,
               COALESCE(NULLIF(TRIM(account_email), ''), 'unknown') AS account_email,
               COUNT(*) AS count
        FROM emails
        GROUP BY provider, account_email
        ORDER BY count DESC, account_email ASC
        """
    ).fetchall()
    by_account = [
        {
            "provider": str(r["provider"]),
            "account_email": str(r["account_email"]),
            "count": int(r["count"]),
        }
        for r in by_account_rows
    ]

    date_row = conn.execute(
        """
        SELECT
            MIN(COALESCE(received_at, created_at)) AS oldest_received,
            MAX(COALESCE(received_at, created_at)) AS newest_received,
            MIN(created_at) AS oldest_stored,
            MAX(created_at) AS newest_stored
        FROM emails
        """
    ).fetchone()

    now = time.time()
    age_buckets: list[dict[str, Any]] = []
    for label, days in (
        ("Last 24 hours", 1),
        ("Last 7 days", 7),
        ("Last 30 days", 30),
        ("Last 90 days", 90),
        ("Last 365 days", 365),
    ):
        since = now - days * 86400
        count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM emails
                WHERE COALESCE(received_at, created_at) >= ?
                """,
                (since,),
            ).fetchone()[0]
        )
        age_buckets.append({"label": label, "days": days, "count": count})

    month_rows = conn.execute(
        """
        SELECT strftime('%Y-%m', datetime(COALESCE(received_at, created_at), 'unixepoch')) AS month,
               COUNT(*) AS count
        FROM emails
        WHERE COALESCE(received_at, created_at) > 0
        GROUP BY month
        ORDER BY month DESC
        LIMIT 18
        """
    ).fetchall()
    by_month = [
        {"month": str(r["month"]), "count": int(r["count"])}
        for r in month_rows
        if r["month"]
    ]

    size_row = conn.execute(
        """
        SELECT
            SUM(
                LENGTH(COALESCE(id, '')) +
                LENGTH(COALESCE(thread_id, '')) +
                LENGTH(COALESCE(account_id, '')) +
                LENGTH(COALESCE(account_email, '')) +
                LENGTH(COALESCE(provider, '')) +
                LENGTH(COALESCE(sender, '')) +
                LENGTH(COALESCE(subject, '')) +
                LENGTH(COALESCE(snippet, '')) +
                LENGTH(COALESCE(body_text, '')) +
                LENGTH(COALESCE(summary_short, '')) +
                LENGTH(COALESCE(summary_detailed, ''))
            ) AS content_bytes,
            SUM(LENGTH(COALESCE(body_text, ''))) AS body_bytes,
            SUM(LENGTH(COALESCE(snippet, ''))) AS snippet_bytes,
            SUM(
                LENGTH(COALESCE(summary_short, '')) +
                LENGTH(COALESCE(summary_detailed, ''))
            ) AS summary_bytes,
            SUM(LENGTH(COALESCE(subject, '')) + LENGTH(COALESCE(sender, ''))) AS meta_bytes,
            AVG(LENGTH(COALESCE(body_text, ''))) AS avg_body_bytes
        FROM emails
        """
    ).fetchone()

    summarized = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM emails
            WHERE summary_short IS NOT NULL AND TRIM(summary_short) != ''
            """
        ).fetchone()[0]
    )
    alerted = int(
        conn.execute(
            "SELECT COUNT(*) FROM emails WHERE alerted_at IS NOT NULL AND alerted_at > 0"
        ).fetchone()[0]
    )
    starred = int(conn.execute("SELECT COUNT(*) FROM emails WHERE starred = 1").fetchone()[0])

    calendar_events = 0
    calendar_scanned = 0
    if _table_exists(conn, "calendar_events"):
        calendar_events = int(conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0])
    if _table_exists(conn, "calendar_extraction_log"):
        calendar_scanned = int(
            conn.execute("SELECT COUNT(*) FROM calendar_extraction_log").fetchone()[0]
        )

    recent_rows = conn.execute(
        """
        SELECT id, provider, account_email, sender, subject,
               COALESCE(received_at, created_at) AS sort_ts,
               received_at, created_at,
               LENGTH(COALESCE(body_text, '')) +
               LENGTH(COALESCE(snippet, '')) +
               LENGTH(COALESCE(summary_short, '')) +
               LENGTH(COALESCE(summary_detailed, '')) AS approx_bytes
        FROM emails
        ORDER BY sort_ts DESC
        LIMIT 20
        """
    ).fetchall()
    recent = [
        {
            "id": str(r["id"]),
            "provider": str(r["provider"] or "unknown"),
            "account_email": str(r["account_email"] or ""),
            "sender": str(r["sender"] or ""),
            "subject": str(r["subject"] or "(no subject)"),
            "received_at": float(r["received_at"] or 0),
            "created_at": float(r["created_at"] or 0),
            "sort_ts": float(r["sort_ts"] or 0),
            "approx_bytes": int(r["approx_bytes"] or 0),
        }
        for r in recent_rows
    ]

    db_file = Path(db_path)
    wal_path = Path(f"{db_path}-wal")
    shm_path = Path(f"{db_path}-shm")

    return {
        "total_emails": total,
        "by_provider": by_provider,
        "by_account": by_account,
        "dates": {
            "oldest_received": float(date_row["oldest_received"] or 0) if date_row else 0,
            "newest_received": float(date_row["newest_received"] or 0) if date_row else 0,
            "oldest_stored": float(date_row["oldest_stored"] or 0) if date_row else 0,
            "newest_stored": float(date_row["newest_stored"] or 0) if date_row else 0,
        },
        "age_buckets": age_buckets,
        "by_month": by_month,
        "content": {
            "content_bytes": int(size_row["content_bytes"] or 0) if size_row else 0,
            "body_bytes": int(size_row["body_bytes"] or 0) if size_row else 0,
            "snippet_bytes": int(size_row["snippet_bytes"] or 0) if size_row else 0,
            "summary_bytes": int(size_row["summary_bytes"] or 0) if size_row else 0,
            "meta_bytes": int(size_row["meta_bytes"] or 0) if size_row else 0,
            "avg_body_bytes": int(size_row["avg_body_bytes"] or 0) if size_row else 0,
        },
        "summarized": summarized,
        "unsummarized": max(0, total - summarized),
        "alerted": alerted,
        "starred": starred,
        "calendar_events": calendar_events,
        "calendar_scanned": calendar_scanned,
        "recent": recent,
        "database": {
            "path": str(db_file.resolve()),
            "file_bytes": _file_size(db_file),
            "wal_bytes": _file_size(wal_path),
            "shm_bytes": _file_size(shm_path),
            "total_bytes": _file_size(db_file) + _file_size(wal_path) + _file_size(shm_path),
        },
    }