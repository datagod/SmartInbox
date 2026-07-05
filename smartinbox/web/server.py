"""FastAPI web server for SmartInbox."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.staticfiles import StaticFiles

from smartinbox import __version__
from smartinbox.chatterbox_models import CHATTERBOX_TTS_MODELS, normalize_tts_model
from smartinbox.chatterbox_tts import (
    apply_delivery_mode_settings,
    apply_tts_model_settings,
    apply_voice_override,
    get_or_synthesize_phrase,
    get_or_synthesize_speech,
    list_chatterbox_voices,
)
from smartinbox.core import SmartInboxCore
from smartinbox.delivery_modes import (
    all_delivery_phrases,
    apply_delivery_mode,
    delivery_mode_label,
    normalize_delivery_mode,
    spoken_delivery_phrase,
)
from smartinbox.imap_mail import (
    connect_gmail,
    connect_mail_account,
    disconnect_gmail,
    disconnect_mail_account,
    gmail_connection,
    mail_accounts_status,
)
from smartinbox.prompt_storage import read_prompt_file
from smartinbox.tts_recording_cache import (
    delete_recordings,
    list_recordings,
    media_type_for_filename,
    phrase_recording_path,
    phrases_dir,
    recording_file_path,
    resolve_recordings_dir,
    safe_recording_relpath,
)

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


class Broadcaster:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._queues.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._queues.discard(q)

    async def publish(self, message: dict[str, Any]) -> None:
        async with self._lock:
            qs = list(self._queues)
        for q in qs:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(message)
                except Exception:
                    pass


def create_app(core: SmartInboxCore) -> FastAPI:
    broadcaster = Broadcaster()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        def _forward(kind: str, payload: Any) -> None:
            if kind == "log":
                asyncio.create_task(broadcaster.publish({"type": "log", "data": payload}))
            elif kind == "logs":
                asyncio.create_task(broadcaster.publish({"type": "logs", "data": payload}))
            elif kind == "emails":
                asyncio.create_task(broadcaster.publish({"type": "emails", "data": payload}))
            elif kind == "email_alerts":
                asyncio.create_task(broadcaster.publish({"type": "email_alerts", "data": payload}))
            elif kind == "important_senders":
                asyncio.create_task(
                    broadcaster.publish({"type": "important_senders", "data": payload})
                )
            elif kind == "sender_interest":
                asyncio.create_task(
                    broadcaster.publish({"type": "sender_interest", "data": payload})
                )

        core.add_update_listener(_forward)
        await core.start()
        yield
        await core.stop()

    app = FastAPI(title="SmartInbox", version=__version__, lifespan=lifespan)
    app.state.core = core
    app.state.broadcaster = broadcaster

    static_dir = WEB_DIR / "static"
    icon_path = static_dir / "img" / "smartinbox-icon.svg"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        if icon_path.is_file():
            return FileResponse(icon_path, media_type="image/svg+xml")
        return Response(status_code=404)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "index.html", {"version": __version__}
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "settings.html", {"version": __version__}
        )

    @app.get("/llm", response_class=HTMLResponse)
    async def llm_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "llm.html", {"version": __version__}
        )

    @app.get("/phrases", response_class=HTMLResponse)
    async def phrases_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "phrases.html", {"version": __version__}
        )

    @app.get("/senders", response_class=HTMLResponse)
    async def senders_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "senders.html", {"version": __version__}
        )

    @app.get("/calendar", response_class=HTMLResponse)
    async def calendar_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "calendar.html", {"version": __version__}
        )

    @app.get("/pipelines", response_class=HTMLResponse)
    async def pipelines_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "pipelines.html", {"version": __version__}
        )

    @app.get("/process")
    async def process_page_redirect():
        return RedirectResponse(url="/pipelines", status_code=301)

    @app.get("/storage", response_class=HTMLResponse)
    async def storage_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "storage.html", {"version": __version__}
        )

    @app.get("/search", response_class=HTMLResponse)
    async def search_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request, "search.html", {"version": __version__}
        )

    def _recordings_cache_dir() -> str:
        return str(core.chatterbox_tts_config.get("cache_dir", "localrecordings"))

    @app.get("/api/state")
    async def api_state():
        return JSONResponse(core.get_snapshot())

    @app.get("/api/health")
    async def api_health():
        return JSONResponse(await core.health())

    @app.post("/api/poll")
    async def api_poll(request: Request):
        detailed = True
        try:
            body = await request.json()
            if isinstance(body, dict) and "detailed" in body:
                detailed = bool(body.get("detailed"))
        except Exception:
            pass
        core.add_log("Inbox check — Check now requested", "info")
        count = await core.poll_inbox(detailed=detailed)
        return JSONResponse({"ok": True, "new_count": count, "detailed": detailed})

    @app.post("/api/inbox/empty")
    async def api_inbox_empty():
        removed = await asyncio.wait_for(
            asyncio.to_thread(core.empty_inbox),
            timeout=120.0,
        )
        return JSONResponse({"ok": True, "removed": removed})

    @app.post("/api/logs/clear")
    async def api_logs_clear():
        core.clear_logs()
        return JSONResponse({"ok": True})

    @app.get("/api/stream")
    async def api_stream(request: Request):
        q = await broadcaster.subscribe()

        async def gen():
            snap = core.get_snapshot()
            yield f"data: {json.dumps({'type': 'snapshot', 'data': snap})}\n\n"
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=25.0)
                        yield f"data: {json.dumps(msg)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                await broadcaster.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/gmail/connect")
    async def gmail_connect(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        email_addr = str(body.get("email") or "").strip()
        app_password = str(body.get("app_password") or "").strip()
        if not email_addr or not app_password:
            return JSONResponse(
                {"ok": False, "error": "email and app_password are required"},
                status_code=400,
            )
        try:
            saved = await asyncio.to_thread(connect_gmail, core.conn, email_addr, app_password)
            core.add_log(f"Gmail connected via IMAP as {saved}", "info")
            return JSONResponse({"ok": True, "email": saved})
        except Exception as e:
            core.add_log(f"Gmail connect failed: {e}", "error")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/gmail/disconnect")
    async def gmail_disconnect():
        disconnect_gmail(core.conn)
        core.add_log("Gmail disconnected", "info")
        return JSONResponse({"ok": True})

    @app.get("/api/gmail/status")
    async def gmail_status():
        return JSONResponse(gmail_connection(core.conn))

    @app.get("/api/mail/accounts")
    async def mail_accounts():
        return JSONResponse(mail_accounts_status(core.conn))

    @app.post("/api/mail/connect")
    async def mail_connect(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        provider = str(body.get("provider") or "").strip().lower()
        email_addr = str(body.get("email") or "").strip()
        password = str(body.get("password") or body.get("app_password") or "").strip()
        if provider not in ("gmail", "proton"):
            return JSONResponse(
                {"ok": False, "error": "provider must be gmail or proton"},
                status_code=400,
            )
        if not email_addr or not password:
            return JSONResponse(
                {"ok": False, "error": "email and password are required"},
                status_code=400,
            )
        try:
            saved = await asyncio.to_thread(
                connect_mail_account,
                core.conn,
                provider=provider,
                email_addr=email_addr,
                password=password,
                imap_host=body.get("imap_host"),
                imap_port=body.get("imap_port"),
            )
            label = saved.get("label", provider)
            core.add_log(f"{label} connected via IMAP as {saved['email']}", "info")
            return JSONResponse({"ok": True, "account": saved})
        except Exception as e:
            core.add_log(f"{provider} connect failed: {e}", "error")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/mail/disconnect")
    async def mail_disconnect(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        provider = str(body.get("provider") or "").strip().lower()
        if provider not in ("gmail", "proton"):
            return JSONResponse(
                {"ok": False, "error": "provider must be gmail or proton"},
                status_code=400,
            )
        disconnect_mail_account(core.conn, provider)
        core.add_log(f"{provider} disconnected", "info")
        return JSONResponse({"ok": True})

    @app.get("/api/settings")
    async def api_settings_get():
        return JSONResponse({
            "poll_interval": core.get_poll_interval(),
            "alert_cooldown": core.get_alert_cooldown(),
            "alerts_enabled": core.get_alerts_enabled(),
            "important_alert_mode": core.get_important_alert_mode(),
            "other_alert_mode": core.get_other_alert_mode(),
            "important_senders": core.get_important_senders(),
            "gmail": mail_accounts_status(core.conn)["gmail"],
            "proton": mail_accounts_status(core.conn)["proton"],
            "mail_accounts": mail_accounts_status(core.conn),
            "chatterbox_tts": core.get_snapshot()["chatterbox_tts"],
            "demo_mode": core.get_demo_mode(),
            "calendar_extract_concurrency": core.get_calendar_extract_concurrency(),
            "calendar_ollama_main_gpu": core.get_calendar_ollama_main_gpu(),
        })

    @app.post("/api/settings")
    async def api_settings_set(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if "poll_interval" in body:
            core.set_poll_interval(float(body["poll_interval"]))
        if "alert_cooldown" in body:
            core.set_alert_cooldown(float(body["alert_cooldown"]))
        if "alerts_enabled" in body:
            core.set_alerts_enabled(bool(body["alerts_enabled"]))
        if "important_alert_mode" in body:
            core.set_important_alert_mode(body.get("important_alert_mode"))
        if "other_alert_mode" in body:
            core.set_other_alert_mode(body.get("other_alert_mode"))
        if "demo_mode" in body:
            core.set_demo_mode(bool(body.get("demo_mode")))
        if "calendar_extract_concurrency" in body:
            try:
                core.set_calendar_extract_concurrency(body["calendar_extract_concurrency"])
            except ValueError as e:
                return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        if "calendar_ollama_main_gpu" in body:
            try:
                core.set_calendar_ollama_main_gpu(body.get("calendar_ollama_main_gpu"))
            except ValueError as e:
                return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        return JSONResponse({
            "ok": True,
            "poll_interval": core.get_poll_interval(),
            "alert_cooldown": core.get_alert_cooldown(),
            "alerts_enabled": core.get_alerts_enabled(),
            "important_alert_mode": core.get_important_alert_mode(),
            "other_alert_mode": core.get_other_alert_mode(),
            "demo_mode": core.get_demo_mode(),
            "calendar_extract_concurrency": core.get_calendar_extract_concurrency(),
            "calendar_ollama_main_gpu": core.get_calendar_ollama_main_gpu(),
        })

    @app.get("/api/important-senders")
    async def api_important_senders_list():
        return JSONResponse({"senders": core.get_important_senders()})

    @app.post("/api/important-senders")
    async def api_important_senders_add(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        sender = str(body.get("sender") or "").strip()
        if not sender:
            return JSONResponse({"ok": False, "error": "sender required"}, status_code=400)
        try:
            entry = core.mark_sender_important(sender)
            return JSONResponse({"ok": True, "sender": entry})
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.delete("/api/important-senders/{sender_key}")
    async def api_important_senders_remove(sender_key: str):
        removed = core.unmark_sender_important(sender_key)
        if not removed:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return JSONResponse({"ok": True})

    @app.get("/api/senders")
    async def api_senders_ranked():
        ranked = core.get_ranked_sender_interest()
        return JSONResponse({
            "ok": True,
            "upvoted": ranked.get("upvoted") or [],
            "downvoted": ranked.get("downvoted") or [],
        })

    @app.get("/api/storage")
    async def api_storage():
        view = core.get_storage_view()
        return JSONResponse({"ok": True, **view})

    @app.get("/api/process")
    async def api_process():
        view = core.get_process_view()
        return JSONResponse({"ok": True, **view})

    @app.post("/api/process/fetch")
    async def api_process_fetch(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        unit = str(body.get("unit") or "days").strip().lower()
        lookback_raw = body.get("lookback", body.get("days" if unit == "days" else "hours", 5))
        try:
            lookback = int(lookback_raw)
        except (TypeError, ValueError):
            lookback = 5
        replace = bool(body.get("replace") or body.get("delete_first"))
        if unit == "hours":
            started = core.start_mail_fetch(hours=lookback, replace=replace)
        else:
            started = core.start_mail_fetch(days=lookback, replace=replace)
        view = core.get_process_view()
        if not started and not core.get_demo_mode():
            if core.mail_fetch_running():
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "A mail fetch is already running",
                        **view,
                    },
                    status_code=409,
                )
            return JSONResponse(
                {"ok": False, "error": "Could not start mail fetch", **view},
                status_code=400,
            )
        return JSONResponse({"ok": True, **view})

    @app.get("/api/search")
    async def api_search(request: Request):
        query = str(request.query_params.get("q") or "").strip()
        try:
            limit = int(request.query_params.get("limit", "50"))
        except (TypeError, ValueError):
            limit = 50
        try:
            offset = int(request.query_params.get("offset", "0"))
        except (TypeError, ValueError):
            offset = 0
        if not query:
            return JSONResponse(
                {
                    "ok": True,
                    "query": "",
                    "results": [],
                    "total": 0,
                    "limit": max(1, min(limit, 200)),
                    "offset": max(0, offset),
                    "demo_mode": core.get_demo_mode(),
                }
            )
        data = core.search_emails(query, limit=limit, offset=offset)
        return JSONResponse({"ok": True, **data})

    @app.post("/api/search/emails/{email_id}/calendar")
    async def api_search_email_calendar(email_id: str, request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        force = bool(body.get("force", True))
        try:
            result = await core.extract_calendar_for_email_id(email_id, force=force)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        return JSONResponse({"ok": True, **result})

    @app.get("/api/calendar/queue")
    async def api_calendar_queue(request: Request):
        try:
            list_days = int(request.query_params.get("list_days", "30"))
        except (TypeError, ValueError):
            list_days = 30
        queue = core.get_calendar_queue_stats(list_days=list_days)
        return JSONResponse({"ok": True, "queue": queue})

    @app.get("/api/calendar")
    async def api_calendar(request: Request):
        try:
            list_days = int(request.query_params.get("list_days", "30"))
        except (TypeError, ValueError):
            list_days = 30
        view_mode = request.query_params.get("view", "week")
        anchor_date = request.query_params.get("anchor") or None
        view = core.get_calendar_view(
            list_days=list_days,
            view_mode=view_mode,
            anchor_date=anchor_date,
        )
        return JSONResponse({"ok": True, **view})

    def _parse_calendar_lookback(body: dict[str, Any]) -> tuple[int | None, int | None, int]:
        unit = str(body.get("unit") or "days").strip().lower()
        if body.get("hours") is not None:
            try:
                hours = int(body.get("hours"))
            except (TypeError, ValueError):
                hours = 24
            list_days = max(1, min(365, (hours + 23) // 24))
            return None, hours, list_days
        raw = body.get("lookback", body.get("days", 5))
        try:
            lookback = int(raw)
        except (TypeError, ValueError):
            lookback = 5
        if unit == "hours":
            list_days = max(1, min(365, (lookback + 23) // 24))
            return None, lookback, list_days
        list_days = max(1, min(365, lookback))
        return lookback, None, list_days

    @app.post("/api/calendar/backfill")
    async def api_calendar_backfill(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        days, hours, default_list_days = _parse_calendar_lookback(body)
        force = bool(body.get("force", False))
        import_from_source = bool(body.get("import_from_source", False))
        started = core.start_calendar_backfill(
            days=days,
            hours=hours,
            force=force,
            import_from_source=import_from_source,
        )
        try:
            list_days = int(body.get("list_days", default_list_days))
        except (TypeError, ValueError):
            list_days = default_list_days
        view = core.get_calendar_view(list_days=list_days)
        if not started and not core.get_demo_mode():
            if core.calendar_scan_running():
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "A calendar scan is already running",
                        **view,
                    },
                    status_code=409,
                )
        return JSONResponse({"ok": True, "started": started, **view})

    @app.post("/api/calendar/queue/clear")
    async def api_calendar_queue_clear(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if core.get_demo_mode():
            return JSONResponse(
                {"ok": False, "error": "Demo mode — calendar queue clear disabled"},
                status_code=400,
            )
        days, hours, default_list_days = _parse_calendar_lookback(body)
        result = core.clear_calendar_queue(days=days, hours=hours)
        try:
            list_days = int(body.get("list_days", default_list_days))
        except (TypeError, ValueError):
            list_days = default_list_days
        view = core.get_calendar_view(list_days=list_days)
        process_view = core.get_process_view()
        return JSONResponse(
            {
                "ok": True,
                **result,
                **view,
                "pipeline": process_view.get("pipeline") or {},
                "logs": process_view.get("logs") or [],
            }
        )

    @app.post("/api/calendar/reprocess")
    async def api_calendar_reprocess(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        days, hours, default_list_days = _parse_calendar_lookback(body)
        started = core.start_calendar_reprocess(days=days, hours=hours)
        try:
            list_days = int(body.get("list_days", default_list_days))
        except (TypeError, ValueError):
            list_days = default_list_days
        view = core.get_calendar_view(list_days=list_days)
        if not started and not core.get_demo_mode():
            if core.calendar_scan_running():
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "A calendar scan is already running",
                        **view,
                    },
                    status_code=409,
                )
        return JSONResponse(
            {
                "ok": True,
                "started": started,
                **view,
                "logs": core.get_process_view().get("logs") or [],
            }
        )

    @app.post("/api/summary/queue/clear")
    async def api_summary_queue_clear(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if core.get_demo_mode():
            return JSONResponse(
                {"ok": False, "error": "Demo mode — summary queue clear disabled"},
                status_code=400,
            )
        days, hours, _default_list_days = _parse_calendar_lookback(body)
        result = core.clear_summary_queue(days=days, hours=hours)
        view = core.get_process_view()
        return JSONResponse(
            {
                "ok": True,
                **result,
                "pipeline": view.get("pipeline") or {},
                "logs": view.get("logs") or [],
            }
        )

    @app.post("/api/pipelines/kickstart")
    async def api_pipelines_kickstart(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if core.get_demo_mode():
            return JSONResponse(
                {"ok": False, "error": "Demo mode — pipeline kickstart disabled"},
                status_code=400,
            )
        try:
            calendar_days = int(body.get("calendar_days", 30))
        except (TypeError, ValueError):
            calendar_days = 30
        calendar_days = max(1, min(calendar_days, 365))
        result = await core.kickstart_pipelines(calendar_days=calendar_days)
        view = core.get_process_view()
        return JSONResponse(
            {
                "ok": True,
                **result,
                "pipeline": view.get("pipeline") or {},
                "logs": view.get("logs") or [],
            }
        )

    @app.post("/api/pipelines/investigate")
    async def api_pipelines_investigate(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            list_days = int(body.get("list_days", 30))
        except (TypeError, ValueError):
            list_days = 30
        list_days = max(1, min(list_days, 365))
        result = await core.investigate_pipelines(list_days=list_days)
        view = core.get_process_view()
        return JSONResponse(
            {
                "ok": True,
                **result,
                "pipeline": view.get("pipeline") or result.get("pipeline") or {},
                "logs": view.get("logs") or [],
            }
        )

    @app.post("/api/pipelines/tts-investigate")
    async def api_pipelines_tts_investigate(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        test_synthesis = body.get("test_synthesis", True)
        if isinstance(test_synthesis, str):
            test_synthesis = test_synthesis.strip().lower() not in ("0", "false", "no")
        else:
            test_synthesis = bool(test_synthesis)
        result = await core.investigate_tts(test_synthesis=test_synthesis)
        view = core.get_process_view()
        return JSONResponse(
            {
                "ok": True,
                **result,
                "logs": view.get("logs") or [],
            }
        )

    @app.post("/api/pipelines/tts-restart")
    async def api_pipelines_tts_restart(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        verify = body.get("verify", True)
        if isinstance(verify, str):
            verify = verify.strip().lower() not in ("0", "false", "no")
        else:
            verify = bool(verify)
        result = await core.restart_chatterbox_tts(verify=verify)
        view = core.get_process_view()
        status = 200 if result.get("ok") else 400
        return JSONResponse(
            {
                **result,
                "logs": view.get("logs") or [],
            },
            status_code=status,
        )

    @app.post("/api/summary/reprocess")
    async def api_summary_reprocess(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if core.get_demo_mode():
            return JSONResponse(
                {"ok": False, "error": "Demo mode — summary reprocess disabled"},
                status_code=400,
            )
        days, hours, _default_list_days = _parse_calendar_lookback(body)
        started = core.start_summary_reprocess(days=days, hours=hours)
        view = core.get_process_view()
        if not started and not core.get_demo_mode():
            if core.summary_backfill_running():
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "A summary backfill is already running",
                        "pipeline": view.get("pipeline") or {},
                    },
                    status_code=409,
                )
        return JSONResponse(
            {
                "ok": True,
                "started": started,
                "pipeline": view.get("pipeline") or {},
                "logs": view.get("logs") or [],
            }
        )

    @app.post("/api/calendar/events/{event_id}/vote")
    async def api_calendar_event_vote(event_id: str, request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        vote = str(body.get("vote") or "").strip().lower()
        if vote not in ("up", "down"):
            return JSONResponse(
                {"ok": False, "error": "vote must be 'up' or 'down'"},
                status_code=400,
            )
        try:
            result = core.vote_calendar_event(event_id, vote=vote)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        if result is None:
            return JSONResponse({"ok": False, "error": "event not found"}, status_code=404)
        return JSONResponse({"ok": True, **result})

    @app.post("/api/calendar/events/{event_id}/remove")
    async def api_calendar_event_remove(event_id: str):
        result = core.remove_calendar_event(event_id)
        if result is None:
            return JSONResponse({"ok": False, "error": "event not found"}, status_code=404)
        return JSONResponse({"ok": True, **result})

    @app.get("/api/calendar/ignored-senders")
    async def api_calendar_ignored_senders_list():
        return JSONResponse(
            {
                "ok": True,
                "ignored_senders": core.list_calendar_ignored_senders_for_display(),
            }
        )

    @app.delete("/api/calendar/ignored-senders/{sender_key}")
    async def api_calendar_ignored_senders_remove(sender_key: str):
        if not core.remove_calendar_ignored_sender_entry(sender_key):
            return JSONResponse(
                {"ok": False, "error": "sender not found"},
                status_code=404,
            )
        return JSONResponse({"ok": True, "sender_key": sender_key})

    @app.get("/api/tts/voice")
    async def api_tts_voice_get():
        cfg = core.chatterbox_tts_config
        return JSONResponse({
            "enabled": cfg.get("enabled", False),
            "chosen": core.get_event_tts_voice(),
            "delivery_mode": core.get_event_tts_delivery_mode(),
            "tts_model": core.get_event_tts_model(),
            "alert_greeting_name": core.get_alert_greeting_name(),
            "alert_greeting_enabled": core.get_alert_greeting_enabled(),
            "voice_summary_enabled": core.get_voice_summary_enabled(),
            "voice_style_prompt": core.get_voice_style_system_prompt(),
            "voice_style_prompt_file": core.get_voice_style_prompt_file(),
            "voice_style_default_prompt": core.get_default_voice_style_prompt(),
            "saved_voice_style_prompts": core.list_voice_style_prompts(),
            "default_test_message": cfg.get("default_test_message"),
        })

    @app.post("/api/tts/voice")
    async def api_tts_voice_set(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if "voice_mode" in body and "voice" in body:
            core.set_event_tts_voice(
                voice_mode=str(body.get("voice_mode") or ""),
                voice=str(body.get("voice") or ""),
            )
        if "delivery_mode" in body:
            core.set_event_tts_delivery_mode(body.get("delivery_mode"))
        if "tts_model" in body:
            core.set_event_tts_model(body.get("tts_model"))
        if "alert_greeting_name" in body or "alert_greeting_enabled" in body:
            core.set_alert_greeting(
                name=body.get("alert_greeting_name") if "alert_greeting_name" in body else None,
                enabled=body.get("alert_greeting_enabled") if "alert_greeting_enabled" in body else None,
            )
        if "voice_summary_enabled" in body:
            core.set_voice_summary_enabled(bool(body.get("voice_summary_enabled")))
        if "voice_style_prompt" in body:
            prompt = str(body.get("voice_style_prompt") or "").strip()
            if prompt:
                core.set_voice_style_system_prompt(prompt, clear_source_file=True)
        return JSONResponse({
            "ok": True,
            "chosen": core.get_event_tts_voice(),
            "alert_greeting_name": core.get_alert_greeting_name(),
            "alert_greeting_enabled": core.get_alert_greeting_enabled(),
            "voice_summary_enabled": core.get_voice_summary_enabled(),
            "voice_style_prompt": core.get_voice_style_system_prompt(),
            "voice_style_prompt_file": core.get_voice_style_prompt_file(),
        })

    @app.get("/api/tts/voices")
    async def api_tts_voices():
        cfg = core.chatterbox_tts_config
        if not cfg.get("enabled"):
            return JSONResponse({"ok": False, "error": "Chatterbox disabled"}, status_code=503)
        try:
            voices = await list_chatterbox_voices(cfg)
            return JSONResponse({
                "ok": True,
                "voices": voices,
                "models": list(CHATTERBOX_TTS_MODELS),
                "chosen": core.get_event_tts_voice(),
                "delivery_mode": core.get_event_tts_delivery_mode(),
                "tts_model": core.get_event_tts_model(),
                "alert_greeting_name": core.get_alert_greeting_name(),
                "alert_greeting_enabled": core.get_alert_greeting_enabled(),
                "voice_summary_enabled": core.get_voice_summary_enabled(),
            })
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    @app.get("/api/tts/voice-style-prompts")
    async def api_voice_style_prompts_list():
        return JSONResponse({
            "ok": True,
            "directory": str(core.get_prompts_dir()),
            "saved_prompts": core.list_voice_style_prompts(),
            "active_prompt_file": core.get_voice_style_prompt_file(),
            "prompt": core.get_voice_style_system_prompt(),
            "default_prompt": core.get_default_voice_style_prompt(),
        })

    @app.post("/api/tts/voice-style-prompts/load")
    async def api_voice_style_prompt_load(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        filename = str(body.get("filename") or "").strip()
        if not filename:
            return JSONResponse({"ok": False, "error": "filename required"}, status_code=400)
        try:
            saved = core.load_voice_style_prompt_file(filename)
            return JSONResponse({
                "ok": True,
                "filename": filename,
                "prompt": saved,
                "active_prompt_file": core.get_voice_style_prompt_file(),
            })
        except FileNotFoundError:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/tts/voice-style-prompts/save")
    async def api_voice_style_prompt_save(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        name = str(body.get("name") or "").strip()
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            return JSONResponse({"ok": False, "error": "prompt required"}, status_code=400)
        try:
            filename = core.save_voice_style_prompt_to_file(name or "voice_style", prompt=prompt)
            core.set_voice_style_system_prompt(prompt, source_file=filename)
            return JSONResponse({
                "ok": True,
                "filename": filename,
                "saved_prompts": core.list_voice_style_prompts(),
                "active_prompt_file": core.get_voice_style_prompt_file(),
            })
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/tts/speak")
    async def api_tts_speak(request: Request):
        cfg = core.chatterbox_tts_config
        if not cfg.get("enabled"):
            return JSONResponse({"ok": False, "error": "Chatterbox disabled"}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            body = {}

        voice_style_prompt = None
        if "voice_style_prompt" in body:
            raw_prompt = str(body.get("voice_style_prompt") or "").strip()
            if raw_prompt:
                voice_style_prompt = raw_prompt

        voice_style_prompt_file = None
        if "voice_style_prompt_file" in body:
            raw_file = str(body.get("voice_style_prompt_file") or "").strip()
            if raw_file:
                voice_style_prompt_file = raw_file

        greeting_name = None
        if "alert_greeting_name" in body:
            greeting_name = str(body.get("alert_greeting_name") or "").strip()
        greeting_enabled = None
        if "alert_greeting_enabled" in body:
            greeting_enabled = bool(body.get("alert_greeting_enabled"))

        voice_mode = None
        voice = None
        if "voice_mode" in body and "voice" in body:
            voice_mode = str(body.get("voice_mode") or "").strip()
            voice = str(body.get("voice") or "").strip()

        try:
            _base, spoken, _debug = await core.compose_test_alert_speech(
                voice_summary_enabled=body.get("voice_summary_enabled")
                if "voice_summary_enabled" in body
                else None,
                delivery_mode=body.get("delivery_mode")
                if "delivery_mode" in body
                else None,
                alert_greeting_name=greeting_name,
                alert_greeting_enabled=greeting_enabled,
                voice_style_prompt=voice_style_prompt,
                voice_style_prompt_file=voice_style_prompt_file,
                voice_mode=voice_mode,
                voice=voice,
                tts_model=body.get("tts_model") if "tts_model" in body else None,
            )
        except Exception as e:
            core.add_log(f"Test speak — compose failed: {e}", "error")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

        if not spoken:
            return JSONResponse({"ok": False, "error": "no speech text produced"}, status_code=400)

        speak_cfg = core.get_event_tts_settings()
        if "voice_mode" in body and "voice" in body:
            mode = str(body.get("voice_mode") or "").strip().lower()
            voice = str(body.get("voice") or "").strip()
            if mode in ("clone", "predefined") and voice:
                speak_cfg = apply_voice_override(
                    speak_cfg,
                    voice_mode=mode,
                    voice=voice,
                )
        if "tts_model" in body:
            speak_cfg = apply_tts_model_settings(
                speak_cfg,
                normalize_tts_model(body.get("tts_model")),
            )
        if "delivery_mode" in body:
            speak_cfg = apply_delivery_mode_settings(
                speak_cfg,
                normalize_delivery_mode(body.get("delivery_mode")),
            )

        try:
            audio, media_type, from_cache, saved_path = await get_or_synthesize_speech(
                spoken, settings=speak_cfg
            )
            if from_cache:
                core.add_log(f"Test speak — TTS cache hit: {saved_path}", "info")
            else:
                core.add_log(f"Test speak — TTS generated: {saved_path}", "info")
            return Response(
                content=audio,
                media_type=media_type,
                headers={"X-TTS-Cache": "hit" if from_cache else "miss"},
            )
        except Exception as e:
            core.add_log(f"Test speak — TTS failed: {e}", "error")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    @app.get("/api/recordings")
    async def api_recordings_list():
        cache_dir = _recordings_cache_dir()
        root = resolve_recordings_dir(cache_dir)
        recordings = [
            row for row in list_recordings(cache_dir) if row.get("kind") != "phrase"
        ]
        return JSONResponse(
            {
                "ok": True,
                "directory": str(root),
                "phrases_directory": str(phrases_dir(cache_dir)),
                "total": len(recordings),
                "recordings": recordings,
            }
        )

    @app.get("/api/recordings/{filename:path}")
    async def api_recording(filename: str, download: bool = False):
        cache_dir = _recordings_cache_dir()
        safe = safe_recording_relpath(filename)
        if not safe:
            return JSONResponse({"ok": False, "error": "invalid filename"}, status_code=400)
        path = recording_file_path(safe, cache_dir=cache_dir)
        if path is None or not path.is_file() or path.stat().st_size <= 0:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        media_type = media_type_for_filename(safe)
        disposition = "attachment" if download else "inline"
        headers = {
            "Content-Disposition": f'{disposition}; filename="{Path(safe).name}"',
            "Cache-Control": "public, max-age=86400",
        }
        return Response(content=path.read_bytes(), media_type=media_type, headers=headers)

    @app.post("/api/recordings/delete")
    async def api_recordings_delete(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        names = body.get("filenames") or body.get("files") or []
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, list) or not names:
            return JSONResponse(
                {"ok": False, "error": "filenames array required"},
                status_code=400,
            )
        cache_dir = _recordings_cache_dir()
        result = delete_recordings([str(n) for n in names], cache_dir=cache_dir)
        deleted = result.get("deleted") or []
        errors = result.get("errors") or {}
        if deleted:
            core.add_log(
                f"Deleted {len(deleted)} TTS recording(s) from {cache_dir}",
                "info",
            )
        if errors and not deleted:
            return JSONResponse(
                {
                    "ok": False,
                    "deleted": deleted,
                    "errors": errors,
                    "error": "no files deleted",
                },
                status_code=400,
            )
        return JSONResponse(
            {
                "ok": True,
                "deleted": deleted,
                "errors": errors,
                "remaining": len(list_recordings(cache_dir)),
            }
        )

    @app.get("/api/phrases")
    async def api_phrases_catalog():
        cache_dir = _recordings_cache_dir()
        speak_cfg = core.get_event_tts_settings()
        root = resolve_recordings_dir(cache_dir)
        phrase_files = {
            row["filename"]: row
            for row in list_recordings(cache_dir)
            if row.get("kind") == "phrase"
        }
        catalog: list[dict[str, Any]] = []
        for entry in all_delivery_phrases():
            text = entry["text"]
            mode = entry["mode"]
            spoken = spoken_delivery_phrase(text, mode, tts_model=speak_cfg.get("tts_model"))
            expected = phrase_recording_path(text, mode, settings=speak_cfg)
            rel_name = str(expected.relative_to(root)).replace("\\", "/")
            row = phrase_files.get(rel_name)
            recorded = row is not None
            catalog.append(
                {
                    "mode": mode,
                    "mode_label": delivery_mode_label(mode),
                    "text": text,
                    "spoken_text": spoken,
                    "filename": rel_name if recorded else None,
                    "recorded": recorded,
                    "size_bytes": row.get("size_bytes", 0) if row else 0,
                    "modified_at": row.get("modified_at") if row else None,
                }
            )
        recorded_count = sum(1 for item in catalog if item["recorded"])
        return JSONResponse(
            {
                "ok": True,
                "phrases_directory": str(phrases_dir(cache_dir)),
                "total": len(catalog),
                "recorded": recorded_count,
                "phrases": catalog,
            }
        )

    @app.post("/api/phrases/generate")
    async def api_phrases_generate(request: Request):
        cfg = core.chatterbox_tts_config
        if not cfg.get("enabled"):
            return JSONResponse({"ok": False, "error": "Chatterbox disabled"}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            body = {}
        mode_filter = normalize_delivery_mode(body.get("mode")) if body.get("mode") else None
        text_filter = str(body.get("text") or "").strip()
        missing_only = bool(body.get("missing_only", True))
        speak_cfg = core.get_event_tts_settings()
        targets = all_delivery_phrases()
        if mode_filter and mode_filter != "normal":
            targets = [p for p in targets if p["mode"] == mode_filter]
        if text_filter:
            targets = [p for p in targets if p["text"] == text_filter]
        if not targets:
            return JSONResponse({"ok": False, "error": "no phrases matched"}, status_code=400)

        generated: list[str] = []
        skipped: list[str] = []
        errors: dict[str, str] = {}
        for entry in targets:
            text = entry["text"]
            mode = entry["mode"]
            path = phrase_recording_path(text, mode, settings=speak_cfg)
            if missing_only and path.is_file() and path.stat().st_size > 0:
                skipped.append(text)
                continue
            spoken = spoken_delivery_phrase(text, mode, tts_model=speak_cfg.get("tts_model"))
            phrase_cfg = dict(speak_cfg)
            phrase_cfg["delivery_mode"] = mode
            try:
                _audio, _media_type, from_cache, saved_path = await get_or_synthesize_phrase(
                    spoken,
                    phrase=text,
                    mode=mode,
                    settings=phrase_cfg,
                )
                rel = str(Path(saved_path).relative_to(resolve_recordings_dir(_recordings_cache_dir()))).replace("\\", "/")
                generated.append(rel)
                if from_cache:
                    core.add_log(f"Phrase TTS cache hit: {rel}", "info")
                else:
                    core.add_log(f"Phrase TTS generated: {rel}", "info")
            except Exception as e:
                errors[text] = str(e)
                core.add_log(f"Phrase TTS failed ({text!r}): {e}", "warning")

        return JSONResponse(
            {
                "ok": True,
                "generated": generated,
                "skipped": skipped,
                "errors": errors,
            }
        )

    @app.get("/api/llm")
    async def api_llm_get():
        return JSONResponse(await core.get_llm_state())

    @app.post("/api/llm/model")
    async def api_llm_set_model(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        model = str(body.get("model") or "").strip()
        if not model:
            return JSONResponse({"ok": False, "error": "model required"}, status_code=400)
        try:
            saved = core.set_ollama_model(model)
            core.add_log(f"Ollama model set to {saved}", "info")
            return JSONResponse({"ok": True, "model": saved})
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/llm/spam")
    async def api_llm_set_spam(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        model = str(body.get("model") or "").strip()
        if not model:
            return JSONResponse({"ok": False, "error": "model required"}, status_code=400)
        try:
            saved = core.set_spam_ollama_model(model)
            gpu = core.get_spam_ollama_main_gpu()
            if "main_gpu" in body:
                gpu = core.set_spam_ollama_main_gpu(body.get("main_gpu"))
            core.add_log(
                f"Spam Ollama model set to {saved}"
                + (f" (GPU {gpu})" if gpu is not None else " (auto GPU)"),
                "info",
            )
            return JSONResponse(
                {
                    "ok": True,
                    "model": saved,
                    "spam_main_gpu": gpu,
                }
            )
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/llm/prompt")
    async def api_llm_set_prompt(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            return JSONResponse({"ok": False, "error": "prompt required"}, status_code=400)
        try:
            saved = core.set_summary_system_prompt(prompt, clear_source_file=True)
            core.add_log("Email summary prompt updated", "info")
            return JSONResponse({
                "ok": True,
                "prompt": saved,
                "is_custom_prompt": True,
                "prompt_source": core.get_prompt_source(),
            })
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/llm/prompt/reset")
    async def api_llm_reset_prompt():
        core.reset_summary_system_prompt()
        core.add_log("Email summary prompt reset to default", "info")
        return JSONResponse({
            "ok": True,
            "prompt": core.get_summary_system_prompt(),
            "is_custom_prompt": False,
            "prompt_source": "default",
        })

    @app.get("/api/llm/prompts")
    async def api_llm_prompts_list():
        return JSONResponse({
            "ok": True,
            "directory": str(core.get_prompts_dir()),
            "saved_prompts": core.list_saved_prompts(),
            "active_prompt_file": core.get_summary_prompt_file(),
            "prompt_source": core.get_prompt_source(),
        })

    @app.get("/api/llm/prompts/{filename}")
    async def api_llm_prompt_get(filename: str):
        try:
            text = read_prompt_file(filename, prompts_dir=core.get_prompts_dir())
        except FileNotFoundError:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "filename": filename, "prompt": text})

    @app.post("/api/llm/prompts/load")
    async def api_llm_prompt_load(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        filename = str(body.get("filename") or "").strip()
        if not filename:
            return JSONResponse({"ok": False, "error": "filename required"}, status_code=400)
        try:
            saved = core.load_summary_prompt_file(filename)
            core.add_log(f"Loaded summary prompt from {filename}", "info")
            return JSONResponse({
                "ok": True,
                "filename": filename,
                "prompt": saved,
                "is_custom_prompt": True,
                "prompt_source": core.get_prompt_source(),
                "active_prompt_file": core.get_summary_prompt_file(),
            })
        except FileNotFoundError:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/llm/prompts/save")
    async def api_llm_prompt_save(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        name = str(body.get("name") or "").strip()
        filename = str(body.get("filename") or "").strip()
        prompt = str(body.get("prompt") or "").strip()
        overwrite = bool(body.get("overwrite"))
        if not prompt:
            return JSONResponse({"ok": False, "error": "prompt required"}, status_code=400)
        try:
            if overwrite:
                if not filename:
                    return JSONResponse(
                        {"ok": False, "error": "filename required"},
                        status_code=400,
                    )
                saved_name = core.overwrite_summary_prompt_file(filename, prompt)
            else:
                if not name:
                    return JSONResponse(
                        {"ok": False, "error": "name required"},
                        status_code=400,
                    )
                saved_name = core.save_summary_prompt_to_file(name, prompt)
            core.add_log(f"Saved summary prompt to {saved_name}", "info")
            return JSONResponse({
                "ok": True,
                "filename": saved_name,
                "saved_prompts": core.list_saved_prompts(),
            })
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.delete("/api/llm/prompts/{filename}")
    async def api_llm_prompt_delete(filename: str):
        try:
            core.delete_summary_prompt_file(filename)
            core.add_log(f"Deleted summary prompt file {filename}", "info")
            return JSONResponse({
                "ok": True,
                "saved_prompts": core.list_saved_prompts(),
                "active_prompt_file": core.get_summary_prompt_file(),
                "prompt_source": core.get_prompt_source(),
            })
        except FileNotFoundError:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.get("/api/emails/{email_id}")
    async def api_get_email(email_id: str):
        row = core.get_email_for_display(email_id)
        if row is None:
            return JSONResponse({"ok": False, "error": "email not found"}, status_code=404)
        return JSONResponse({"ok": True, "email": row})

    @app.post("/api/emails/{email_id}/vote")
    async def api_vote_email(email_id: str, request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        vote = str(body.get("vote") or "").strip().lower()
        if vote not in ("up", "down"):
            return JSONResponse({"ok": False, "error": "vote must be 'up' or 'down'"}, status_code=400)
        try:
            result = core.vote_email_sender(email_id, vote=vote)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        if result is None:
            return JSONResponse({"ok": False, "error": "email not found"}, status_code=404)
        return JSONResponse({"ok": True, **result})

    @app.post("/api/emails/{email_id}/star")
    async def api_star_email(email_id: str):
        try:
            result = core.star_email(email_id)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        if result is None:
            return JSONResponse({"ok": False, "error": "email not found"}, status_code=404)
        return JSONResponse({"ok": True, **result})

    @app.post("/api/emails/{email_id}/calendar")
    async def api_email_calendar(email_id: str, request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        force = bool(body.get("force", True))
        try:
            result = await core.extract_calendar_for_email_id(email_id, force=force)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        return JSONResponse({"ok": True, **result})

    @app.post("/api/summarize/{email_id}")
    async def api_summarize(email_id: str):
        summary, err = await core.summarize_one(email_id)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=502)
        if not core.save_email_summary(email_id, summary):
            return JSONResponse({"ok": False, "error": "email not found"}, status_code=404)
        return JSONResponse({"ok": True, "summary": summary})

    return app