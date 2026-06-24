"""Save and load LLM system prompts from the prompts/ folder."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from smartinbox.config import ROOT
from smartinbox.email_summary import default_system_prompt
from smartinbox.voice_summary import default_voice_style_prompt

_PROMPT_EXTENSION = ".txt"
_DEFAULT_FILENAME = "default.txt"
_VOICE_STYLE_DEFAULT_FILENAME = "voice_style_default.txt"
_VOICE_STYLE_PREFIX = "voice_style_"
_MAX_SLUG_LEN = 80


def resolve_prompts_dir(settings: dict[str, Any] | None = None) -> Path:
    s = settings or {}
    ollama = s.get("ollama") if isinstance(s.get("ollama"), dict) else {}
    raw = str(
        (ollama or {}).get("prompts_dir")
        or s.get("prompts_dir")
        or "prompts"
    ).strip() or "prompts"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug_filename(name: str) -> str:
    text = (name or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text).strip("_")
    if not text:
        return ""
    if len(text) > _MAX_SLUG_LEN:
        text = text[:_MAX_SLUG_LEN].rstrip("_")
    return text


def safe_prompt_filename(name: str) -> str | None:
    """Reject path traversal; return a safe .txt basename."""
    raw = str(name or "").strip().replace("\\", "/")
    if not raw or "/" in raw or ".." in raw.split("/"):
        return None
    base = raw
    if not base.endswith(_PROMPT_EXTENSION):
        base = f"{base}{_PROMPT_EXTENSION}" if base else ""
    if not base or base == _PROMPT_EXTENSION:
        return None
    stem = Path(base).stem
    if not stem or not re.match(r"^[\w][\w._-]*$", stem):
        return None
    return base


def prompt_file_path(filename: str, *, prompts_dir: Path) -> Path | None:
    safe = safe_prompt_filename(filename)
    if not safe:
        return None
    return prompts_dir / safe


def ensure_default_prompt_file(*, prompts_dir: Path) -> Path:
    """Write built-in default to default.txt when the folder is first used."""
    path = prompts_dir / _DEFAULT_FILENAME
    if not path.is_file():
        path.write_text(default_system_prompt().strip() + "\n", encoding="utf-8")
    return path


def ensure_default_voice_style_prompt_file(*, prompts_dir: Path) -> Path:
    path = prompts_dir / _VOICE_STYLE_DEFAULT_FILENAME
    if not path.is_file():
        path.write_text(default_voice_style_prompt().strip() + "\n", encoding="utf-8")
    return path


def list_voice_style_prompt_files(*, prompts_dir: Path) -> list[dict[str, Any]]:
    ensure_default_voice_style_prompt_file(prompts_dir=prompts_dir)
    items: list[dict[str, Any]] = []
    for entry in sorted(prompts_dir.glob(f"{_VOICE_STYLE_PREFIX}*{_PROMPT_EXTENSION}")):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        if stat.st_size <= 0:
            continue
        items.append(
            {
                "filename": entry.name,
                "label": entry.stem.replace("_", " ").strip() or entry.stem,
                "is_default": entry.name == _VOICE_STYLE_DEFAULT_FILENAME,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            }
        )
    items.sort(key=lambda row: (not row.get("is_default"), str(row.get("label") or "").lower()))
    return items


def list_prompt_files(*, prompts_dir: Path) -> list[dict[str, Any]]:
    ensure_default_prompt_file(prompts_dir=prompts_dir)
    items: list[dict[str, Any]] = []
    for entry in sorted(prompts_dir.glob(f"*{_PROMPT_EXTENSION}")):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        if stat.st_size <= 0:
            continue
        items.append(
            {
                "filename": entry.name,
                "label": entry.stem.replace("_", " ").strip() or entry.stem,
                "is_default": entry.name == _DEFAULT_FILENAME,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            }
        )
    items.sort(key=lambda row: (not row.get("is_default"), str(row.get("label") or "").lower()))
    return items


def read_prompt_file(filename: str, *, prompts_dir: Path) -> str:
    path = prompt_file_path(filename, prompts_dir=prompts_dir)
    if path is None or not path.is_file():
        raise FileNotFoundError(filename)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("prompt file is empty")
    return text


def write_prompt_file(
    filename: str,
    content: str,
    *,
    prompts_dir: Path,
) -> str:
    text = (content or "").strip()
    if not text:
        raise ValueError("prompt required")
    safe = safe_prompt_filename(filename)
    if not safe:
        raise ValueError("invalid filename")
    path = prompts_dir / safe
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)
    return safe


def save_prompt_as(
    name: str,
    content: str,
    *,
    prompts_dir: Path,
) -> str:
    """Save content under a human-readable name (slugged to .txt)."""
    slug = _slug_filename(name)
    if not slug:
        raise ValueError("name required")
    return write_prompt_file(f"{slug}{_PROMPT_EXTENSION}", content, prompts_dir=prompts_dir)


def delete_prompt_file(filename: str, *, prompts_dir: Path) -> None:
    safe = safe_prompt_filename(filename)
    if not safe:
        raise ValueError("invalid filename")
    if safe == _DEFAULT_FILENAME:
        raise ValueError("cannot delete default.txt — use Reset to built-in default instead")
    path = prompts_dir / safe
    if not path.is_file():
        raise FileNotFoundError(safe)
    path.unlink()