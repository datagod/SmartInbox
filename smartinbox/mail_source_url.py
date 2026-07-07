"""Deep links to open a stored message in Gmail or Proton Mail web UI."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote


def normalize_rfc822_message_id(value: str | None) -> str | None:
    """Strip angle brackets from a Message-ID header value."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    return text or None


def build_mail_source_url(
    *,
    provider: str | None,
    account_email: str | None,
    rfc822_message_id: str | None = None,
    subject: str | None = None,
) -> str | None:
    """Return a web URL to open this message in the provider's mail UI."""
    prov = str(provider or "").strip().lower()
    msgid = normalize_rfc822_message_id(rfc822_message_id)
    subj = str(subject or "").strip()

    if prov == "gmail":
        if msgid:
            query = quote(f"rfc822msgid:{msgid}", safe="")
        elif subj:
            query = quote(f'subject:"{subj}"', safe="")
        else:
            return None
        auth = quote(str(account_email or "").strip(), safe="")
        if auth:
            return f"https://mail.google.com/mail/u/0/?authuser={auth}#search/{query}"
        return f"https://mail.google.com/mail/u/0/#search/{query}"

    if prov == "proton":
        keyword = msgid or subj
        if not keyword:
            return None
        return f"https://mail.proton.me/u/0/search?keyword={quote(keyword)}"

    return None


def enrich_email_source_url(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["source_url"] = build_mail_source_url(
        provider=data.get("provider"),
        account_email=data.get("account_email"),
        rfc822_message_id=data.get("rfc822_message_id"),
        subject=data.get("subject"),
    )
    return data