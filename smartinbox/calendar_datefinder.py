"""Extract calendar dates from email text using the datefinder library."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from smartinbox.calendar_body import (
    _CALENDAR_HINT_RE,
    _DATE_SIGNAL_RE,
    _event_title,
    _extract_location,
    email_has_calendar_date_signals,
    filter_confident_calendar_events,
    is_reply_header_source,
    strip_reply_headers,
)
from smartinbox.calendar_events import make_event_id

try:
    import datefinder
except ImportError:  # pragma: no cover - exercised when optional dep missing
    datefinder = None  # type: ignore[assignment]

_TIME_IN_TEXT_RE = re.compile(
    r"(?i)"
    r"(?:\bat\s+)?\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?|"
    r"(?:\bat\s+)?\d{1,2}\s*(?:am|pm)|"
    r"\d{1,2}:\d{2}(?:am|pm)"
)
_METADATA_BEFORE_RE = re.compile(
    r"(?i)(?:created|modified|sent|copyright|published|posted|updated|"
    r"expires?|valid\s+until|invoice\s+date)\s*(?:on|:)?\s*$"
)
_DEADLINE_HINT_RE = re.compile(
    r"(?i)\b(?:due|deadline|by|before|join\s+us|meeting|appointment|"
    r"webinar|invite|scheduled|starts?)\b"
)
_TZ_IN_SOURCE_RE = re.compile(
    r"(?i)\b(?:UTC|GMT|Z\b|MDT|MST|PDT|PST|EDT|EST|CDT|CST|"
    r"Atlantic|Pacific|Mountain|Central|Eastern)\b"
)


def datefinder_available() -> bool:
    return datefinder is not None


def _reference_datetime(reference_ts: float, timezone: str) -> datetime:
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.fromtimestamp(reference_ts, tz=tz)


def _match_datetime(
    match: Any, timezone: str, *, source_fragment: str
) -> datetime | None:
    value = getattr(match, "value", None)
    if value is None:
        return None
    dt = getattr(value, "datetime_value", None) or getattr(
        value, "resolved_datetime", None
    )
    if not isinstance(dt, datetime):
        return None
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    if dt.tzinfo is None or not _TZ_IN_SOURCE_RE.search(source_fragment):
        wall = dt.replace(tzinfo=None)
        return wall.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _fragment_has_time(text: str, start: int, end: int) -> bool:
    lo = max(0, start - 24)
    hi = min(len(text), end + 48)
    return bool(_TIME_IN_TEXT_RE.search(text[lo:hi]))


def _match_is_calendar_relevant(text: str, match: Any) -> bool:
    start = int(getattr(match, "start", 0) or 0)
    end = int(getattr(match, "end", start) or start)
    lo = max(0, start - 80)
    hi = min(len(text), end + 80)
    fragment = text[lo:hi]
    before = text[max(0, start - 40) : start]
    if is_reply_header_source(fragment):
        return False
    if _METADATA_BEFORE_RE.search(before):
        return False
    grain = str(getattr(match, "grain", "") or "").lower()
    has_time = grain in {"minute", "second"} or _fragment_has_time(text, start, end)
    if not has_time:
        return False
    if _CALENDAR_HINT_RE.search(fragment) or _DEADLINE_HINT_RE.search(fragment):
        return True
    if _DATE_SIGNAL_RE.search(fragment):
        return True
    return False


def _is_reasonable_event_time(dt: datetime, reference: datetime) -> bool:
    ref_day = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    earliest = ref_day - timedelta(days=1)
    latest = ref_day + timedelta(days=366 * 2)
    return earliest <= dt <= latest


def parse_datefinder_calendar_events(
    *,
    body: str,
    subject: str,
    summary: str | None,
    email_id: str,
    sender: str,
    timezone: str,
    reference_ts: float,
) -> tuple[list[dict[str, Any]], str | None]:
    """Use datefinder to locate datetimes; returns SmartInbox calendar event dicts."""
    if datefinder is None:
        return [], None

    subject_text = str(subject or "").strip()
    body_text = strip_reply_headers(str(body or ""))
    summary_text = str(summary or "").strip()
    combined = "\n\n".join(
        part for part in (subject_text, summary_text, body_text) if part
    )
    if not combined.strip():
        return [], None
    if not email_has_calendar_date_signals(body_text, subject_text, summary_text):
        return [], None

    reference = _reference_datetime(reference_ts, timezone)
    location = _extract_location(combined)
    title = _event_title(subject_text, location)

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        matches = datefinder.extract(combined, reference_dt=reference)
    except Exception:
        return [], None

    for match in matches:
        if not _match_is_calendar_relevant(combined, match):
            continue
        start = int(getattr(match, "start", 0) or 0)
        end = int(getattr(match, "end", start) or start)
        fragment = combined[max(0, start - 24) : min(len(combined), end + 48)]
        dt = _match_datetime(match, timezone, source_fragment=fragment)
        if dt is None or not _is_reasonable_event_time(dt, reference):
            continue
        start_ts = dt.timestamp()
        event_id = make_event_id(email_id, title, start_ts)
        if event_id in seen:
            continue
        seen.add(event_id)
        source_text = str(getattr(match, "text", "") or "").strip()
        results.append(
            {
                "id": event_id,
                "email_id": email_id,
                "title": title[:200],
                "description": None,
                "location": location,
                "event_start": start_ts,
                "event_end": None,
                "source_text": source_text[:500] or None,
                "sender": sender,
                "subject": subject_text or subject,
            }
        )

    confident = filter_confident_calendar_events(results)
    if not confident:
        return [], None
    return confident, "datefinder"