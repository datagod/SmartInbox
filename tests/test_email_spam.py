"""SpamAssassin integration tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from smartinbox.email_spam import (
    build_rfc822_message,
    classify_email_spam,
    parse_spamassassin_output,
    resolve_spam_command,
    sanitize_rfc822_header,
)


def test_sanitize_rfc822_header_strips_newlines():
    assert sanitize_rfc822_header("Hello\r\nWorld") == "Hello World"
    assert sanitize_rfc822_header("  spaced  ") == "spaced"


def test_build_rfc822_header_newlines_do_not_raise():
    raw = build_rfc822_message(
        sender="News <news@example.com>\r\n",
        subject="Line one\nLine two",
        body="Body text.",
    )
    text = raw.decode("utf-8", errors="replace")
    assert "From: News <news@example.com>" in text
    assert "Subject: Line one Line two" in text


def test_build_rfc822_includes_sender_subject_body():
    raw = build_rfc822_message(
        sender="Alice <alice@example.com>",
        subject="Hello",
        body="Please review the attached invoice.",
    )
    text = raw.decode("utf-8", errors="replace")
    assert "From: Alice <alice@example.com>" in text
    assert "Subject: Hello" in text
    assert "Please review the attached invoice." in text


def test_parse_spamassassin_spamc_output():
    output = "Spam: True ; 7.8 / 5.0\n"
    verdict, score, required = parse_spamassassin_output(output, threshold=5.0)
    assert verdict is True
    assert score == 7.8
    assert required == 5.0


def test_parse_spamassassin_spamc_score_only():
    output = "6.3/5.0\n"
    verdict, score, required = parse_spamassassin_output(output, threshold=5.0)
    assert verdict is True
    assert score == 6.3
    assert required == 5.0


def test_parse_spamassassin_report_output():
    output = (
        "X-Spam-Status: No, score=-2.1 required=5.0 tests=ALL_TRUSTED\n"
        "Content analysis details:   (-2.1 points, 5.0 required).\n"
    )
    verdict, score, required = parse_spamassassin_output(output, threshold=5.0)
    assert verdict is False
    assert score == -2.1
    assert required == 5.0


def test_resolve_spam_command_prefers_spamassassin():
    with patch("smartinbox.email_spam.shutil.which", return_value="/usr/bin/spamassassin"):
        argv = resolve_spam_command()
    assert argv == ["/usr/bin/spamassassin", "-t"]


def test_resolve_spam_command_uses_spamc_when_configured():
    with patch("smartinbox.email_spam.shutil.which", return_value="/usr/bin/spamc"):
        argv = resolve_spam_command(use_spamd=True, spamd_host="127.0.0.1", spamd_port=783)
    assert argv == ["/usr/bin/spamc", "-d", "127.0.0.1", "-p", "783", "-c"]


def test_classify_email_spam_parses_subprocess_output():
    report = "Spam: False ; 1.2 / 5.0\n"

    async def _fake_communicate(_payload):
        return report.encode(), b""

    proc = AsyncMock()
    proc.communicate = _fake_communicate
    proc.returncode = 0

    with patch(
        "smartinbox.email_spam.resolve_spam_command",
        return_value=["spamassassin", "-t"],
    ), patch(
        "smartinbox.email_spam.asyncio.create_subprocess_exec",
        return_value=proc,
    ):
        is_spam, err, score = asyncio.run(
            classify_email_spam(
                sender="bob@example.com",
                subject="Meeting",
                body="See you at 3pm.",
            )
        )

    assert err is None
    assert is_spam is False
    assert score == 1.2


def test_classify_email_spam_missing_binary():
    with patch("smartinbox.email_spam.resolve_spam_command", return_value=None):
        is_spam, err, score = asyncio.run(
            classify_email_spam(
                sender="bob@example.com",
                subject="Meeting",
                body="See you at 3pm.",
            )
        )
    assert is_spam is None
    assert score is None
    assert err and "not found" in err.lower()