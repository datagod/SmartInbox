"""Summarize email content via local Ollama."""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_SYSTEM = """You summarize incoming email for a busy homeowner.
Be thorough and factual. Use only information from the email — do not invent details.
Return markdown with:
## Summary
A clear paragraph of 4-6 sentences covering who sent it, what it is about, and anything time-sensitive.

## Key points
- bullet list (5-8 items)
- Each bullet should be a full sentence with specific details: names, dates, amounts, deadlines, links, or requests from the email.

## Action needed
yes or no, and one short line explaining what to do if yes."""


def build_summary_ollama_options() -> dict[str, Any]:
    """Ollama runtime options for email summaries (see Ollama API `options`)."""
    return {"num_predict": 2048}


def default_system_prompt() -> str:
    return DEFAULT_SYSTEM


def resolve_system_prompt(custom: str | None) -> str:
    text = (custom or "").strip()
    return text if text else DEFAULT_SYSTEM


def model_matches_listed(model: str, names: list[str]) -> bool:
    m = model.strip()
    if not m:
        return False
    return any(
        n == m or n.startswith(f"{m}:") or m.startswith(f"{n}:")
        for n in names
    )


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
    system_prompt: str | None = None,
    timeout: float = 120.0,
) -> tuple[str | None, str | None]:
    """Call Ollama /api/chat. Returns (markdown, error)."""
    url = f"{base_url.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": resolve_system_prompt(system_prompt)},
            {"role": "user", "content": build_prompt(sender, subject, body)},
        ],
        "stream": False,
        "options": build_summary_ollama_options(),
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


async def list_ollama_models(
    base_url: str, *, timeout: float = 8.0
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch models from Ollama /api/tags. Returns (models, error)."""
    url = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}/api/tags")
        if resp.status_code >= 400:
            return [], f"HTTP {resp.status_code}"
        data = resp.json()
        models = [
            {
                "name": str(m.get("name", "")),
                "size": m.get("size"),
                "modified_at": m.get("modified_at"),
            }
            for m in data.get("models", [])
            if m.get("name")
        ]
        models.sort(key=lambda m: m["name"].lower())
        return models, None
    except httpx.RequestError as e:
        return [], str(e)


async def probe_ollama(base_url: str, model: str, *, timeout: float = 8.0) -> dict[str, Any]:
    result: dict[str, Any] = {
        "reachable": False,
        "model_listed": False,
        "error": None,
        "models_found": 0,
    }
    models, err = await list_ollama_models(base_url, timeout=timeout)
    if err:
        result["error"] = err
        return result
    names = [m["name"] for m in models]
    result["reachable"] = True
    result["models_found"] = len(names)
    result["model_listed"] = model_matches_listed(model, names)
    if not result["model_listed"]:
        result["error"] = f"model {model!r} not loaded"
    return result