"""Extract calendar events from explicit dates in email body text."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
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
_ZOOM_URL_RE = re.compile(r"https?://[^\s<>\"']*zoom\.us/[^\s<>\"']+", re.I)
_NATURAL_DATE_RE = re.compile(
    r"(?is)"
    r"(?:please\s+join\s+us\s+on\s+|join\s+us\s+on\s+|scheduled\s+for\s+|on\s+)?"
    r"(?:\w+day,?\s+)?"
    r"(\w+)\s+"
    r"(\d{1,2}),?\s+"
    r"(\d{4}),?\s+"
    r"(?:at\s+)?"
    r"(\d{1,2}):(\d{2})\s*"
    r"(am|pm)\b"
    r"(?:\s+([^(\n]+))?"
)
_CALENDAR_HINT_RE = re.compile(
    r"(?i)add (?:this )?date to your calendar|put (?:this )?on your calendar|"
    r"join us on|join zoom|zoom meeting|webinar|calendar invitation|"
    r"invitation:\s|meeting invite|teams meeting|google meet|"
    r"scheduled for|appointment|reservation confirm|delivery date|pickup time"
)
_DATE_SIGNAL_RE = re.compile(
    r"(?i)"
    r"\b(?:tomorrow|today)\b|"
    r"\b(?:next|this)\s+(?:mon|tue|wed|thu|fri|sat|sun)\w*\b|"
    r"\b(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b"
    r".{0,40}\b\d{4}\b|"
    r"\b\d{4}-\d{2}-\d{2}\b|"
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b\d{1,2}:\d{2}\s*(?:am|pm)\b|"
    r"(?:^|[\r\n])\s*date\s*:|"
    r"(?:^|[\r\n])\s*(?:when|scheduled|starts?)\s*:"
)
_MONTH_DAY_YEAR_TIME_RE = re.compile(
    r"(?is)"
    r"(?:\w+day,?\s+)?"
    r"(\w+)\s+"
    r"(\d{1,2}),?\s+"
    r"(\d{4})"
    r"(?:,?\s+at\s+(\d{1,2}):(\d{2})\s*(am|pm)|,?\s+at\s+(\d{1,2}):(\d{2})(am|pm))?"
)
_NEARBY_AT_TIME_RE = re.compile(
    r"(?i)\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b"
)
_NEARBY_HM_AMPM_RE = re.compile(
    r"(?i)\b(\d{1,2}):(\d{2})\s*(am|pm)\b"
)
_NEARBY_HMAMP_RE = re.compile(r"(?i)\b(\d{1,2}):(\d{2})(am|pm)\b")
_NEARBY_HOUR_AMPM_RE = re.compile(r"(?i)(?<!\d)(\d{1,2})\s*(am|pm)\b")
_NEARBY_TIME_RES = (
    _NEARBY_AT_TIME_RE,
    _NEARBY_HM_AMPM_RE,
    _NEARBY_HMAMP_RE,
    _NEARBY_HOUR_AMPM_RE,
)
_ISO_INLINE_RE = re.compile(
    r"(?i)\b(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?)?"
)
_WHEN_SCHEDULED_LINE_RE = re.compile(
    r"(?im)^\s*(?:when|scheduled(?:\s+for)?|starts?|event\s+time|date\s*&\s*time)\s*:\s*(.+)$"
)
_SUBJECT_DATE_RE = re.compile(
    r"(?i)"
    r"(?:\w+day,?\s+)?(\w+)\s+(\d{1,2}),?\s+(\d{4})"
    r"(?:\s+@?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm))?"
)
_TOMORROW_AT_RE = re.compile(
    r"(?i)\btomorrow\b(?:\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?"
)
_TODAY_AT_RE = re.compile(
    r"(?i)\btoday\b(?:\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?"
)
_RELATIVE_WEEKDAY_RE = re.compile(
    r"(?i)\b(next|this)\s+"
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b"
    r"(?:\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?"
)
_SUMMARY_APPOINTMENT_RE = re.compile(
    r"(?i)calendar appointment set for\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})"
)
_SUMMARY_NATURAL_DATE_RE = re.compile(
    r"(?i)(?:on\s+)?(?:\w+day,?\s+)?(\w+)\s+(\d{1,2}),?\s+(\d{4}),?\s+at\s+"
    r"(\d{1,2}):(\d{2})\s*(am|pm)(?:\s+([^.\n]+))?"
)
_SUMMARY_LOCATION_RE = re.compile(r"(?i)meeting location is\s+(.+)")

_MONTH_NAMES = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_WEEKDAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "mon": 0,
    "tue": 1,
    "tues": 1,
    "wed": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


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


def _groups_to_time(
    hour_raw: str | None,
    minute_raw: str | None,
    ampm_raw: str | None,
) -> tuple[int, int] | None:
    if hour_raw is None or str(hour_raw).strip() == "":
        return None
    hour = int(hour_raw)
    minute = int(minute_raw) if minute_raw else 0
    hour = _apply_ampm(hour, _normalize_ampm(ampm_raw))
    return hour, minute


def _extract_nearby_time(
    text: str, match: re.Match[str]
) -> tuple[int, int, str | None] | None:
    """Find an explicit time close to a date match (within 40 chars after the date)."""
    fragment = text[match.start() : min(len(text), match.end() + 160)]
    date_end = match.end() - match.start()
    best: tuple[int, int, str | None] | None = None
    best_distance: int | None = None
    for pattern in _NEARBY_TIME_RES:
        for time_match in pattern.finditer(fragment):
            if time_match.start() < date_end:
                continue
            distance = time_match.start() - date_end
            if distance > 40:
                continue
            groups = time_match.groups()
            if pattern is _NEARBY_HOUR_AMPM_RE:
                parsed = _groups_to_time(groups[0], None, groups[1])
            else:
                parsed = _groups_to_time(groups[0], groups[1], groups[2])
            if parsed is None:
                continue
            hour, minute = parsed
            ampm = _normalize_ampm(groups[-1])
            if best_distance is None or distance < best_distance:
                best = (hour, minute, ampm)
                best_distance = distance
    return best


def _month_day_year_time_groups(
    groups: tuple[str | None, ...],
) -> tuple[int, int, str | None] | None:
    if groups[3] is not None:
        parsed = _groups_to_time(groups[3], groups[4], groups[5])
        if parsed is None:
            return None
        hour, minute = parsed
        return hour, minute, _normalize_ampm(groups[5])
    if len(groups) > 6 and groups[6] is not None:
        parsed = _groups_to_time(groups[6], groups[7], groups[8])
        if parsed is None:
            return None
        hour, minute = parsed
        return hour, minute, _normalize_ampm(groups[8])
    return None


def _iso_match_has_time(groups: tuple[str | None, ...]) -> bool:
    return groups[3] is not None and str(groups[3]).strip() != ""


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


def _parse_month_name(token: str) -> int | None:
    return _MONTH_NAMES.get(str(token or "").strip().lower())


def _resolve_tz_name(phrase: str | None, default: str) -> str:
    text = str(phrase or "").strip().lower()
    if not text:
        return default
    if "pacific" in text:
        return "America/Los_Angeles"
    if "mountain" in text:
        return "America/Denver"
    if "central" in text:
        return "America/Chicago"
    if "eastern" in text:
        return "America/New_York"
    if "atlantic" in text:
        return "America/Halifax"
    return default


def _extract_location(body: str) -> str | None:
    zoom = _ZOOM_URL_RE.search(body)
    if zoom:
        return zoom.group(0).strip()[:200]
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
    patterns = (
        _EXPLICIT_DATE_RE,
        _SLASH_DATE_RE,
        _NATURAL_DATE_RE,
        _MONTH_DAY_YEAR_TIME_RE,
        _ISO_INLINE_RE,
    )
    for pattern in patterns:
        for match in pattern.finditer(body):
            span = match.span()
            if any(_spans_overlap(span, prior) for prior in seen_spans):
                continue
            seen_spans.append(span)
            yield match.group(0).strip(), match
    for line_match in _WHEN_SCHEDULED_LINE_RE.finditer(body):
        fragment = line_match.group(1).strip()
        if not fragment:
            continue
        for pattern in (_NATURAL_DATE_RE, _MONTH_DAY_YEAR_TIME_RE, _ISO_INLINE_RE):
            match = pattern.search(fragment)
            if not match:
                continue
            source = f"{line_match.group(0).strip()} -> {match.group(0).strip()}"
            yield source, match


def _timestamp_from_match(
    match: re.Match[str], timezone: str, *, text: str = ""
) -> float | None:
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
    if match.re is _NATURAL_DATE_RE:
        month = _parse_month_name(groups[0])
        if month is None:
            return None
        day = int(groups[1])
        year = int(groups[2])
        hour = int(groups[3])
        minute = int(groups[4])
        ampm = groups[5]
        tz_name = _resolve_tz_name(groups[6], timezone)
        return _to_timestamp(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=0,
            ampm=ampm,
            timezone=tz_name,
        )
    if match.re is _MONTH_DAY_YEAR_TIME_RE:
        month = _parse_month_name(groups[0])
        if month is None:
            return None
        day = int(groups[1])
        year = int(groups[2])
        time_parts = _month_day_year_time_groups(groups)
        if time_parts is None and text:
            time_parts = _extract_nearby_time(text, match)
        if time_parts is None:
            return None
        hour, minute, _ampm = time_parts
        return _to_timestamp(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=0,
            ampm=None,
            timezone=timezone,
        )
    if match.re is _ISO_INLINE_RE:
        if not _iso_match_has_time(groups):
            return None
        year = int(groups[0])
        month = int(groups[1])
        day = int(groups[2])
        hour = int(groups[3])
        minute = int(groups[4] or 0)
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
        start_ts = _timestamp_from_match(match, timezone, text=text)
        if start_ts is None:
            continue
        is_explicit = source_text.lower().lstrip().startswith("date:")
        is_natural = match.re is _NATURAL_DATE_RE
        is_when_line = "->" in source_text
        iso_has_time = match.re is _ISO_INLINE_RE and _iso_match_has_time(match.groups())
        iso_in_date_line = iso_has_time and "date:" in source_text.lower()
        is_strong = (
            is_explicit
            or is_natural
            or is_when_line
            or match.re is _MONTH_DAY_YEAR_TIME_RE
            or iso_in_date_line
            or (iso_has_time and has_hint)
        )
        if (
            not has_hint
            and not location
            and "calendar" not in source_text.lower()
            and not is_strong
        ):
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
    if match:
        year, month, day = (int(x) for x in match.group(1).split("-"))
        hour = int(match.group(2))
        minute = int(match.group(3))
        tz_name = timezone
        source_text = match.group(0).strip()[:500]
    else:
        match = _SUMMARY_NATURAL_DATE_RE.search(text)
        if not match:
            return []
        month = _parse_month_name(match.group(1))
        if month is None:
            return []
        day = int(match.group(2))
        year = int(match.group(3))
        hour = int(match.group(4))
        minute = int(match.group(5))
        tz_name = _resolve_tz_name(match.group(7), timezone)
        source_text = match.group(0).strip()[:500]

    start_ts = _to_timestamp(
        year=year,
        month=month,
        day=day,
        hour=hour,
        minute=minute,
        second=0,
        ampm=match.group(6) if match.re is _SUMMARY_NATURAL_DATE_RE else None,
        timezone=tz_name,
    )
    if start_ts is None:
        return []

    location = _extract_location(text)
    if not location:
        loc_match = _SUMMARY_LOCATION_RE.search(text)
        if loc_match:
            location = loc_match.group(1).strip(" .")[:200] or None
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


def _reference_dt(reference_ts: float, timezone: str) -> datetime:
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.fromtimestamp(reference_ts, tz=tz)


def _next_weekday(
    ref: datetime, weekday: int, *, which: str
) -> datetime:
    days_ahead = (weekday - ref.weekday()) % 7
    if which == "next":
        if days_ahead == 0:
            days_ahead = 7
        elif ref.hour >= 17:
            days_ahead = (days_ahead or 7)
    elif days_ahead < 0:
        days_ahead += 7
    target = ref + timedelta(days=days_ahead)
    return target.replace(hour=0, minute=0, second=0, microsecond=0)


def _time_from_groups(
    hour_raw: str | None, minute_raw: str | None, ampm: str | None
) -> tuple[int, int] | None:
    return _groups_to_time(hour_raw, minute_raw, ampm)


def _append_event(
    results: list[dict[str, Any]],
    seen: set[str],
    *,
    email_id: str,
    sender: str,
    subject: str,
    title: str,
    location: str | None,
    start_ts: float,
    source_text: str,
) -> None:
    event_id = make_event_id(email_id, title, start_ts)
    if event_id in seen:
        return
    seen.add(event_id)
    results.append(
        {
            "id": event_id,
            "email_id": email_id,
            "title": title[:200],
            "description": None,
            "location": location,
            "event_start": start_ts,
            "event_end": None,
            "source_text": source_text[:500],
            "sender": sender,
            "subject": subject,
        }
    )


def parse_relative_calendar_events(
    body: str,
    *,
    email_id: str,
    sender: str,
    subject: str,
    timezone: str,
    reference_ts: float,
) -> list[dict[str, Any]]:
    """Resolve tomorrow / today / next Monday style phrases."""
    text = str(body or "").strip()
    if not text:
        return []
    ref = _reference_dt(reference_ts, timezone)
    location = _extract_location(text)
    title = _event_title(subject, location)
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for match in _TOMORROW_AT_RE.finditer(text):
        day = (ref + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        time_parts = _time_from_groups(match.group(1), match.group(2), match.group(3))
        if time_parts is None:
            continue
        hour, minute = time_parts
        start_ts = _to_timestamp(
            year=day.year,
            month=day.month,
            day=day.day,
            hour=hour,
            minute=minute,
            second=0,
            ampm=None,
            timezone=timezone,
        )
        if start_ts is not None:
            _append_event(
                results,
                seen,
                email_id=email_id,
                sender=sender,
                subject=subject,
                title=title,
                location=location,
                start_ts=start_ts,
                source_text=match.group(0).strip(),
            )

    for match in _TODAY_AT_RE.finditer(text):
        day = ref.replace(hour=0, minute=0, second=0, microsecond=0)
        time_parts = _time_from_groups(match.group(1), match.group(2), match.group(3))
        if time_parts is None:
            continue
        hour, minute = time_parts
        start_ts = _to_timestamp(
            year=day.year,
            month=day.month,
            day=day.day,
            hour=hour,
            minute=minute,
            second=0,
            ampm=None,
            timezone=timezone,
        )
        if start_ts is not None:
            _append_event(
                results,
                seen,
                email_id=email_id,
                sender=sender,
                subject=subject,
                title=title,
                location=location,
                start_ts=start_ts,
                source_text=match.group(0).strip(),
            )

    for match in _RELATIVE_WEEKDAY_RE.finditer(text):
        which = str(match.group(1) or "next").lower()
        weekday_name = str(match.group(2) or "").lower()
        weekday = _WEEKDAY_NAMES.get(weekday_name)
        if weekday is None:
            continue
        day = _next_weekday(ref, weekday, which=which)
        time_parts = _time_from_groups(match.group(3), match.group(4), match.group(5))
        if time_parts is None:
            continue
        hour, minute = time_parts
        start_ts = _to_timestamp(
            year=day.year,
            month=day.month,
            day=day.day,
            hour=hour,
            minute=minute,
            second=0,
            ampm=None,
            timezone=timezone,
        )
        if start_ts is not None:
            _append_event(
                results,
                seen,
                email_id=email_id,
                sender=sender,
                subject=subject,
                title=title,
                location=location,
                start_ts=start_ts,
                source_text=match.group(0).strip(),
            )
    return results


def parse_subject_calendar_events(
    subject: str,
    *,
    email_id: str,
    sender: str,
    timezone: str,
) -> list[dict[str, Any]]:
    """Find dates embedded in the email subject line."""
    text = str(subject or "").strip()
    if not text or text.lower() == "(no subject)":
        return []
    match = _SUBJECT_DATE_RE.search(text)
    if not match:
        return []
    month = _parse_month_name(match.group(1))
    if month is None:
        return []
    day = int(match.group(2))
    year = int(match.group(3))
    time_parts = _time_from_groups(match.group(4), match.group(5), match.group(6))
    if time_parts is None:
        return []
    hour, minute = time_parts
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
    location = _extract_location(text)
    title = _event_title(text, location)
    return [
        {
            "id": make_event_id(email_id, title, start_ts),
            "email_id": email_id,
            "title": title,
            "description": None,
            "location": location,
            "event_start": start_ts,
            "event_end": None,
            "source_text": match.group(0).strip()[:500],
            "sender": sender,
            "subject": text,
        }
    ]


def email_has_calendar_date_signals(
    body: str, subject: str, summary: str | None = None
) -> bool:
    """Fast check: might this email contain a calendar date worth LLM fallback?"""
    combined = "\n".join(
        part for part in (subject, body, summary or "") if str(part or "").strip()
    )
    if not combined.strip():
        return False
    if _CALENDAR_HINT_RE.search(combined):
        return True
    return bool(_DATE_SIGNAL_RE.search(combined))


def _line_has_date_signal(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    return bool(_DATE_SIGNAL_RE.search(text) or _CALENDAR_HINT_RE.search(text))


def build_calendar_llm_context(
    body: str, subject: str, summary: str | None = None
) -> str:
    """Condensed context for Ollama — date-relevant lines only."""
    parts: list[str] = []
    subj = str(subject or "").strip()
    if subj:
        parts.append(f"Subject: {subj}")
    summary_text = str(summary or "").strip()
    if summary_text:
        parts.append(f"Summary:\n{summary_text[:2500]}")
    relevant: list[str] = []
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped and _line_has_date_signal(stripped):
            relevant.append(stripped)
    if relevant:
        parts.append("Date-related lines from body:\n" + "\n".join(relevant[:50]))
    else:
        excerpt = str(body or "").strip()[:4000]
        if excerpt:
            parts.append(f"Body excerpt:\n{excerpt}")
    return "\n\n".join(parts)[:10000]


def _merge_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        event_id = str(event.get("id") or "")
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        merged.append(event)
    return merged


def preprocess_email_calendar_events(
    *,
    body: str,
    subject: str,
    summary: str | None,
    email_id: str,
    sender: str,
    timezone: str,
    reference_ts: float,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    Rule-based calendar extraction before any LLM call.
    Returns (events, source_label).
    """
    candidates: list[dict[str, Any]] = []
    sources: list[str] = []

    body_events = parse_body_calendar_events(
        body,
        email_id=email_id,
        sender=sender,
        subject=subject,
        timezone=timezone,
    )
    if body_events:
        candidates.extend(body_events)
        sources.append("body date")

    subject_events = parse_subject_calendar_events(
        subject,
        email_id=email_id,
        sender=sender,
        timezone=timezone,
    )
    if subject_events:
        candidates.extend(subject_events)
        sources.append("subject date")

    relative_events = parse_relative_calendar_events(
        body,
        email_id=email_id,
        sender=sender,
        subject=subject,
        timezone=timezone,
        reference_ts=reference_ts,
    )
    if relative_events:
        candidates.extend(relative_events)
        sources.append("relative date")

    summary_text = str(summary or "").strip()
    if summary_text:
        summary_events = parse_summary_calendar_events(
            summary_text,
            email_id=email_id,
            sender=sender,
            subject=subject,
            timezone=timezone,
        )
        if summary_events:
            candidates.extend(summary_events)
            sources.append("summary date")

    events = _merge_events(candidates)
    if not events:
        return [], None
    return events, sources[0] if len(sources) == 1 else "preprocess"


def should_call_calendar_llm(
    body: str,
    subject: str,
    summary: str | None,
    *,
    events_found: bool,
) -> bool:
    """Only use Ollama when rule-based parsing found signals but no events."""
    if events_found:
        return False
    return email_has_calendar_date_signals(body, subject, summary)