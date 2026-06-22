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


def resolve_recordings_dir(save_dir: str) -> Path:
    root = Path(save_dir).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def event_voice_pref_path(cache_dir: str) -> Path:
    return resolve_recordings_dir(cache_dir) / ".event_voice.json"


def load_event_tts_prefs(cache_dir: str) -> dict[str, str]:
    path = event_voice_pref_path(cache_dir)
    prefs: dict[str, str] = {"delivery_mode": "normal"}
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


def recording_file_path(filename: str, *, cache_dir: str) -> Path | None:
    safe = safe_recording_filename(filename)
    if not safe:
        return None
    return resolve_recordings_dir(cache_dir) / safe