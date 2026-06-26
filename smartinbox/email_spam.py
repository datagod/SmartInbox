"""Classify whether an email looks spammy before voice alerts."""

from __future__ import annotations

import re

import httpx

DEFAULT_SPAM_SYSTEM = """You classify incoming email for a personal inbox.
Decide if the message looks like spam, phishing, scam, or unwanted bulk marketing.

Reply with exactly one token on the first line:
NOT_SPAM — personal mail, receipts, appointments, work, newsletters the user likely wants
SPAM — scams, phishing, fake invoices, SEO pitches, crypto/Nigerian prince, obvious junk

Optional second line: one short reason (max 12 words)."""


def default_spam_system_prompt() -> str:
    return DEFAULT_SPAM_SYSTEM


def build_spam_prompt(
    *,
    sender: str,
    subject: str,
    body: str,
    summary: str | None = None,
) -> str:
    body_trim = (body or "")[:8000]
    summary_trim = (summary or "").strip()[:1200]
    parts = [
        f"From: {sender or '(unknown)'}",
        f"Subject: {subject or '(no subject)'}",
    ]
    if summary_trim:
        parts.append(f"Summary:\n{summary_trim}")
    parts.append(f"Body:\n{body_trim or '(empty)'}")
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
    timeout: float = 60.0,
) -> tuple[bool | None, str | None]:
    """Ask Ollama if email looks spammy. Returns (is_spam, error)."""
    url = f"{base_url.rstrip('/')}/api/chat"
    system = (system_prompt or "").strip() or DEFAULT_SPAM_SYSTEM
    payload = {
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