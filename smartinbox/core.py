"""SmartInbox core — Gmail polling, Ollama summaries, Chatterbox alerts."""

from __future__ import annotations

import asyncio
import copy
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
from smartinbox.demo_data import build_demo_emails
from smartinbox.prompt_storage import (
    delete_prompt_file,
    ensure_default_prompt_file,
    ensure_default_voice_style_prompt_file,
    list_prompt_files,
    list_voice_style_prompt_files,
    read_prompt_file,
    resolve_prompts_dir,
    save_prompt_as,
    write_prompt_file,
)
from smartinbox.db import (
    connect,
    get_email,
    get_setting,
    init_db,
    clear_all_emails,
    list_emails,
    mark_email_alerted,
    set_email_starred,
    set_setting,
    update_email_summary,
    upsert_email,
)
from smartinbox.sender_interest import (
    is_sender_muted,
    list_ranked_sender_interest,
    record_sender_vote,
    sender_interest_map,
)
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
from smartinbox.voice_summary import (
    brief_summary_for_tts,
    default_voice_style_prompt,
    format_voice_llm_prompt,
    resolve_voice_style_prompt,
    style_summary_for_voice,
)
from smartinbox.imap_mail import (
    fetch_unread_for_account_record,
    gmail_connection,
    list_imap_account_records,
    mark_all_unseen_seen_for_account,
    mark_imap_uids_seen_for_account,
    mail_accounts_status,
    preset_use_starttls,
    test_imap_login,
)
from smartinbox.tts_recording_cache import (
    format_email_alert_message,
    load_event_tts_prefs,
    prepend_sender_announcement,
    recording_filename,
    save_event_tts_prefs,
)

UpdateListener = Callable[[str, Any], None]

DEFAULT_TEST_EMAIL: dict[str, str] = {
    "sender": "Example Store <orders@example.com>",
    "subject": "Your package has shipped",
    "summary_detailed": (
        "## Summary\n"
        "Your order has shipped via UPS and should arrive Thursday.\n\n"
        "## Key points\n"
        "- Tracking link included\n"
        "- No signature required\n\n"
        "## Action needed\n"
        "No action needed."
    ),
    "summary_short": (
        "Your order has shipped via UPS and should arrive Thursday. "
        "Tracking link included; no signature required."
    ),
}


class SmartInboxCore:
    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        s = settings or {}
        self._settings = s
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
        saved_prompt_file = get_setting(self._conn, "summary_prompt_file")
        self._summary_prompt_file = (
            str(saved_prompt_file).strip()
            if saved_prompt_file and str(saved_prompt_file).strip()
            else None
        )
        self._prompts_dir = resolve_prompts_dir(s)
        ensure_default_prompt_file(prompts_dir=self._prompts_dir)
        ensure_default_voice_style_prompt_file(prompts_dir=self._prompts_dir)

        saved_voice_style_prompt = get_setting(self._conn, "voice_style_system_prompt")
        self._voice_style_system_prompt = (
            str(saved_voice_style_prompt).strip()
            if saved_voice_style_prompt and str(saved_voice_style_prompt).strip()
            else None
        )
        saved_voice_style_file = get_setting(self._conn, "voice_style_prompt_file")
        self._voice_style_prompt_file = (
            str(saved_voice_style_file).strip()
            if saved_voice_style_file and str(saved_voice_style_file).strip()
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
        self._voice_summary_enabled = bool(prefs.get("voice_summary_enabled"))

        saved_watermark = get_setting(self._conn, "inbox_watermark")
        self._inbox_watermark = (
            float(saved_watermark)
            if saved_watermark is not None and float(saved_watermark) > 0
            else None
        )

        saved_demo = get_setting(self._conn, "demo_mode")
        self._demo_mode = str(saved_demo).lower() in ("1", "true", "yes", "on")
        self._demo_emails: list[dict[str, Any]] = (
            copy.deepcopy(build_demo_emails()) if self._demo_mode else []
        )

        self.log_entries: list[dict[str, str]] = []
        self._max_log_entries = 500
        self._listeners: list[UpdateListener] = []
        self._running = False
        self._poll_task: asyncio.Task[None] | None = None
        self._poll_lock = asyncio.Lock()
        self._email_tasks: set[asyncio.Task[None]] = set()
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

    def _track_email_task(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._email_tasks.add(task)
        task.add_done_callback(self._email_tasks.discard)

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

    def clear_logs(self) -> None:
        self.log_entries.clear()
        self._notify("logs", [])

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
            voice_summary_enabled=self._voice_summary_enabled,
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
        self.add_log(f"Marked important: {entry['display']}", "info")
        self._notify("important_senders", self.get_important_senders())
        return entry

    def get_ranked_sender_interest(self) -> dict[str, list[dict[str, Any]]]:
        return list_ranked_sender_interest(self._conn)

    def vote_email_sender(self, email_id: str, *, vote: str) -> dict[str, Any] | None:
        row = self.get_email_for_display(email_id)
        if row is None:
            return None
        entry = record_sender_vote(self._conn, str(row.get("sender") or ""), vote=vote)
        label = "Upvoted" if vote == "up" else "Downvoted"
        self.add_log(f"{label} sender: {entry['display']} (score {entry['score']:+d})", "info")
        self._notify("sender_interest", sender_interest_map(self._conn))
        return {"email_id": email_id, "interest": entry}

    def get_demo_mode(self) -> bool:
        return self._demo_mode

    def set_demo_mode(self, enabled: bool) -> bool:
        self._demo_mode = bool(enabled)
        set_setting(self._conn, "demo_mode", "1" if self._demo_mode else "0")
        if self._demo_mode:
            self._demo_emails = copy.deepcopy(build_demo_emails())
            self.add_log(
                "Demo mode enabled — inbox shows sample emails for screenshots",
                "info",
            )
        else:
            self._demo_emails = []
            self.add_log("Demo mode disabled — showing live inbox", "info")
        self._notify_emails()
        return self._demo_mode

    def list_emails_for_display(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if self._demo_mode:
            return [dict(row) for row in self._demo_emails[:limit]]
        return list_emails(self._conn, limit=limit)

    def get_email_for_display(self, email_id: str) -> dict[str, Any] | None:
        if self._demo_mode:
            for row in self._demo_emails:
                if str(row.get("id")) == email_id:
                    return dict(row)
            return None
        return get_email(self._conn, email_id)

    def _update_demo_email(self, email_id: str, **fields: Any) -> dict[str, Any] | None:
        for index, row in enumerate(self._demo_emails):
            if str(row.get("id")) == email_id:
                updated = {**row, **fields}
                self._demo_emails[index] = updated
                return dict(updated)
        return None

    def _notify_emails(self) -> None:
        self._notify("emails", self.list_emails_for_display(limit=50))

    def save_email_summary(self, email_id: str, summary: str) -> bool:
        short = summary[:500]
        if self._demo_mode:
            if self._update_demo_email(
                email_id,
                summary_short=short,
                summary_detailed=summary,
            ):
                self._notify_emails()
                return True
            return False
        update_email_summary(
            self._conn,
            email_id,
            summary_short=short,
            summary_detailed=summary,
        )
        self._notify_emails()
        return True

    def get_inbox_watermark(self) -> float | None:
        return self._inbox_watermark

    def set_inbox_watermark(self, when: float | None) -> float | None:
        if when is not None and float(when) > 0:
            ts = float(when)
            self._inbox_watermark = ts
            set_setting(self._conn, "inbox_watermark", ts)
        else:
            self._inbox_watermark = None
            set_setting(self._conn, "inbox_watermark", None)
        return self._inbox_watermark

    def _email_passes_watermark(self, msg: dict[str, Any]) -> bool:
        watermark = self._inbox_watermark
        if watermark is None:
            return True
        received = float(msg.get("received_at") or 0)
        if received <= 0:
            return False
        return received > watermark

    def _mark_all_unseen_seen_on_accounts(self) -> int:
        total = 0
        for acct in list_imap_account_records(self._conn):
            label = str(acct.get("provider") or "mail")
            try:
                count = mark_all_unseen_seen_for_account(acct)
                total += count
                if count:
                    self.add_log(
                        f"Marked {count} unread {label} message{'s' if count != 1 else ''} as read on server",
                        "info",
                    )
            except Exception as e:
                self.add_log(f"Mark seen failed ({label}): {e}", "warning")
        return total

    def empty_inbox(self) -> int:
        if self._demo_mode:
            self._demo_emails = copy.deepcopy(build_demo_emails())
            self.add_log("Demo inbox reset to sample emails", "info")
            self._notify_emails()
            return len(self._demo_emails)
        removed = clear_all_emails(self._conn)
        watermark = self.set_inbox_watermark(time.time())
        marked_seen = self._mark_all_unseen_seen_on_accounts()
        stamp = self._format_log_timestamp(watermark) if watermark else "—"
        self.add_log(
            f"Inbox emptied ({removed} email{'s' if removed != 1 else ''} removed). "
            f"Watermark set to {stamp} — older mail will be skipped.",
            "info",
        )
        if marked_seen:
            self.add_log(
                f"Server unread mail cleared ({marked_seen} marked read) — will not re-import on next check",
                "info",
            )
        self._notify_emails()
        return removed

    def _format_log_timestamp(self, when: float | None) -> str:
        if when is None:
            return "—"
        try:
            tz = ZoneInfo(self.timezone)
            return datetime.fromtimestamp(float(when), tz=tz).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.fromtimestamp(float(when)).strftime("%Y-%m-%d %H:%M:%S")

    def _log_inbox_check(self, *, new_count: int, skipped_old: int) -> None:
        if self._inbox_watermark:
            stamp = self._format_log_timestamp(self._inbox_watermark)
            cutoff = f"cutoff date: only mail received after {stamp}"
        else:
            cutoff = "no cutoff date (importing all unread mail)"
        if new_count == 0:
            status = "no new mail"
        else:
            status = f"{new_count} new email{'s' if new_count != 1 else ''}"
        msg = f"Inbox check — {status} — {cutoff}"
        if skipped_old:
            msg += (
                f"; skipped {skipped_old} older email"
                f"{'s' if skipped_old != 1 else ''}"
            )
        self.add_log(msg, "success")

    def star_email(self, email_id: str) -> dict[str, Any] | None:
        row = self.get_email_for_display(email_id)
        if row is None:
            return None
        starred = not bool(row.get("starred"))
        sender_entry = None
        if self._demo_mode:
            updated = self._update_demo_email(email_id, starred=1 if starred else 0)
            if starred:
                self.add_log(f"Starred: {row.get('subject') or '(no subject)'}", "info")
            else:
                self.add_log(f"Unstarred: {row.get('subject') or '(no subject)'}", "info")
            if updated is not None:
                self._notify_emails()
            return {
                "email": updated,
                "starred": starred,
                "sender": sender_entry,
            }
        set_email_starred(self._conn, email_id, starred=starred)
        if starred:
            sender_entry = self.mark_sender_important(str(row.get("sender") or ""))
            self.add_log(f"Starred: {row.get('subject') or '(no subject)'}", "info")
        else:
            self.add_log(f"Unstarred: {row.get('subject') or '(no subject)'}", "info")
        updated = get_email(self._conn, email_id)
        if updated is not None:
            self._notify_emails()
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

    def get_voice_summary_enabled(self) -> bool:
        return self._voice_summary_enabled

    def set_voice_summary_enabled(self, enabled: bool) -> bool:
        self._voice_summary_enabled = bool(enabled)
        self._save_tts_prefs()
        return self._voice_summary_enabled

    def get_default_voice_style_prompt(self) -> str:
        return default_voice_style_prompt()

    def get_voice_style_system_prompt(self) -> str:
        return resolve_voice_style_prompt(self._voice_style_system_prompt)

    def get_voice_style_prompt_file(self) -> str | None:
        return self._voice_style_prompt_file

    def list_voice_style_prompts(self) -> list[dict[str, Any]]:
        return list_voice_style_prompt_files(prompts_dir=self._prompts_dir)

    def set_voice_style_system_prompt(
        self,
        prompt: str | None,
        *,
        source_file: str | None = None,
        clear_source_file: bool = False,
    ) -> str:
        text = str(prompt or "").strip()
        if not text:
            raise ValueError("prompt required")
        self._voice_style_system_prompt = text
        set_setting(self._conn, "voice_style_system_prompt", text)
        if clear_source_file:
            self._voice_style_prompt_file = None
            set_setting(self._conn, "voice_style_prompt_file", None)
        elif source_file is not None:
            self._voice_style_prompt_file = str(source_file).strip() or None
            set_setting(self._conn, "voice_style_prompt_file", self._voice_style_prompt_file)
        return text

    def load_voice_style_prompt_file(self, filename: str) -> str:
        text = read_prompt_file(filename, prompts_dir=self._prompts_dir)
        return self.set_voice_style_system_prompt(text, source_file=filename)

    def save_voice_style_prompt_to_file(self, name: str, prompt: str | None = None) -> str:
        text = str(
            prompt if prompt is not None else self._voice_style_system_prompt or ""
        ).strip()
        if not text:
            raise ValueError("prompt required")
        slug_name = name.strip() or "voice_style"
        if not slug_name.lower().startswith("voice_style"):
            slug_name = f"voice_style_{slug_name}"
        return save_prompt_as(slug_name, text, prompts_dir=self._prompts_dir)

    def reset_voice_style_system_prompt(self) -> None:
        self._voice_style_system_prompt = None
        self._voice_style_prompt_file = None
        set_setting(self._conn, "voice_style_system_prompt", None)
        set_setting(self._conn, "voice_style_prompt_file", None)

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

    def get_prompts_dir(self) -> Path:
        return self._prompts_dir

    def get_summary_prompt_file(self) -> str | None:
        return self._summary_prompt_file

    def get_prompt_source(self) -> str:
        if self._summary_system_prompt is None:
            return "default"
        if self._summary_prompt_file:
            return "file"
        return "custom"

    def list_saved_prompts(self) -> list[dict[str, Any]]:
        return list_prompt_files(prompts_dir=self._prompts_dir)

    def load_summary_prompt_file(self, filename: str) -> str:
        text = read_prompt_file(filename, prompts_dir=self._prompts_dir)
        self._summary_system_prompt = text
        self._summary_prompt_file = str(filename).strip()
        set_setting(self._conn, "summary_system_prompt", text)
        set_setting(self._conn, "summary_prompt_file", self._summary_prompt_file)
        return text

    def save_summary_prompt_to_file(self, name: str, prompt: str | None = None) -> str:
        text = str(prompt if prompt is not None else self._summary_system_prompt or "").strip()
        if not text:
            raise ValueError("prompt required")
        filename = save_prompt_as(name, text, prompts_dir=self._prompts_dir)
        return filename

    def overwrite_summary_prompt_file(self, filename: str, prompt: str | None = None) -> str:
        text = str(prompt if prompt is not None else self._summary_system_prompt or "").strip()
        if not text:
            raise ValueError("prompt required")
        saved = write_prompt_file(filename, text, prompts_dir=self._prompts_dir)
        return saved

    def delete_summary_prompt_file(self, filename: str) -> None:
        delete_prompt_file(filename, prompts_dir=self._prompts_dir)
        if self._summary_prompt_file == str(filename).strip():
            self._summary_prompt_file = None
            set_setting(self._conn, "summary_prompt_file", None)

    def set_summary_system_prompt(
        self,
        prompt: str | None,
        *,
        source_file: str | None = None,
        clear_source_file: bool = False,
    ) -> str:
        text = str(prompt or "").strip()
        if not text:
            raise ValueError("prompt required")
        self._summary_system_prompt = text
        set_setting(self._conn, "summary_system_prompt", text)
        if clear_source_file:
            self._summary_prompt_file = None
            set_setting(self._conn, "summary_prompt_file", None)
        elif source_file is not None:
            self._summary_prompt_file = str(source_file).strip() or None
            set_setting(self._conn, "summary_prompt_file", self._summary_prompt_file)
        return text

    def reset_summary_system_prompt(self) -> None:
        self._summary_system_prompt = None
        self._summary_prompt_file = None
        set_setting(self._conn, "summary_system_prompt", None)
        set_setting(self._conn, "summary_prompt_file", None)

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
            "emails": self.list_emails_for_display(limit=50),
            "demo_mode": self._demo_mode,
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
                "voice_summary_enabled": self._voice_summary_enabled,
                "voice_style_prompt": self.get_voice_style_system_prompt(),
                "voice_style_prompt_file": self.get_voice_style_prompt_file(),
                "voice_style_default_prompt": self.get_default_voice_style_prompt(),
                "saved_voice_style_prompts": self.list_voice_style_prompts(),
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
        self.add_log("SmartInbox started", "info")
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
        if self._poll_lock.locked():
            return 0
        async with self._poll_lock:
            return await self._poll_inbox_locked()

    async def _poll_inbox_locked(self) -> int:
        accounts = list_imap_account_records(self._conn)
        if not accounts:
            self._last_poll_at = time.time()
            return 0
        max_fetch = int(self.gmail_config.get("max_fetch", 20))
        self._last_poll_at = time.time()
        new_count = 0
        skipped_old = 0
        for acct in accounts:
            label = acct.get("provider", "mail")
            try:
                raw_messages = await asyncio.to_thread(
                    fetch_unread_for_account_record, acct, max_results=max_fetch
                )
            except Exception as e:
                self.add_log(f"{label} poll failed: {e}", "error")
                continue
            skipped_uids: list[str] = []
            for msg in raw_messages:
                if not self._email_passes_watermark(msg):
                    skipped_old += 1
                    uid = str(msg.get("imap_uid") or "").strip()
                    if uid:
                        skipped_uids.append(uid)
                    continue
                if not upsert_email(self._conn, msg):
                    continue
                new_count += 1
                provider = msg.get("provider") or label
                self.add_log(
                    f"New email ({provider}): {msg.get('sender')} — {msg.get('subject')}",
                    "info",
                )
                if not self._demo_mode:
                    self._notify_emails()
                    self._track_email_task(self._process_new_email(dict(msg)))

            if skipped_uids:
                try:
                    marked = await asyncio.wait_for(
                        asyncio.to_thread(
                            mark_imap_uids_seen_for_account, acct, skipped_uids
                        ),
                        timeout=60.0,
                    )
                    if marked:
                        self.add_log(
                            f"Marked {marked} old unread message{'s' if marked != 1 else ''} "
                            f"as read on {label}",
                            "info",
                        )
                except asyncio.TimeoutError:
                    self.add_log(f"Mark seen timed out ({label})", "warning")
                except Exception as e:
                    self.add_log(f"Mark seen failed ({label}): {e}", "warning")

        self._log_inbox_check(new_count=new_count, skipped_old=skipped_old)
        return new_count

    async def _process_new_email(self, msg: dict[str, Any]) -> None:
        subject = str(msg.get("subject") or "(no subject)")
        try:
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
                msg["summary_short"] = summary[:500]
                msg["summary_detailed"] = summary
                update_email_summary(
                    self._conn,
                    str(msg["id"]),
                    summary_short=summary[:500],
                    summary_detailed=summary,
                )
                self.add_log(f"Summarized: {subject}", "info")
                self._notify_emails()
            elif err:
                self.add_log(f"Summary failed: {err}", "warning")

            if self._email_passes_watermark(msg):
                alert = await self._build_alert(msg)
                if alert:
                    self._notify("email_alerts", [alert])
        except Exception as e:
            self.add_log(f"Email processing failed ({subject}): {e}", "error")

    def _should_alert_for_sender(self, sender: str | None, now: float) -> tuple[bool, str]:
        if not self._alerts_enabled:
            return False, "alerts disabled"
        if is_sender_muted(self._conn, sender):
            return False, "sender score too low (muted)"
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

    async def _compose_alert_speech(
        self,
        msg: dict[str, Any],
        *,
        voice_summary_enabled: bool | None = None,
        delivery_mode: str | None = None,
        alert_greeting_name: str | None = None,
        alert_greeting_enabled: bool | None = None,
        voice_style_prompt: str | None = None,
        use_important_prefix: bool = True,
        log_events: bool = True,
    ) -> tuple[str, str, dict[str, Any]]:
        """Build base and spoken alert text using current voice alert rules."""
        debug: dict[str, Any] = {"llm_used": False}
        delivery = normalize_delivery_mode(
            delivery_mode if delivery_mode is not None else self._delivery_mode
        )
        vs_enabled = (
            self._voice_summary_enabled
            if voice_summary_enabled is None
            else bool(voice_summary_enabled)
        )
        greeting_name = (
            self._alert_greeting_name
            if alert_greeting_name is None
            else str(alert_greeting_name or "").strip()
        )
        greeting_on = (
            self._alert_greeting_enabled
            if alert_greeting_enabled is None
            else bool(alert_greeting_enabled)
        )
        style_prompt = resolve_voice_style_prompt(
            voice_style_prompt
            if voice_style_prompt is not None
            else self._voice_style_system_prompt
        )
        tts_model = self.get_event_tts_model()
        sender = str(msg.get("sender") or "")
        base_text = ""
        spoken = ""

        if vs_enabled:
            summary_source = str(
                msg.get("summary_detailed") or msg.get("summary_short") or ""
            ).strip()
            if summary_source:
                llm_user_message = brief_summary_for_tts(summary_source)
                debug.update(
                    {
                        "llm_used": True,
                        "llm_model": self.get_ollama_model(),
                        "llm_system_prompt": style_prompt,
                        "llm_user_message": llm_user_message,
                        "llm_full_prompt": format_voice_llm_prompt(
                            system_prompt=style_prompt,
                            user_message=llm_user_message,
                        ),
                    }
                )
                styled, style_err = await style_summary_for_voice(
                    base_url=self.get_ollama_base_url(),
                    model=self.get_ollama_model(),
                    summary=summary_source,
                    system_prompt=style_prompt,
                    timeout=self.get_ollama_timeout(),
                )
                debug["llm_response"] = styled
                debug["llm_error"] = style_err
                if styled:
                    base_text = styled
                    spoken = styled
                    if log_events:
                        self.add_log("Voice alert uses voice prompt summary", "info")
                else:
                    if log_events:
                        self.add_log(
                            f"Voice summary failed: {style_err or 'unknown'}",
                            "warning",
                        )
                    base_text = brief_summary_for_tts(summary_source)
                    spoken = base_text
                important_sender = (
                    use_important_prefix
                    and is_important_sender(self._conn, sender)
                    and self._important_alert_mode == "always"
                )
                base_text = prepend_sender_announcement(
                    base_text,
                    sender,
                    important=important_sender,
                )
                spoken = prepend_sender_announcement(
                    spoken,
                    sender,
                    important=important_sender,
                )
                base_text = prepend_name_greeting(
                    base_text,
                    greeting_name,
                    enabled=greeting_on,
                )
                spoken = prepend_name_greeting(
                    spoken,
                    greeting_name,
                    enabled=greeting_on,
                )

        if not spoken:
            template = str(self.chatterbox_tts_config.get("alert_template") or "")
            if (
                use_important_prefix
                and is_important_sender(self._conn, sender)
                and self._important_alert_mode == "always"
            ):
                template = "Important. " + template
            base_text = format_email_alert_message(
                sender,
                str(msg.get("subject") or ""),
                template=template,
            )
            base_text = prepend_name_greeting(
                base_text,
                greeting_name,
                enabled=greeting_on,
            )
            spoken = apply_delivery_mode(
                base_text,
                delivery,
                tts_model=tts_model,
            )
        debug["spoken_text"] = spoken
        return base_text, spoken, debug

    def _log_timestamp(self) -> str:
        return self._format_log_timestamp(time.time())

    def _resolve_voice_prompt_name(
        self,
        *,
        voice_style_prompt: str | None,
        voice_style_prompt_file: str | None = None,
        resolved_prompt: str,
    ) -> str:
        incoming = str(voice_style_prompt or "").strip()
        if incoming:
            if voice_style_prompt_file:
                try:
                    file_text = read_prompt_file(
                        voice_style_prompt_file,
                        prompts_dir=self._prompts_dir,
                    )
                    if incoming == file_text.strip():
                        return voice_style_prompt_file
                except (FileNotFoundError, ValueError):
                    pass
            saved_text = str(self._voice_style_system_prompt or "").strip()
            if (
                saved_text
                and incoming == saved_text
                and self._voice_style_prompt_file
            ):
                return self._voice_style_prompt_file
            return "unsaved (settings textarea)"
        if voice_style_prompt_file:
            return voice_style_prompt_file
        if self._voice_style_prompt_file:
            return self._voice_style_prompt_file
        if resolved_prompt.strip() == default_voice_style_prompt().strip():
            return "built-in default"
        if self._voice_style_system_prompt:
            return "saved (database)"
        return "built-in default"

    def _plan_test_speak(
        self,
        msg: dict[str, Any],
        *,
        voice_summary_enabled: bool | None = None,
        delivery_mode: str | None = None,
        alert_greeting_name: str | None = None,
        alert_greeting_enabled: bool | None = None,
        voice_style_prompt: str | None = None,
        voice_style_prompt_file: str | None = None,
        voice_mode: str | None = None,
        voice: str | None = None,
        tts_model: str | None = None,
    ) -> dict[str, Any]:
        delivery = normalize_delivery_mode(
            delivery_mode if delivery_mode is not None else self._delivery_mode
        )
        vs_enabled = (
            self._voice_summary_enabled
            if voice_summary_enabled is None
            else bool(voice_summary_enabled)
        )
        greeting_name = (
            self._alert_greeting_name
            if alert_greeting_name is None
            else str(alert_greeting_name or "").strip()
        )
        greeting_on = (
            self._alert_greeting_enabled
            if alert_greeting_enabled is None
            else bool(alert_greeting_enabled)
        )
        raw_style_prompt = (
            voice_style_prompt
            if voice_style_prompt is not None
            else self._voice_style_system_prompt
        )
        style_prompt = resolve_voice_style_prompt(raw_style_prompt)
        voice_prompt_name = self._resolve_voice_prompt_name(
            voice_style_prompt=voice_style_prompt,
            voice_style_prompt_file=voice_style_prompt_file,
            resolved_prompt=style_prompt,
        )
        chosen = self.get_event_tts_voice() or {}
        resolved_voice_mode = str(voice_mode or chosen.get("voice_mode") or "").strip()
        resolved_voice = str(voice or chosen.get("voice") or "").strip()
        resolved_tts_model = normalize_tts_model(
            tts_model if tts_model is not None else self._tts_model
        )
        summary_source = str(
            msg.get("summary_detailed") or msg.get("summary_short") or ""
        ).strip()
        llm_user_message = brief_summary_for_tts(summary_source) if summary_source else ""
        llm_used = bool(vs_enabled and summary_source and llm_user_message)
        plan: dict[str, Any] = {
            "timestamp": self._log_timestamp(),
            "voice_summary_enabled": vs_enabled,
            "delivery_mode": delivery,
            "alert_greeting_enabled": greeting_on,
            "alert_greeting_name": greeting_name,
            "voice_mode": resolved_voice_mode,
            "voice": resolved_voice,
            "tts_model": resolved_tts_model,
            "ollama_model": self.get_ollama_model(),
            "ollama_base_url": self.get_ollama_base_url(),
            "ollama_timeout": self.get_ollama_timeout(),
            "sender": str(msg.get("sender") or ""),
            "subject": str(msg.get("subject") or ""),
            "summary_detailed": str(msg.get("summary_detailed") or ""),
            "summary_short": str(msg.get("summary_short") or ""),
            "summary_source": summary_source,
            "llm_used": llm_used,
            "llm_model": self.get_ollama_model() if llm_used else None,
            "voice_prompt_name": voice_prompt_name,
            "voice_prompt": style_prompt,
            "llm_system_prompt": style_prompt if llm_used else None,
            "llm_user_message": llm_user_message if llm_used else None,
            "alert_template": str(self.chatterbox_tts_config.get("alert_template") or ""),
        }
        if llm_used:
            plan["llm_full_prompt"] = format_voice_llm_prompt(
                system_prompt=style_prompt,
                user_message=llm_user_message,
            )
        return plan

    def _log_test_speak_before_llm(self, plan: dict[str, Any]) -> None:
        ts = str(plan.get("timestamp") or self._log_timestamp())
        greeting_line = (
            f"on ({plan.get('alert_greeting_name') or 'no name'})"
            if plan.get("alert_greeting_enabled")
            else "off"
        )
        voice_line = (
            f"{plan.get('voice_mode') or 'default'} / {plan.get('voice') or 'default'}"
        )
        self.add_log(f"Test speak — button pressed @ {ts}", "info")
        self.add_log(
            f"Test speak — voice summary: {'on' if plan.get('voice_summary_enabled') else 'off'}",
            "info",
        )
        self.add_log(f"Test speak — greet by name: {greeting_line}", "info")
        self.add_log(
            f"Test speak — delivery mode: {plan.get('delivery_mode') or 'normal'}",
            "info",
        )
        self.add_log(
            f"Test speak — TTS model: {plan.get('tts_model') or 'chatterbox-turbo'}",
            "info",
        )
        self.add_log(f"Test speak — voice: {voice_line}", "info")
        self.add_log(f"Test speak — sender: {plan.get('sender') or '(none)'}", "info")
        self.add_log(f"Test speak — subject: {plan.get('subject') or '(none)'}", "info")
        if plan.get("summary_detailed"):
            self.add_log(
                f"Test speak — summary (detailed):\n{plan['summary_detailed']}",
                "info",
            )
        if plan.get("summary_short"):
            self.add_log(
                f"Test speak — summary (short):\n{plan['summary_short']}",
                "info",
            )
        if plan.get("summary_source"):
            self.add_log(
                f"Test speak — summary source used:\n{plan['summary_source']}",
                "info",
            )
        if plan.get("llm_used"):
            self.add_log(
                "Test speak — Ollama: "
                f"{plan.get('ollama_base_url')} "
                f"(model: {plan.get('ollama_model')}, timeout: {plan.get('ollama_timeout')}s)",
                "info",
            )
            self.add_log(
                f"Test speak — voice prompt name: {plan.get('voice_prompt_name') or '(unknown)'}",
                "info",
            )
            voice_prompt = str(plan.get("voice_prompt") or "").strip()
            if voice_prompt:
                self.add_log(
                    f"Test speak — voice prompt:\n{voice_prompt}",
                    "info",
                )
            user_message = str(plan.get("llm_user_message") or "").strip()
            if user_message:
                self.add_log(
                    f"Test speak — LLM user message:\n{user_message}",
                    "info",
                )
            self.add_log("Test speak — calling LLM now…", "info")
        else:
            reason = (
                "voice summary off"
                if not plan.get("voice_summary_enabled")
                else "no summary text"
            )
            self.add_log(f"Test speak — LLM skipped ({reason})", "info")
            self.add_log(
                f"Test speak — fallback alert template: {plan.get('alert_template') or '(default)'}",
                "info",
            )
            self.add_log(
                f"Test speak — delivery mode for TTS styling: {plan.get('delivery_mode') or 'normal'}",
                "info",
            )

    def _log_test_speak_after_llm(self, debug: dict[str, Any]) -> None:
        if debug.get("llm_used"):
            response = str(debug.get("llm_response") or "").strip()
            if response:
                self.add_log(f"Test speak — LLM response:\n{response}", "info")
            elif debug.get("llm_error"):
                self.add_log(
                    f"Test speak — LLM failed: {debug.get('llm_error')}",
                    "warning",
                )
        spoken = str(debug.get("spoken_text") or "").strip()
        if spoken:
            self.add_log(f"Test speak — final TTS text:\n{spoken}", "info")

    async def compose_test_alert_speech(
        self,
        *,
        voice_summary_enabled: bool | None = None,
        delivery_mode: str | None = None,
        alert_greeting_name: str | None = None,
        alert_greeting_enabled: bool | None = None,
        voice_style_prompt: str | None = None,
        voice_style_prompt_file: str | None = None,
        voice_mode: str | None = None,
        voice: str | None = None,
        tts_model: str | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        """Spoken text for Settings → Test speak using the same rules as live alerts."""
        plan = self._plan_test_speak(
            DEFAULT_TEST_EMAIL,
            voice_summary_enabled=voice_summary_enabled,
            delivery_mode=delivery_mode,
            alert_greeting_name=alert_greeting_name,
            alert_greeting_enabled=alert_greeting_enabled,
            voice_style_prompt=voice_style_prompt,
            voice_style_prompt_file=voice_style_prompt_file,
            voice_mode=voice_mode,
            voice=voice,
            tts_model=tts_model,
        )
        self._log_test_speak_before_llm(plan)
        _base, spoken, debug = await self._compose_alert_speech(
            DEFAULT_TEST_EMAIL,
            voice_summary_enabled=voice_summary_enabled,
            delivery_mode=delivery_mode,
            alert_greeting_name=alert_greeting_name,
            alert_greeting_enabled=alert_greeting_enabled,
            voice_style_prompt=voice_style_prompt,
            use_important_prefix=False,
            log_events=False,
        )
        self._log_test_speak_after_llm(debug)
        return _base, spoken, debug

    async def _build_alert(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        if self._demo_mode:
            return None
        if not self.chatterbox_tts_config.get("enabled"):
            return None
        if not self._email_passes_watermark(msg):
            return None
        row = get_email(self._conn, str(msg.get("id") or ""))
        if row and row.get("alerted_at"):
            return None
        now = time.time()
        sender = str(msg.get("sender") or "")
        should_alert, reason = self._should_alert_for_sender(sender, now)
        if not should_alert:
            self.add_log(f"Alert skipped ({reason})", "info")
            return None
        base_text, spoken, _debug = await self._compose_alert_speech(msg, log_events=True)
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
                self.add_log(f"TTS generated: {saved_path}", "info")
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
        row = self.get_email_for_display(email_id)
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
            "prompts_dir": str(self._prompts_dir),
            "saved_prompts": self.list_saved_prompts(),
            "active_prompt_file": self.get_summary_prompt_file(),
            "prompt_source": self.get_prompt_source(),
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