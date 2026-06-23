"""Backward-compatible re-exports — use imap_mail.py for new code."""

from smartinbox.imap_mail import (
    connect_gmail,
    disconnect_gmail,
    fetch_unread_for_account,
    gmail_connection,
    load_imap_account,
    normalize_password as normalize_app_password,
    test_imap_login,
)

__all__ = [
    "connect_gmail",
    "disconnect_gmail",
    "fetch_unread_for_account",
    "gmail_connection",
    "load_imap_account",
    "normalize_app_password",
    "test_imap_login",
]