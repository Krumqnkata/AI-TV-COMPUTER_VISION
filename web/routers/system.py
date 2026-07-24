import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse

from engine.db import SystemEvent, today_bg
from utils.config import Config
from web.connections import connection_manager
from web.database import SessionLocal
from web.services.device_control import authenticate_device, context_allows_scope
from web.services.delivery import acknowledge_delivery


router = APIRouter(tags=["system"])
templates = Jinja2Templates(directory=str(Config.PROJECT_ROOT / "web" / "templates"))


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("web/static/favicon.ico")

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@router.get("/api/stats")
def get_stats():
    db = SessionLocal()
    try:
        today = today_bg()
        total = db.query(SystemEvent).filter(
            SystemEvent.event_type == "badge_detected",
            SystemEvent.timestamp >= datetime.combine(today, datetime.min.time()),
        ).count()
        return {"total": total, "status": "Online", "screens": connection_manager.count()}
    finally:
        db.close()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    registered = False
    try:
        raw_registration = await asyncio.wait_for(websocket.receive_text(), timeout=5)
        registration = json.loads(raw_registration)
        if registration.get("type") != "register":
            await websocket.close(code=1008, reason="Invalid device registration")
            return
        screen_id = str(registration.get("screen_id", "")).strip()
        zone_id = str(registration.get("zone_id", "")).strip()
        if not screen_id or not zone_id or len(screen_id) > 50 or len(zone_id) > 50:
            await websocket.close(code=1008, reason="Missing screen_id or zone_id")
            return

        auth_db = SessionLocal()
        try:
            device_context = authenticate_device(
                auth_db,
                str(registration.get("device_id", "")).strip() or None,
                registration.get("device_key"),
            )
            if device_context is None or not context_allows_scope(
                device_context,
                screen_id=screen_id,
                zone_id=zone_id,
            ):
                await websocket.close(code=1008, reason="Invalid device registration")
                return
            registered_device_id = device_context.device.identifier if device_context.device else None
        finally:
            auth_db.close()

        await connection_manager.register(websocket, screen_id, zone_id, registered_device_id)
        registered = True
        await websocket.send_text(json.dumps({
            "type": "registered",
            "data": {"screen_id": screen_id, "zone_id": zone_id, "device_id": registered_device_id},
        }, ensure_ascii=False))

        while True:
            message = json.loads(await websocket.receive_text())
            if message.get("type") == "ack":
                db = SessionLocal()
                try:
                    result = acknowledge_delivery(
                        db,
                        str(message.get("delivery_id", "")),
                        [int(value) for value in message.get("message_ids", [])],
                    )
                finally:
                    db.close()
                await websocket.send_text(json.dumps({
                    "type": "ack_result",
                    "data": {"delivery_id": message.get("delivery_id"), **result},
                }, ensure_ascii=False))
            elif message.get("type") == "ping":
                await websocket.send_text('{"type":"pong"}')
    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError, ValueError):
        pass
    finally:
        if registered:
            await connection_manager.disconnect(websocket)
