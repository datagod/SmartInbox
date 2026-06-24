"""Cache Chatterbox TTS output under localrecordings/ keyed by message text."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from smartinbox.chatterbox_models import normalize_tts_model
from smartinbox.delivery_modes import normalize_delivery_mode

_MAX_BASENAME_LEN = 120
_RECORDING_EXTENSIONS = {".wav", ".mp3", ".opus"}
_MEDIA_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "opus": "audio/opus"}
_PHRASES_SUBDIR = "phrases"
_KNOWN_DELIVERY_MODES = frozenset({"conspiracy", "panicky", "neurotic", "playful", "normal"})
_KNOWN_TTS_MODELS = frozenset({"chatterbox_turbo", "chatterbox", "chatterbox_multilingual"})


def resolve_recordings_dir(save_dir: str) -> Path:
    root = Path(save_dir).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def event_voice_pref_path(cache_dir: str) -> Path:
    return resolve_recordings_dir(cache_dir) / ".event_voice.json"


def load_event_tts_prefs(cache_dir: str) -> dict[str, Any]:
    path = event_voice_pref_path(cache_dir)
    prefs: dict[str, Any] = {"delivery_mode": "normal"}
    if not path.is_file():
        return prefs
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return prefs
    if not isinstance(data, dict):
        return prefs
    mode = str(data.get("voice_mode") or "").strip().lower()
    voice = str(data.get("voice") or "").strip()
    if mode in ("clone", "predefined") and voice:
        prefs["voice_mode"] = mode
        prefs["voice"] = voice
    prefs["delivery_mode"] = normalize_delivery_mode(data.get("delivery_mode"))
    model = str(data.get("tts_model") or "").strip()
    if model:
        prefs["tts_model"] = normalize_tts_model(model)
    greeting_name = str(data.get("alert_greeting_name") or "").strip()
    if greeting_name:
        prefs["alert_greeting_name"] = greeting_name
    if "alert_greeting_enabled" in data:
        prefs["alert_greeting_enabled"] = bool(data.get("alert_greeting_enabled"))
    if "poll_interval" in data:
        prefs["poll_interval"] = float(data["poll_interval"])
    if "alert_cooldown" in data:
        prefs["alert_cooldown"] = float(data["alert_cooldown"])
    if "alerts_enabled" in data:
        prefs["alerts_enabled"] = bool(data["alerts_enabled"])
    if "voice_summary_enabled" in data:
        prefs["voice_summary_enabled"] = bool(data["voice_summary_enabled"])
    return prefs


def save_event_tts_prefs(
    cache_dir: str,
    *,
    voice_mode: str | None = None,
    voice: str | None = None,
    delivery_mode: str | None = None,
    tts_model: str | None = None,
    poll_interval: float | None = None,
    alert_cooldown: float | None = None,
    alerts_enabled: bool | None = None,
    alert_greeting_name: str | None = None,
    alert_greeting_enabled: bool | None = None,
    voice_summary_enabled: bool | None = None,
) -> None:
    path = event_voice_pref_path(cache_dir)
    existing = load_event_tts_prefs(cache_dir)
    try:
        extra = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        extra = {}
    if not isinstance(extra, dict):
        extra = {}

    mode = str(voice_mode if voice_mode is not None else existing.get("voice_mode") or "").strip().lower()
    name = str(voice if voice is not None else existing.get("voice") or "").strip()
    delivery = normalize_delivery_mode(
        delivery_mode if delivery_mode is not None else existing.get("delivery_mode")
    )
    model = normalize_tts_model(
        tts_model if tts_model is not None else existing.get("tts_model")
    )
    payload: dict[str, Any] = {
        "delivery_mode": delivery,
        "tts_model": model,
    }
    if mode in ("clone", "predefined") and name:
        payload["voice_mode"] = mode
        payload["voice"] = name
    if poll_interval is not None:
        payload["poll_interval"] = float(poll_interval)
    elif "poll_interval" in extra:
        payload["poll_interval"] = extra["poll_interval"]
    if alert_cooldown is not None:
        payload["alert_cooldown"] = float(alert_cooldown)
    elif "alert_cooldown" in extra:
        payload["alert_cooldown"] = extra["alert_cooldown"]
    if alerts_enabled is not None:
        payload["alerts_enabled"] = bool(alerts_enabled)
    elif "alerts_enabled" in extra:
        payload["alerts_enabled"] = extra["alerts_enabled"]
    if alert_greeting_name is not None:
        name = str(alert_greeting_name).strip()
        if name:
            payload["alert_greeting_name"] = name
        elif "alert_greeting_name" in extra:
            del payload["alert_greeting_name"]
    elif "alert_greeting_name" in extra:
        payload["alert_greeting_name"] = extra["alert_greeting_name"]
    if alert_greeting_enabled is not None:
        payload["alert_greeting_enabled"] = bool(alert_greeting_enabled)
    elif "alert_greeting_enabled" in extra:
        payload["alert_greeting_enabled"] = extra["alert_greeting_enabled"]
    if voice_summary_enabled is not None:
        payload["voice_summary_enabled"] = bool(voice_summary_enabled)
    elif "voice_summary_enabled" in extra:
        payload["voice_summary_enabled"] = extra["voice_summary_enabled"]

    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def voice_key_from_settings(settings: dict[str, Any]) -> str:
    if settings.get("voice_mode") == "clone":
        return str(settings.get("reference_audio_filename") or "clone").strip()
    return str(settings.get("predefined_voice_id") or "predefined").strip()


def _slug_part(value: str, *, max_len: int = _MAX_BASENAME_LEN) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text).strip("_")
    if not text:
        return ""
    if len(text) > max_len:
        text = text[:max_len].rstrip("_")
    return text


def recording_filename(text: str, *, settings: dict[str, Any]) -> str:
    output_format = str(settings.get("output_format", "wav")).lower()
    if output_format not in ("wav", "mp3", "opus"):
        output_format = "wav"
    message_slug = _slug_part(text) or "message"
    voice_slug = _slug_part(Path(voice_key_from_settings(settings)).stem, max_len=48)
    mode_slug = _slug_part(normalize_delivery_mode(settings.get("delivery_mode")), max_len=16)
    model_slug = _slug_part(normalize_tts_model(settings.get("tts_model")), max_len=24)
    parts = [message_slug]
    if voice_slug:
        parts.append(voice_slug)
    if mode_slug and mode_slug != "normal":
        parts.append(mode_slug)
    if model_slug and model_slug != "chatterbox_turbo":
        parts.append(model_slug)
    return f"{'__'.join(parts)}.{output_format}"


def recording_path(text: str, *, settings: dict[str, Any]) -> Path:
    root = resolve_recordings_dir(str(settings.get("cache_dir", "localrecordings")))
    return root / recording_filename(text, settings=settings)


def load_cached_recording(text: str, *, settings: dict[str, Any]) -> tuple[bytes, str] | None:
    path = recording_path(text, settings=settings)
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    ext = path.suffix.lower().lstrip(".")
    media_types = {"wav": "audio/wav", "mp3": "audio/mpeg", "opus": "audio/opus"}
    media_type = media_types.get(ext, "application/octet-stream")
    return path.read_bytes(), media_type


def format_email_alert_message(
    sender: str | None,
    subject: str | None,
    *,
    template: str = "New email from {sender}. {subject}",
) -> str:
    snd = (sender or "unknown sender").strip()
    sub = (subject or "no subject").strip()
    try:
        return template.format(sender=snd, subject=sub).strip()
    except (KeyError, ValueError):
        return f"New email from {snd}. {sub}"


def prepend_sender_announcement(
    text: str,
    sender: str | None,
    *,
    important: bool = False,
) -> str:
    """Prepend who the email is from before a voice-summary announcement."""
    base = (text or "").strip()
    snd = (sender or "unknown sender").strip()
    prefix = f"New email from {snd}."
    if important:
        prefix = f"Important. {prefix}"
    if not base:
        return prefix
    return f"{prefix} {base}"


def save_recording(text: str, audio: bytes, *, settings: dict[str, Any]) -> str:
    path = recording_path(text, settings=settings)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(audio)
    tmp.replace(path)
    return str(path)


def safe_recording_filename(name: str) -> str | None:
    base = Path(name).name
    if not base or base != name.strip() or base.startswith("."):
        return None
    suffix = Path(base).suffix.lower()
    if suffix not in _RECORDING_EXTENSIONS:
        return None
    stem = Path(base).stem
    if not stem or not re.match(r"^[\w][\w._-]*$", stem):
        return None
    return base


def media_type_for_filename(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    return _MEDIA_TYPES.get(ext, "application/octet-stream")


def phrases_dir(cache_dir: str) -> Path:
    root = resolve_recordings_dir(cache_dir) / _PHRASES_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _deslug(value: str) -> str:
    return (value or "").replace("_", " ").strip()


def parse_recording_stem(stem: str, *, kind: str = "alert") -> dict[str, str]:
    """Split a cached filename stem into display-friendly metadata."""
    parts = [p for p in (stem or "").strip().split("__") if p]
    voice = ""
    mode = ""
    model = ""
    message_parts: list[str]

    if kind == "phrase" and parts and parts[0] in _KNOWN_DELIVERY_MODES:
        mode = parts[0]
        remaining = list(parts[1:])
        if remaining and remaining[-1] in _KNOWN_TTS_MODELS:
            model = remaining.pop()
        if len(remaining) >= 2:
            voice = remaining[-1]
            message_parts = remaining[:-1]
        else:
            message_parts = remaining
    else:
        remaining = list(parts)
        if remaining and remaining[-1] in _KNOWN_TTS_MODELS:
            model = remaining.pop()
        if remaining and remaining[-1] in _KNOWN_DELIVERY_MODES:
            mode = remaining.pop()
        if len(remaining) >= 2:
            voice = remaining[-1]
            message_parts = remaining[:-1]
        else:
            message_parts = remaining

    message = _deslug("__".join(message_parts))
    return {
        "message": message,
        "voice": _deslug(voice),
        "delivery_mode": mode,
        "tts_model": model.replace("_", "-") if model else "",
    }


def phrase_recording_filename(phrase: str, mode: str, *, settings: dict[str, Any]) -> str:
    output_format = str(settings.get("output_format", "wav")).lower()
    if output_format not in ("wav", "mp3", "opus"):
        output_format = "wav"
    mode_slug = _slug_part(normalize_delivery_mode(mode), max_len=16) or "phrase"
    phrase_slug = _slug_part(phrase) or "phrase"
    voice_slug = _slug_part(Path(voice_key_from_settings(settings)).stem, max_len=48)
    model_slug = _slug_part(normalize_tts_model(settings.get("tts_model")), max_len=24)
    parts = [mode_slug, phrase_slug]
    if voice_slug:
        parts.append(voice_slug)
    if model_slug and model_slug != "chatterbox_turbo":
        parts.append(model_slug)
    return f"{'__'.join(parts)}.{output_format}"


def phrase_recording_path(phrase: str, mode: str, *, settings: dict[str, Any]) -> Path:
    cache_dir = str(settings.get("cache_dir", "localrecordings"))
    return phrases_dir(cache_dir) / phrase_recording_filename(phrase, mode, settings=settings)


def safe_recording_relpath(name: str) -> str | None:
    """Reject path traversal; return a relative path under the cache root."""
    raw = str(name or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        return None
    base = Path(raw).name
    if not base or base.startswith("."):
        return None
    suffix = Path(base).suffix.lower()
    if suffix not in _RECORDING_EXTENSIONS:
        return None
    stem = Path(base).stem
    if not stem or not re.match(r"^[\w][\w._-]*$", stem):
        return None
    if "/" in raw:
        prefix = str(Path(raw).parent).replace("\\", "/")
        if prefix == ".":
            return base
        if not re.match(r"^[\w][\w._/-]*$", prefix):
            return None
        return f"{prefix}/{base}"
    return base


def recording_file_path(filename: str, *, cache_dir: str) -> Path | None:
    safe = safe_recording_relpath(filename) or safe_recording_filename(filename)
    if not safe:
        return None
    return resolve_recordings_dir(cache_dir) / safe


def delete_recording(filename: str, *, cache_dir: str) -> tuple[bool, str | None]:
    path = recording_file_path(filename, cache_dir=cache_dir)
    if path is None:
        return False, "invalid filename"
    if not path.is_file():
        return False, "not found"
    try:
        path.unlink()
        return True, None
    except OSError as e:
        return False, str(e)


def delete_recordings(
    filenames: list[str],
    *,
    cache_dir: str,
) -> dict[str, Any]:
    deleted: list[str] = []
    errors: dict[str, str] = {}
    seen: set[str] = set()
    for raw in filenames or []:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        ok, err = delete_recording(name, cache_dir=cache_dir)
        if ok:
            deleted.append(name)
        elif err:
            errors[name] = err
    return {"deleted": deleted, "errors": errors}


def _recording_entry(path: Path, *, cache_dir: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    if path.name.startswith(".") or path.suffix.lower() == ".tmp":
        return None
    if path.suffix.lower() not in _RECORDING_EXTENSIONS:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size <= 0:
        return None
    root = resolve_recordings_dir(cache_dir)
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    rel_str = str(rel).replace("\\", "/")
    kind = "phrase" if rel.parts and rel.parts[0] == _PHRASES_SUBDIR else "alert"
    meta = parse_recording_stem(path.stem, kind=kind)
    return {
        "filename": rel_str,
        "message": meta["message"],
        "voice": meta["voice"],
        "delivery_mode": meta["delivery_mode"],
        "tts_model": meta["tts_model"],
        "format": path.suffix.lower().lstrip("."),
        "size_bytes": stat.st_size,
        "modified_at": stat.st_mtime,
        "kind": kind,
    }


def list_recordings(cache_dir: str) -> list[dict[str, Any]]:
    """Metadata for non-empty cached recordings, newest first."""
    root = resolve_recordings_dir(cache_dir)
    items: list[dict[str, Any]] = []
    for entry in root.rglob("*"):
        row = _recording_entry(entry, cache_dir=cache_dir)
        if row:
            items.append(row)
    items.sort(key=lambda r: float(r.get("modified_at") or 0), reverse=True)
    return items