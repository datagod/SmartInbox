"""SmartInbox core — Gmail polling, Ollama summaries, Chatterbox alerts."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from smartinbox.chatterbox_models import normalize_tts_model
from smartinbox.chatterbox_tts import (
    apply_delivery_mode_settings,
    apply_tts_model_settings,
    apply_voice_override,
    chatterbox_settings_from_config,
    get_or_synthesize_speech,
    list_chatterbox_voices,
)
from smartinbox.config import data_dir
from smartinbox.db import (
    connect,
    get_email,
    init_db,
    list_emails,
    mark_email_alerted,
    update_email_summary,
    upsert_email,
)
from smartinbox.delivery_modes import DELIVERY_MODES, apply_delivery_mode, normalize_delivery_mode
from smartinbox.email_summary import probe_ollama, summarize_email
from smartinbox.gmail_imap import (
    fetch_unread_for_account,
    gmail_connection,
    load_imap_account,
    test_imap_login,
)
from smartinbox.tts_recording_cache import (
    format_email_alert_message,
    load_event_tts_prefs,
    recording_filename,
    save_event_tts_prefs,
)

UpdateListener = Callable[[str, Any], None]


class SmartInboxCore:
    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        s = settings or {}
        self.port = int(s.get("port", 8090))
        self.timezone = str(s.get("timezone", "America/New_York"))
        self.gmail_config: dict[str, Any] = dict(s.get("gmail") or {})
        self.ollama_config: dict[str, Any] = dict(s.get("ollama") or {})
        self.chatterbox_tts_config = chatterbox_settings_from_config(s.get("chatterbox_tts"))
        # SmartInbox-specific keys on chatterbox config
        self.chatterbox_tts_config["alerts_enabled"] = bool(
            (s.get("chatterbox_tts") or {}).get("alerts_enabled", True)
        )
        self.chatterbox_tts_config["alert_cooldown"] = max(
            5.0,
            float((s.get("chatterbox_tts") or {}).get("alert_cooldown", 120)),
        )
        self.chatterbox_tts_config["alert_template"] = str(
            (s.get("chatterbox_tts") or {}).get(
                "alert_template", "New email from {sender}. {subject}"
            )
        ).strip()

        self._db_path = data_dir(s) / "smartinbox.db"
        self._conn = connect(self._db_path)
        init_db(self._conn)

        prefs = load_event_tts_prefs(self.chatterbox_tts_config.get("cache_dir", "localrecordings"))
        self._poll_interval = float(
            prefs.get("poll_interval") or self.gmail_config.get("poll_interval", 60.0)
        )
        self._alert_cooldown = float(
            prefs.get("alert_cooldown") or self.chatterbox_tts_config.get("alert_cooldown", 120)
        )
        self._alerts_enabled = bool(
            prefs.get("alerts_enabled", self.chatterbox_tts_config.get("alerts_enabled", True))
        )
        self._voice_mode: str | None = prefs.get("voice_mode")
        self._voice: str | None = prefs.get("voice")
        self._delivery_mode = normalize_delivery_mode(prefs.get("delivery_mode"))
        self._tts_model = normalize_tts_model(prefs.get("tts_model"))

        self.log_entries: list[dict[str, str]] = []
        self._max_log_entries = 500
        self._listeners: list[UpdateListener] = []
        self._running = False
        self._poll_task: asyncio.Task[None] | None = None
        self._last_alert_at = 0.0
        self._last_poll_at: float | None = None

    def add_update_listener(self, listener: UpdateListener) -> None:
        self._listeners.append(listener)

    def _notify(self, kind: str, payload: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, payload)
            except Exception:
                pass

    def add_log(self, message: str, level: str = "info") -> None:
        try:
            tz = ZoneInfo(self.timezone)
            dt = datetime.now(tz)
            ts = dt.strftime("%H:%M:%S")
        except Exception:
            ts = datetime.now().strftime("%H:%M:%S")
        entry = {"ts": ts, "level": level, "message": message}
        self.log_entries.append(entry)
        if len(self.log_entries) > self._max_log_entries:
            self.log_entries = self.log_entries[-self._max_log_entries :]
        self._notify("log", entry)

    def get_poll_interval(self) -> float:
        return self._poll_interval

    def set_poll_interval(self, seconds: float) -> float:
        self._poll_interval = max(15.0, min(3600.0, float(seconds)))
        save_event_tts_prefs(
            self.chatterbox_tts_config.get("cache_dir", "localrecordings"),
            voice_mode=self._voice_mode,
            voice=self._voice,
            delivery_mode=self._delivery_mode,
            tts_model=self._tts_model,
            poll_interval=self._poll_interval,
            alert_cooldown=self._alert_cooldown,
            alerts_enabled=self._alerts_enabled,
        )
        return self._poll_interval

    def get_alert_cooldown(self) -> float:
        return self._alert_cooldown

    def set_alert_cooldown(self, seconds: float) -> float:
        self._alert_cooldown = max(5.0, min(3600.0, float(seconds)))
        save_event_tts_prefs(
            self.chatterbox_tts_config.get("cache_dir", "localrecordings"),
            voice_mode=self._voice_mode,
            voice=self._voice,
            delivery_mode=self._delivery_mode,
            tts_model=self._tts_model,
            poll_interval=self._poll_interval,
            alert_cooldown=self._alert_cooldown,
            alerts_enabled=self._alerts_enabled,
        )
        return self._alert_cooldown

    def get_alerts_enabled(self) -> bool:
        return self._alerts_enabled

    def set_alerts_enabled(self, enabled: bool) -> bool:
        self._alerts_enabled = bool(enabled)
        save_event_tts_prefs(
            self.chatterbox_tts_config.get("cache_dir", "localrecordings"),
            voice_mode=self._voice_mode,
            voice=self._voice,
            delivery_mode=self._delivery_mode,
            tts_model=self._tts_model,
            poll_interval=self._poll_interval,
            alert_cooldown=self._alert_cooldown,
            alerts_enabled=self._alerts_enabled,
        )
        return self._alerts_enabled

    def get_event_tts_voice(self) -> dict[str, str] | None:
        mode = (self._voice_mode or "").strip()
        voice = (self._voice or "").strip()
        if mode in ("clone", "predefined") and voice:
            return {"voice_mode": mode, "voice": voice}
        return None

    def set_event_tts_voice(self, *, voice_mode: str | None, voice: str | None) -> dict[str, str] | None:
        mode = (voice_mode or "").strip().lower()
        name = (voice or "").strip()
        if mode in ("clone", "predefined") and name:
            self._voice_mode = mode
            self._voice = name
        else:
            self._voice_mode = None
            self._voice = None
        save_event_tts_prefs(
            self.chatterbox_tts_config.get("cache_dir", "localrecordings"),
            voice_mode=self._voice_mode,
            voice=self._voice,
            delivery_mode=self._delivery_mode,
            tts_model=self._tts_model,
            poll_interval=self._poll_interval,
            alert_cooldown=self._alert_cooldown,
            alerts_enabled=self._alerts_enabled,
        )
        return self.get_event_tts_voice()

    def get_event_tts_delivery_mode(self) -> str:
        return self._delivery_mode

    def set_event_tts_delivery_mode(self, mode: str | None) -> str:
        self._delivery_mode = normalize_delivery_mode(mode)
        save_event_tts_prefs(
            self.chatterbox_tts_config.get("cache_dir", "localrecordings"),
            voice_mode=self._voice_mode,
            voice=self._voice,
            delivery_mode=self._delivery_mode,
            tts_model=self._tts_model,
            poll_interval=self._poll_interval,
            alert_cooldown=self._alert_cooldown,
            alerts_enabled=self._alerts_enabled,
        )
        return self._delivery_mode

    def get_event_tts_model(self) -> str:
        return self._tts_model

    def set_event_tts_model(self, model: str | None) -> str:
        self._tts_model = normalize_tts_model(model)
        save_event_tts_prefs(
            self.chatterbox_tts_config.get("cache_dir", "localrecordings"),
            voice_mode=self._voice_mode,
            voice=self._voice,
            delivery_mode=self._delivery_mode,
            tts_model=self._tts_model,
            poll_interval=self._poll_interval,
            alert_cooldown=self._alert_cooldown,
            alerts_enabled=self._alerts_enabled,
        )
        return self._tts_model

    def get_event_tts_settings(self) -> dict[str, Any]:
        chosen = self.get_event_tts_voice()
        if chosen:
            settings = apply_voice_override(
                self.chatterbox_tts_config,
                voice_mode=chosen["voice_mode"],
                voice=chosen["voice"],
            )
        else:
            settings = dict(self.chatterbox_tts_config)
        settings = apply_delivery_mode_settings(settings, self.get_event_tts_delivery_mode())
        return apply_tts_model_settings(settings, self.get_event_tts_model())

    def get_snapshot(self) -> dict[str, Any]:
        gmail = gmail_connection(self._conn)
        return {
            "gmail": gmail,
            "emails": list_emails(self._conn, limit=50),
            "logs": list(self.log_entries[-100:]),
            "poll_interval": self._poll_interval,
            "alert_cooldown": self._alert_cooldown,
            "alerts_enabled": self._alerts_enabled,
            "last_poll_at": self._last_poll_at,
            "chatterbox_tts": {
                "enabled": self.chatterbox_tts_config.get("enabled", False),
                "chosen_voice": self.get_event_tts_voice(),
                "delivery_mode": self.get_event_tts_delivery_mode(),
                "delivery_modes": list(DELIVERY_MODES),
                "tts_model": self.get_event_tts_model(),
                "alert_template": self.chatterbox_tts_config.get("alert_template"),
            },
            "ollama": {
                "base_url": self.ollama_config.get("base_url"),
                "model": self.ollama_config.get("model"),
            },
        }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.add_log("SmartInbox started", "success")
        gmail = gmail_connection(self._conn)
        if gmail.get("connected"):
            self.add_log(f"Gmail connected as {gmail.get('email')}", "info")
        else:
            self.add_log("Gmail not connected — add address and app password in Settings", "warning")
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self.poll_inbox()
            except Exception as e:
                self.add_log(f"Poll error: {e}", "error")
            await asyncio.sleep(self._poll_interval)

    async def poll_inbox(self) -> int:
        if load_imap_account(self._conn) is None:
            self._last_poll_at = time.time()
            return 0
        max_fetch = int(self.gmail_config.get("max_fetch", 20))
        raw_messages = await asyncio.to_thread(
            fetch_unread_for_account, self._conn, max_results=max_fetch
        )
        self._last_poll_at = time.time()
        new_count = 0
        alerts: list[dict[str, Any]] = []
        for msg in raw_messages:
            if upsert_email(self._conn, msg):
                new_count += 1
                self.add_log(
                    f"New email: {msg.get('sender')} — {msg.get('subject')}",
                    "success",
                )
                summary, err = await summarize_email(
                    base_url=str(self.ollama_config.get("base_url", "http://127.0.0.1:11434")),
                    model=str(self.ollama_config.get("model", "qwen2.5:3b")),
                    sender=str(msg.get("sender") or ""),
                    subject=str(msg.get("subject") or ""),
                    body=str(msg.get("body_text") or msg.get("snippet") or ""),
                    timeout=float(self.ollama_config.get("timeout", 120)),
                )
                if summary:
                    update_email_summary(
                        self._conn,
                        str(msg["id"]),
                        summary_short=summary[:500],
                        summary_detailed=summary,
                    )
                    self.add_log(f"Summarized: {msg.get('subject')}", "info")
                elif err:
                    self.add_log(f"Summary failed: {err}", "warning")

                alert = await self._build_alert(msg)
                if alert:
                    alerts.append(alert)

        if new_count:
            self._notify("emails", list_emails(self._conn, limit=50))
        if alerts:
            self._notify("email_alerts", alerts)
        if new_count == 0:
            self.add_log("Inbox check — no new mail", "info")
        return new_count

    async def _build_alert(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        if not self._alerts_enabled or not self.chatterbox_tts_config.get("enabled"):
            return None
        now = time.time()
        if now - self._last_alert_at < self._alert_cooldown:
            self.add_log("Alert skipped (cooldown)", "info")
            return None
        base_text = format_email_alert_message(
            str(msg.get("sender") or ""),
            str(msg.get("subject") or ""),
            template=str(self.chatterbox_tts_config.get("alert_template") or ""),
        )
        tts_model = self.get_event_tts_model()
        spoken = apply_delivery_mode(
            base_text,
            self.get_event_tts_delivery_mode(),
            tts_model=tts_model,
        )
        settings = self.get_event_tts_settings()
        try:
            _audio, _mt, from_cache, saved_path = await get_or_synthesize_speech(
                spoken, settings=settings
            )
            filename = recording_filename(spoken, settings=settings)
            mark_email_alerted(self._conn, str(msg["id"]))
            self._last_alert_at = now
            if from_cache:
                self.add_log(f"TTS cache hit: {filename}", "info")
            else:
                self.add_log(f"TTS generated: {saved_path}", "success")
            return {
                "email_id": msg.get("id"),
                "text": spoken,
                "base_text": base_text,
                "recording": filename,
                "sender": msg.get("sender"),
                "subject": msg.get("subject"),
            }
        except Exception as e:
            self.add_log(f"TTS alert failed: {e}", "warning")
            return None

    async def summarize_one(self, email_id: str) -> tuple[str | None, str | None]:
        row = get_email(self._conn, email_id)
        if not row:
            return None, "Email not found"
        return await summarize_email(
            base_url=str(self.ollama_config.get("base_url", "http://127.0.0.1:11434")),
            model=str(self.ollama_config.get("model", "qwen2.5:3b")),
            sender=str(row.get("sender") or ""),
            subject=str(row.get("subject") or ""),
            body=str(row.get("body_text") or row.get("snippet") or ""),
            timeout=float(self.ollama_config.get("timeout", 120)),
        )

    async def health(self) -> dict[str, Any]:
        gmail = dict(gmail_connection(self._conn))
        acct = load_imap_account(self._conn)
        if acct:
            try:
                await asyncio.to_thread(
                    test_imap_login, acct["email"], acct["app_password"]
                )
                gmail["imap_ok"] = True
            except Exception as e:
                gmail["imap_ok"] = False
                gmail["imap_error"] = str(e)
        ollama_probe = await probe_ollama(
            str(self.ollama_config.get("base_url", "http://127.0.0.1:11434")),
            str(self.ollama_config.get("model", "qwen2.5:3b")),
        )
        return {"gmail": gmail, "ollama": ollama_probe}

    @property
    def conn(self):
        return self._conn