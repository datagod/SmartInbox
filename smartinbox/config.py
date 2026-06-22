"""Load SmartInbox YAML config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _config_paths() -> list[Path]:
    return [
        ROOT / "config.yaml",
        Path.home() / ".config" / "smartinbox" / "config.yaml",
    ]


def load_settings() -> dict[str, Any]:
    settings: dict[str, Any] = {}
    for path in _config_paths():
        if path.is_file():
            with path.open(encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                settings = loaded
            break
    return settings


def data_dir(settings: dict[str, Any] | None = None) -> Path:
    s = settings or load_settings()
    raw = str(s.get("data_dir", "data")).strip() or "data"
    path = Path(raw)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path