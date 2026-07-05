"""Calendar event storage and sender-ignore tests."""

from __future__ import annotations

import sqlite3
import time

import pytest

from smartinbox.calendar_events import (
    CALENDAR_QUEUE_SKIP_EVENT_COUNT,
    add_calendar_ignored_sender,
    calendar_extraction_event_counts,
    email_ids_for_sender_key,
    hide_calendar_events_for_sender,
    init_calendar_tables,
    mark_email_extracted,
    unhide_calendar_events_for_sender,
    upsert_calendar_event,
)
from smartinbox.db import init_db, upsert_email


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    init_db(connection)
    init_calendar_tables(connection)
    yield connection
    connection.close()


def _insert_email(conn: sqlite3.Connection, email_id: str, sender: str) -> None:
    upsert_email(
        conn,
        {
            "id": email_id,
            "sender": sender,
            "subject": "Hello",
            "body_text": "Body",
            "received_at": time.time(),
        },
    )


def test_email_ids_for_sender_key_uses_indexed_lookup(conn):
    _insert_email(conn, "e1", "Alice <alice@example.com>")
    _insert_email(conn, "e2", "bob@example.com")
    _insert_email(conn, "e3", "Alice <alice@example.com>")

    ids = email_ids_for_sender_key(conn, "alice@example.com")
    assert sorted(ids) == ["e1", "e3"]


def test_hide_and_unhide_events_for_sender(conn):
    now = time.time()
    for index, title in enumerate(("A", "B"), start=1):
        upsert_calendar_event(
            conn,
            {
                "id": f"ev{index}",
                "email_id": f"e{index}",
                "title": title,
                "event_start": now + index,
                "sender": "Promo <promo@example.com>",
                "subject": "Promo",
            },
        )

    hidden = hide_calendar_events_for_sender(conn, "Promo <promo@example.com>")
    assert hidden == 2

    rows = conn.execute("SELECT hidden FROM calendar_events ORDER BY id").fetchall()
    assert [int(r["hidden"]) for r in rows] == [1, 1]

    restored = unhide_calendar_events_for_sender(conn, "promo@example.com")
    assert restored == 2
    rows = conn.execute("SELECT hidden FROM calendar_events ORDER BY id").fetchall()
    assert [int(r["hidden"]) for r in rows] == [0, 0]


def test_calendar_extraction_event_counts_batch(conn):
    mark_email_extracted(conn, "e1", event_count=2)
    mark_email_extracted(conn, "e2", event_count=CALENDAR_QUEUE_SKIP_EVENT_COUNT)
    mark_email_extracted(conn, "e3", event_count=0)

    counts = calendar_extraction_event_counts(conn, ["e1", "e2", "e4"])
    assert counts == {"e1": 2, "e2": CALENDAR_QUEUE_SKIP_EVENT_COUNT}


def test_add_calendar_ignored_sender(conn):
    entry = add_calendar_ignored_sender(conn, "Noise <noise@example.com>")
    assert entry["sender_key"] == "noise@example.com"
    assert entry["display"] == "Noise <noise@example.com>"