import json
import uuid

from sqlalchemy.orm import Session

from engine.db import (
    Badge,
    Camera,
    DeliveryReceipt,
    Message,
    SystemEvent,
    Timetable,
    hash_token,
    now_bg,
    today_bg,
)
from web.schemas import QRDetectionRequest
from web.services.runtime import runtime_registry
from web.services.admin_control import get_setting


def process_badge_detection(
    request: QRDetectionRequest,
    db: Session,
    *,
    session_timeout_seconds: int | None = None,
) -> dict:
    now = now_bg()
    token_hash = hash_token(request.badge_token)
    duplicate_reason = runtime_registry.check_duplicate(
        token_hash,
        request.camera_id,
        request.confidence,
        now,
        same_camera_seconds=int(get_setting(db, "qr.same_camera_seconds")),
        cross_camera_seconds=int(get_setting(db, "qr.cross_camera_seconds")),
    )
    if duplicate_reason:
        return {"status": "ignored", "reason": duplicate_reason}

    badge = db.query(Badge).filter(
        Badge.token_hash == token_hash,
        Badge.status == "active",
    ).first()
    if not badge:
        event_id = uuid.uuid4().hex
        db.add(SystemEvent(
            event_type="unknown_badge_detected",
            timestamp=now,
            metadata_json=json.dumps({
                "camera_id": request.camera_id,
                "zone_id": request.zone_id,
                "token_fingerprint": token_hash[:12],
            }, ensure_ascii=False),
        ))
        db.commit()
        return {
            "status": "error",
            "message": "Неразпознат или неактивен бадж",
            "ws_type": "unknown_badge",
            "ws_data": {
                "event_id": event_id,
                "camera_id": request.camera_id,
                "zone_id": request.zone_id,
            },
            "event_id": event_id,
            "zone_id": request.zone_id,
            "screen_id": None,
        }

    person = badge.person
    if not person or not person.active:
        return {"status": "error", "message": "Профилът е деактивиран"}

    camera = db.query(Camera).filter(Camera.name == request.camera_id).first()
    if camera and not camera.active:
        return {"status": "error", "message": "Камерата е деактивирана"}
    if camera and camera.zone_id != request.zone_id:
        return {"status": "error", "message": "Камерата не принадлежи към подадената зона"}

    point = camera.interaction_point if camera else None
    screen_id = point.screen_id if point and point.active else None
    interaction_point_id = point.id if point else None
    acquired, _session_key = runtime_registry.acquire_session(
        person.id,
        request.zone_id,
        interaction_point_id,
        screen_id,
        now,
        session_timeout_seconds=(
            int(session_timeout_seconds)
            if session_timeout_seconds is not None
            else int(get_setting(db, "sessions.kiosk_idle_seconds"))
        ),
    )
    if not acquired:
        return {"status": "ignored", "reason": "kiosk_busy"}

    runtime_registry.register_detection(
        token_hash,
        request.camera_id,
        request.confidence,
        now,
    )

    # Expired messages remain auditable but are no longer candidates for delivery.
    expired_messages = db.query(Message).filter(
        Message.recipient_id == person.id,
        Message.status == "active",
        Message.valid_until <= now,
    ).all()
    for message in expired_messages:
        message.status = "expired"

    pending_messages = db.query(Message).filter(
        Message.recipient_id == person.id,
        Message.status == "active",
        Message.valid_until > now,
    ).all()
    delivered_texts = [message.text for message in pending_messages]
    message_ids = [message.id for message in pending_messages]

    next_class = db.query(Timetable).filter(
        Timetable.person_id == person.id,
        Timetable.date == today_bg(),
        Timetable.start_time > now.time(),
    ).order_by(Timetable.start_time).first()

    next_class_info = None
    next_class_text = ""
    if next_class:
        next_class_info = {
            "subject": next_class.subject,
            "room": next_class.room,
            "start_time": next_class.start_time.strftime("%H:%M"),
            "end_time": next_class.end_time.strftime("%H:%M"),
            "class_name": next_class.class_name,
        }
        if person.role == "teacher":
            next_class_text = (
                f"Следващият Ви час е {next_class.subject} с {next_class.class_name} "
                f"в {next_class.room} от {next_class_info['start_time']} ч."
            )
        else:
            next_class_text = (
                f"Следващият ти час е {next_class.subject} в {next_class.room} "
                f"от {next_class_info['start_time']} ч."
            )

    if person.role == "student":
        greeting = f"Здравей, {person.full_name.split()[0]}!"
    elif person.role == "teacher":
        greeting = f"Здравейте, {person.full_name}!"
    elif person.role == "admin":
        greeting = f"Здравейте, администратор {person.full_name}!"
    else:
        greeting = f"Здравейте, {person.full_name}!"

    messages_text = ""
    if delivered_texts:
        prefix = "Имате" if person.role in ("teacher", "admin") else "Имаш"
        messages_text = f"{prefix} {len(delivered_texts)} нови съобщения: " + " | ".join(delivered_texts)

    welcome_message = " ".join(part for part in (greeting, messages_text, next_class_text) if part)
    event_id = uuid.uuid4().hex
    delivery_id = uuid.uuid4().hex

    if message_ids:
        db.add(DeliveryReceipt(
            delivery_id=delivery_id,
            person_id=person.id,
            screen_id=screen_id,
            zone_id=request.zone_id,
            message_ids_json=json.dumps(message_ids),
            status="pending",
            created_at=now,
        ))

    db.add(SystemEvent(
        event_type="badge_detected",
        camera_id=camera.id if camera else None,
        interaction_point_id=interaction_point_id,
        person_id=person.id,
        timestamp=now,
        metadata_json=json.dumps({
            "camera_identifier": request.camera_id,
            "zone_id": request.zone_id,
            "screen_id": screen_id,
            "event_id": event_id,
            "confidence": request.confidence,
            "delivery_id": delivery_id if message_ids else None,
            "message_ids": message_ids,
        }, ensure_ascii=False),
    ))
    db.commit()

    ws_data = {
        "event_id": event_id,
        "person_id": person.id,
        "name": person.full_name,
        "role": person.role,
        "class_name": person.class_name,
        "message": welcome_message,
        "next_class": next_class_info,
        "pending_messages_count": len(message_ids),
        "message_ids": message_ids,
        "delivery_id": delivery_id if message_ids else None,
        "zone_id": request.zone_id,
        "screen_id": screen_id,
    }
    return {
        "status": "success",
        "event_id": event_id,
        "person": {"id": person.id, "name": person.full_name, "role": person.role},
        "message": welcome_message,
        "messages_delivered": delivered_texts,
        "message_ids": message_ids,
        "delivery_id": delivery_id if message_ids else None,
        "next_class": next_class_info,
        "ws_type": "badge_detected",
        "ws_data": ws_data,
        "zone_id": request.zone_id,
        "screen_id": screen_id,
    }
