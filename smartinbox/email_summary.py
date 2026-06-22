"""Summarize email content via local Ollama."""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_SYSTEM = """You summarize incoming email for a busy homeowner.
Be concise and factual. Use only information from the email.
Return markdown with:
## Summary
2-3 sentences.

## Key points
- bullet list (2-5 items)

## Action needed
yes/no and one short line if yes."""


def build_prompt(sender: str, subject: str, body: str) -> str:
    body_trim = (body or "")[:12000]
    return f"""Summarize this email.

From: {sender}
Subject: {subject}

Body:
{body_trim}
"""


async def summarize_email(
    *,
    base_url: str,
    model: str,
    sender: str,
    subject: str,
    body: str,
    timeout: float = 120.0,
) -> tuple[str | None, str | None]:
    """Call Ollama /api/chat. Returns (markdown, error)."""
    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": DEFAULT_SYSTEM},
            {"role": "user", "content": build_prompt(sender, subject, body)},
        ],
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return None, f"Ollama timed out after {timeout:.0f}s"
    except httpx.HTTPStatusError as e:
        return None, f"Ollama HTTP {e.response.status_code}: {e.response.text[:300]}"
    except httpx.RequestError as e:
        return None, f"Ollama request error: {e}"

    if isinstance(data, dict) and data.get("error"):
        return None, str(data["error"])
    content = (data.get("message") or {}).get("content")
    if not content or not str(content).strip():
        return None, "Ollama returned empty summary"
    return str(content).strip(), None


async def probe_ollama(base_url: str, model: str, *, timeout: float = 8.0) -> dict[str, Any]:
    url = base_url.rstrip("/")
    result: dict[str, Any] = {
        "reachable": False,
        "model_listed": False,
        "error": None,
        "models_found": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}/api/tags")
        if resp.status_code >= 400:
            result["error"] = f"HTTP {resp.status_code}"
            return result
        data = resp.json()
        names = [str(m.get("name", "")) for m in data.get("models", []) if m.get("name")]
        result["reachable"] = True
        result["models_found"] = len(names)
        m = model.strip()
        result["model_listed"] = any(
            n == m or n.startswith(f"{m}:") or m.startswith(f"{n}:")
            for n in names
        )
        if not result["model_listed"]:
            result["error"] = f"model {model!r} not loaded"
    except httpx.RequestError as e:
        result["error"] = str(e)
    return result