"""Load SmartInbox YAML config and environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _config_paths() -> list[Path]:
    return [
        ROOT / "config.yaml",
        Path.home() / ".config" / "smartinbox" / "config.yaml",
    ]


def load_settings() -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    settings: dict[str, Any] = {}
    for path in _config_paths():
        if path.is_file():
            with path.open(encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                settings = loaded
            break
    return settings


def base_url() -> str:
    return os.getenv("SMARTINBOX_BASE_URL", "http://127.0.0.1:8090").rstrip("/")


def _load_env() -> None:
    """Reload .env so credential updates apply without restarting the server."""
    load_dotenv(ROOT / ".env", override=True)


def google_oauth_config() -> dict[str, str]:
    _load_env()
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if "your-" in client_id or client_id.startswith("your"):
        client_id = ""
    if "your-" in client_secret or client_secret.startswith("your"):
        client_secret = ""
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": f"{base_url()}/api/auth/google/callback",
    }


def data_dir(settings: dict[str, Any] | None = None) -> Path:
    s = settings or load_settings()
    raw = str(s.get("data_dir", "data")).strip() or "data"
    path = Path(raw)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path