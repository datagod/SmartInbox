"""Voice alert summaries — brief TTS text and LLM rewrite from a voice prompt."""

from __future__ import annotations

import re
from typing import Any

import httpx

from smartinbox.email_summary import build_summary_ollama_options
from smartinbox.important_senders import sanitize_text_for_tts

DEFAULT_VOICE_STYLE_PROMPT = (
    "Give a very brief summary of the email, one sentence max. "
    "Do not include email addresses."
)


def default_voice_style_prompt() -> str:
    return DEFAULT_VOICE_STYLE_PROMPT


def resolve_voice_style_prompt(custom: str | None) -> str:
    text = (custom or "").strip()
    return text if text else DEFAULT_VOICE_STYLE_PROMPT


def format_voice_llm_prompt(
    *,
    system_prompt: str | None,
    user_message: str,
) -> str:
    """Format the exact system + user messages sent to Ollama for voice summary."""
    system = resolve_voice_style_prompt(system_prompt)
    user = (user_message or "").strip()
    return f"System:\n{system}\n\nUser:\n{user}"


def brief_summary_for_tts(markdown: str, *, max_len: int = 450) -> str:
    """Extract a short plain-text summary suitable for TTS."""
    text = (markdown or "").strip()
    if not text:
        return ""
    match = re.search(r"##\s*Summary\s*\n+([\s\S]*?)(?=\n##|\Z)", text, re.I)
    if match:
        text = match.group(1).strip()
    else:
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        cut = text[:max_len]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        text = cut + "…"
    return sanitize_text_for_tts(text)


async def style_summary_for_voice(
    *,
    base_url: str,
    model: str,
    summary: str,
    system_prompt: str | None = None,
    timeout: float = 120.0,
    ollama_options: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """Rewrite a brief summary for TTS using the voice prompt."""
    brief = brief_summary_for_tts(summary)
    if not brief:
        return None, "empty summary"
    url = f"{base_url.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": resolve_voice_style_prompt(system_prompt)},
            {"role": "user", "content": brief},
        ],
        "stream": False,
        "options": ollama_options or build_summary_ollama_options(),
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
        return None, "Ollama returned empty styled summary"
    spoken = sanitize_text_for_tts(re.sub(r"\s+", " ", str(content).strip()))
    return spoken, None