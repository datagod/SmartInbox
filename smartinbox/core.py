"""SmartInbox core — Gmail polling, Ollama summaries, Chatterbox alerts."""

from __future__ import annotations

import asyncio
import copy
import time
from datetime import date, datetime, timedelta
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
from smartinbox.calendar_events import (
    count_emails_by_provider,
    format_provider_counts,
    calendar_extraction_event_count,
    is_email_extracted,
    list_calendar_events,
    list_emails_for_calendar_scan,
    mark_email_extracted,
    record_event_vote,
    reset_calendar_data_for_emails,
    upsert_calendar_event,
)
from smartinbox.calendar_extract import (
    build_calendar_ollama_options,
    extract_calendar_events,
    preload_ollama_model,
)
from smartinbox.calendar_body import parse_body_calendar_events, parse_summary_calendar_events
from smartinbox.calendar_ics import parse_ics_calendar_events
from smartinbox.demo_data import build_demo_calendar_events, build_demo_emails
from smartinbox.email_search import search_demo_emails, search_stored_emails
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
    update_email_spam,
    update_email_summary,
    upsert_email,
)
from smartinbox.storage_stats import get_storage_stats
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
    sanitize_text_for_tts,
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
from smartinbox.email_spam import classify_email_spam
from smartinbox.voice_summary import (
    brief_summary_for_tts,
    default_voice_style_prompt,
    format_voice_llm_prompt,
    resolve_voice_style_prompt,
    style_summary_for_voice,
)
from smartinbox.imap_mail import (
    fetch_messages_since_for_account_record,
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
        self.calendar_config: dict[str, Any] = dict(s.get("calendar") or {})
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
        default_concurrency = int(self.calendar_config.get("extract_concurrency", 6))
        saved_concurrency = get_setting(self._conn, "calendar_extract_concurrency")
        try:
            concurrency = int(saved_concurrency) if saved_concurrency is not None else default_concurrency
        except (TypeError, ValueError):
            concurrency = default_concurrency
        self._calendar_extract_concurrency = max(1, min(32, concurrency))
        self._calendar_ollama_main_gpu = self._load_calendar_ollama_main_gpu()
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
        self._demo_calendar_events: list[dict[str, Any]] = (
            copy.deepcopy(build_demo_calendar_events(timezone=self.timezone))
            if self._demo_mode
            else []
        )
        self._calendar_backfill: dict[str, Any] = {
            "running": False,
            "done": 0,
            "total": 0,
        }
        self._calendar_backfill_task: asyncio.Task[None] | None = None
        self._mail_fetch_job: dict[str, Any] = {
            "running": False,
            "done": 0,
            "total": 0,
            "lookback": 5,
            "unit": "days",
            "days": 5,
            "hours": None,
            "phase": "fetch",
        }
        self._mail_fetch_task: asyncio.Task[None] | None = None
        self._mail_fetch_lock = asyncio.Lock()

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

    def _load_calendar_ollama_main_gpu(self) -> int | None:
        saved = get_setting(self._conn, "calendar_ollama_main_gpu")
        if saved is None:
            cfg = self.calendar_config.get("ollama_main_gpu")
            if cfg is None or cfg == "":
                return None
            try:
                return max(0, min(15, int(cfg)))
            except (TypeError, ValueError):
                return None
        try:
            gpu = int(saved)
        except (TypeError, ValueError):
            return None
        if gpu < 0:
            return None
        return max(0, min(15, gpu))

    @staticmethod
    def _log_subject(subject: str | None, *, max_len: int = 56) -> str:
        text = (subject or "(no subject)").strip()
        if len(text) <= max_len:
            return text
        return text[: max_len - 1].rstrip() + "…"

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

    def _calendar_tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def _parse_anchor_date(self, anchor_date: str | None) -> datetime:
        tz = self._calendar_tz()
        if anchor_date:
            try:
                parts = anchor_date.strip().split("-")
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                return datetime(year, month, day, tzinfo=tz)
            except (ValueError, IndexError):
                pass
        return datetime.now(tz=tz)

    def _calendar_week_bounds(
        self, anchor: datetime | None = None
    ) -> tuple[float, float, datetime, datetime]:
        tz = self._calendar_tz()
        now = anchor or datetime.now(tz=tz)
        if now.tzinfo is None:
            now = now.replace(tzinfo=tz)
        monday = now - timedelta(days=now.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = monday + timedelta(days=7)
        return monday.timestamp(), week_end.timestamp(), monday, week_end

    def _events_for_day(
        self,
        events: list[dict[str, Any]],
        day_start: datetime,
    ) -> list[dict[str, Any]]:
        day_end = day_start + timedelta(days=1)
        start_ts = day_start.timestamp()
        end_ts = day_end.timestamp()
        return [
            e
            for e in events
            if start_ts <= float(e["event_start"]) < end_ts
        ]

    def _build_week_period(
        self, anchor: datetime, today: date
    ) -> dict[str, Any]:
        week_start, week_end, monday, week_end_dt = self._calendar_week_bounds(anchor)
        week_events = self._list_calendar_events_for_display(
            start_ts=week_start,
            end_ts=week_end,
            include_hidden=False,
        )
        days: list[dict[str, Any]] = []
        for offset in range(7):
            day_start_dt = monday + timedelta(days=offset)
            day_events = self._events_for_day(week_events, day_start_dt)
            days.append(
                {
                    "date": day_start_dt.date().isoformat(),
                    "label": day_start_dt.strftime("%a %b %-d"),
                    "weekday": day_start_dt.strftime("%a"),
                    "day_num": day_start_dt.day,
                    "is_today": day_start_dt.date() == today,
                    "events": day_events,
                }
            )
        return {
            "label": (
                f"{monday.strftime('%b %-d')} – "
                f"{(week_end_dt - timedelta(days=1)).strftime('%b %-d, %Y')}"
            ),
            "start": week_start,
            "end": week_end,
            "days": days,
        }

    def _build_month_period(
        self, anchor: datetime, today: date
    ) -> dict[str, Any]:
        first = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if first.month == 12:
            next_month = first.replace(year=first.year + 1, month=1)
        else:
            next_month = first.replace(month=first.month + 1)
        last = next_month - timedelta(days=1)
        grid_start = first - timedelta(days=first.weekday())
        grid_end = last + timedelta(days=6 - last.weekday())
        grid_end_exclusive = grid_end + timedelta(days=1)
        month_events = self._list_calendar_events_for_display(
            start_ts=grid_start.timestamp(),
            end_ts=grid_end_exclusive.timestamp(),
            include_hidden=False,
        )
        weeks: list[dict[str, Any]] = []
        cursor = grid_start
        while cursor <= grid_end:
            week_days: list[dict[str, Any]] = []
            for offset in range(7):
                day_start_dt = cursor + timedelta(days=offset)
                day_events = self._events_for_day(month_events, day_start_dt)
                week_days.append(
                    {
                        "date": day_start_dt.date().isoformat(),
                        "label": day_start_dt.strftime("%a %b %-d"),
                        "day_num": day_start_dt.day,
                        "in_month": day_start_dt.month == first.month,
                        "is_today": day_start_dt.date() == today,
                        "events": day_events,
                    }
                )
            weeks.append({"days": week_days})
            cursor += timedelta(days=7)
        return {
            "label": first.strftime("%B %Y"),
            "month": first.month,
            "year": first.year,
            "start": grid_start.timestamp(),
            "end": grid_end_exclusive.timestamp(),
            "weekday_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "weeks": weeks,
        }

    def _build_day_period(
        self, anchor: datetime, today: date
    ) -> dict[str, Any]:
        day_start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        day_events = self._list_calendar_events_for_display(
            start_ts=day_start.timestamp(),
            end_ts=day_end.timestamp(),
            include_hidden=False,
        )
        return {
            "date": day_start.date().isoformat(),
            "label": day_start.strftime("%A, %B %-d, %Y"),
            "short_label": day_start.strftime("%a %b %-d"),
            "start": day_start.timestamp(),
            "end": day_end.timestamp(),
            "is_today": day_start.date() == today,
            "events": day_events,
        }

    def _list_calendar_events_for_display(
        self,
        *,
        start_ts: float,
        end_ts: float,
        include_hidden: bool = True,
    ) -> list[dict[str, Any]]:
        if self._demo_mode:
            events = [
                dict(row)
                for row in self._demo_calendar_events
                if float(row["event_start"]) >= start_ts
                and float(row["event_start"]) < end_ts
                and (include_hidden or not row.get("hidden"))
            ]
            events.sort(key=lambda e: (float(e["event_start"]), e.get("title") or ""))
            return events
        return list_calendar_events(
            self._conn,
            start_ts=start_ts,
            end_ts=end_ts,
            include_hidden=include_hidden,
        )

    def get_calendar_view(
        self,
        *,
        list_days: int = 30,
        view_mode: str = "week",
        anchor_date: str | None = None,
    ) -> dict[str, Any]:
        mode = (view_mode or "week").strip().lower()
        if mode not in ("week", "month", "day"):
            mode = "week"
        anchor = self._parse_anchor_date(anchor_date)
        tz = self._calendar_tz()
        today = datetime.now(tz=tz).date()
        if mode == "month":
            period = self._build_month_period(anchor, today)
        elif mode == "day":
            period = self._build_day_period(anchor, today)
        else:
            period = self._build_week_period(anchor, today)
        list_days = max(1, min(int(list_days), 365))
        list_start = time.time() - list_days * 86400
        list_end = time.time() + 90 * 86400
        all_events = self._list_calendar_events_for_display(
            start_ts=list_start,
            end_ts=list_end,
            include_hidden=True,
        )
        mail = mail_accounts_status(self._conn)
        return {
            "view_mode": mode,
            "anchor": anchor.date().isoformat(),
            "period": period,
            "week": period if mode == "week" else None,
            "events": all_events,
            "list_days": list_days,
            "backfill": dict(self._calendar_backfill),
            "extract_concurrency": self.get_calendar_extract_concurrency(),
            "calendar_ollama_main_gpu": self.get_calendar_ollama_main_gpu(),
            "mail_accounts": mail.get("accounts") or [],
            "demo_mode": self._demo_mode,
        }

    def vote_calendar_event(self, event_id: str, *, vote: str) -> dict[str, Any] | None:
        if self._demo_mode:
            for index, row in enumerate(self._demo_calendar_events):
                if str(row.get("id")) == event_id:
                    direction = vote.strip().lower()
                    if direction not in ("up", "down"):
                        raise ValueError("vote must be 'up' or 'down'")
                    up = int(row.get("upvotes") or 0)
                    down = int(row.get("downvotes") or 0)
                    if direction == "up":
                        up += 1
                        hidden = False
                    else:
                        down += 1
                        hidden = True
                    updated = {
                        **row,
                        "upvotes": up,
                        "downvotes": down,
                        "score": up - down,
                        "last_vote": direction,
                        "hidden": hidden,
                        "updated_at": time.time(),
                    }
                    self._demo_calendar_events[index] = updated
                    label = "Upvoted" if direction == "up" else "Downvoted"
                    self.add_log(
                        f"{label} calendar event: {updated.get('title')} (score {updated['score']:+d})",
                        "info",
                    )
                    return {"event_id": event_id, "interest": updated}
            return None
        try:
            entry = record_event_vote(self._conn, event_id, vote=vote)
        except ValueError:
            return None
        label = "Upvoted" if vote == "up" else "Downvoted"
        self.add_log(
            f"{label} calendar event: {entry.get('title')} (score {entry['score']:+d})",
            "info",
        )
        return {"event_id": event_id, "interest": entry}

    async def _extract_calendar_for_email(
        self,
        msg: dict[str, Any],
        *,
        index: int | None = None,
        total: int | None = None,
    ) -> int:
        email_id = str(msg.get("id") or "")
        if not email_id:
            return 0
        provider = str(msg.get("provider") or "mail").strip().lower() or "mail"
        provider_label = {"gmail": "Gmail", "proton": "Proton"}.get(
            provider, provider.title()
        )
        subject = self._log_subject(str(msg.get("subject") or ""))
        sender = str(msg.get("sender") or "")
        mail_subject = str(msg.get("subject") or "")
        progress = f"[{index}/{total}] " if index is not None and total is not None else ""
        ref_ts = float(msg.get("received_at") or time.time())

        ics_text = str(msg.get("calendar_ics") or "").strip()
        if not ics_text and not self._demo_mode:
            row = get_email(self._conn, email_id)
            if row:
                ics_text = str(row.get("calendar_ics") or "").strip()

        events: list[dict[str, Any]] = []
        err: str | None = None
        source = ""

        if ics_text:
            events = parse_ics_calendar_events(
                ics_text,
                email_id=email_id,
                sender=sender,
                subject=mail_subject,
                timezone=self.timezone,
            )
            if events:
                source = "ICS invite"
            elif ics_text:
                self.add_log(
                    f"Calendar ICS {progress}{provider_label}: could not parse invite — {subject}",
                    "warning",
                )

        body_text = str(msg.get("body_text") or msg.get("snippet") or "")
        if not events and body_text:
            events = parse_body_calendar_events(
                body_text,
                email_id=email_id,
                sender=sender,
                subject=mail_subject,
                timezone=self.timezone,
            )
            if events:
                source = "body date"

        if not events:
            summary_text = str(
                msg.get("summary_detailed") or msg.get("summary_short") or ""
            ).strip()
            if not summary_text and not self._demo_mode:
                row = get_email(self._conn, email_id)
                if row:
                    summary_text = str(
                        row.get("summary_detailed") or row.get("summary_short") or ""
                    ).strip()
            if summary_text:
                events = parse_summary_calendar_events(
                    summary_text,
                    email_id=email_id,
                    sender=sender,
                    subject=mail_subject,
                    timezone=self.timezone,
                )
                if events:
                    source = "summary date"

        if not events:
            events, err = await extract_calendar_events(
                base_url=self.get_ollama_base_url(),
                model=self.get_ollama_model(),
                email_id=email_id,
                sender=sender,
                subject=mail_subject,
                body=body_text,
                reference_ts=ref_ts,
                timezone=self.timezone,
                timeout=self.get_ollama_timeout(),
                ollama_options=self.get_calendar_ollama_options(),
                keep_alive="30m",
            )
            if events:
                source = "Ollama"

        saved = 0
        for event in events:
            upsert_calendar_event(self._conn, event)
            saved += 1
        mark_email_extracted(self._conn, email_id, event_count=saved)
        if saved:
            via = f" ({source})" if source else ""
            self.add_log(
                f"Calendar extract {progress}{provider_label}: "
                f"found {saved} event{'s' if saved != 1 else ''}{via} — {subject}",
                "info",
            )
        elif err:
            self.add_log(
                f"Calendar extract {progress}{provider_label} failed — {subject}: {err}",
                "warning",
            )
        else:
            self.add_log(
                f"Calendar extract {progress}{provider_label}: no dates — {subject}",
                "info",
            )
        return saved

    async def import_mail_from_sources(self, days: int) -> dict[str, int]:
        """Pull mail from connected IMAP accounts into the local DB for calendar scanning."""
        stats = {
            "accounts": 0,
            "fetched": 0,
            "new": 0,
            "existing": 0,
            "filtered": 0,
        }
        accounts = list_imap_account_records(self._conn)
        if not accounts:
            self.add_log("Calendar import: no mail accounts connected", "warning")
            return stats
        days = max(1, min(int(days), 365))
        since_ts = time.time() - days * 86400
        max_fetch = min(2000, max(100, days * 40))
        self.add_log(
            f"Calendar import: checking {len(accounts)} connected account"
            f"{'s' if len(accounts) != 1 else ''} (last {days} day{'s' if days != 1 else ''}, "
            f"up to {max_fetch} messages each)",
            "info",
        )
        for acct in accounts:
            provider = str(acct.get("provider") or "mail").strip().lower() or "mail"
            provider_label = {"gmail": "Gmail", "proton": "Proton"}.get(
                provider, provider.title()
            )
            email_addr = str(acct.get("email") or "").strip() or "unknown"
            self.add_log(
                f"Calendar import ({provider_label}): connecting to {email_addr}…",
                "info",
            )
            try:
                messages = await asyncio.to_thread(
                    fetch_messages_since_for_account_record,
                    acct,
                    since_days=days,
                    max_results=max_fetch,
                )
            except Exception as e:
                self.add_log(f"Calendar import ({provider_label}) failed: {e}", "error")
                continue
            stats["accounts"] += 1
            stats["fetched"] += len(messages)
            new_for_acct = 0
            existing_for_acct = 0
            filtered_for_acct = 0
            for msg in messages:
                received = float(msg.get("received_at") or 0)
                if received > 0 and received < since_ts:
                    filtered_for_acct += 1
                    stats["filtered"] += 1
                    continue
                result = self._persist_imported_email(msg)
                if result == "new":
                    new_for_acct += 1
                    stats["new"] += 1
                    self._queue_stored_mail_processing(msg, result)
                elif result == "ics_updated":
                    self._queue_stored_mail_processing(msg, result)
                else:
                    existing_for_acct += 1
                    stats["existing"] += 1
            self.add_log(
                f"Calendar import ({provider_label}): done — {new_for_acct} new, "
                f"{existing_for_acct} already stored, {filtered_for_acct} outside window, "
                f"{len(messages)} fetched from server",
                "info",
            )
        return stats

    def _normalize_mail_fetch_lookback(
        self, *, days: int | None = None, hours: int | None = None
    ) -> tuple[int, str, int, str]:
        if hours is not None:
            hours = max(1, min(int(hours), 8760))
            since_seconds = hours * 3600
            label = f"{hours} hour{'s' if hours != 1 else ''}"
            return since_seconds, "hours", hours, label
        days = max(1, min(int(days or 5), 365))
        since_seconds = days * 86400
        label = f"{days} day{'s' if days != 1 else ''}"
        return since_seconds, "days", days, label

    async def run_mail_fetch(self, *, days: int | None = None, hours: int | None = None) -> None:
        """Re-fetch mail from IMAP for the lookback window and store new messages normally."""
        if self._demo_mode or self._mail_fetch_job.get("running"):
            return
        since_seconds, unit, lookback, lookback_label = self._normalize_mail_fetch_lookback(
            days=days, hours=hours
        )
        since_ts = time.time() - since_seconds
        max_fetch = min(2000, max(100, ((since_seconds + 86399) // 86400) * 40))
        self._mail_fetch_job = {
            "running": True,
            "done": 0,
            "total": 0,
            "lookback": lookback,
            "unit": unit,
            "days": lookback if unit == "days" else None,
            "hours": lookback if unit == "hours" else None,
            "phase": "fetch",
            "by_provider": {},
        }
        stats = {
            "accounts": 0,
            "fetched": 0,
            "new": 0,
            "existing": 0,
            "filtered": 0,
        }
        try:
            accounts = list_imap_account_records(self._conn)
            if not accounts:
                self.add_log("Mail fetch: no mail accounts connected", "warning")
                return
            self.add_log(
                f"Mail fetch started: re-fetch from {len(accounts)} connected account"
                f"{'s' if len(accounts) != 1 else ''} (last {lookback_label}, "
                f"up to {max_fetch} messages each)",
                "info",
            )
            pending: list[dict[str, Any]] = []
            for acct in accounts:
                provider = str(acct.get("provider") or "mail").strip().lower() or "mail"
                provider_label = {"gmail": "Gmail", "proton": "Proton"}.get(
                    provider, provider.title()
                )
                email_addr = str(acct.get("email") or "").strip() or "unknown"
                self._mail_fetch_job["phase"] = "fetch"
                self.add_log(
                    f"Mail fetch ({provider_label}): connecting to {email_addr}…",
                    "info",
                )
                try:
                    messages = await asyncio.to_thread(
                        fetch_messages_since_for_account_record,
                        acct,
                        since_seconds=since_seconds,
                        max_results=max_fetch,
                    )
                except Exception as e:
                    self.add_log(f"Mail fetch ({provider_label}) failed: {e}", "error")
                    continue
                stats["accounts"] += 1
                stats["fetched"] += len(messages)
                for msg in messages:
                    msg["provider"] = msg.get("provider") or provider
                    pending.append(msg)
                self.add_log(
                    f"Mail fetch ({provider_label}): received {len(messages)} message"
                    f"{'s' if len(messages) != 1 else ''} from server",
                    "info",
                )
            in_window: list[dict[str, Any]] = []
            for msg in pending:
                received = float(msg.get("received_at") or 0)
                if received > 0 and received < since_ts:
                    stats["filtered"] += 1
                    continue
                in_window.append(msg)
            total = len(in_window)
            by_provider = count_emails_by_provider(in_window)
            self._mail_fetch_job.update(
                {
                    "phase": "store",
                    "total": total,
                    "done": 0,
                    "by_provider": by_provider,
                }
            )
            if total == 0:
                self.add_log(
                    f"Mail fetch: no messages in the last {lookback_label}",
                    "info",
                )
                self.add_log("Mail fetch complete: 0 messages stored", "info")
                return
            provider_summary = format_provider_counts(by_provider)
            inbox_note = f" — {provider_summary}" if provider_summary else ""
            self.add_log(
                f"Mail fetch: storing {total} message{'s' if total != 1 else ''}"
                f"{inbox_note} in local database",
                "info",
            )
            for i, msg in enumerate(in_window):
                provider = str(msg.get("provider") or "mail").strip().lower() or "mail"
                result = self._persist_imported_email(msg)
                if result in ("new", "ics_updated"):
                    if result == "new":
                        stats["new"] += 1
                    else:
                        stats["existing"] += 1
                    self._log_import_result(msg, result, provider=provider)
                    self._queue_stored_mail_processing(msg, result)
                else:
                    stats["existing"] += 1
                self._mail_fetch_job["done"] = i + 1
            self.add_log(
                f"Mail fetch complete: {stats['new']} new, {stats['existing']} already stored, "
                f"{stats['filtered']} outside window, {stats['fetched']} fetched from "
                f"{stats['accounts']} account{'s' if stats['accounts'] != 1 else ''}",
                "info",
            )
        finally:
            snap = dict(self._mail_fetch_job)
            self._mail_fetch_job = {
                "running": False,
                "done": snap.get("done", 0),
                "total": snap.get("total", 0),
                "lookback": snap.get("lookback", lookback),
                "unit": snap.get("unit", unit),
                "days": snap.get("days"),
                "hours": snap.get("hours"),
                "phase": "done",
                "by_provider": snap.get("by_provider") or {},
            }

    def mail_fetch_running(self) -> bool:
        return bool(self._mail_fetch_job.get("running"))

    def start_mail_fetch(self, *, days: int | None = None, hours: int | None = None) -> bool:
        if self._demo_mode:
            return False
        if self._mail_fetch_job.get("running"):
            return False
        if self._mail_fetch_task and not self._mail_fetch_task.done():
            return False

        async def _run() -> None:
            if self._mail_fetch_lock.locked():
                return
            async with self._mail_fetch_lock:
                await self.run_mail_fetch(days=days, hours=hours)

        self._mail_fetch_task = asyncio.create_task(_run())
        return True

    def get_process_view(self) -> dict[str, Any]:
        mail = mail_accounts_status(self._conn)
        return {
            "demo_mode": self._demo_mode,
            "mail_accounts": mail.get("accounts") or [],
            "fetch": dict(self._mail_fetch_job),
        }

    def get_storage_view(self) -> dict[str, Any]:
        stats = get_storage_stats(self._conn, self._db_path)
        mail = mail_accounts_status(self._conn)
        return {
            "demo_mode": self._demo_mode,
            "connected_accounts": mail.get("accounts") or [],
            "stats": stats,
        }

    async def backfill_calendar_events(
        self, *, days: int = 5, force: bool = False, import_from_source: bool = False
    ) -> None:
        if self._demo_mode or self._calendar_backfill.get("running"):
            return
        days = max(1, min(int(days), 365))
        self._calendar_backfill = {
            "running": True,
            "done": 0,
            "total": 0,
            "days": days,
            "force": force,
            "by_provider": {},
            "phase": "import" if import_from_source else "scan",
        }
        import_stats: dict[str, int] = {}
        events_found = 0
        try:
            if import_from_source and force:
                mode = "import from mail sources and re-extract dates"
            elif import_from_source:
                mode = "import from mail sources and extract dates"
            elif force:
                mode = "re-extract dates from local mail"
            else:
                mode = "extract dates from local mail"
            self.add_log(
                f"Calendar process started: {mode} (last {days} day{'s' if days != 1 else ''})",
                "info",
            )
            if import_from_source:
                import_stats = await self.import_mail_from_sources(days)
                self.add_log(
                    f"Calendar import complete: {import_stats['new']} new, "
                    f"{import_stats['existing']} already stored, "
                    f"{import_stats['filtered']} outside window, "
                    f"{import_stats['fetched']} fetched from "
                    f"{import_stats['accounts']} account"
                    f"{'s' if import_stats['accounts'] != 1 else ''}",
                    "info",
                )
            since_ts = time.time() - days * 86400
            self.add_log(
                f"Calendar scan: loading emails from local database "
                f"(since {days} day{'s' if days != 1 else ''} ago)…",
                "info",
            )
            to_scan = list_emails_for_calendar_scan(
                self._conn,
                since_ts=since_ts,
                pending_only=not force,
            )
            if force and to_scan:
                self.add_log(
                    f"Calendar scan: clearing prior extraction for {len(to_scan)} email"
                    f"{'s' if len(to_scan) != 1 else ''} before re-processing",
                    "info",
                )
                reset_calendar_data_for_emails(
                    self._conn, [str(msg["id"]) for msg in to_scan]
                )
            total = len(to_scan)
            by_provider = count_emails_by_provider(to_scan)
            provider_summary = format_provider_counts(by_provider)
            label = "re-processing" if force else "processing"
            self._calendar_backfill.update(
                {
                    "phase": "scan",
                    "done": 0,
                    "total": total,
                    "by_provider": by_provider,
                }
            )
            if total == 0:
                self.add_log(
                    f"Calendar scan: no emails in the last {days} day{'s' if days != 1 else ''} "
                    f"(all connected inboxes)",
                    "info",
                )
                self.add_log("Calendar process complete: 0 emails scanned, 0 events found", "info")
                return
            inbox_note = f" — {provider_summary}" if provider_summary else ""
            model = self.get_ollama_model()
            batch_size = self.get_calendar_extract_concurrency()
            gpu = self.get_calendar_ollama_main_gpu()
            await self._preload_calendar_ollama_model()
            gpu_note = f", GPU {gpu}" if gpu is not None else ""
            self.add_log(
                f"Calendar scan: {label} {total} email{'s' if total != 1 else ''}"
                f"{inbox_note} with Ollama model {model}{gpu_note} "
                f"({batch_size} parallel extraction{'s' if batch_size != 1 else ''})",
                "info",
            )
            for i in range(0, total, batch_size):
                batch = to_scan[i : i + batch_size]
                results = await asyncio.gather(
                    *(
                        self._extract_calendar_for_email(
                            dict(msg), index=i + j + 1, total=total
                        )
                        for j, msg in enumerate(batch)
                    )
                )
                events_found += sum(results)
                done = min(i + len(batch), total)
                self._calendar_backfill["done"] = done
                self.add_log(
                    f"Calendar extract: {done}/{total} emails done "
                    f"({events_found} event{'s' if events_found != 1 else ''} found so far)",
                    "info",
                )
            done_note = f" ({provider_summary})" if provider_summary else ""
            summary_parts = [
                f"{total} email{'s' if total != 1 else ''} scanned",
                f"{events_found} event{'s' if events_found != 1 else ''} found",
            ]
            if import_stats.get("new"):
                summary_parts.append(
                    f"{import_stats['new']} new email{'s' if import_stats['new'] != 1 else ''} imported"
                )
            self.add_log(
                f"Calendar process complete{done_note}: {', '.join(summary_parts)}",
                "info",
            )
        finally:
            snap = dict(self._calendar_backfill)
            self._calendar_backfill = {
                "running": False,
                "done": snap.get("done", 0),
                "total": snap.get("total", 0),
                "days": days,
                "force": force,
                "by_provider": snap.get("by_provider") or {},
            }

    def calendar_scan_running(self) -> bool:
        return bool(self._calendar_backfill.get("running"))

    def start_calendar_backfill(
        self, *, days: int = 5, force: bool = False, import_from_source: bool = False
    ) -> bool:
        if self._demo_mode:
            return False
        if self._calendar_backfill.get("running"):
            return False
        if self._calendar_backfill_task and not self._calendar_backfill_task.done():
            return False
        self._calendar_backfill_task = asyncio.create_task(
            self.backfill_calendar_events(
                days=days, force=force, import_from_source=import_from_source
            )
        )
        return True

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
            self._demo_calendar_events = copy.deepcopy(
                build_demo_calendar_events(timezone=self.timezone)
            )
            self.add_log(
                "Demo mode enabled — inbox shows sample emails for screenshots",
                "info",
            )
        else:
            self._demo_emails = []
            self._demo_calendar_events = []
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

    def search_emails(
        self,
        query: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if self._demo_mode:
            results, total = search_demo_emails(
                self._demo_emails,
                query,
                limit=limit,
                offset=offset,
            )
        else:
            results, total = search_stored_emails(
                self._conn,
                query,
                limit=limit,
                offset=offset,
            )
        return {
            "query": (query or "").strip(),
            "results": results,
            "total": total,
            "limit": max(1, min(int(limit), 200)),
            "offset": max(0, int(offset)),
            "demo_mode": self._demo_mode,
        }

    async def extract_calendar_for_email_id(
        self,
        email_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        if self._demo_mode:
            return {
                "email_id": email_id,
                "events_found": 0,
                "demo_mode": True,
                "message": "Calendar extraction is disabled in demo mode",
            }
        msg = get_email(self._conn, email_id)
        if msg is None:
            raise ValueError("email not found")
        if force:
            reset_calendar_data_for_emails(self._conn, [email_id])
        events_found = await self._extract_calendar_for_email(msg)
        return {
            "email_id": email_id,
            "events_found": events_found,
            "calendar_extracted": True,
            "subject": msg.get("subject"),
        }

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

    def get_calendar_extract_concurrency(self) -> int:
        return self._calendar_extract_concurrency

    def get_calendar_ollama_main_gpu(self) -> int | None:
        return self._calendar_ollama_main_gpu

    def get_calendar_ollama_options(self) -> dict[str, Any]:
        return build_calendar_ollama_options(main_gpu=self._calendar_ollama_main_gpu)

    def set_calendar_ollama_main_gpu(self, value: int | float | str | None) -> int | None:
        if value is None or value == "" or str(value).strip().lower() == "auto":
            self._calendar_ollama_main_gpu = None
            set_setting(self._conn, "calendar_ollama_main_gpu", -1)
            self.add_log("Calendar Ollama GPU set to auto (Ollama scheduler)", "info")
            return None
        try:
            gpu = int(value)
        except (TypeError, ValueError):
            raise ValueError("GPU index must be an integer") from None
        if gpu < 0:
            self._calendar_ollama_main_gpu = None
            set_setting(self._conn, "calendar_ollama_main_gpu", -1)
            self.add_log("Calendar Ollama GPU set to auto (Ollama scheduler)", "info")
            return None
        self._calendar_ollama_main_gpu = max(0, min(15, gpu))
        set_setting(self._conn, "calendar_ollama_main_gpu", self._calendar_ollama_main_gpu)
        self.add_log(
            f"Calendar Ollama GPU set to GPU {self._calendar_ollama_main_gpu}",
            "info",
        )
        return self._calendar_ollama_main_gpu

    async def _preload_calendar_ollama_model(self) -> None:
        model = self.get_ollama_model()
        options = self.get_calendar_ollama_options()
        gpu = self.get_calendar_ollama_main_gpu()
        if gpu is not None:
            self.add_log(
                f"Calendar: loading {model} on GPU {gpu} for event extraction…",
                "info",
            )
        else:
            self.add_log(f"Calendar: loading {model} for event extraction…", "info")
        err = await preload_ollama_model(
            base_url=self.get_ollama_base_url(),
            model=model,
            options=options,
            keep_alive="30m",
            timeout=self.get_ollama_timeout(),
        )
        if err:
            self.add_log(f"Calendar Ollama preload failed: {err}", "warning")
        elif gpu is not None:
            self.add_log(f"Calendar: {model} ready on GPU {gpu}", "info")

    def set_calendar_extract_concurrency(self, value: int | float | str) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError("concurrency must be an integer") from None
        self._calendar_extract_concurrency = max(1, min(32, n))
        set_setting(self._conn, "calendar_extract_concurrency", self._calendar_extract_concurrency)
        self.add_log(
            f"Calendar extract concurrency set to {self._calendar_extract_concurrency}",
            "info",
        )
        return self._calendar_extract_concurrency

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
            "calendar": {
                "extract_concurrency": self.get_calendar_extract_concurrency(),
                "ollama_main_gpu": self.get_calendar_ollama_main_gpu(),
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
        self.start_calendar_backfill(days=30)

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._calendar_backfill_task:
            self._calendar_backfill_task.cancel()
            try:
                await self._calendar_backfill_task
            except asyncio.CancelledError:
                pass
            self._calendar_backfill_task = None
        if self._mail_fetch_task:
            self._mail_fetch_task.cancel()
            try:
                await self._mail_fetch_task
            except asyncio.CancelledError:
                pass
            self._mail_fetch_task = None

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self.poll_inbox()
            except Exception as e:
                self.add_log(f"Poll error: {e}", "error")
            await asyncio.sleep(self._poll_interval)

    async def poll_inbox(self, *, detailed: bool = False) -> int:
        if self._poll_lock.locked():
            if detailed:
                self.add_log("Inbox check — already running", "warning")
            return 0
        async with self._poll_lock:
            return await self._poll_inbox_locked(detailed=detailed)

    async def _poll_inbox_locked(self, *, detailed: bool = False) -> int:
        accounts = list_imap_account_records(self._conn)
        if not accounts:
            self._last_poll_at = time.time()
            if detailed:
                self.add_log("Inbox check — no mail accounts connected", "warning")
            return 0
        max_fetch = int(self.gmail_config.get("max_fetch", 20))
        self._last_poll_at = time.time()
        new_count = 0
        skipped_old = 0
        existing_count = 0
        ics_updated_count = 0
        if detailed:
            if self._inbox_watermark:
                stamp = self._format_log_timestamp(self._inbox_watermark)
                cutoff = f"only mail received after {stamp}"
            else:
                cutoff = "no cutoff date (all unread mail eligible)"
            self.add_log(
                f"Inbox check started: {len(accounts)} connected account"
                f"{'s' if len(accounts) != 1 else ''}, up to {max_fetch} unread each — {cutoff}",
                "info",
            )
        for acct in accounts:
            provider = str(acct.get("provider") or "mail").strip().lower() or "mail"
            provider_label = {"gmail": "Gmail", "proton": "Proton"}.get(
                provider, provider.title()
            )
            email_addr = str(acct.get("email") or "").strip() or "unknown"
            if detailed:
                self.add_log(
                    f"Inbox check ({provider_label}): connecting to {email_addr}…",
                    "info",
                )
            try:
                raw_messages = await asyncio.to_thread(
                    fetch_unread_for_account_record, acct, max_results=max_fetch
                )
            except Exception as e:
                self.add_log(f"Inbox check ({provider_label}) failed: {e}", "error")
                continue
            if detailed:
                unread_n = len(raw_messages)
                self.add_log(
                    f"Inbox check ({provider_label}): {unread_n} unread message"
                    f"{'s' if unread_n != 1 else ''} on server",
                    "info",
                )
            acct_new = 0
            acct_existing = 0
            acct_ics = 0
            acct_skipped = 0
            skipped_uids: list[str] = []
            for msg in raw_messages:
                if not self._email_passes_watermark(msg):
                    skipped_old += 1
                    acct_skipped += 1
                    uid = str(msg.get("imap_uid") or "").strip()
                    if uid:
                        skipped_uids.append(uid)
                    if detailed:
                        subject = self._log_subject(str(msg.get("subject") or "(no subject)"))
                        sender = str(msg.get("sender") or "unknown sender")
                        self.add_log(
                            f"Inbox check ({provider_label}): skipped before cutoff — "
                            f"{sender} — {subject}",
                            "info",
                        )
                    continue
                result = self._persist_imported_email(msg)
                if result == "existing":
                    existing_count += 1
                    acct_existing += 1
                    if detailed:
                        subject = self._log_subject(str(msg.get("subject") or "(no subject)"))
                        sender = str(msg.get("sender") or "unknown sender")
                        self.add_log(
                            f"Inbox check ({provider_label}): already stored — {sender} — {subject}",
                            "info",
                        )
                    continue
                msg_provider = msg.get("provider") or provider
                if result == "new":
                    new_count += 1
                    acct_new += 1
                elif result == "ics_updated":
                    ics_updated_count += 1
                    acct_ics += 1
                self._log_import_result(msg, result, provider=msg_provider)
                self._queue_stored_mail_processing(msg, result)

            if detailed:
                self.add_log(
                    f"Inbox check ({provider_label}): account done — {acct_new} new, "
                    f"{acct_existing} already stored"
                    + (f", {acct_ics} calendar ICS updated" if acct_ics else "")
                    + (f", {acct_skipped} before cutoff" if acct_skipped else ""),
                    "info",
                )

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
                            f"as read on {provider_label} ({email_addr})",
                            "info",
                        )
                except asyncio.TimeoutError:
                    self.add_log(
                        f"Mark seen timed out ({provider_label}: {email_addr})",
                        "warning",
                    )
                except Exception as e:
                    self.add_log(
                        f"Mark seen failed ({provider_label}: {email_addr}): {e}",
                        "warning",
                    )

        if detailed and (existing_count or ics_updated_count):
            extras: list[str] = []
            if existing_count:
                extras.append(
                    f"{existing_count} already stored email"
                    f"{'s' if existing_count != 1 else ''}"
                )
            if ics_updated_count:
                extras.append(
                    f"{ics_updated_count} calendar ICS update"
                    f"{'s' if ics_updated_count != 1 else ''}"
                )
            self.add_log(f"Inbox check: {', '.join(extras)}", "info")
        self._log_inbox_check(new_count=new_count, skipped_old=skipped_old)
        return new_count

    def _persist_imported_email(self, msg: dict[str, Any]) -> str:
        """Store an imported message and keep calendar_ics on the in-memory row."""
        result = upsert_email(self._conn, msg)
        email_id = str(msg.get("id") or "")
        if email_id:
            row = get_email(self._conn, email_id)
            if row and str(row.get("calendar_ics") or "").strip():
                msg["calendar_ics"] = row["calendar_ics"]
        return result

    def _log_import_result(self, msg: dict[str, Any], result: str, *, provider: str) -> None:
        subject = str(msg.get("subject") or "(no subject)")
        has_ics = bool(str(msg.get("calendar_ics") or "").strip())
        if result == "new":
            if has_ics:
                self.add_log(
                    f"New email ({provider}): {msg.get('sender')} — {subject} "
                    f"(calendar invite ICS stored)",
                    "info",
                )
            else:
                self.add_log(
                    f"New email ({provider}): {msg.get('sender')} — {subject}",
                    "info",
                )
        elif result == "ics_updated":
            self.add_log(
                f"Calendar invite ICS stored ({provider}): {subject}",
                "info",
            )

    def _message_has_calendar_ics(self, msg: dict[str, Any]) -> bool:
        if str(msg.get("calendar_ics") or "").strip():
            return True
        email_id = str(msg.get("id") or "")
        if not email_id or self._demo_mode:
            return False
        row = get_email(self._conn, email_id)
        return bool(row and str(row.get("calendar_ics") or "").strip())

    def _queue_stored_mail_processing(self, msg: dict[str, Any], upsert_result: str) -> None:
        if self._demo_mode:
            return
        stored = dict(msg)
        if upsert_result in ("new", "ics_updated"):
            self._track_email_task(self._process_new_email_calendar(stored))
        if upsert_result == "new":
            self._notify_emails()
            self._track_email_task(self._process_new_email(stored))

    async def _process_new_email_calendar(self, msg: dict[str, Any]) -> None:
        if self._demo_mode:
            return
        email_id = str(msg.get("id") or "")
        if not email_id:
            return
        has_ics = self._message_has_calendar_ics(msg)
        prior_count = calendar_extraction_event_count(self._conn, email_id)
        if prior_count is not None and prior_count > 0 and not has_ics:
            return
        subject = self._log_subject(str(msg.get("subject") or "(no subject)"))
        try:
            if has_ics and prior_count is not None:
                reset_calendar_data_for_emails(self._conn, [email_id])
            elif prior_count == 0:
                reset_calendar_data_for_emails(self._conn, [email_id])
            await self._extract_calendar_for_email(msg)
        except Exception as e:
            self.add_log(f"Calendar extract failed — {subject}: {e}", "warning")

    async def _classify_email_spam(self, msg: dict[str, Any]) -> bool | None:
        """Return True if spam, False if not, None if check failed."""
        email_id = str(msg.get("id") or "")
        if not email_id:
            return None
        subject = self._log_subject(str(msg.get("subject") or "(no subject)"))
        is_spam, err = await classify_email_spam(
            base_url=self.get_ollama_base_url(),
            model=self.get_ollama_model(),
            sender=str(msg.get("sender") or ""),
            subject=str(msg.get("subject") or ""),
            body=str(msg.get("body_text") or msg.get("snippet") or ""),
            summary=str(
                msg.get("summary_detailed") or msg.get("summary_short") or ""
            ).strip()
            or None,
            timeout=min(60.0, self.get_ollama_timeout()),
        )
        if err:
            self.add_log(
                f"Spam check failed for {subject} (voice alert allowed): {err}",
                "warning",
            )
            return None
        update_email_spam(self._conn, email_id, is_spam=bool(is_spam))
        msg["is_spam"] = 1 if is_spam else 0
        if is_spam:
            self.add_log(
                f"Spam check: junk — no voice alert for {subject}",
                "info",
            )
        else:
            self.add_log(f"Spam check: not spam — {subject}", "info")
        return bool(is_spam)

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

            is_spam = await self._classify_email_spam(msg)
            if is_spam:
                return

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
        base_text = sanitize_text_for_tts(base_text)
        spoken = sanitize_text_for_tts(spoken)
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
        if row and row.get("is_spam"):
            self.add_log("Alert skipped (classified as spam)", "info")
            return None
        if msg.get("is_spam"):
            self.add_log("Alert skipped (classified as spam)", "info")
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