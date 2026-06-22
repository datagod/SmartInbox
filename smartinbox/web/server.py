"""FastAPI web server for SmartInbox."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.staticfiles import StaticFiles

from smartinbox import __version__
from smartinbox.chatterbox_models import CHATTERBOX_TTS_MODELS, normalize_tts_model
from smartinbox.chatterbox_tts import (
    apply_delivery_mode_settings,
    apply_tts_model_settings,
    apply_voice_override,
    get_or_synthesize_speech,
    list_chatterbox_voices,
)
from smartinbox.core import SmartInboxCore
from smartinbox.delivery_modes import apply_delivery_mode, normalize_delivery_mode
from smartinbox.gmail_imap import connect_gmail, disconnect_gmail, gmail_connection
from smartinbox.tts_recording_cache import media_type_for_filename, recording_file_path

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
            elif kind == "emails":
                asyncio.create_task(broadcaster.publish({"type": "emails", "data": payload}))
            elif kind == "email_alerts":
                asyncio.create_task(broadcaster.publish({"type": "email_alerts", "data": payload}))
            elif kind == "important_senders":
                asyncio.create_task(
                    broadcaster.publish({"type": "important_senders", "data": payload})
                )

        core.add_update_listener(_forward)
        await core.start()
        yield
        await core.stop()

    app = FastAPI(title="SmartInbox", version=__version__, lifespan=lifespan)
    app.state.core = core
    app.state.broadcaster = broadcaster

    static_dir = WEB_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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

    @app.get("/api/state")
    async def api_state():
        return JSONResponse(core.get_snapshot())

    @app.get("/api/health")
    async def api_health():
        return JSONResponse(await core.health())

    @app.post("/api/poll")
    async def api_poll():
        count = await core.poll_inbox()
        return JSONResponse({"ok": True, "new_count": count})

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
            core.add_log(f"Gmail connected via IMAP as {saved}", "success")
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

    @app.get("/api/settings")
    async def api_settings_get():
        return JSONResponse({
            "poll_interval": core.get_poll_interval(),
            "alert_cooldown": core.get_alert_cooldown(),
            "alerts_enabled": core.get_alerts_enabled(),
            "important_alert_mode": core.get_important_alert_mode(),
            "other_alert_mode": core.get_other_alert_mode(),
            "important_senders": core.get_important_senders(),
            "gmail": gmail_connection(core.conn),
            "chatterbox_tts": core.get_snapshot()["chatterbox_tts"],
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
        return JSONResponse({
            "ok": True,
            "poll_interval": core.get_poll_interval(),
            "alert_cooldown": core.get_alert_cooldown(),
            "alerts_enabled": core.get_alerts_enabled(),
            "important_alert_mode": core.get_important_alert_mode(),
            "other_alert_mode": core.get_other_alert_mode(),
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

    @app.get("/api/tts/voice")
    async def api_tts_voice_get():
        cfg = core.chatterbox_tts_config
        return JSONResponse({
            "enabled": cfg.get("enabled", False),
            "chosen": core.get_event_tts_voice(),
            "delivery_mode": core.get_event_tts_delivery_mode(),
            "tts_model": core.get_event_tts_model(),
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
        return JSONResponse({"ok": True, "chosen": core.get_event_tts_voice()})

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
            })
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    @app.post("/api/tts/speak")
    async def api_tts_speak(request: Request):
        cfg = core.chatterbox_tts_config
        if not cfg.get("enabled"):
            return JSONResponse({"ok": False, "error": "Chatterbox disabled"}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            body = {}
        text = str(body.get("text") or cfg.get("default_test_message") or "").strip()
        if not text:
            return JSONResponse({"ok": False, "error": "text required"}, status_code=400)
        delivery_mode = normalize_delivery_mode(
            body.get("delivery_mode") or core.get_event_tts_delivery_mode()
        )
        tts_model = core.get_event_tts_model()
        spoken = apply_delivery_mode(text, delivery_mode, tts_model=tts_model)
        speak_cfg = core.get_event_tts_settings()
        try:
            audio, media_type, from_cache, saved_path = await get_or_synthesize_speech(
                spoken, settings=speak_cfg
            )
            if from_cache:
                core.add_log(f"TTS test cache hit: {saved_path}", "info")
            else:
                core.add_log(f"TTS test generated: {saved_path}", "success")
            return Response(
                content=audio,
                media_type=media_type,
                headers={"X-TTS-Cache": "hit" if from_cache else "miss"},
            )
        except Exception as e:
            core.add_log(f"TTS test failed: {e}", "error")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=502)

    @app.get("/api/recordings/{filename}")
    async def api_recording(filename: str):
        cache_dir = str(core.chatterbox_tts_config.get("cache_dir", "localrecordings"))
        path = recording_file_path(filename, cache_dir=cache_dir)
        if path is None or not path.is_file():
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return Response(
            content=path.read_bytes(),
            media_type=media_type_for_filename(filename),
        )

    @app.post("/api/summarize/{email_id}")
    async def api_summarize(email_id: str):
        summary, err = await core.summarize_one(email_id)
        if err:
            return JSONResponse({"ok": False, "error": err}, status_code=502)
        from smartinbox.db import update_email_summary

        update_email_summary(core.conn, email_id, summary_detailed=summary, summary_short=summary[:500])
        return JSONResponse({"ok": True, "summary": summary})

    return app