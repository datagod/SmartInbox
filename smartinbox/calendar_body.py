"""Extract calendar events from explicit dates in email body text."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from smartinbox.calendar_events import make_event_id

_EXPLICIT_DATE_RE = re.compile(
    r"(?is)"
    r"(?:^|[\r\n])\s*date\s*:\s*"
    r"(\d{4}-\d{2}-\d{2})"
    r"(?:[,\s]+|T)"
    r"(\d{1,2})"
    r":"
    r"(\d{2})"
    r"(?:\s*:\s*(\d{2}))?"
    r"\s*"
    r"(am|pm)?"
)

_SLASH_DATE_RE = re.compile(
    r"(?is)"
    r"(?:^|[\r\n])\s*date\s*:\s*"
    r"(\d{1,2})/(\d{1,2})/(\d{4})"
    r"(?:[,\s]+(?:at\s+)?)?"
    r"(\d{1,2})"
    r":"
    r"(\d{2})"
    r"(?:\s*:\s*(\d{2}))?"
    r"\s*"
    r"(am|pm)?"
)

_GO_TO_RE = re.compile(r"(?im)^\s*go to\s+(.+?)\s*\.?\s*$")
_LOCATION_RE = re.compile(r"(?im)^\s*location\s*:\s*(.+?)\s*$")
_CALENDAR_HINT_RE = re.compile(
    r"(?i)add (?:this )?date to your calendar|put (?:this )?on your calendar"
)
_SUMMARY_APPOINTMENT_RE = re.compile(
    r"(?i)calendar appointment set for\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})"
)
_SUMMARY_LOCATION_RE = re.compile(r"(?i)meeting location is\s+(.+)")


def _normalize_ampm(value: str | None) -> str | None:
    if not value:
        return None
    token = value.strip().lower()
    return token if token in ("am", "pm") else None


def _apply_ampm(hour: int, ampm: str | None) -> int:
    if not ampm:
        return hour
    if ampm == "pm" and hour < 12:
        return hour + 12
    if ampm == "am" and hour == 12:
        return 0
    return hour


def _to_timestamp(
    *,
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int,
    ampm: str | None,
    timezone: str,
) -> float | None:
    hour = _apply_ampm(hour, _normalize_ampm(ampm))
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    try:
        dt = datetime(year, month, day, hour, minute, second, tzinfo=tz)
        return dt.timestamp()
    except ValueError:
        return None


def _extract_location(body: str) -> str | None:
    for pattern in (_GO_TO_RE, _LOCATION_RE):
        match = pattern.search(body)
        if match:
            text = match.group(1).strip(" .")
            if text:
                return text[:200]
    return None


def _event_title(subject: str, location: str | None) -> str:
    subj = (subject or "").strip()
    if subj and subj.lower() != "(no subject)":
        return subj[:200]
    if location:
        return f"Go to {location}"[:200]
    return "Calendar event"


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return not (a[1] <= b[0] or a[0] >= b[1])


def _iter_body_date_matches(body: str):
    seen_spans: list[tuple[int, int]] = []
    for pattern in (_EXPLICIT_DATE_RE, _SLASH_DATE_RE):
        for match in pattern.finditer(body):
            span = match.span()
            if any(_spans_overlap(span, prior) for prior in seen_spans):
                continue
            seen_spans.append(span)
            yield match.group(0).strip(), match


def _timestamp_from_match(match: re.Match[str], timezone: str) -> float | None:
    groups = match.groups()
    if match.re is _EXPLICIT_DATE_RE:
        y, m, d = (int(x) for x in groups[0].split("-"))
        hour = int(groups[1])
        minute = int(groups[2])
        second = int(groups[3] or 0)
        ampm = groups[4]
        return _to_timestamp(
            year=y,
            month=m,
            day=d,
            hour=hour,
            minute=minute,
            second=second,
            ampm=ampm,
            timezone=timezone,
        )
    if match.re is _SLASH_DATE_RE:
        month = int(groups[0])
        day = int(groups[1])
        year = int(groups[2])
        hour = int(groups[3])
        minute = int(groups[4])
        second = int(groups[5] or 0)
        ampm = groups[6]
        return _to_timestamp(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
            ampm=ampm,
            timezone=timezone,
        )
    return None


def parse_body_calendar_events(
    body: str,
    *,
    email_id: str,
    sender: str,
    subject: str,
    timezone: str,
) -> list[dict[str, Any]]:
    """Find explicit calendar dates in plain-text email bodies."""
    text = str(body or "").strip()
    if not text:
        return []

    has_hint = bool(_CALENDAR_HINT_RE.search(text))
    location = _extract_location(text)
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for source_text, match in _iter_body_date_matches(text):
        start_ts = _timestamp_from_match(match, timezone)
        if start_ts is None:
            continue
        if not has_hint and not location and "calendar" not in source_text.lower():
            # Require a calendar hint unless the line itself says "Date:" clearly.
            if not source_text.lower().lstrip().startswith("date:"):
                continue
        title = _event_title(subject, location)
        event_id = make_event_id(email_id, title, start_ts)
        if event_id in seen:
            continue
        seen.add(event_id)
        results.append(
            {
                "id": event_id,
                "email_id": email_id,
                "title": title,
                "description": None,
                "location": location,
                "event_start": start_ts,
                "event_end": None,
                "source_text": source_text[:500],
                "sender": sender,
                "subject": subject,
            }
        )
    return results


def parse_summary_calendar_events(
    summary: str,
    *,
    email_id: str,
    sender: str,
    subject: str,
    timezone: str,
) -> list[dict[str, Any]]:
    """Parse structured calendar hints from an Ollama summary."""
    text = str(summary or "").strip()
    if not text:
        return []

    match = _SUMMARY_APPOINTMENT_RE.search(text)
    if not match:
        return []

    year, month, day = (int(x) for x in match.group(1).split("-"))
    hour = int(match.group(2))
    minute = int(match.group(3))
    start_ts = _to_timestamp(
        year=year,
        month=month,
        day=day,
        hour=hour,
        minute=minute,
        second=0,
        ampm=None,
        timezone=timezone,
    )
    if start_ts is None:
        return []

    location = None
    loc_match = _SUMMARY_LOCATION_RE.search(text)
    if loc_match:
        location = loc_match.group(1).strip(" .")[:200] or None

    source_text = match.group(0).strip()[:500]
    title = _event_title(subject, location)
    return [
        {
            "id": make_event_id(email_id, title, start_ts),
            "email_id": email_id,
            "title": title,
            "description": None,
            "location": location,
            "event_start": start_ts,
            "event_end": None,
            "source_text": source_text,
            "sender": sender,
            "subject": subject,
        }
    ]