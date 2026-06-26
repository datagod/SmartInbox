"""Parse iCalendar (ICS) data from Gmail and other calendar invites."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from smartinbox.calendar_events import make_event_id


def unfold_ics_lines(text: str) -> list[str]:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in raw.split("\n"):
        if not line:
            continue
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line.strip())
    return lines


def _split_property(line: str) -> tuple[str, dict[str, str], str] | None:
    if ":" not in line:
        return None
    head, value = line.split(":", 1)
    key_part, *param_parts = head.split(";")
    key = key_part.strip().upper()
    if not key:
        return None
    params: dict[str, str] = {}
    for part in param_parts:
        if "=" not in part:
            continue
        pk, pv = part.split("=", 1)
        params[pk.strip().upper()] = pv.strip()
    return key, params, value.strip()


def _parse_ics_instant(
    value: str,
    params: dict[str, str],
    *,
    tz_name: str,
) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    if params.get("VALUE") == "DATE" or (len(text) == 8 and "T" not in text):
        try:
            dt = datetime.strptime(text, "%Y%m%d")
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
            return dt.timestamp()
        except (ValueError, OSError):
            return None
    if text.endswith("Z"):
        try:
            fmt = "%Y%m%dT%H%M%S" if len(text) == 16 else "%Y%m%dT%H%M"
            dt = datetime.strptime(text.rstrip("Z"), fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    tzid = params.get("TZID") or tz_name
    try:
        zone = ZoneInfo(tzid)
    except Exception:
        zone = ZoneInfo(tz_name)
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=zone)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def _unescape_ics_text(value: str) -> str:
    text = str(value or "")
    text = text.replace("\\n", "\n").replace("\\N", "\n")
    text = text.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    return text.strip()


def _iter_vevent_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    in_event = False
    for line in lines:
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = []
            in_event = True
            continue
        if upper == "END:VEVENT":
            if in_event and current:
                blocks.append(current)
            current = []
            in_event = False
            continue
        if in_event:
            current.append(line)
    return blocks


def parse_ics_calendar_events(
    ics_text: str,
    *,
    email_id: str,
    sender: str,
    subject: str,
    timezone: str,
) -> list[dict[str, Any]]:
    """Parse one or more ICS payloads into calendar event dicts."""
    if not (ics_text or "").strip():
        return []

    try:
        default_tz = str(ZoneInfo(timezone))
    except Exception:
        default_tz = "UTC"

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for chunk in re.split(r"(?=BEGIN:VCALENDAR)", ics_text, flags=re.I):
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = unfold_ics_lines(chunk)
        for block in _iter_vevent_blocks(lines):
            fields: dict[str, tuple[dict[str, str], str]] = {}
            for line in block:
                parsed = _split_property(line)
                if parsed is None:
                    continue
                key, params, value = parsed
                fields[key] = (params, value)

            status = _unescape_ics_text(fields.get("STATUS", ({}, ""))[1]).upper()
            if status == "CANCELLED":
                continue

            summary = _unescape_ics_text(fields.get("SUMMARY", ({}, ""))[1])
            if not summary:
                summary = _unescape_ics_text(subject) or "Calendar invite"

            start_params, start_value = fields.get("DTSTART", ({}, ""))
            start_ts = _parse_ics_instant(start_value, start_params, tz_name=default_tz)
            if start_ts is None:
                continue

            end_ts = None
            if "DTEND" in fields:
                end_params, end_value = fields["DTEND"]
                end_ts = _parse_ics_instant(end_value, end_params, tz_name=default_tz)

            location = _unescape_ics_text(fields.get("LOCATION", ({}, ""))[1]) or None
            description = _unescape_ics_text(fields.get("DESCRIPTION", ({}, ""))[1]) or None
            source_bits = [summary]
            if location:
                source_bits.append(location)
            if description:
                source_bits.append(description[:240])
            source_text = " · ".join(source_bits)[:500]

            event_id = make_event_id(email_id, summary, start_ts)
            if event_id in seen:
                continue
            seen.add(event_id)

            results.append(
                {
                    "id": event_id,
                    "email_id": email_id,
                    "title": summary[:200],
                    "description": description,
                    "location": location,
                    "event_start": start_ts,
                    "event_end": end_ts,
                    "source_text": source_text,
                    "sender": sender,
                    "subject": subject,
                }
            )
    return results