"""Fetch and parse unread Gmail messages."""

from __future__ import annotations

import base64
import re
from email.utils import parsedate_to_datetime
from typing import Any


def _decode_body(data: str) -> str:
    raw = base64.urlsafe_b64decode(data + "==")
    return raw.decode("utf-8", errors="replace")


def _walk_parts(payload: dict[str, Any]) -> tuple[str, str]:
    """Return (plain_text, html) from MIME payload."""
    plain = ""
    html = ""
    mime = payload.get("mimeType", "")
    body = payload.get("body") or {}
    data = body.get("data")
    if data:
        text = _decode_body(data)
        if mime == "text/plain":
            plain = text
        elif mime == "text/html":
            html = text
    for part in payload.get("parts") or []:
        p, h = _walk_parts(part)
        if p and not plain:
            plain = p
        if h and not html:
            html = h
    return plain, html


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _header(headers: list[dict[str, str]], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return str(h.get("value") or "").strip()
    return ""


def parse_message(msg: dict[str, Any]) -> dict[str, Any]:
    payload = msg.get("payload") or {}
    headers = payload.get("headers") or []
    plain, html = _walk_parts(payload)
    body = plain or _strip_html(html) or str(msg.get("snippet") or "")
    date_hdr = _header(headers, "Date")
    received_at = 0.0
    if date_hdr:
        try:
            received_at = parsedate_to_datetime(date_hdr).timestamp()
        except (TypeError, ValueError, OSError):
            received_at = 0.0
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "sender": _header(headers, "From"),
        "subject": _header(headers, "Subject") or "(no subject)",
        "snippet": str(msg.get("snippet") or ""),
        "body_text": body[:50000],
        "received_at": received_at,
    }


def fetch_unread_messages(service, *, max_results: int = 20) -> list[dict[str, Any]]:
    if service is None:
        return []
    result = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], q="is:unread", maxResults=max_results)
        .execute()
    )
    messages: list[dict[str, Any]] = []
    for item in result.get("messages") or []:
        msg_id = item.get("id")
        if not msg_id:
            continue
        full = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )
        messages.append(parse_message(full))
    return messages