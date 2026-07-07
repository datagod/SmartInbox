"""Email LLM pipeline ordering and backlog rules."""

import sqlite3

from smartinbox.db import (
    DOWNVOTED_SENDER_SKIP_MARKER,
    SPAM_SKIP_MARKER,
    count_unsummarized_emails,
    init_db,
    skip_summary_backlog_for_emails,
    update_email_spam,
    upsert_email,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _insert_email(
    conn: sqlite3.Connection,
    email_id: str,
    *,
    sender: str = "test@example.com",
    summary: str | None = None,
) -> None:
    upsert_email(
        conn,
        {
            "id": email_id,
            "sender": sender,
            "subject": "Test",
            "snippet": "body",
            "body_text": "body",
            "received_at": 1.0,
            "summary_short": summary[:500] if summary else None,
            "summary_detailed": summary,
        },
    )


def test_unsummarized_count_excludes_spam():
    conn = _conn()
    _insert_email(conn, "clean")
    _insert_email(conn, "spam")
    update_email_spam(conn, "spam", is_spam=True)
    assert count_unsummarized_emails(conn) == 1


def test_skip_markers_remove_mail_from_backlog():
    conn = _conn()
    _insert_email(conn, "downvoted")
    _insert_email(conn, "junk")
    skip_summary_backlog_for_emails(
        conn, ["downvoted"], marker=DOWNVOTED_SENDER_SKIP_MARKER
    )
    skip_summary_backlog_for_emails(conn, ["junk"], marker=SPAM_SKIP_MARKER)
    update_email_spam(conn, "junk", is_spam=True)
    assert count_unsummarized_emails(conn) == 0