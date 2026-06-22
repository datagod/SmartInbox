"""Gmail via IMAP + App Password (no OAuth)."""

from __future__ import annotations

import email
import hashlib
import imaplib
import re
import sqlite3
import time
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Any

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


def normalize_app_password(value: str) -> str:
    """Google app passwords are often shown with spaces."""
    return re.sub(r"\s+", "", (value or "").strip())


def format_imap_error(exc: Exception) -> str:
    """Turn imaplib errors into readable, actionable messages."""
    raw = exc.args[0] if getattr(exc, "args", None) else exc
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    text = text.strip()

    if "AUTHENTICATIONFAILED" in text or "Invalid credentials" in text:
        return (
            "Gmail rejected the login. Checklist: "
            "(1) Enable IMAP in Gmail → Settings → See all settings → "
            "Forwarding and POP/IMAP → Enable IMAP. "
            "(2) Use a 16-character App Password from myaccount.google.com/apppasswords "
            "— not your regular Gmail password. "
            "(3) 2-Step Verification must be on. "
            "(4) The email address must match the account that created the app password."
        )
    if "IMAP" in text.upper() and ("disabled" in text.lower() or "not enabled" in text.lower()):
        return (
            "IMAP is disabled on this Gmail account. "
            "In Gmail web: Settings → See all settings → Forwarding and POP/IMAP → Enable IMAP."
        )
    return text or str(exc)


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts).strip()


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_body(msg: email.message.Message) -> str:
    plain = ""
    html = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                continue
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if ctype == "text/plain" and not plain:
                plain = text
            elif ctype == "text/html" and not html:
                html = text
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                if msg.get_content_type() == "text/html":
                    html = text
                else:
                    plain = text
        except Exception:
            pass
    return (plain or _strip_html(html) or "").strip()


def message_id_for(msg: email.message.Message, imap_uid: bytes | str) -> str:
    raw = (msg.get("Message-ID") or "").strip()
    if raw:
        slug = re.sub(r"[^\w@.+_-]", "_", raw)[:200]
        return slug or hashlib.sha256(raw.encode()).hexdigest()[:32]
    return f"imap-{imap_uid}"


def parse_imap_message(raw_bytes: bytes, imap_uid: bytes | str) -> dict[str, Any]:
    msg = email.message_from_bytes(raw_bytes)
    sender = decode_mime_header(msg.get("From"))
    subject = decode_mime_header(msg.get("Subject")) or "(no subject)"
    body = _extract_body(msg)
    snippet = re.sub(r"\s+", " ", body[:240]).strip()
    date_hdr = msg.get("Date")
    received_at = 0.0
    if date_hdr:
        try:
            received_at = parsedate_to_datetime(date_hdr).timestamp()
        except (TypeError, ValueError, OSError):
            received_at = time.time()
    return {
        "id": message_id_for(msg, imap_uid),
        "thread_id": (msg.get("In-Reply-To") or msg.get("References") or "")[:200] or None,
        "sender": sender,
        "subject": subject,
        "snippet": snippet,
        "body_text": body[:50000],
        "received_at": received_at,
    }


def test_imap_login(email_addr: str, app_password: str) -> None:
    """Raise on bad credentials or connection failure."""
    pwd = normalize_app_password(app_password)
    if not email_addr or "@" not in email_addr:
        raise ValueError("A valid Gmail address is required.")
    if len(pwd) < 8:
        raise ValueError("App password looks too short.")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        mail.login(email_addr.strip(), pwd)
    except imaplib.IMAP4.error as e:
        raise RuntimeError(format_imap_error(e)) from e
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def fetch_unread_imap(email_addr: str, app_password: str, *, max_results: int = 20) -> list[dict[str, Any]]:
    pwd = normalize_app_password(app_password)
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    messages: list[dict[str, Any]] = []
    try:
        mail.login(email_addr.strip(), pwd)
        mail.select("INBOX", readonly=True)
        _status, data = mail.search(None, "UNSEEN")
        if not data or not data[0]:
            return []
        uids = data[0].split()
        for uid in uids[-max_results:]:
            _status, fetched = mail.fetch(uid, "(RFC822)")
            if not fetched:
                continue
            for item in fetched:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                raw = item[1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                messages.append(parse_imap_message(bytes(raw), uid))
    finally:
        try:
            mail.logout()
        except Exception:
            pass
    return messages


# --- DB-backed account ---


def save_imap_account(conn: sqlite3.Connection, email_addr: str, app_password: str) -> None:
    conn.execute(
        """
        INSERT INTO imap_account (id, email, app_password, updated_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            email = excluded.email,
            app_password = excluded.app_password,
            updated_at = excluded.updated_at
        """,
        (email_addr.strip(), normalize_app_password(app_password), time.time()),
    )
    conn.commit()


def load_imap_account(conn: sqlite3.Connection) -> dict[str, str] | None:
    row = conn.execute(
        "SELECT email, app_password FROM imap_account WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return {"email": row["email"], "app_password": row["app_password"]}


def clear_imap_account(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM imap_account WHERE id = 1")
    conn.commit()


def gmail_connection(conn: sqlite3.Connection) -> dict[str, Any]:
    acct = load_imap_account(conn)
    if not acct:
        return {"connected": False, "email": None, "method": "imap"}
    return {"connected": True, "email": acct["email"], "method": "imap"}


def connect_gmail(conn: sqlite3.Connection, email_addr: str, app_password: str) -> str:
    test_imap_login(email_addr, app_password)
    save_imap_account(conn, email_addr, app_password)
    return email_addr.strip()


def disconnect_gmail(conn: sqlite3.Connection) -> None:
    clear_imap_account(conn)


def fetch_unread_for_account(conn: sqlite3.Connection, *, max_results: int = 20) -> list[dict[str, Any]]:
    acct = load_imap_account(conn)
    if not acct:
        return []
    return fetch_unread_imap(
        acct["email"],
        acct["app_password"],
        max_results=max_results,
    )