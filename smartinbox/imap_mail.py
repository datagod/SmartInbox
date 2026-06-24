"""IMAP mail — Gmail, Proton Mail (Bridge), and multi-account polling."""

from __future__ import annotations

import email
import hashlib
import html
import imaplib
import re
import sqlite3
import time
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Any

IMAP_TIMEOUT_SECONDS = 30.0

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {
        "label": "Gmail",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "use_ssl": True,
    },
    "proton": {
        "label": "Proton Mail",
        "imap_host": "127.0.0.1",
        "imap_port": 1143,
        "use_ssl": False,
        "use_starttls": True,
    },
}


def preset_use_starttls(provider: str, *, use_ssl: bool) -> bool:
    if use_ssl:
        return False
    preset = PROVIDER_PRESETS.get((provider or "").strip().lower(), {})
    return bool(preset.get("use_starttls", False))


def normalize_password(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip())


def provider_preset(provider: str) -> dict[str, Any]:
    key = (provider or "").strip().lower()
    if key not in PROVIDER_PRESETS:
        raise ValueError(f"Unknown provider: {provider!r}")
    return dict(PROVIDER_PRESETS[key])


def format_imap_error(exc: Exception, *, provider: str = "gmail") -> str:
    raw = exc.args[0] if getattr(exc, "args", None) else exc
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    text = text.strip()
    prov = (provider or "gmail").lower()

    if prov == "proton":
        if "AUTHENTICATIONFAILED" in text or "Invalid credentials" in text or "authentication failed" in text.lower():
            return (
                "Proton Bridge rejected the login. Checklist: "
                "(1) Install and run Proton Mail Bridge on this machine. "
                "(2) Sign in to Bridge with your Proton account. "
                "(3) In Bridge, copy the IMAP password — not your Proton account password. "
                "(4) Use your full Proton email address as the username."
            )
        if "Connection refused" in text or "111" in text:
            return (
                "Cannot reach Proton Mail Bridge at 127.0.0.1:1143. "
                "Start Proton Mail Bridge and ensure IMAP is enabled in Bridge settings."
            )
        return text or str(exc)

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


def _strip_html(html_doc: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_doc, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|h[1-6]|li|tr|table|blockquote)>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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


def message_id_for(msg: email.message.Message, imap_uid: bytes | str, account_id: str) -> str:
    raw = (msg.get("Message-ID") or "").strip()
    if raw:
        slug = re.sub(r"[^\w@.+_-]", "_", raw)[:180]
        base = slug or hashlib.sha256(raw.encode()).hexdigest()[:32]
    else:
        base = f"imap-{imap_uid}"
    return f"{account_id}:{base}"


def parse_imap_message(
    raw_bytes: bytes, imap_uid: bytes | str, *, account_id: str, account_email: str, provider: str
) -> dict[str, Any]:
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
            received_at = 0.0
    uid_str = imap_uid.decode() if isinstance(imap_uid, bytes) else str(imap_uid)
    return {
        "id": message_id_for(msg, imap_uid, account_id),
        "imap_uid": uid_str,
        "account_id": account_id,
        "account_email": account_email,
        "provider": provider,
        "thread_id": (msg.get("In-Reply-To") or msg.get("References") or "")[:200] or None,
        "sender": sender,
        "subject": subject,
        "snippet": snippet,
        "body_text": body[:50000],
        "received_at": received_at,
    }


def _imap_connect(
    host: str, port: int, *, use_ssl: bool, use_starttls: bool = False
) -> imaplib.IMAP4:
    timeout = IMAP_TIMEOUT_SECONDS
    if use_ssl:
        mail = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    else:
        mail = imaplib.IMAP4(host, port, timeout=timeout)
        if use_starttls:
            mail.starttls()
    return mail


def test_imap_login(
    email_addr: str,
    password: str,
    *,
    imap_host: str,
    imap_port: int,
    use_ssl: bool = True,
    use_starttls: bool = False,
    provider: str = "gmail",
) -> None:
    pwd = normalize_password(password)
    if not email_addr or "@" not in email_addr:
        raise ValueError("A valid email address is required.")
    if len(pwd) < 4:
        raise ValueError("Password looks too short.")
    mail = _imap_connect(
        imap_host, imap_port, use_ssl=use_ssl, use_starttls=use_starttls
    )
    try:
        mail.login(email_addr.strip(), pwd)
    except imaplib.IMAP4.error as e:
        raise RuntimeError(format_imap_error(e, provider=provider)) from e
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def fetch_unread_imap(
    email_addr: str,
    password: str,
    *,
    imap_host: str,
    imap_port: int,
    use_ssl: bool = True,
    use_starttls: bool = False,
    provider: str = "gmail",
    account_id: str,
    max_results: int = 20,
) -> list[dict[str, Any]]:
    pwd = normalize_password(password)
    mail = _imap_connect(
        imap_host, imap_port, use_ssl=use_ssl, use_starttls=use_starttls
    )
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
                messages.append(
                    parse_imap_message(
                        bytes(raw),
                        uid,
                        account_id=account_id,
                        account_email=email_addr.strip(),
                        provider=provider,
                    )
                )
    except imaplib.IMAP4.error as e:
        raise RuntimeError(format_imap_error(e, provider=provider)) from e
    finally:
        try:
            mail.logout()
        except Exception:
            pass
    return messages


def _login_imap_for_account(acct: dict[str, Any], *, readonly: bool = False) -> imaplib.IMAP4:
    use_ssl = bool(acct["use_ssl"])
    mail = _imap_connect(
        acct["imap_host"],
        int(acct["imap_port"]),
        use_ssl=use_ssl,
        use_starttls=preset_use_starttls(acct["provider"], use_ssl=use_ssl),
    )
    mail.login(acct["email"].strip(), normalize_password(acct["password"]))
    mail.select("INBOX", readonly=readonly)
    return mail


def mark_imap_uids_seen_for_account(acct: dict[str, Any], uids: list[str]) -> int:
    clean = [str(u).strip() for u in uids if str(u).strip()]
    if not clean:
        return 0
    mail = _login_imap_for_account(acct, readonly=False)
    marked = 0
    try:
        for uid in clean:
            status, _ = mail.store(uid, "+FLAGS", "\\Seen")
            if status == "OK":
                marked += 1
    except imaplib.IMAP4.error as e:
        raise RuntimeError(
            format_imap_error(e, provider=str(acct.get("provider") or "mail"))
        ) from e
    finally:
        try:
            mail.logout()
        except Exception:
            pass
    return marked


def mark_all_unseen_seen_for_account(acct: dict[str, Any]) -> int:
    mail = _login_imap_for_account(acct, readonly=False)
    try:
        _status, data = mail.search(None, "UNSEEN")
        if not data or not data[0]:
            return 0
        uids = [u.decode() if isinstance(u, bytes) else str(u) for u in data[0].split()]
        if not uids:
            return 0
        marked = 0
        for uid in uids:
            status, _ = mail.store(uid, "+FLAGS", "\\Seen")
            if status == "OK":
                marked += 1
        return marked
    except imaplib.IMAP4.error as e:
        raise RuntimeError(
            format_imap_error(e, provider=str(acct.get("provider") or "mail"))
        ) from e
    finally:
        try:
            mail.logout()
        except Exception:
            pass


# --- DB-backed accounts ---


def _migrate_legacy_account(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='imap_account'"
    ).fetchone()
    if row is None:
        return
    legacy = conn.execute(
        "SELECT email, app_password FROM imap_account WHERE id = 1"
    ).fetchone()
    if legacy is None:
        return
    existing = conn.execute(
        "SELECT id FROM imap_accounts WHERE provider = 'gmail'"
    ).fetchone()
    if existing is None:
        preset = provider_preset("gmail")
        conn.execute(
            """
            INSERT INTO imap_accounts (
                id, provider, email, password, imap_host, imap_port, use_ssl, updated_at
            ) VALUES (?, 'gmail', ?, ?, ?, ?, ?, ?)
            """,
            (
                "gmail",
                legacy["email"],
                legacy["app_password"],
                preset["imap_host"],
                preset["imap_port"],
                1 if preset["use_ssl"] else 0,
                time.time(),
            ),
        )
        conn.commit()


def init_imap_accounts_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS imap_accounts (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            imap_host TEXT NOT NULL,
            imap_port INTEGER NOT NULL,
            use_ssl INTEGER NOT NULL DEFAULT 1,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    _migrate_legacy_account(conn)


def save_imap_account(
    conn: sqlite3.Connection,
    *,
    provider: str,
    email_addr: str,
    password: str,
    imap_host: str | None = None,
    imap_port: int | None = None,
    use_ssl: bool | None = None,
) -> dict[str, Any]:
    preset = provider_preset(provider)
    host = (imap_host or preset["imap_host"]).strip()
    port = int(imap_port if imap_port is not None else preset["imap_port"])
    ssl_flag = preset["use_ssl"] if use_ssl is None else bool(use_ssl)
    account_id = provider.strip().lower()
    conn.execute(
        """
        INSERT INTO imap_accounts (
            id, provider, email, password, imap_host, imap_port, use_ssl, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider) DO UPDATE SET
            email = excluded.email,
            password = excluded.password,
            imap_host = excluded.imap_host,
            imap_port = excluded.imap_port,
            use_ssl = excluded.use_ssl,
            updated_at = excluded.updated_at
        """,
        (
            account_id,
            account_id,
            email_addr.strip(),
            normalize_password(password),
            host,
            port,
            1 if ssl_flag else 0,
            time.time(),
        ),
    )
    conn.commit()
    return account_public_row(
        {
            "id": account_id,
            "provider": account_id,
            "email": email_addr.strip(),
            "imap_host": host,
            "imap_port": port,
            "use_ssl": ssl_flag,
            "updated_at": time.time(),
        }
    )


def clear_imap_account(conn: sqlite3.Connection, provider: str) -> bool:
    cur = conn.execute(
        "DELETE FROM imap_accounts WHERE provider = ?",
        (provider.strip().lower(),),
    )
    conn.commit()
    return cur.rowcount > 0


def load_imap_account(conn: sqlite3.Connection, provider: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, provider, email, password, imap_host, imap_port, use_ssl, updated_at
        FROM imap_accounts WHERE provider = ?
        """,
        (provider.strip().lower(),),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_imap_accounts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, provider, email, imap_host, imap_port, use_ssl, updated_at
        FROM imap_accounts ORDER BY provider
        """
    ).fetchall()
    return [account_public_row(dict(r)) for r in rows]


def account_public_row(row: dict[str, Any]) -> dict[str, Any]:
    preset = PROVIDER_PRESETS.get(row["provider"], {})
    return {
        "id": row["id"],
        "provider": row["provider"],
        "label": preset.get("label", row["provider"]),
        "email": row["email"],
        "connected": True,
        "imap_host": row.get("imap_host"),
        "imap_port": row.get("imap_port"),
        "use_ssl": bool(row.get("use_ssl", 1)),
        "updated_at": row.get("updated_at"),
    }


def mail_accounts_status(conn: sqlite3.Connection) -> dict[str, Any]:
    accounts = list_imap_accounts(conn)
    by_provider = {a["provider"]: a for a in accounts}
    gmail = by_provider.get("gmail") or {"connected": False, "email": None, "provider": "gmail"}
    proton = by_provider.get("proton") or {"connected": False, "email": None, "provider": "proton"}
    if not gmail.get("connected"):
        gmail = {"connected": False, "email": None, "provider": "gmail", "label": "Gmail"}
    if not proton.get("connected"):
        proton = {
            "connected": False,
            "email": None,
            "provider": "proton",
            "label": "Proton Mail",
        }
    return {
        "accounts": accounts,
        "gmail": gmail,
        "proton": proton,
        "connected": bool(accounts),
        "count": len(accounts),
    }


def connect_mail_account(
    conn: sqlite3.Connection,
    *,
    provider: str,
    email_addr: str,
    password: str,
    imap_host: str | None = None,
    imap_port: int | None = None,
) -> dict[str, Any]:
    preset = provider_preset(provider)
    host = (imap_host or preset["imap_host"]).strip()
    port = int(imap_port if imap_port is not None else preset["imap_port"])
    test_imap_login(
        email_addr,
        password,
        imap_host=host,
        imap_port=port,
        use_ssl=bool(preset["use_ssl"]),
        use_starttls=bool(preset.get("use_starttls", False)),
        provider=provider,
    )
    return save_imap_account(
        conn,
        provider=provider,
        email_addr=email_addr,
        password=password,
        imap_host=host,
        imap_port=port,
        use_ssl=bool(preset["use_ssl"]),
    )


def disconnect_mail_account(conn: sqlite3.Connection, provider: str) -> None:
    clear_imap_account(conn, provider)


def list_imap_account_records(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, provider, email, password, imap_host, imap_port, use_ssl
        FROM imap_accounts ORDER BY provider
        """
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_unread_for_account_record(
    acct: dict[str, Any], *, max_results: int = 20
) -> list[dict[str, Any]]:
    use_ssl = bool(acct["use_ssl"])
    return fetch_unread_imap(
        acct["email"],
        acct["password"],
        imap_host=acct["imap_host"],
        imap_port=int(acct["imap_port"]),
        use_ssl=use_ssl,
        use_starttls=preset_use_starttls(acct["provider"], use_ssl=use_ssl),
        provider=acct["provider"],
        account_id=acct["id"],
        max_results=max_results,
    )


def fetch_unread_for_all_accounts(
    conn: sqlite3.Connection, *, max_results: int = 20
) -> list[dict[str, Any]]:
    all_messages: list[dict[str, Any]] = []
    for acct in list_imap_account_records(conn):
        all_messages.extend(fetch_unread_for_account_record(acct, max_results=max_results))
    return all_messages


# Backward-compatible Gmail helpers


def gmail_connection(conn: sqlite3.Connection) -> dict[str, Any]:
    acct = load_imap_account(conn, "gmail")
    if not acct:
        return {"connected": False, "email": None, "method": "imap", "provider": "gmail"}
    return {
        "connected": True,
        "email": acct["email"],
        "method": "imap",
        "provider": "gmail",
    }


def connect_gmail(conn: sqlite3.Connection, email_addr: str, app_password: str) -> str:
    saved = connect_mail_account(
        conn, provider="gmail", email_addr=email_addr, password=app_password
    )
    return saved["email"]


def disconnect_gmail(conn: sqlite3.Connection) -> None:
    disconnect_mail_account(conn, "gmail")


def fetch_unread_for_account(conn: sqlite3.Connection, *, max_results: int = 20) -> list[dict[str, Any]]:
    return fetch_unread_for_all_accounts(conn, max_results=max_results)