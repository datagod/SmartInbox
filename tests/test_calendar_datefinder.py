"""datefinder-backed calendar extraction tests."""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from smartinbox.calendar_datefinder import (
    datefinder_available,
    parse_datefinder_calendar_events,
)

TZ = "America/Toronto"


@pytest.mark.skipif(not datefinder_available(), reason="datefinder not installed")
def test_datefinder_parses_natural_meeting_date():
    ref = datetime(2026, 6, 20, 10, 0, tzinfo=ZoneInfo(TZ)).timestamp()
    events, source = parse_datefinder_calendar_events(
        body="Please join us on June 27, 2026 at 2:30 PM for the webinar.",
        subject="Webinar invite",
        summary=None,
        email_id="df1",
        sender="host@example.com",
        timezone=TZ,
        reference_ts=ref,
    )
    assert source == "datefinder"
    assert len(events) == 1
    dt = datetime.fromtimestamp(events[0]["event_start"], tz=ZoneInfo(TZ))
    assert (dt.year, dt.month, dt.day) == (2026, 6, 27)
    assert (dt.hour, dt.minute) == (14, 30)  # 2:30 PM in America/Toronto


@pytest.mark.skipif(not datefinder_available(), reason="datefinder not installed")
def test_datefinder_skips_created_metadata_dates():
    ref = time.time()
    events, source = parse_datefinder_calendar_events(
        body="created 01/15/2005 by ACME Inc. Meeting next week.",
        subject="Newsletter",
        summary=None,
        email_id="df2",
        sender="news@example.com",
        timezone=TZ,
        reference_ts=ref,
    )
    assert source is None
    assert events == []


@pytest.mark.skipif(not datefinder_available(), reason="datefinder not installed")
def test_datefinder_skips_when_no_calendar_signals():
    events, source = parse_datefinder_calendar_events(
        body="Thanks for your order. Your tracking number is 12345.",
        subject="Shipment update",
        summary=None,
        email_id="df3",
        sender="shop@example.com",
        timezone=TZ,
        reference_ts=time.time(),
    )
    assert source is None
    assert events == []