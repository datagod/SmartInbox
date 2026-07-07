"""Extract calendar events from email via local Ollama."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import httpx

from smartinbox.ollama_options import build_ollama_gpu_options

DEFAULT_SYSTEM = """You extract calendar events mentioned in email.
Return ONLY valid JSON with this shape (no markdown, no commentary):
{"events":[{"title":"short title","start":"ISO-8601 datetime","end":null or ISO-8601,"location":"optional","source_text":"quote from email"}]}

Rules:
- Include only real appointments, deadlines, deliveries, meetings, reservations, or dated actions.
- Use the reference date and timezone to resolve relative phrases (tomorrow, next Friday, this Monday).
- Pay special attention to explicit lines like "Date: 2026-06-26 7:00pm" or "add this date to your calendar".
- Include webinar and Zoom invites (e.g. "join us on June 27, 2026 at 10:00 AM Pacific Time").
- Put destination phrases such as "Go to Perth" in location when present.
- If no date/time is mentioned, omit the event.
- Include start time only when the email states one explicitly (e.g. "at 2:30 PM", "7:00pm", "14:00"). Never invent or default a time.
- source_text must be a short verbatim phrase from the email supporting the date and time.
- If no events found, return {"events":[]}."""


def default_system_prompt() -> str:
    return DEFAULT_SYSTEM


def resolve_system_prompt(custom: str | None) -> str:
    text = (custom or "").strip()
    return text if text else DEFAULT_SYSTEM


def build_prompt(
    *,
    sender: str,
    subject: str,
    body: str,
    reference_iso: str,
    timezone: str,
) -> str:
    body_trim = (body or "")[:12000]
    return f"""Extract calendar events from this email.

Reference date (when email was received): {reference_iso}
Timezone: {timezone}

From: {sender}
Subject: {subject}

Body:
{body_trim}
"""


_SOURCE_TIME_RE = re.compile(
    r"(?i)"
    r"(?:\bat\s+)?\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?|"
    r"(?:\bat\s+)?\d{1,2}\s*(?:am|pm)|"
    r"\d{1,2}:\d{2}(?:am|pm)|"
    r"\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}|"
    r"T\d{1,2}:\d{2}"
)


def _source_has_explicit_time(text: str) -> bool:
    return bool(_SOURCE_TIME_RE.search(str(text or "")))


def _strip_json_fences(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _parse_iso_datetime(value: str, tz_name: str) -> float | None:
    from zoneinfo import ZoneInfo

    text = (value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def parse_extracted_events(
    raw_json: str,
    *,
    email_id: str,
    sender: str,
    subject: str,
    timezone: str,
) -> list[dict[str, Any]]:
    from smartinbox.calendar_events import make_event_id

    text = _strip_json_fences(raw_json)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    items = data.get("events") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []

    results: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        start_ts = _parse_iso_datetime(str(item.get("start") or ""), timezone)
        if start_ts is None:
            continue
        end_raw = item.get("end")
        end_ts = (
            _parse_iso_datetime(str(end_raw), timezone)
            if end_raw not in (None, "", "null")
            else None
        )
        source_text = str(item.get("source_text") or "").strip() or None
        if not source_text or not _source_has_explicit_time(source_text):
            continue
        event_id = make_event_id(email_id, title, start_ts)
        results.append(
            {
                "id": event_id,
                "email_id": email_id,
                "title": title[:200],
                "description": str(item.get("description") or "").strip() or None,
                "location": str(item.get("location") or "").strip() or None,
                "event_start": start_ts,
                "event_end": end_ts,
                "source_text": source_text[:500],
                "sender": sender,
                "subject": subject,
            }
        )
    return results


def build_calendar_ollama_options(*, main_gpu: int | None = None) -> dict[str, Any]:
    """Ollama runtime options for calendar extraction (see Ollama API `options`)."""
    return build_ollama_gpu_options(main_gpu=main_gpu)


async def list_loaded_ollama_models(
    base_url: str, *, timeout: float = 8.0
) -> tuple[list[dict[str, Any]], str | None]:
    """Models currently loaded in Ollama VRAM (/api/ps)."""
    url = f"{base_url.rstrip('/')}/api/ps"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return [], f"Ollama timed out after {timeout:.0f}s"
    except httpx.HTTPStatusError as e:
        return [], f"Ollama HTTP {e.response.status_code}: {e.response.text[:300]}"
    except httpx.RequestError as e:
        return [], f"Ollama request error: {e}"
    models = [
        {
            "name": str(m.get("name") or m.get("model") or ""),
            "size_vram": int(m.get("size_vram") or 0),
        }
        for m in data.get("models", [])
        if m.get("name") or m.get("model")
    ]
    return models, None


async def preload_ollama_model(
    *,
    base_url: str,
    model: str,
    options: dict[str, Any] | None = None,
    keep_alive: str | int = "30m",
    timeout: float = 120.0,
) -> str | None:
    """Load a model into Ollama memory. Returns an error string or None on success."""
    url = f"{base_url.rstrip('/')}/api/generate"
    payload: dict[str, Any] = {
        "model": model,
        "prompt": "",
        "stream": False,
        "keep_alive": keep_alive,
    }
    payload["options"] = options or build_calendar_ollama_options()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return f"Ollama timed out after {timeout:.0f}s"
    except httpx.HTTPStatusError as e:
        return f"Ollama HTTP {e.response.status_code}: {e.response.text[:300]}"
    except httpx.RequestError as e:
        return f"Ollama request error: {e}"
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])
    return None


async def extract_calendar_events(
    *,
    base_url: str,
    model: str,
    email_id: str,
    sender: str,
    subject: str,
    body: str,
    reference_ts: float,
    timezone: str,
    system_prompt: str | None = None,
    timeout: float = 120.0,
    ollama_options: dict[str, Any] | None = None,
    keep_alive: str | int = "30m",
) -> tuple[list[dict[str, Any]], str | None]:
    """Call Ollama and return (events, error)."""
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    ref_dt = datetime.fromtimestamp(reference_ts, tz=tz)
    reference_iso = ref_dt.isoformat(timespec="minutes")

    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": resolve_system_prompt(system_prompt)},
            {
                "role": "user",
                "content": build_prompt(
                    sender=sender,
                    subject=subject,
                    body=body,
                    reference_iso=reference_iso,
                    timezone=timezone,
                ),
            },
        ],
        "stream": False,
        "keep_alive": keep_alive,
        "options": ollama_options or build_calendar_ollama_options(),
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return [], f"Ollama timed out after {timeout:.0f}s"
    except httpx.HTTPStatusError as e:
        return [], f"Ollama HTTP {e.response.status_code}: {e.response.text[:300]}"
    except httpx.RequestError as e:
        return [], f"Ollama request error: {e}"

    if isinstance(data, dict) and data.get("error"):
        return [], str(data["error"])
    content = (data.get("message") or {}).get("content")
    if not content or not str(content).strip():
        return [], None

    events = parse_extracted_events(
        str(content),
        email_id=email_id,
        sender=sender,
        subject=subject,
        timezone=timezone,
    )
    return events, None