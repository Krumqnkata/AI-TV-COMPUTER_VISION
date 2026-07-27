"""Cross-platform browser PWA contracts for kiosk and screen profiles."""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from engine.db import DeliveryReceipt, Message, Person, SystemEvent, now_bg
from utils.config import Config
from web.connections import connection_manager
from web.database import get_db
from web.schemas import (
    DeliveryAckRequest,
    DeviceCommandAckRequest,
    DeviceHeartbeatRequest,
    KioskDetectRequest,
    KioskMessageRequest,
    KioskQueryRequest,
    KioskQuerySuggestionsResponse,
    PwaPairRequest,
    QRDetectionRequest,
)
from web.security import (
    PWA_PROFILE_COOKIES,
    get_profile_device_context,
    require_profile_device,
)
from web.services.admin_control import get_setting
from web.services.assistant import handle_voice_command
from web.services.assistant_suggestions import build_kiosk_query_suggestions
from web.services.badges import process_badge_detection
from web.services.delivery import acknowledge_delivery
from web.services.device_control import (
    DeviceContext,
    acknowledge_command,
    device_config,
    enroll_device,
    pending_commands,
    revoke_device_context,
    update_heartbeat,
)
from web.services.metrics import metrics_registry
from web.services.runtime import ActiveSession, runtime_registry
from web.services.screen_content import build_screen_feed


router = APIRouter(tags=["browser PWA"])
require_kiosk = require_profile_device("kiosk")
require_screen = require_profile_device("screen")

PWA_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
PROFILE_CAPABILITIES = {
    "kiosk": ["audio", "camera", "kiosk", "qr", "screen", "tts"],
    "screen": ["screen"],
}


def _set_profile_cookies(
    response: Response,
    profile: str,
    device_id: str,
    device_key: str,
) -> None:
    identifier_cookie, key_cookie = PWA_PROFILE_COOKIES[profile]
    common = {
        "max_age": PWA_COOKIE_MAX_AGE,
        "httponly": True,
        "secure": Config.COOKIE_SECURE,
        "samesite": "strict",
        "path": "/",
    }
    response.set_cookie(identifier_cookie, device_id, **common)
    response.set_cookie(key_cookie, device_key, **common)
    response.headers["Cache-Control"] = "no-store"


def _clear_profile_cookies(response: Response, profile: str) -> None:
    identifier_cookie, key_cookie = PWA_PROFILE_COOKIES[profile]
    for name in (identifier_cookie, key_cookie):
        response.delete_cookie(
            name,
            path="/",
            secure=Config.COOKIE_SECURE,
            httponly=True,
            samesite="strict",
        )
    response.headers["Cache-Control"] = "no-store"


def _profile_settings(config: dict, profile: str) -> dict:
    settings = dict(config.get("settings") or {})
    settings.setdefault("display_brightness", 100)
    if profile == "screen":
        settings.setdefault("screen_mode", "public")
        settings.setdefault("screen_audience", "all")
        settings.setdefault("screen_rotation_seconds", 12)
        settings.setdefault("show_announcements", True)
        settings.setdefault("show_events", True)
        settings.setdefault("show_substitutions", True)
    config["settings"] = settings
    config["profile"] = profile
    config["server_time"] = now_bg().isoformat()
    return config


def _pair_profile(
    profile: str,
    request: PwaPairRequest,
    raw_token: str,
    response: Response,
    db: Session,
) -> dict:
    try:
        device, raw_key = enroll_device(
            db,
            enrollment_token=raw_token,
            identifier=request.identifier,
            name=request.name,
            device_type=profile,
            capabilities=PROFILE_CAPABILITIES[profile],
            software_version=request.software_version,
            require_interaction_point=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _set_profile_cookies(response, profile, device.identifier, raw_key)
    return {
        "success": True,
        "profile": profile,
        "device_id": device.identifier,
        "device_name": device.name,
        "zone_id": device.zone_id,
        "screen_id": device.screen_id,
        "camera_id": device.camera.name if device.camera else None,
        "interaction_point_id": device.interaction_point_id,
        "config_version": device.config_version,
    }


@router.post("/api/kiosk/pair")
def pair_kiosk(
    request: PwaPairRequest,
    response: Response,
    x_enrollment_token: str = Header(min_length=20, max_length=200),
    db: Session = Depends(get_db),
):
    return _pair_profile("kiosk", request, x_enrollment_token, response, db)


@router.post("/api/screen/pair")
def pair_screen(
    request: PwaPairRequest,
    response: Response,
    x_enrollment_token: str = Header(min_length=20, max_length=200),
    db: Session = Depends(get_db),
):
    return _pair_profile("screen", request, x_enrollment_token, response, db)


def _bootstrap(profile: str, db: Session, context: DeviceContext, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return _profile_settings(device_config(db, context), profile)


@router.get("/api/kiosk/bootstrap")
def kiosk_bootstrap(
    response: Response,
    context: DeviceContext = Depends(require_kiosk),
    db: Session = Depends(get_db),
):
    return _bootstrap("kiosk", db, context, response)


@router.get("/api/screen/bootstrap")
def screen_bootstrap(
    response: Response,
    context: DeviceContext = Depends(require_screen),
    db: Session = Depends(get_db),
):
    return _bootstrap("screen", db, context, response)


def _active_session(
    db: Session,
    context: DeviceContext,
    *,
    required: bool,
) -> ActiveSession | None:
    if context.device is None:
        if required:
            raise HTTPException(status_code=403, detail="Няма активна kiosk сесия")
        return None
    config = device_config(db, context)
    session_timeout = int(config["settings"]["kiosk_idle_seconds"])
    session = runtime_registry.current_session(
        now_bg(),
        zone_id=context.device.zone_id,
        screen_id=context.device.screen_id,
        session_timeout_seconds=session_timeout,
        touch=True,
    )
    if required and session is None:
        raise HTTPException(status_code=403, detail="Първо сканирайте своя QR бадж")
    return session


@router.post("/api/kiosk/detect")
async def kiosk_detect(
    request: KioskDetectRequest,
    context: DeviceContext = Depends(require_kiosk),
    db: Session = Depends(get_db),
):
    device = context.device
    if device is None or device.camera is None or not device.zone_id:
        raise HTTPException(status_code=409, detail="Киоскът няма зададена активна камера или зона")
    qr_request = QRDetectionRequest(
        camera_id=device.camera.name,
        zone_id=device.zone_id,
        badge_token=request.badge_token,
        timestamp=request.timestamp,
        confidence=request.confidence,
    )
    config = device_config(db, context)
    result = process_badge_detection(
        qr_request,
        db,
        session_timeout_seconds=int(config["settings"]["kiosk_idle_seconds"]),
    )
    metrics_registry.record_qr_result(str(result.get("status", "error")))
    ws_type = result.pop("ws_type", None)
    ws_data = result.pop("ws_data", None)
    zone_id = result.pop("zone_id", device.zone_id)
    screen_id = result.pop("screen_id", device.screen_id)
    if ws_type and ws_data:
        await connection_manager.send_to_screen_or_zone(
            {"type": ws_type, "data": ws_data},
            screen_id=screen_id,
            zone_id=zone_id,
        )
    return result


@router.post("/api/kiosk/query")
def kiosk_query(
    request: KioskQueryRequest,
    context: DeviceContext = Depends(require_kiosk),
    db: Session = Depends(get_db),
):
    if not bool(get_setting(db, "features.voice_enabled")):
        raise HTTPException(status_code=403, detail="Асистентът е изключен от администратора")
    session = _active_session(db, context, required=False)
    return handle_voice_command(
        session.person_id if session else None,
        request.text_query,
        db,
    )


@router.get(
    "/api/kiosk/query-suggestions",
    response_model=KioskQuerySuggestionsResponse,
)
def kiosk_query_suggestions(
    response: Response,
    context: DeviceContext = Depends(require_kiosk),
    db: Session = Depends(get_db),
):
    if not bool(get_setting(db, "features.voice_enabled")):
        raise HTTPException(
            status_code=403,
            detail="Асистентът е изключен от администратора",
        )
    session = _active_session(db, context, required=True)
    assert session is not None
    person = db.get(Person, session.person_id)
    if person is None or not person.active:
        raise HTTPException(status_code=403, detail="Профилът не е активен")
    response.headers["Cache-Control"] = "no-store"
    return build_kiosk_query_suggestions(db, person)


@router.get("/api/kiosk/recipients")
def kiosk_recipients(
    q: str = Query(default="", max_length=100),
    context: DeviceContext = Depends(require_kiosk),
    db: Session = Depends(get_db),
):
    session = _active_session(db, context, required=True)
    assert session is not None
    search = q.strip()
    query = db.query(Person).filter(
        Person.active.is_(True),
        Person.id != session.person_id,
    )
    if search:
        pattern = f"%{search}%"
        query = query.filter(or_(
            Person.full_name.ilike(pattern),
            Person.class_name.ilike(pattern),
        ))
    people = query.order_by(Person.full_name).limit(20).all()
    return [
        {
            "id": person.id,
            "display_name": person.full_name,
            "role": person.role,
            "class_name": person.class_name,
        }
        for person in people
    ]


@router.post("/api/kiosk/messages")
def kiosk_message(
    request: KioskMessageRequest,
    context: DeviceContext = Depends(require_kiosk),
    db: Session = Depends(get_db),
):
    session = _active_session(db, context, required=True)
    assert session is not None
    sender = db.get(Person, session.person_id)
    recipient = db.get(Person, request.recipient_id)
    if not sender or not sender.active or not recipient or not recipient.active:
        raise HTTPException(status_code=404, detail="Подателят или получателят не е активен")
    if sender.id == recipient.id:
        raise HTTPException(status_code=400, detail="Не можете да изпратите съобщение до себе си")
    message = Message(
        sender_id=sender.id,
        recipient_id=recipient.id,
        text=request.text,
        valid_until=now_bg() + timedelta(hours=request.valid_hours),
        status="active",
    )
    db.add(message)
    db.flush()
    db.add(SystemEvent(
        event_type="message_created",
        person_id=sender.id,
        timestamp=now_bg(),
        metadata_json=json.dumps(
            {"message_id": message.id, "recipient_id": recipient.id, "source": "kiosk_pwa"},
            ensure_ascii=False,
        ),
    ))
    db.commit()
    return {
        "success": True,
        "message_id": message.id,
        "recipient_name": recipient.full_name,
        "valid_until": message.valid_until.isoformat(),
    }


async def _close_profile_session(context: DeviceContext) -> dict:
    device = context.device
    if device is None:
        return {"success": False, "closed_count": 0}
    closed = runtime_registry.close(
        zone_id=device.zone_id,
        interaction_point_id=device.interaction_point_id,
        screen_id=device.screen_id,
    )
    payload = {"type": "session_closed", "data": {
        "zone_id": device.zone_id,
        "screen_id": device.screen_id,
    }}
    if device.screen_id:
        await connection_manager.send_to_screen(payload, device.screen_id)
    elif device.zone_id:
        await connection_manager.send_to_zone(payload, device.zone_id)
    return {"success": bool(closed), "closed_count": len(closed)}


@router.post("/api/kiosk/session/close")
async def kiosk_close_session(context: DeviceContext = Depends(require_kiosk)):
    return await _close_profile_session(context)


@router.post("/api/screen/session/close")
async def screen_close_session(context: DeviceContext = Depends(require_screen)):
    return await _close_profile_session(context)


def _ack_profile_delivery(
    request: DeliveryAckRequest,
    context: DeviceContext,
    db: Session,
) -> dict:
    receipt = db.query(DeliveryReceipt).filter(
        DeliveryReceipt.delivery_id == request.delivery_id,
    ).first()
    if receipt is None:
        raise HTTPException(status_code=404, detail="unknown_delivery")
    device = context.device
    if device is None or (
        receipt.zone_id != device.zone_id
        or (receipt.screen_id and receipt.screen_id != device.screen_id)
    ):
        raise HTTPException(status_code=403, detail="Доставката не принадлежи на този екран")
    result = acknowledge_delivery(db, request.delivery_id, request.message_ids)
    if not result["success"]:
        raise HTTPException(status_code=409, detail=result["reason"])
    return result


@router.post("/api/kiosk/deliveries/ack")
def kiosk_delivery_ack(
    request: DeliveryAckRequest,
    context: DeviceContext = Depends(require_kiosk),
    db: Session = Depends(get_db),
):
    return _ack_profile_delivery(request, context, db)


@router.post("/api/screen/deliveries/ack")
def screen_delivery_ack(
    request: DeliveryAckRequest,
    context: DeviceContext = Depends(require_screen),
    db: Session = Depends(get_db),
):
    return _ack_profile_delivery(request, context, db)


def _heartbeat_profile(
    request: DeviceHeartbeatRequest,
    context: DeviceContext,
    db: Session,
) -> dict:
    try:
        device = update_heartbeat(
            db,
            context,
            status=request.status,
            software_version=request.software_version,
            capabilities=request.capabilities,
            diagnostics=(
                request.diagnostics.model_dump(exclude_none=True)
                if request.diagnostics is not None
                else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "success": True,
        "device_id": device.identifier,
        "status": device.status,
        "config_version": device.config_version,
        "server_time": device.last_seen_at.isoformat(),
    }


@router.post("/api/kiosk/device/heartbeat")
def kiosk_heartbeat(
    request: DeviceHeartbeatRequest,
    context: DeviceContext = Depends(require_kiosk),
    db: Session = Depends(get_db),
):
    return _heartbeat_profile(request, context, db)


@router.post("/api/screen/device/heartbeat")
def screen_heartbeat(
    request: DeviceHeartbeatRequest,
    context: DeviceContext = Depends(require_screen),
    db: Session = Depends(get_db),
):
    return _heartbeat_profile(request, context, db)


def _pending_profile_commands(db: Session, context: DeviceContext) -> list[dict]:
    return [
        {
            "id": item.id,
            "command": item.command,
            "payload": json.loads(item.payload_json or "{}"),
            "created_at": item.created_at.isoformat(),
        }
        for item in pending_commands(db, context)
    ]


@router.get("/api/kiosk/device/commands/pending")
def kiosk_pending_commands(
    context: DeviceContext = Depends(require_kiosk),
    db: Session = Depends(get_db),
):
    return _pending_profile_commands(db, context)


@router.get("/api/screen/device/commands/pending")
def screen_pending_commands(
    context: DeviceContext = Depends(require_screen),
    db: Session = Depends(get_db),
):
    return _pending_profile_commands(db, context)


def _ack_profile_command(
    command_id: int,
    request: DeviceCommandAckRequest,
    context: DeviceContext,
    db: Session,
) -> dict:
    try:
        item = acknowledge_command(
            db,
            context,
            command_id,
            success=request.success,
            result=request.result,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"success": True, "command_id": item.id, "status": item.status}


@router.post("/api/kiosk/device/commands/{command_id}/ack")
def kiosk_command_ack(
    command_id: int,
    request: DeviceCommandAckRequest,
    context: DeviceContext = Depends(require_kiosk),
    db: Session = Depends(get_db),
):
    return _ack_profile_command(command_id, request, context, db)


@router.post("/api/screen/device/commands/{command_id}/ack")
def screen_command_ack(
    command_id: int,
    request: DeviceCommandAckRequest,
    context: DeviceContext = Depends(require_screen),
    db: Session = Depends(get_db),
):
    return _ack_profile_command(command_id, request, context, db)


@router.get("/api/screen/feed")
def screen_feed(
    request: Request,
    context: DeviceContext = Depends(require_screen),
    db: Session = Depends(get_db),
):
    config = _profile_settings(device_config(db, context), "screen")
    feed = build_screen_feed(db, config["settings"])
    etag = f'"{feed["revision"]}"'
    headers = {
        "ETag": etag,
        "Cache-Control": "private, max-age=0, must-revalidate",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(
        content=json.dumps(feed, ensure_ascii=False),
        media_type="application/json",
        headers=headers,
    )


def _unpair_profile(
    profile: str,
    request: Request,
    response: Response,
    db: Session,
) -> dict:
    context = get_profile_device_context(request, db, profile)
    if context is not None:
        revoke_device_context(db, context)
    _clear_profile_cookies(response, profile)
    return {"success": True, "profile": profile}


@router.post("/api/kiosk/unpair")
def unpair_kiosk(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    return _unpair_profile("kiosk", request, response, db)


@router.post("/api/screen/unpair")
def unpair_screen(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    return _unpair_profile("screen", request, response, db)
