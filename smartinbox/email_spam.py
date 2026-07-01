"""Classify whether an email looks spammy before voice alerts."""

from __future__ import annotations

import re
from typing import Any

import httpx

DEFAULT_SPAM_MODEL = "llama3.2:1b"


DEFAULT_SPAM_SYSTEM = """You classify incoming email for a personal inbox.
Decide if the message looks like spam, phishing, scam, or unwanted bulk marketing.

Reply with exactly one token on the first line:
NOT_SPAM — personal mail, receipts, appointments, work, newsletters the user likely wants
SPAM — scams, phishing, fake invoices, SEO pitches, crypto/Nigerian prince, obvious junk

Optional second line: one short reason (max 12 words)."""


def default_spam_system_prompt() -> str:
    return DEFAULT_SPAM_SYSTEM


def build_spam_ollama_options(*, main_gpu: int | None = None) -> dict[str, Any]:
    """Ollama runtime options for spam checks — tiny output, fast turnaround.

    Spam runs on CPU (num_gpu=0) so a large summary model can stay resident in GPU
    VRAM without llama-server crashing on dual load.
    """
    opts: dict[str, Any] = {
        "num_predict": 48,
        "temperature": 0,
        "num_gpu": 0,
    }
    if main_gpu is not None and main_gpu >= 0:
        opts["num_gpu"] = -1
        opts["main_gpu"] = int(main_gpu)
    return opts


def build_spam_prompt(
    *,
    sender: str,
    subject: str,
    body: str,
    summary: str | None = None,
) -> str:
    summary_trim = (summary or "").strip()[:1200]
    parts = [
        f"From: {sender or '(unknown)'}",
        f"Subject: {subject or '(no subject)'}",
    ]
    if summary_trim:
        parts.append(f"Summary:\n{summary_trim}")
        snippet = (body or "").strip()[:800]
        if snippet:
            parts.append(f"Opening snippet:\n{snippet}")
    else:
        parts.append(f"Body:\n{(body or '')[:3500] or '(empty)'}")
    return "\n\n".join(parts)


def parse_spam_verdict(content: str) -> bool | None:
    """Return True if spam, False if not, None if unclear."""
    text = (content or "").strip()
    if not text:
        return None
    first = text.splitlines()[0].strip().upper()
    first = re.sub(r"[^A-Z_ ]+", " ", first)
    first = re.sub(r"\s+", " ", first).strip()
    if "NOT SPAM" in first or first.startswith("NOT_SPAM"):
        return False
    if first.startswith("SPAM") or first == "SPAM" or " SPAM" in f" {first} ":
        return True
    if first in {"YES", "JUNK", "PHISHING", "SCAM"}:
        return True
    if first in {"NO", "LEGIT", "LEGITIMATE", "OK", "CLEAN"}:
        return False
    upper = text.upper()
    if "NOT_SPAM" in upper or "NOT SPAM" in upper:
        return False
    if re.search(r"\bSPAM\b", upper):
        return True
    return None


async def classify_email_spam(
    *,
    base_url: str,
    model: str,
    sender: str,
    subject: str,
    body: str,
    summary: str | None = None,
    system_prompt: str | None = None,
    ollama_options: dict[str, Any] | None = None,
    timeout: float = 60.0,
    keep_alive: str | int = -1,
) -> tuple[bool | None, str | None]:
    """Ask Ollama if email looks spammy. Returns (is_spam, error)."""
    url = f"{base_url.rstrip('/')}/api/chat"
    system = (system_prompt or "").strip() or DEFAULT_SPAM_SYSTEM
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": build_spam_prompt(
                    sender=sender,
                    subject=subject,
                    body=body,
                    summary=summary,
                ),
            },
        ],
        "stream": False,
        "keep_alive": keep_alive,
        "options": ollama_options or build_spam_ollama_options(),
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return None, f"Ollama spam check timed out after {timeout:.0f}s"
    except httpx.HTTPStatusError as e:
        return None, f"Ollama HTTP {e.response.status_code}: {e.response.text[:300]}"
    except httpx.RequestError as e:
        return None, f"Ollama request error: {e}"

    if isinstance(data, dict) and data.get("error"):
        return None, str(data["error"])
    content = (data.get("message") or {}).get("content")
    if not content or not str(content).strip():
        return None, "Ollama returned empty spam verdict"
    verdict = parse_spam_verdict(str(content))
    if verdict is None:
        return None, f"Could not parse spam verdict: {str(content).strip()[:120]}"
    return verdict, None