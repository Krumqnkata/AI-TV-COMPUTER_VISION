import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from engine.db import DeliveryReceipt, Message, Person, SystemEvent, now_bg
from web.connections import connection_manager
from web.database import get_db
from web.schemas import (
    CloseSessionRequest,
    DeliveryAckRequest,
    MessageCreateRequest,
    QRDetectionRequest,
    VoiceCommandRequest,
)
from web.security import require_device
from web.services.assistant import handle_voice_command
from web.services.admin_control import get_setting
from web.services.badges import process_badge_detection
from web.services.delivery import acknowledge_delivery
from web.services.device_control import DeviceContext, context_allows_scope
from web.services.runtime import runtime_registry


router = APIRouter(prefix="/api", tags=["devices"], dependencies=[Depends(require_device)])


def _require_scope(
    context: DeviceContext,
    *,
    zone_id: str | None = None,
    screen_id: str | None = None,
    camera_identifier: str | None = None,
    interaction_point_id: int | None = None,
) -> None:
    if not context_allows_scope(
        context,
        zone_id=zone_id,
        screen_id=screen_id,
        camera_identifier=camera_identifier,
        interaction_point_id=interaction_point_id,
    ):
        raise HTTPException(
            status_code=403,
            detail="Устройството няма достъп до тази точка, зона или екран",
        )


@router.post("/detect_qr")
async def detect_qr(
    request: QRDetectionRequest,
    db: Session = Depends(get_db),
    device: DeviceContext = Depends(require_device),
):
    _require_scope(device, zone_id=request.zone_id, camera_identifier=request.camera_id)
    result = process_badge_detection(request, db)
    ws_type = result.pop("ws_type", None)
    ws_data = result.pop("ws_data", None)
    zone_id = result.pop("zone_id", request.zone_id)
    screen_id = result.pop("screen_id", None)
    if ws_type and ws_data:
        await connection_manager.send_to_screen_or_zone(
            {"type": ws_type, "data": ws_data},
            screen_id=screen_id,
            zone_id=zone_id,
        )
    return result


@router.post("/sessions/close")
async def close_session(
    request: CloseSessionRequest,
    device: DeviceContext = Depends(require_device),
):
    if not any((request.zone_id, request.interaction_point_id, request.screen_id)):
        raise HTTPException(status_code=400, detail="Посочете зона, точка или екран")
    _require_scope(
        device,
        zone_id=request.zone_id,
        screen_id=request.screen_id,
        interaction_point_id=request.interaction_point_id,
    )
    closed = runtime_registry.close(
        zone_id=request.zone_id,
        interaction_point_id=request.interaction_point_id,
        screen_id=request.screen_id,
    )
    notified_screens: set[str] = set()
    notified_zones: set[str] = set()
    for session in closed:
        payload = {"type": "session_closed", "data": {
            "zone_id": session.zone_id,
            "screen_id": session.screen_id,
        }}
        if session.screen_id and session.screen_id not in notified_screens:
            await connection_manager.send_to_screen(payload, session.screen_id)
            notified_screens.add(session.screen_id)
        elif not session.screen_id and session.zone_id not in notified_zones:
            await connection_manager.send_to_zone(payload, session.zone_id)
            notified_zones.add(session.zone_id)
    return {"success": bool(closed), "closed_count": len(closed)}


@router.post("/messages")
def create_message(
    request: MessageCreateRequest,
    db: Session = Depends(get_db),
    device: DeviceContext = Depends(require_device),
):
    _require_scope(device, zone_id=request.zone_id, screen_id=request.screen_id)
    sender = db.get(Person, request.sender_id)
    recipient = db.get(Person, request.recipient_id)
    if not sender or not recipient or not sender.active or not recipient.active:
        raise HTTPException(status_code=404, detail="Подателят или получателят не е активен")
    if sender.id == recipient.id:
        raise HTTPException(status_code=400, detail="Не можете да изпратите съобщение до себе си")
    if not runtime_registry.person_has_session(
        sender.id,
        now_bg(),
        zone_id=request.zone_id,
        screen_id=request.screen_id,
        session_timeout_seconds=int(get_setting(db, "sessions.kiosk_idle_seconds")),
    ):
        raise HTTPException(status_code=403, detail="Подателят няма активна сесия на тази точка")
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
        metadata_json=json.dumps({"message_id": message.id, "recipient_id": recipient.id}),
    ))
    db.commit()
    return {
        "success": True,
        "message_id": message.id,
        "text": message.text,
        "valid_until": message.valid_until.isoformat(),
    }


@router.get("/messages/pending")
def get_pending_messages(
    person_id: int,
    db: Session = Depends(get_db),
    device: DeviceContext = Depends(require_device),
):
    if device.device and not runtime_registry.person_has_session(
        person_id,
        now_bg(),
        zone_id=device.device.zone_id,
        screen_id=device.device.screen_id,
        session_timeout_seconds=int(get_setting(db, "sessions.kiosk_idle_seconds")),
    ):
        raise HTTPException(status_code=403, detail="Няма активна сесия на това устройство")
    messages = db.query(Message).filter(
        Message.recipient_id == person_id,
        Message.status == "active",
        Message.valid_until > now_bg(),
    ).all()
    return [
        {
            "id": message.id,
            "sender_name": message.sender.full_name,
            "text": message.text,
            "valid_until": message.valid_until.isoformat(),
        }
        for message in messages
    ]


@router.post("/voice_command")
def voice_command(
    request: VoiceCommandRequest,
    db: Session = Depends(get_db),
    device: DeviceContext = Depends(require_device),
):
    _require_scope(device, zone_id=request.zone_id, screen_id=request.screen_id)
    if not bool(get_setting(db, "features.voice_enabled")):
        raise HTTPException(status_code=403, detail="Гласовият асистент е изключен от администратора")
    if request.person_id:
        if not runtime_registry.person_has_session(
            request.person_id,
            now_bg(),
            zone_id=request.zone_id,
            screen_id=request.screen_id,
            session_timeout_seconds=int(get_setting(db, "sessions.kiosk_idle_seconds")),
        ):
            raise HTTPException(status_code=403, detail="Потребителят няма активна сесия на тази точка")
        runtime_registry.touch_person(
            request.person_id,
            now_bg(),
            zone_id=request.zone_id,
            screen_id=request.screen_id,
            session_timeout_seconds=int(get_setting(db, "sessions.kiosk_idle_seconds")),
        )
    return handle_voice_command(request.person_id, request.text_query, db)


@router.post("/deliveries/ack")
def delivery_ack(
    request: DeliveryAckRequest,
    db: Session = Depends(get_db),
    device: DeviceContext = Depends(require_device),
):
    receipt = db.query(DeliveryReceipt).filter(DeliveryReceipt.delivery_id == request.delivery_id).first()
    if receipt:
        _require_scope(device, zone_id=receipt.zone_id, screen_id=receipt.screen_id)
    result = acknowledge_delivery(db, request.delivery_id, request.message_ids)
    if not result["success"]:
        raise HTTPException(status_code=404 if result["reason"] == "unknown_delivery" else 409, detail=result["reason"])
    return result
