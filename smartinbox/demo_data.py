"""Sample inbox data for screenshot / demo mode."""

from __future__ import annotations

import time
from typing import Any

_NOW = time.time()


def build_demo_emails() -> list[dict[str, Any]]:
    """Clearly fake messages for demo mode (never mixed with live mail in the UI)."""
    hour = 3600.0
    return [
        {
            "id": "demo-001",
            "thread_id": "demo-thread-001",
            "account_id": "demo-gmail",
            "account_email": "you@demo-gmail.fake",
            "provider": "gmail",
            "sender": "Prince of Fontasia <demo.prince@fakemail.example>",
            "subject": "[DEMO] Your royal inbox awaits",
            "snippet": "Greetings! This is a sample message for SmartInbox screenshots only.",
            "body_text": (
                "Greetings from the Kingdom of Fontasia!\n\n"
                "This is a DEMO email. No real gold, no real prince, no real inbox.\n\n"
                "Use demo mode in Settings to capture theme screenshots without exposing "
                "your real mail.\n\n"
                "— The entirely fictional Prince of Fontasia"
            ),
            "received_at": _NOW - hour * 2,
            "created_at": _NOW - hour * 2,
            "summary_short": (
                "**Demo message** from a fake prince inviting you to use SmartInbox demo mode "
                "for screenshots. No action required."
            )[:500],
            "summary_detailed": (
                "## Demo royal greeting\n\n"
                "A **fictional prince** says hello and explains this mail is **sample data only**.\n\n"
                "### Key points\n\n"
                "- Clearly marked `[DEMO]`\n"
                "- Safe for screenshots and theme previews\n"
                "- Not connected to any real account"
            ),
            "alerted_at": None,
            "starred": 1,
        },
        {
            "id": "demo-002",
            "thread_id": "demo-thread-002",
            "account_id": "demo-gmail",
            "account_email": "you@demo-gmail.fake",
            "provider": "gmail",
            "sender": "Totally Real Business Person <definitely.real@demo-mail.fake>",
            "subject": "[DEMO] URGENT: Please verify your demo fortune",
            "snippet": "This is satire. Do not send money to demo mode.",
            "body_text": (
                "DEAR FRIEND,\n\n"
                "I am writing to offer you 1,000,000 DEMO DOLLARS that exist only in this "
                "screenshot.\n\n"
                "This message is part of SmartInbox demo mode. There is no fortune, no wire "
                "transfer, and no prince waiting in a parking lot.\n\n"
                "Thank you for testing themes responsibly."
            ),
            "received_at": _NOW - hour * 5,
            "created_at": _NOW - hour * 5,
            "summary_short": (
                "Satirical **demo spam** about a fake fortune. Explicitly not real; useful for "
                "showing junk styling."
            )[:500],
            "summary_detailed": (
                "## Demo junk mail\n\n"
                "A parody **urgent money** message used to preview how SmartInbox handles "
                "low-trust senders.\n\n"
                "- Marked `[DEMO]` in the subject\n"
                "- Good for testing vote/downvote UI\n"
                "- No real request inside"
            ),
            "alerted_at": None,
            "starred": 0,
        },
        {
            "id": "demo-003",
            "thread_id": "demo-thread-003",
            "account_id": "demo-gmail",
            "account_email": "you@demo-gmail.fake",
            "provider": "gmail",
            "sender": "SmartInbox QA Bot <qa-bot@smartinbox.demo>",
            "subject": "[DEMO] Weekly summary test message",
            "snippet": "Automated sample used to preview Ollama summaries in every theme.",
            "body_text": (
                "Hello!\n\n"
                "This automated DEMO message includes bullet points, a short agenda, and "
                "enough text to fill a summary panel.\n\n"
                "Agenda:\n"
                "1. Open the Summary panel\n"
                "2. Switch themes (C64, Macintosh, PacMail, etc.)\n"
                "3. Capture screenshots\n\n"
                "End of demo transmission."
            ),
            "received_at": _NOW - hour * 9,
            "created_at": _NOW - hour * 9,
            "summary_short": (
                "**QA demo mail** with an agenda for testing summary themes and markdown "
                "rendering."
            )[:500],
            "summary_detailed": (
                "## Demo QA checklist\n\n"
                "Fake weekly update from **SmartInbox QA Bot**.\n\n"
                "### Suggested screenshot flow\n\n"
                "1. Select this message\n"
                "2. Toggle Summary / Original\n"
                "3. Cycle display themes\n\n"
                "**Reminder:** demo mode hides your real inbox."
            ),
            "alerted_at": None,
            "starred": 0,
        },
        {
            "id": "demo-004",
            "thread_id": "demo-thread-004",
            "account_id": "demo-gmail",
            "account_email": "you@demo-gmail.fake",
            "provider": "gmail",
            "sender": "Janet from Accounting <janet.demo@notarealcompany.test>",
            "subject": "[DEMO] Expense report: Giant rubber duck (SAMPLE)",
            "snippet": "Fictional expense report for UI screenshots.",
            "body_text": (
                "Hi,\n\n"
                "Attached is a completely imaginary expense report for a giant rubber duck "
                "used in a demo video shoot.\n\n"
                "Amount: $0.00 (DEMO)\n"
                "Project code: SCREENSHOT-64\n\n"
                "Please approve in your imagination only.\n\n"
                "— Janet (not a real accountant)"
            ),
            "received_at": _NOW - hour * 26,
            "created_at": _NOW - hour * 26,
            "summary_short": (
                "Fake **expense report** about a rubber duck prop. Amount is zero; marked "
                "sample data."
            )[:500],
            "summary_detailed": (
                "## Demo expense mail\n\n"
                "**Janet from Accounting** sends a humorous **sample expense** note.\n\n"
                "- Project: `SCREENSHOT-64`\n"
                "- Total: **$0.00 DEMO**\n"
                "- Useful for business-themed summary layouts"
            ),
            "alerted_at": None,
            "starred": 0,
        },
        {
            "id": "demo-005",
            "thread_id": "demo-thread-005",
            "account_id": "demo-proton",
            "account_email": "you@demo-proton.fake",
            "provider": "proton",
            "sender": "The Weather Cube <cube@fake-weather.demo>",
            "subject": "[DEMO] Tomorrow: 100% chance of demo rain",
            "snippet": "Fabricated forecast for theme previews.",
            "body_text": (
                "WEATHER CUBE DEMO FORECAST\n\n"
                "Tomorrow in Screenshot Valley:\n"
                "• Morning: pixelated sunshine\n"
                "• Afternoon: light theme showers\n"
                "• Evening: 100% chance of demo rain\n\n"
                "Carry an imaginary umbrella."
            ),
            "received_at": _NOW - hour * 40,
            "created_at": _NOW - hour * 40,
            "summary_short": (
                "Playful **demo weather** forecast with pixel sunshine and theme showers."
            )[:500],
            "summary_detailed": (
                "## Demo weather update\n\n"
                "The **Weather Cube** predicts screenshot-friendly conditions.\n\n"
                "- Morning: pixelated sunshine\n"
                "- Afternoon: light theme showers\n"
                "- Evening: demo rain everywhere"
            ),
            "alerted_at": None,
            "starred": 0,
        },
        {
            "id": "demo-006",
            "thread_id": "demo-thread-006",
            "account_id": "demo-proton",
            "account_email": "you@demo-proton.fake",
            "provider": "proton",
            "sender": "Proton Demo Mailbox <secure.demo@proton.fake>",
            "subject": "[DEMO] Encrypted test message (not really encrypted)",
            "snippet": "Sample Proton-styled message for multi-account inbox shots.",
            "body_text": (
                "This message pretends to be secure.\n\n"
                "In demo mode it is simply local sample text shown in the Proton badge color.\n\n"
                "Your real Proton mail is not displayed while demo mode is on."
            ),
            "received_at": _NOW - hour * 52,
            "created_at": _NOW - hour * 52,
            "summary_short": (
                "**Demo Proton** note explaining that real encrypted mail stays hidden during "
                "screenshots."
            )[:500],
            "summary_detailed": (
                "## Demo secure mail\n\n"
                "Explains how **demo mode** keeps real Proton messages out of the inbox view "
                "while preserving badges and layout."
            ),
            "alerted_at": None,
            "starred": 0,
        },
    ]