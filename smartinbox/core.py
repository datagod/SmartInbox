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
    get_setting,
    init_db,
    list_emails,
    mark_email_alerted,
    set_email_starred,
    set_setting,
    update_email_summary,
    upsert_email,
)
from smartinbox.sender_interest import record_sender_vote, sender_interest_map
from smartinbox.important_senders import (
    DEFAULT_IMPORTANT_ALERT_MODE,
    DEFAULT_OTHER_ALERT_MODE,
    add_important_sender,
    important_sender_keys,
    is_important_sender,
    list_important_senders,
    normalize_important_alert_mode,
    normalize_other_alert_mode,
    remove_important_sender,
)
from smartinbox.delivery_modes import (
    DELIVERY_MODES,
    apply_delivery_mode,
    normalize_delivery_mode,
    prepend_name_greeting,
)
from smartinbox.email_summary import (
    default_system_prompt,
    list_ollama_models,
    model_matches_listed,
    probe_ollama,
    resolve_system_prompt,
    summarize_email,
)
from smartinbox.imap_mail import (
    fetch_unread_for_account_record,
    gmail_connection,
    list_imap_account_records,
    mail_accounts_status,
    preset_use_starttls,
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
        self._important_alert_mode = normalize_important_alert_mode(
            get_setting(self._conn, "important_alert_mode", DEFAULT_IMPORTANT_ALERT_MODE)
        )
        self._other_alert_mode = normalize_other_alert_mode(
            get_setting(self._conn, "other_alert_mode", DEFAULT_OTHER_ALERT_MODE)
        )
        saved_model = get_setting(self._conn, "ollama_model")
        self._ollama_model = (
            str(saved_model).strip()
            if saved_model and str(saved_model).strip()
            else str(self.ollama_config.get("model", "qwen2.5:3b"))
        )
        saved_prompt = get_setting(self._conn, "summary_system_prompt")
        self._summary_system_prompt = (
            str(saved_prompt).strip()
            if saved_prompt and str(saved_prompt).strip()
            else None
        )

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
        self._alert_greeting_name = str(prefs.get("alert_greeting_name") or "").strip()
        self._alert_greeting_enabled = bool(prefs.get("alert_greeting_enabled"))

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
        entry = {"ts": ts, "at": time.time(), "level": level, "message": message}
        self.log_entries.append(entry)
        if len(self.log_entries) > self._max_log_entries:
            self.log_entries = self.log_entries[-self._max_log_entries :]
        self._notify("log", entry)

    def _save_tts_prefs(self) -> None:
        save_event_tts_prefs(
            self.chatterbox_tts_config.get("cache_dir", "localrecordings"),
            voice_mode=self._voice_mode,
            voice=self._voice,
            delivery_mode=self._delivery_mode,
            tts_model=self._tts_model,
            poll_interval=self._poll_interval,
            alert_cooldown=self._alert_cooldown,
            alerts_enabled=self._alerts_enabled,
            alert_greeting_name=self._alert_greeting_name,
            alert_greeting_enabled=self._alert_greeting_enabled,
        )

    def get_poll_interval(self) -> float:
        return self._poll_interval

    def set_poll_interval(self, seconds: float) -> float:
        self._poll_interval = max(15.0, min(3600.0, float(seconds)))
        self._save_tts_prefs()
        return self._poll_interval

    def get_alert_cooldown(self) -> float:
        return self._alert_cooldown

    def set_alert_cooldown(self, seconds: float) -> float:
        self._alert_cooldown = max(5.0, min(3600.0, float(seconds)))
        self._save_tts_prefs()
        return self._alert_cooldown

    def get_important_alert_mode(self) -> str:
        return self._important_alert_mode

    def set_important_alert_mode(self, mode: str | None) -> str:
        self._important_alert_mode = normalize_important_alert_mode(mode)
        set_setting(self._conn, "important_alert_mode", self._important_alert_mode)
        return self._important_alert_mode

    def get_other_alert_mode(self) -> str:
        return self._other_alert_mode

    def set_other_alert_mode(self, mode: str | None) -> str:
        self._other_alert_mode = normalize_other_alert_mode(mode)
        set_setting(self._conn, "other_alert_mode", self._other_alert_mode)
        return self._other_alert_mode

    def get_important_senders(self) -> list[dict[str, Any]]:
        return list_important_senders(self._conn)

    def mark_sender_important(self, sender: str | None) -> dict[str, Any]:
        entry = add_important_sender(self._conn, sender)
        self.add_log(f"Marked important: {entry['display']}", "success")
        self._notify("important_senders", self.get_important_senders())
        return entry

    def vote_email_sender(self, email_id: str, *, vote: str) -> dict[str, Any] | None:
        row = get_email(self._conn, email_id)
        if row is None:
            return None
        entry = record_sender_vote(self._conn, str(row.get("sender") or ""), vote=vote)
        label = "Upvoted" if vote == "up" else "Downvoted"
        self.add_log(f"{label} sender: {entry['display']} (score {entry['score']:+d})", "info")
        self._notify("sender_interest", sender_interest_map(self._conn))
        return {"email_id": email_id, "interest": entry}

    def star_email(self, email_id: str) -> dict[str, Any] | None:
        row = get_email(self._conn, email_id)
        if row is None:
            return None
        starred = not bool(row.get("starred"))
        set_email_starred(self._conn, email_id, starred=starred)
        sender_entry = None
        if starred:
            sender_entry = self.mark_sender_important(str(row.get("sender") or ""))
            self.add_log(f"Starred: {row.get('subject') or '(no subject)'}", "success")
        else:
            self.add_log(f"Unstarred: {row.get('subject') or '(no subject)'}", "info")
        updated = get_email(self._conn, email_id)
        if updated is not None:
            self._notify("emails", list_emails(self._conn, limit=50))
        return {
            "email": updated,
            "starred": starred,
            "sender": sender_entry,
        }

    def unmark_sender_important(self, sender_key: str) -> bool:
        removed = remove_important_sender(self._conn, sender_key)
        if removed:
            self.add_log(f"Removed important sender: {sender_key}", "info")
            self._notify("important_senders", self.get_important_senders())
        return removed

    def get_alerts_enabled(self) -> bool:
        return self._alerts_enabled

    def set_alerts_enabled(self, enabled: bool) -> bool:
        self._alerts_enabled = bool(enabled)
        self._save_tts_prefs()
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
        self._save_tts_prefs()
        return self.get_event_tts_voice()

    def get_event_tts_delivery_mode(self) -> str:
        return self._delivery_mode

    def set_event_tts_delivery_mode(self, mode: str | None) -> str:
        self._delivery_mode = normalize_delivery_mode(mode)
        self._save_tts_prefs()
        return self._delivery_mode

    def get_event_tts_model(self) -> str:
        return self._tts_model

    def set_event_tts_model(self, model: str | None) -> str:
        self._tts_model = normalize_tts_model(model)
        self._save_tts_prefs()
        return self._tts_model

    def get_alert_greeting_name(self) -> str:
        return self._alert_greeting_name

    def get_alert_greeting_enabled(self) -> bool:
        return self._alert_greeting_enabled

    def set_alert_greeting(self, *, name: str | None, enabled: bool | None) -> dict[str, Any]:
        if name is not None:
            self._alert_greeting_name = str(name).strip()
        if enabled is not None:
            self._alert_greeting_enabled = bool(enabled)
        self._save_tts_prefs()
        return {
            "alert_greeting_name": self._alert_greeting_name,
            "alert_greeting_enabled": self._alert_greeting_enabled,
        }

    def get_ollama_base_url(self) -> str:
        return str(self.ollama_config.get("base_url", "http://127.0.0.1:11434"))

    def get_ollama_timeout(self) -> float:
        return float(self.ollama_config.get("timeout", 120))

    def get_config_default_model(self) -> str:
        return str(self.ollama_config.get("model", "qwen2.5:3b"))

    def get_ollama_model(self) -> str:
        return self._ollama_model

    def set_ollama_model(self, model: str | None) -> str:
        name = str(model or "").strip()
        if not name:
            raise ValueError("model required")
        self._ollama_model = name
        set_setting(self._conn, "ollama_model", name)
        return self._ollama_model

    def get_default_summary_system_prompt(self) -> str:
        return default_system_prompt()

    def get_custom_summary_system_prompt(self) -> str | None:
        return self._summary_system_prompt

    def get_summary_system_prompt(self) -> str:
        return resolve_system_prompt(self._summary_system_prompt)

    def set_summary_system_prompt(self, prompt: str | None) -> str:
        text = str(prompt or "").strip()
        if not text:
            raise ValueError("prompt required")
        self._summary_system_prompt = text
        set_setting(self._conn, "summary_system_prompt", text)
        return text

    def reset_summary_system_prompt(self) -> None:
        self._summary_system_prompt = None
        set_setting(self._conn, "summary_system_prompt", None)

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
        mail = mail_accounts_status(self._conn)
        return {
            "gmail": mail["gmail"],
            "proton": mail["proton"],
            "mail_accounts": mail,
            "emails": list_emails(self._conn, limit=50),
            "logs": sorted(
                self.log_entries[-100:],
                key=lambda e: float(e.get("at") or 0),
                reverse=True,
            ),
            "poll_interval": self._poll_interval,
            "alert_cooldown": self._alert_cooldown,
            "alerts_enabled": self._alerts_enabled,
            "last_poll_at": self._last_poll_at,
            "chatterbox_tts": {
                "enabled": self.chatterbox_tts_config.get("enabled", False),
                "chosen_voice": self.get_event_tts_voice(),
                "delivery_mode": self.get_event_tts_delivery_mode(),
                "alert_greeting_name": self._alert_greeting_name,
                "alert_greeting_enabled": self._alert_greeting_enabled,
                "delivery_modes": list(DELIVERY_MODES),
                "tts_model": self.get_event_tts_model(),
                "alert_template": self.chatterbox_tts_config.get("alert_template"),
            },
            "ollama": {
                "base_url": self.get_ollama_base_url(),
                "model": self.get_ollama_model(),
                "config_default_model": self.get_config_default_model(),
            },
            "important_senders": self.get_important_senders(),
            "important_sender_keys": sorted(important_sender_keys(self._conn)),
            "sender_interest": sender_interest_map(self._conn),
            "important_alert_mode": self._important_alert_mode,
            "other_alert_mode": self._other_alert_mode,
        }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.add_log("SmartInbox started", "success")
        mail = mail_accounts_status(self._conn)
        if mail.get("accounts"):
            for acct in mail["accounts"]:
                self.add_log(
                    f"{acct.get('label', acct.get('provider'))} connected as {acct.get('email')}",
                    "info",
                )
        else:
            self.add_log(
                "No mail accounts connected — add Gmail or Proton in Settings",
                "warning",
            )
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
        accounts = list_imap_account_records(self._conn)
        if not accounts:
            self._last_poll_at = time.time()
            return 0
        max_fetch = int(self.gmail_config.get("max_fetch", 20))
        self._last_poll_at = time.time()
        new_count = 0
        alerts: list[dict[str, Any]] = []
        for acct in accounts:
            label = acct.get("provider", "mail")
            try:
                raw_messages = await asyncio.to_thread(
                    fetch_unread_for_account_record, acct, max_results=max_fetch
                )
            except Exception as e:
                self.add_log(f"{label} poll failed: {e}", "error")
                continue
            for msg in raw_messages:
                if not upsert_email(self._conn, msg):
                    continue
                new_count += 1
                provider = msg.get("provider") or label
                self.add_log(
                    f"New email ({provider}): {msg.get('sender')} — {msg.get('subject')}",
                    "success",
                )
                summary, err = await summarize_email(
                    base_url=self.get_ollama_base_url(),
                    model=self.get_ollama_model(),
                    sender=str(msg.get("sender") or ""),
                    subject=str(msg.get("subject") or ""),
                    body=str(msg.get("body_text") or msg.get("snippet") or ""),
                    system_prompt=self._summary_system_prompt,
                    timeout=self.get_ollama_timeout(),
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

    def _should_alert_for_sender(self, sender: str | None, now: float) -> tuple[bool, str]:
        if not self._alerts_enabled:
            return False, "alerts disabled"
        important = is_important_sender(self._conn, sender)
        if important:
            mode = self._important_alert_mode
            if mode == "silent":
                return False, "important sender (silent mode)"
            if mode == "cooldown" and now - self._last_alert_at < self._alert_cooldown:
                return False, "important sender (cooldown)"
            return True, "important sender"
        mode = self._other_alert_mode
        if mode == "silent":
            return False, "other sender (silent mode)"
        if now - self._last_alert_at < self._alert_cooldown:
            return False, "cooldown"
        return True, "other sender"

    async def _build_alert(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        if not self.chatterbox_tts_config.get("enabled"):
            return None
        now = time.time()
        sender = str(msg.get("sender") or "")
        should_alert, reason = self._should_alert_for_sender(sender, now)
        if not should_alert:
            self.add_log(f"Alert skipped ({reason})", "info")
            return None
        template = str(self.chatterbox_tts_config.get("alert_template") or "")
        if is_important_sender(self._conn, sender) and self._important_alert_mode == "always":
            template = "Important. " + template
        base_text = format_email_alert_message(
            sender,
            str(msg.get("subject") or ""),
            template=template,
        )
        base_text = prepend_name_greeting(
            base_text,
            self._alert_greeting_name,
            enabled=self._alert_greeting_enabled,
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
            base_url=self.get_ollama_base_url(),
            model=self.get_ollama_model(),
            sender=str(row.get("sender") or ""),
            subject=str(row.get("subject") or ""),
            body=str(row.get("body_text") or row.get("snippet") or ""),
            system_prompt=self._summary_system_prompt,
            timeout=self.get_ollama_timeout(),
        )

    async def get_llm_state(self) -> dict[str, Any]:
        base_url = self.get_ollama_base_url()
        models, err = await list_ollama_models(base_url)
        selected = self.get_ollama_model()
        names = [m["name"] for m in models]
        return {
            "base_url": base_url,
            "reachable": err is None,
            "error": err,
            "models": models,
            "selected_model": selected,
            "config_default_model": self.get_config_default_model(),
            "model_listed": model_matches_listed(selected, names) if err is None else False,
            "system_prompt": self.get_summary_system_prompt(),
            "custom_system_prompt": self.get_custom_summary_system_prompt(),
            "default_system_prompt": self.get_default_summary_system_prompt(),
            "is_custom_prompt": self.get_custom_summary_system_prompt() is not None,
        }

    async def health(self) -> dict[str, Any]:
        mail = mail_accounts_status(self._conn)
        account_health: list[dict[str, Any]] = []
        for acct in list_imap_account_records(self._conn):
            entry = {
                "provider": acct["provider"],
                "email": acct["email"],
                "imap_ok": False,
                "imap_error": None,
            }
            try:
                use_ssl = bool(acct["use_ssl"])
                await asyncio.to_thread(
                    test_imap_login,
                    acct["email"],
                    acct["password"],
                    imap_host=acct["imap_host"],
                    imap_port=int(acct["imap_port"]),
                    use_ssl=use_ssl,
                    use_starttls=preset_use_starttls(acct["provider"], use_ssl=use_ssl),
                    provider=acct["provider"],
                )
                entry["imap_ok"] = True
            except Exception as e:
                entry["imap_error"] = str(e)
            account_health.append(entry)
        gmail = dict(mail["gmail"])
        gmail_health = next((h for h in account_health if h["provider"] == "gmail"), None)
        if gmail_health:
            gmail["imap_ok"] = gmail_health["imap_ok"]
            gmail["imap_error"] = gmail_health.get("imap_error")
        proton = dict(mail["proton"])
        proton_health = next((h for h in account_health if h["provider"] == "proton"), None)
        if proton_health:
            proton["imap_ok"] = proton_health["imap_ok"]
            proton["imap_error"] = proton_health.get("imap_error")
        ollama_probe = await probe_ollama(
            self.get_ollama_base_url(),
            self.get_ollama_model(),
        )
        return {
            "gmail": gmail,
            "proton": proton,
            "mail_accounts": mail,
            "account_health": account_health,
            "ollama": ollama_probe,
        }

    @property
    def conn(self):
        return self._conn