"""Classify whether an email looks spammy before voice alerts."""

from __future__ import annotations

import asyncio
import re
import shutil
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any

DEFAULT_SPAM_THRESHOLD = 5.0
DEFAULT_SPAM_TIMEOUT = 30.0
DEFAULT_SPAMD_HOST = "127.0.0.1"
DEFAULT_SPAMD_PORT = 783

_SPAMC_RESULT_RE = re.compile(
    r"Spam:\s*(?P<flag>True|False|Yes|No)\s*;\s*(?P<score>[-\d.]+)\s*/\s*(?P<threshold>[-\d.]+)",
    re.IGNORECASE,
)
_SPAMC_SCORE_RE = re.compile(
    r"(?P<score>[-\d.]+)\s*/\s*(?P<threshold>[-\d.]+)\s*$",
    re.MULTILINE,
)
_SPAM_STATUS_RE = re.compile(
    r"X-Spam-Status:\s*(?P<flag>Yes|No)\b.*?score=(?P<score>[-\d.]+).*?required=(?P<threshold>[-\d.]+)",
    re.IGNORECASE,
)
_POINTS_RE = re.compile(
    r"Content analysis details:\s*\((?P<score>[-\d.]+)\s+points,\s*(?P<threshold>[-\d.]+)\s+required\)",
    re.IGNORECASE,
)
_HEADER_NEWLINES_RE = re.compile(r"[\r\n]+")


def sanitize_rfc822_header(value: str | None) -> str:
    """RFC822 header values must not contain line breaks (Python EmailMessage enforces this)."""
    return _HEADER_NEWLINES_RE.sub(" ", (value or "").strip())


def build_rfc822_message(*, sender: str, subject: str, body: str) -> bytes:
    """Build a minimal RFC822 message for SpamAssassin."""
    msg = EmailMessage()
    msg["From"] = sanitize_rfc822_header(sender) or "unknown@localhost"
    msg["Subject"] = sanitize_rfc822_header(subject) or "(no subject)"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = f"<smartinbox-{abs(hash((sender, subject, body[:200])))}@localhost>"
    msg.set_content((body or "").strip() or "(empty)")
    return msg.as_bytes()


def parse_spamassassin_output(
    text: str, *, threshold: float
) -> tuple[bool | None, float | None, float | None]:
    """Return (is_spam, score, required_threshold)."""
    blob = text or ""
    for pattern in (_SPAMC_RESULT_RE, _SPAMC_SCORE_RE, _SPAM_STATUS_RE, _POINTS_RE):
        match = pattern.search(blob)
        if not match:
            continue
        score = float(match.group("score"))
        required_raw = match.groupdict().get("threshold")
        required = float(required_raw) if required_raw is not None else threshold
        flag = (match.groupdict().get("flag") or "").strip().lower()
        if flag in {"true", "yes"}:
            return True, score, required
        if flag in {"false", "no"}:
            return False, score, required
        return score >= required, score, required
    return None, None, None


def resolve_spam_command(
    *,
    command: str | None = None,
    use_spamd: bool = False,
    spamd_host: str = DEFAULT_SPAMD_HOST,
    spamd_port: int = DEFAULT_SPAMD_PORT,
) -> list[str] | None:
    """Resolve argv for a spam check subprocess."""
    if use_spamd:
        spamc = command or shutil.which("spamc")
        if not spamc:
            return None
        return [
            spamc,
            "-d",
            spamd_host,
            "-p",
            str(int(spamd_port)),
            "-c",
        ]
    spamassassin = command or shutil.which("spamassassin")
    if not spamassassin:
        return None
    return [spamassassin, "-t"]


async def probe_spamassassin(
    *,
    command: str | None = None,
    use_spamd: bool = False,
    spamd_host: str = DEFAULT_SPAMD_HOST,
    spamd_port: int = DEFAULT_SPAMD_PORT,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Check whether SpamAssassin tooling is available."""
    argv = resolve_spam_command(
        command=command,
        use_spamd=use_spamd,
        spamd_host=spamd_host,
        spamd_port=spamd_port,
    )
    if not argv:
        return {
            "available": False,
            "engine": "spamassassin",
            "use_spamd": use_spamd,
            "command": None,
            "version": None,
            "error": "spamassassin or spamc not found on PATH",
        }

    version_argv = [argv[0], "--version"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *version_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, FileNotFoundError, OSError) as exc:
        return {
            "available": False,
            "engine": "spamassassin",
            "use_spamd": use_spamd,
            "command": argv[0],
            "version": None,
            "error": str(exc),
        }

    version_text = (stdout or stderr or b"").decode("utf-8", errors="replace").strip()
    version = version_text.splitlines()[0] if version_text else None
    if proc.returncode not in (0, 1):
        return {
            "available": False,
            "engine": "spamassassin",
            "use_spamd": use_spamd,
            "command": argv[0],
            "version": version,
            "error": version_text or f"exit code {proc.returncode}",
        }
    return {
        "available": True,
        "engine": "spamassassin",
        "use_spamd": use_spamd,
        "command": argv[0],
        "version": version,
        "error": None,
    }


async def classify_email_spam(
    *,
    sender: str,
    subject: str,
    body: str,
    threshold: float = DEFAULT_SPAM_THRESHOLD,
    command: str | None = None,
    use_spamd: bool = False,
    spamd_host: str = DEFAULT_SPAMD_HOST,
    spamd_port: int = DEFAULT_SPAMD_PORT,
    timeout: float = DEFAULT_SPAM_TIMEOUT,
) -> tuple[bool | None, str | None, float | None]:
    """Score email with SpamAssassin. Returns (is_spam, error, score)."""
    argv = resolve_spam_command(
        command=command,
        use_spamd=use_spamd,
        spamd_host=spamd_host,
        spamd_port=spamd_port,
    )
    if not argv:
        return (
            None,
            "SpamAssassin not found — install spamassassin or set spam.command in config.yaml",
            None,
        )

    payload = build_rfc822_message(sender=sender, subject=subject, body=body)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(payload),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return None, f"SpamAssassin timed out after {timeout:.0f}s", None
    except (FileNotFoundError, OSError) as exc:
        return None, f"SpamAssassin failed to start: {exc}", None

    output = b"".join(part for part in (stdout, stderr) if part).decode(
        "utf-8", errors="replace"
    )
    if proc.returncode not in (0, 1):
        detail = (output or "").strip()[:300]
        return (
            None,
            detail or f"SpamAssassin exited with code {proc.returncode}",
            None,
        )

    verdict, score, required = parse_spamassassin_output(output, threshold=threshold)
    if verdict is None:
        detail = (output or "").strip()[:300]
        return None, detail or "Could not parse SpamAssassin score", None
    return verdict, None, score