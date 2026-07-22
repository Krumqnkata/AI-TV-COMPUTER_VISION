import json
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from engine.auth import get_password_hash
from engine.db import (
    Badge,
    Camera,
    DeliveryReceipt,
    Event,
    InteractionPoint,
    Message,
    Person,
    SystemEvent,
    Timetable,
    hash_token,
    now_bg,
)
from web.database import get_db
from web.schemas import (
    BadgeStatusRequest,
    EventCreateRequest,
    PersonCreateRequest,
    PersonStatusRequest,
    TimetableCreateRequest,
)
from web.security import require_admin, require_device_or_staff


router = APIRouter(prefix="/api", tags=["administration"])


def _audit(db: Session, event_type: str, user_id: int | None, metadata: dict) -> None:
    db.add(SystemEvent(
        event_type=event_type,
        person_id=user_id,
        timestamp=now_bg(),
        metadata_json=json.dumps(metadata, ensure_ascii=False),
    ))


@router.get("/persons")
def list_persons(
    role: Optional[str] = None,
    db: Session = Depends(get_db),
    _access=Depends(require_device_or_staff),
):
    query = db.query(Person)
    if role:
        query = query.filter(Person.role == role)
    return [
        {"id": p.id, "full_name": p.full_name, "role": p.role, "class_name": p.class_name, "active": p.active}
        for p in query.order_by(Person.full_name).all()
    ]


@router.post("/persons")
def create_person(
    request: PersonCreateRequest,
    db: Session = Depends(get_db),
    admin: Person = Depends(require_admin),
):
    full_name = request.full_name.strip()
    if request.role not in {"student", "teacher", "admin", "guest"}:
        raise HTTPException(status_code=400, detail="Невалидна роля")
    if db.query(Person).filter(Person.full_name == full_name).first():
        raise HTTPException(status_code=409, detail="Потребител с това име вече съществува")
    person = Person(
        full_name=full_name,
        role=request.role,
        class_name=request.class_name.strip() if request.class_name else None,
        active=True,
        password_hash=get_password_hash(request.password) if request.password else None,
    )
    db.add(person)
    db.flush()
    _audit(db, "admin_person_created", admin.id, {"person_id": person.id, "role": person.role})
    db.commit()
    return {"success": True, "person_id": person.id, "full_name": person.full_name, "role": person.role, "class_name": person.class_name}


@router.get("/persons/{person_id}/timetable")
def get_timetable(
    person_id: int,
    date_str: Optional[str] = None,
    db: Session = Depends(get_db),
    _access=Depends(require_device_or_staff),
):
    query = db.query(Timetable).filter(Timetable.person_id == person_id)
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Датата трябва да е YYYY-MM-DD") from exc
        query = query.filter(Timetable.date == selected_date)
    return [
        {
            "id": row.id,
            "date": row.date.isoformat(),
            "period": row.period,
            "start_time": row.start_time.strftime("%H:%M"),
            "end_time": row.end_time.strftime("%H:%M"),
            "subject": row.subject,
            "class_name": row.class_name,
            "room": row.room,
        }
        for row in query.order_by(Timetable.date, Timetable.period).all()
    ]


@router.post("/persons/{person_id}/status")
def update_person_status(
    person_id: int,
    request: PersonStatusRequest,
    db: Session = Depends(get_db),
    admin: Person = Depends(require_admin),
):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Потребителят не съществува")
    person.active = request.active
    _audit(db, "admin_person_status_updated", admin.id, {"person_id": person.id, "active": person.active})
    db.commit()
    return {"success": True, "person_id": person.id, "active": person.active}


@router.delete("/persons/{person_id}")
def delete_person(
    person_id: int,
    db: Session = Depends(get_db),
    admin: Person = Depends(require_admin),
):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Потребителят не съществува")
    if person.id == admin.id:
        raise HTTPException(status_code=400, detail="Не можете да изтриете собствения си профил")
    db.query(SystemEvent).filter(SystemEvent.person_id == person_id).update({SystemEvent.person_id: None})
    db.query(DeliveryReceipt).filter(DeliveryReceipt.person_id == person_id).delete(synchronize_session=False)
    db.query(Message).filter(or_(Message.sender_id == person_id, Message.recipient_id == person_id)).delete(synchronize_session=False)
    db.query(Badge).filter(Badge.person_id == person_id).delete(synchronize_session=False)
    db.query(Timetable).filter(Timetable.person_id == person_id).delete(synchronize_session=False)
    name = person.full_name
    db.delete(person)
    _audit(db, "admin_person_deleted", admin.id, {"deleted_person_id": person_id, "name": name})
    db.commit()
    return {"success": True}


@router.get("/events")
def get_events(db: Session = Depends(get_db)):
    return [
        {
            "id": event.id,
            "title": event.title,
            "description": event.description,
            "start_time": event.start_time.isoformat(),
            "end_time": event.end_time.isoformat(),
            "target_group": event.target_group,
            "room": event.room,
        }
        for event in db.query(Event).order_by(Event.start_time).all()
    ]


@router.post("/events")
def create_event(
    request: EventCreateRequest,
    db: Session = Depends(get_db),
    admin: Person = Depends(require_admin),
):
    if request.end_time <= request.start_time:
        raise HTTPException(status_code=400, detail="Крайният час трябва да е след началния")
    event = Event(**request.model_dump())
    event.title = event.title.strip()
    db.add(event)
    db.flush()
    _audit(db, "admin_event_created", admin.id, {"event_id": event.id, "title": event.title})
    db.commit()
    return {"success": True, "event_id": event.id}


@router.delete("/events/{event_id}")
def delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    admin: Person = Depends(require_admin),
):
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Събитието не съществува")
    title = event.title
    db.delete(event)
    _audit(db, "admin_event_deleted", admin.id, {"event_id": event_id, "title": title})
    db.commit()
    return {"success": True}


@router.post("/timetable")
def create_timetable(
    request: TimetableCreateRequest,
    db: Session = Depends(get_db),
    admin: Person = Depends(require_admin),
):
    if not db.get(Person, request.person_id):
        raise HTTPException(status_code=404, detail="Потребителят не съществува")
    try:
        start_time = datetime.strptime(request.start_time, "%H:%M").time()
        end_time = datetime.strptime(request.end_time, "%H:%M").time()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Часът трябва да е HH:MM") from exc
    if end_time <= start_time:
        raise HTTPException(status_code=400, detail="Крайният час трябва да е след началния")
    record = Timetable(
        person_id=request.person_id,
        date=request.date,
        period=request.period,
        start_time=start_time,
        end_time=end_time,
        subject=request.subject.strip(),
        class_name=request.class_name,
        room=request.room.strip(),
    )
    db.add(record)
    db.flush()
    _audit(db, "admin_timetable_created", admin.id, {"record_id": record.id, "person_id": record.person_id})
    db.commit()
    return {"success": True, "record_id": record.id}


@router.delete("/timetable/{record_id}")
def delete_timetable(
    record_id: int,
    db: Session = Depends(get_db),
    admin: Person = Depends(require_admin),
):
    record = db.get(Timetable, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Записът не съществува")
    person_id = record.person_id
    db.delete(record)
    _audit(db, "admin_timetable_deleted", admin.id, {"record_id": record_id, "person_id": person_id})
    db.commit()
    return {"success": True}


@router.get("/badges")
def get_badges(
    db: Session = Depends(get_db),
    _admin: Person = Depends(require_admin),
):
    return [
        {
            "id": badge.id,
            "person_id": badge.person_id,
            "person_name": badge.person.full_name,
            "status": badge.status,
            "created_at": badge.created_at.isoformat(),
        }
        for badge in db.query(Badge).all()
    ]


@router.post("/persons/{person_id}/badge")
def generate_badge(
    person_id: int,
    db: Session = Depends(get_db),
    admin: Person = Depends(require_admin),
):
    if not db.get(Person, person_id):
        raise HTTPException(status_code=404, detail="Потребителят не съществува")
    for old_badge in db.query(Badge).filter(Badge.person_id == person_id, Badge.status == "active").all():
        old_badge.status = "disabled"
    token = f"SCH-{secrets.token_hex(16).upper()}"
    badge = Badge(person_id=person_id, token_hash=hash_token(token), status="active", created_at=now_bg())
    db.add(badge)
    db.flush()
    _audit(db, "admin_badge_generated", admin.id, {"badge_id": badge.id, "person_id": person_id})
    db.commit()
    return {"success": True, "badge_id": badge.id, "token": token, "status": badge.status}


@router.post("/badges/{badge_id}/status")
def update_badge_status(
    badge_id: int,
    request: BadgeStatusRequest,
    db: Session = Depends(get_db),
    admin: Person = Depends(require_admin),
):
    if request.status not in {"active", "lost", "disabled"}:
        raise HTTPException(status_code=400, detail="Невалиден статус")
    badge = db.get(Badge, badge_id)
    if not badge:
        raise HTTPException(status_code=404, detail="Баджът не съществува")
    old_status = badge.status
    badge.status = request.status
    _audit(db, "admin_badge_status_updated", admin.id, {"badge_id": badge.id, "old_status": old_status, "new_status": badge.status})
    db.commit()
    return {"success": True, "badge_id": badge.id, "status": badge.status}


@router.get("/messages")
def get_messages(
    db: Session = Depends(get_db),
    _admin: Person = Depends(require_admin),
):
    return [
        {
            "id": message.id,
            "sender_name": message.sender.full_name,
            "recipient_name": message.recipient.full_name,
            "text": message.text,
            "valid_until": message.valid_until.isoformat(),
            "delivered_at": message.delivered_at.isoformat() if message.delivered_at else None,
            "status": message.status,
        }
        for message in db.query(Message).order_by(Message.id.desc()).all()
    ]


@router.get("/cameras")
def get_cameras(
    db: Session = Depends(get_db),
    _access=Depends(require_device_or_staff),
):
    return [
        {"id": c.id, "name": c.name, "zone_id": c.zone_id, "interaction_point_id": c.interaction_point_id, "active": c.active}
        for c in db.query(Camera).order_by(Camera.name).all()
    ]


@router.get("/interaction_points")
def get_interaction_points(
    db: Session = Depends(get_db),
    _access=Depends(require_device_or_staff),
):
    return [
        {"id": p.id, "name": p.name, "zone_id": p.zone_id, "type": p.type, "screen_id": p.screen_id, "active": p.active}
        for p in db.query(InteractionPoint).order_by(InteractionPoint.name).all()
    ]


@router.get("/admin/audit")
def get_audit(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _admin: Person = Depends(require_admin),
):
    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "person_id": event.person_id,
            "timestamp": event.timestamp.isoformat(),
            "metadata": json.loads(event.metadata_json) if event.metadata_json else {},
        }
        for event in db.query(SystemEvent).order_by(SystemEvent.timestamp.desc()).limit(limit).all()
    ]
