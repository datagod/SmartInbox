"""Search stored emails in the local SQLite database."""

from __future__ import annotations

import re
import sqlite3
from typing import Any


_SEARCH_FIELDS = (
    "subject",
    "sender",
    "snippet",
    "body_text",
    "summary_short",
    "summary_detailed",
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _tokenize_query(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", (query or "").strip()) if t]


def _email_matches_tokens(row: dict[str, Any], tokens: list[str]) -> bool:
    haystack = " ".join(
        str(row.get(field) or "") for field in _SEARCH_FIELDS
    ).lower()
    return all(token.lower() in haystack for token in tokens)


def _public_search_row(row: dict[str, Any], *, calendar_extracted: bool, calendar_event_count: int) -> dict[str, Any]:
    body = str(row.get("body_text") or "")
    snippet = str(row.get("snippet") or "")
    preview = snippet.strip() or body.strip()
    if len(preview) > 420:
        preview = preview[:420].rstrip() + "…"
    return {
        "id": row.get("id"),
        "thread_id": row.get("thread_id"),
        "account_id": row.get("account_id"),
        "account_email": row.get("account_email"),
        "provider": row.get("provider"),
        "sender": row.get("sender"),
        "subject": row.get("subject"),
        "snippet": row.get("snippet"),
        "preview": preview,
        "received_at": row.get("received_at"),
        "created_at": row.get("created_at"),
        "summary_short": row.get("summary_short"),
        "summary_detailed": row.get("summary_detailed"),
        "starred": bool(int(row.get("starred") or 0)),
        "calendar_extracted": calendar_extracted,
        "calendar_event_count": calendar_event_count,
    }


def search_stored_emails(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    tokens = _tokenize_query(query)
    if not tokens:
        return [], 0

    clauses: list[str] = []
    params: list[str] = []
    for token in tokens:
        pattern = f"%{_escape_like(token)}%"
        field_clause = " OR ".join(f"{field} LIKE ? ESCAPE '\\'" for field in _SEARCH_FIELDS)
        clauses.append(f"({field_clause})")
        params.extend([pattern] * len(_SEARCH_FIELDS))

    where = " AND ".join(clauses)
    count_row = conn.execute(
        f"SELECT COUNT(*) AS n FROM emails WHERE {where}",
        params,
    ).fetchone()
    total = int(count_row["n"]) if count_row else 0

    rows = conn.execute(
        f"""
        SELECT * FROM emails
        WHERE {where}
        ORDER BY COALESCE(received_at, created_at) DESC
        LIMIT ? OFFSET ?
        """,
        [*params, max(1, min(int(limit), 200)), max(0, int(offset))],
    ).fetchall()

    from smartinbox.calendar_events import is_email_extracted

    results: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        email_id = str(data.get("id") or "")
        extracted = is_email_extracted(conn, email_id) if email_id else False
        event_count = 0
        if extracted:
            log_row = conn.execute(
                "SELECT event_count FROM calendar_extraction_log WHERE email_id = ?",
                (email_id,),
            ).fetchone()
            if log_row is not None:
                event_count = int(log_row["event_count"] or 0)
        results.append(
            _public_search_row(
                data,
                calendar_extracted=extracted,
                calendar_event_count=event_count,
            )
        )
    return results, total


def search_demo_emails(
    emails: list[dict[str, Any]],
    query: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    tokens = _tokenize_query(query)
    if not tokens:
        return [], 0
    matched = [row for row in emails if _email_matches_tokens(row, tokens)]
    matched.sort(
        key=lambda r: float(r.get("received_at") or r.get("created_at") or 0),
        reverse=True,
    )
    total = len(matched)
    page = matched[max(0, int(offset)) : max(0, int(offset)) + max(1, min(int(limit), 200))]
    results = [
        _public_search_row(
            row,
            calendar_extracted=False,
            calendar_event_count=0,
        )
        for row in page
    ]
    return results, total