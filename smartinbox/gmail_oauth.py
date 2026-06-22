"""Gmail OAuth 2.0 — no passwords in the UI."""

from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from smartinbox.config import google_oauth_config
from smartinbox.db import clear_oauth_token, load_oauth_token, pop_oauth_state, save_oauth_state, save_oauth_token

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _client_config() -> dict[str, Any]:
    cfg = google_oauth_config()
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env"
        )
    return {
        "web": {
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [cfg["redirect_uri"]],
        }
    }


def start_oauth_flow(conn: sqlite3.Connection) -> tuple[str, str]:
    """Return (authorization_url, state)."""
    state = secrets.token_urlsafe(32)
    save_oauth_state(conn, state)
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=google_oauth_config()["redirect_uri"],
        state=state,
    )
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return url, state


def finish_oauth_flow(conn: sqlite3.Connection, *, code: str, state: str) -> str:
    """Exchange code for tokens; return connected email address."""
    if not pop_oauth_state(conn, state):
        raise RuntimeError("Invalid or expired OAuth state")
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=google_oauth_config()["redirect_uri"],
        state=state,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    email = str(profile.get("emailAddress") or "").strip()
    token_json = creds.to_json()
    save_oauth_token(conn, email, token_json)
    return email


def disconnect_gmail(conn: sqlite3.Connection) -> None:
    clear_oauth_token(conn)


def _credentials_from_store(conn: sqlite3.Connection) -> Credentials | None:
    stored = load_oauth_token(conn)
    if not stored:
        return None
    creds = Credentials.from_authorized_user_info(stored["token"], SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_oauth_token(conn, stored["email"], creds.to_json())
    return creds


def gmail_connection(conn: sqlite3.Connection) -> dict[str, Any]:
    stored = load_oauth_token(conn)
    if not stored:
        return {"connected": False, "email": None}
    creds = _credentials_from_store(conn)
    if creds is None or not creds.valid:
        return {"connected": False, "email": stored.get("email")}
    return {"connected": True, "email": stored.get("email")}


def build_gmail_service(conn: sqlite3.Connection):
    creds = _credentials_from_store(conn)
    if creds is None or not creds.valid:
        return None
    return build("gmail", "v1", credentials=creds, cache_discovery=False)