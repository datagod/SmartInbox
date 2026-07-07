"""Calendar body time extraction tests."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from smartinbox.calendar_body import (
    calendar_events_are_confident,
    filter_confident_calendar_events,
    is_reply_header_line,
    parse_body_calendar_events,
    preprocess_email_calendar_events,
    parse_relative_calendar_events,
    parse_subject_calendar_events,
    strip_reply_headers,
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


def test_subject_invite_time_with_mdt_defaults_to_reference_day():
    ref = datetime(2026, 7, 6, 11, 24, tzinfo=ZoneInfo("America/Toronto")).timestamp()
    events = parse_subject_calendar_events(
        "Invite from Dr. Laurie Davis h.c. – 9:00 am MDT Self-Work",
        email_id="e10",
        sender="laurie@example.com",
        timezone=TZ,
        reference_ts=ref,
    )
    assert len(events) == 1
    dt = datetime.fromtimestamp(events[0]["event_start"], tz=ZoneInfo("America/Denver"))
    assert (dt.year, dt.month, dt.day) == (2026, 7, 6)
    assert (dt.hour, dt.minute) == (9, 0)


def test_subject_time_without_invite_or_tz_is_skipped():
    ref = datetime(2026, 7, 6, 11, 24, tzinfo=ZoneInfo(TZ)).timestamp()
    events = parse_subject_calendar_events(
        "Weekly newsletter — 9:00 am digest",
        email_id="e11",
        sender="news@example.com",
        timezone=TZ,
        reference_ts=ref,
    )
    assert events == []


RAMIN_THREAD = """Great. I will email you the Zoom link.
On Mon, Jul 6, 2026 at 2:12 PM <rmodiri@aol.com> wrote:

Fantastic. Would 1:30 PM Central Time work for you?
On Monday, July 6, 2026 at 12:58:44 PM CDT, Bill McEvoy <william.mcevoy@gmail.com> wrote:

Tuesday works great, any time.
On Mon, Jul 6, 2026 at 1:52 PM <rmodiri@aol.com> wrote:

Happy Monday, Bill,
I like to talk to you if you are available for a Zoom call on Tuesday or Wednesday.
Many thanks."""


def test_reply_header_lines_are_stripped():
    assert is_reply_header_line(
        "On Mon, Jul 6, 2026 at 2:12 PM <rmodiri@aol.com> wrote:"
    )
    stripped = strip_reply_headers(RAMIN_THREAD)
    assert "wrote:" not in stripped


def test_scheduling_thread_extracts_tuesday_at_central_time():
    ref = datetime(2026, 7, 6, 14, 30, tzinfo=ZoneInfo(TZ)).timestamp()
    events, source = preprocess_email_calendar_events(
        body=RAMIN_THREAD,
        subject="Re: Zoom call",
        summary=None,
        email_id="thread1",
        sender="rmodiri@aol.com",
        timezone=TZ,
        reference_ts=ref,
    )
    assert source == "thread schedule"
    assert len(events) == 1
    dt = datetime.fromtimestamp(events[0]["event_start"], tz=ZoneInfo("America/Chicago"))
    assert (dt.year, dt.month, dt.day) == (2026, 7, 7)
    assert (dt.hour, dt.minute) == (13, 30)
    assert calendar_events_are_confident(events)


def test_reply_header_body_dates_are_filtered():
    ref = datetime(2026, 7, 6, 14, 30, tzinfo=ZoneInfo(TZ)).timestamp()
    body = (
        "On Mon, Jul 6, 2026 at 2:12 PM <rmodiri@aol.com> wrote:\n\n"
        "See you soon."
    )
    events = parse_body_calendar_events(
        body,
        email_id="hdr",
        sender="a@example.com",
        subject="Hi",
        timezone=TZ,
    )
    assert filter_confident_calendar_events(events) == []


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