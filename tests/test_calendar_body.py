"""Calendar body time extraction tests."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from smartinbox.calendar_body import (
    parse_body_calendar_events,
    parse_relative_calendar_events,
    parse_subject_calendar_events,
)

TZ = "America/Toronto"


def _hour_minute(ts: float) -> tuple[int, int]:
    dt = datetime.fromtimestamp(ts, tz=ZoneInfo(TZ))
    return dt.hour, dt.minute


def test_natural_date_with_explicit_time():
    events = parse_body_calendar_events(
        "Join us on June 27, 2026 at 2:30 PM for the webinar.",
        email_id="e1",
        sender="host@example.com",
        subject="Webinar",
        timezone=TZ,
    )
    assert len(events) == 1
    assert _hour_minute(events[0]["event_start"]) == (14, 30)


def test_date_only_without_time_is_skipped():
    events = parse_body_calendar_events(
        "Aug 05, 2026 • Ottawa, ON Find Tickets",
        email_id="e2",
        sender="tickets@example.com",
        subject="Concert",
        timezone=TZ,
    )
    assert events == []


def test_relative_tomorrow_requires_time():
    ref = time.time()
    with_time = parse_relative_calendar_events(
        "Meeting tomorrow at 3pm in the office.",
        email_id="e3",
        sender="boss@example.com",
        subject="Meet",
        timezone=TZ,
        reference_ts=ref,
    )
    without_time = parse_relative_calendar_events(
        "Meeting tomorrow in the office.",
        email_id="e4",
        sender="boss@example.com",
        subject="Meet",
        timezone=TZ,
        reference_ts=ref,
    )
    assert len(with_time) == 1
    assert _hour_minute(with_time[0]["event_start"]) == (15, 0)
    assert without_time == []


def test_explicit_date_line_parses_pm_time():
    events = parse_body_calendar_events(
        "Zoom meeting\nDate: 2026-06-26 7:00pm",
        email_id="e5",
        sender="zoom@example.com",
        subject="Zoom",
        timezone=TZ,
    )
    assert len(events) == 1
    assert _hour_minute(events[0]["event_start"]) == (19, 0)


def test_inline_table_timestamp_without_calendar_hint_is_skipped():
    events = parse_body_calendar_events(
        "Date: 2026-06-21 19:05:00\n2026-06-21 09:00:00 DatabaseIntegrityCheck",
        email_id="e6",
        sender="sql@example.com",
        subject="SQL jobs",
        timezone=TZ,
    )
    assert len(events) == 1
    assert _hour_minute(events[0]["event_start"]) == (19, 5)


def test_subject_date_requires_time():
    assert (
        parse_subject_calendar_events(
            "Team sync Wed, April 2, 2026",
            email_id="e7",
            sender="team@example.com",
            timezone=TZ,
        )
        == []
    )
    events = parse_subject_calendar_events(
        "Team sync Wed, April 2, 2026 @ 4:30pm",
        email_id="e8",
        sender="team@example.com",
        timezone=TZ,
    )
    assert len(events) == 1
    assert _hour_minute(events[0]["event_start"]) == (16, 30)


def test_distant_nearby_time_is_not_attached():
    events = parse_body_calendar_events(
        "Aug 05, 2026 • Ottawa, ON. General admission and VIP packages available now. "
        "Doors open at 7:00 PM — find tickets online.",
        email_id="e9",
        sender="tickets@example.com",
        subject="Concert",
        timezone=TZ,
    )
    assert events == []