import asyncio
import json
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse

from engine.db import DeliveryReceipt, SystemEvent, today_bg
from utils.config import Config
from web.connections import connection_manager
from web.database import SessionLocal
from web.security import PWA_PROFILE_COOKIES
from web.services.device_control import (
    authenticate_device,
    context_allows_scope,
    device_config,
    record_websocket_event,
)
from web.services.delivery import acknowledge_delivery


router = APIRouter(tags=["system"])
templates = Jinja2Templates(directory=str(Config.PROJECT_ROOT / "web" / "templates"))


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("web/static/favicon.ico")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "pwa/landing.html")


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


def _same_origin_websocket(websocket: WebSocket) -> bool:
    """Reject cross-site browser sockets while allowing non-browser legacy nodes."""
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc == websocket.headers.get("host")


def _scoped_acknowledgment(
    delivery_id: str,
    message_ids: list[int],
    *,
    zone_id: str | None,
    screen_id: str | None,
    legacy: bool = False,
) -> dict:
    db = SessionLocal()
    try:
        receipt = db.query(DeliveryReceipt).filter(
            DeliveryReceipt.delivery_id == delivery_id,
        ).first()
        if receipt is None:
            return {"success": False, "reason": "unknown_delivery", "acknowledged_message_ids": []}
        if not legacy and (
            receipt.zone_id != zone_id
            or (receipt.screen_id and receipt.screen_id != screen_id)
        ):
            return {"success": False, "reason": "wrong_device_scope", "acknowledged_message_ids": []}
        return acknowledge_delivery(db, delivery_id, message_ids)
    finally:
        db.close()


async def _socket_message_loop(
    websocket: WebSocket,
    *,
    zone_id: str | None,
    screen_id: str | None,
    legacy: bool = False,
) -> None:
    while True:
        message = json.loads(await websocket.receive_text())
        await connection_manager.touch(websocket)
        if message.get("type") == "ack":
            delivery_id = str(message.get("delivery_id", "")).strip()
            message_ids = [int(value) for value in message.get("message_ids", [])]
            result = _scoped_acknowledgment(
                delivery_id,
                message_ids,
                zone_id=zone_id,
                screen_id=screen_id,
                legacy=legacy,
            )
            await websocket.send_text(json.dumps({
                "type": "ack_result",
                "data": {"delivery_id": delivery_id, **result},
            }, ensure_ascii=False))
        elif message.get("type") == "ping":
            await websocket.send_text('{"type":"pong"}')


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if not _same_origin_websocket(websocket):
        await websocket.close(code=1008, reason="Invalid origin")
        return
    await websocket.accept()
    registered = False
    registered_device_id = None
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
            legacy = device_context.legacy
        finally:
            auth_db.close()

        await connection_manager.register(websocket, screen_id, zone_id, registered_device_id)
        await asyncio.to_thread(
            _record_websocket_event,
            registered_device_id,
            True,
        )
        registered = True
        await websocket.send_text(json.dumps({
            "type": "registered",
            "data": {"screen_id": screen_id, "zone_id": zone_id, "device_id": registered_device_id},
        }, ensure_ascii=False))
        await _socket_message_loop(
            websocket,
            zone_id=zone_id,
            screen_id=screen_id,
            legacy=legacy,
        )
    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError, ValueError):
        pass
    finally:
        if registered:
            await connection_manager.disconnect(websocket)
            await asyncio.to_thread(
                _record_websocket_event,
                registered_device_id,
                False,
            )


async def _profile_websocket(websocket: WebSocket, profile: str) -> None:
    if not _same_origin_websocket(websocket):
        await websocket.close(code=1008, reason="Invalid origin")
        return

    identifier_cookie, key_cookie = PWA_PROFILE_COOKIES[profile]
    db = SessionLocal()
    try:
        context = authenticate_device(
            db,
            websocket.cookies.get(identifier_cookie),
            websocket.cookies.get(key_cookie),
        )
        if (
            context is None
            or context.device is None
            or context.device.device_type != profile
            or not context.device.zone_id
            or not context.device.screen_id
        ):
            context = None
            registration = None
        else:
            config = device_config(db, context)
            screen_mode = str(config["settings"].get("screen_mode", "public")).strip().casefold()
            registration = {
                "device_id": context.device.identifier,
                "zone_id": context.device.zone_id,
                "screen_id": context.device.screen_id,
                "profile": profile,
                "screen_mode": screen_mode if profile == "screen" else "interactive",
                "accepts_personal": profile == "kiosk" or screen_mode == "paired",
            }
    finally:
        db.close()

    if registration is None:
        await websocket.close(code=1008, reason="Device is not paired")
        return

    await websocket.accept()
    registered = False
    try:
        await connection_manager.register(
            websocket,
            registration["screen_id"],
            registration["zone_id"],
            registration["device_id"],
            profile=profile,
            accepts_personal=registration["accepts_personal"],
        )
        await asyncio.to_thread(
            _record_websocket_event,
            registration["device_id"],
            True,
        )
        registered = True
        await websocket.send_text(json.dumps({
            "type": "registered",
            "data": {
                key: value
                for key, value in registration.items()
                if key != "accepts_personal"
            },
        }, ensure_ascii=False))
        await _socket_message_loop(
            websocket,
            zone_id=registration["zone_id"],
            screen_id=registration["screen_id"],
        )
    except (WebSocketDisconnect, json.JSONDecodeError, ValueError):
        pass
    finally:
        if registered:
            await connection_manager.disconnect(websocket)
            await asyncio.to_thread(
                _record_websocket_event,
                registration["device_id"],
                False,
            )


@router.websocket("/ws/kiosk")
async def kiosk_websocket(websocket: WebSocket):
    await _profile_websocket(websocket, "kiosk")


@router.websocket("/ws/screen")
async def screen_websocket(websocket: WebSocket):
    await _profile_websocket(websocket, "screen")


def _record_websocket_event(
    device_identifier: str | None,
    connected: bool,
) -> None:
    with SessionLocal() as db:
        record_websocket_event(
            db,
            device_identifier,
            connected=connected,
        )
