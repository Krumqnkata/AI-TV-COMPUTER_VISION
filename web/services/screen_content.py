"""Build the public, non-personal information feed for managed screens."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from engine.admin_models import Announcement, Substitution
from engine.db import Event, now_bg, today_bg


PUBLIC_AUDIENCES = {"", "*", "all", "всички"}


def _audience_matches(value: str | None, configured: str) -> bool:
    target = (value or "all").strip().casefold()
    audience = (configured or "all").strip().casefold()
    return target in PUBLIC_AUDIENCES or target == audience


def _bool_setting(settings: dict[str, Any], key: str, default: bool = True) -> bool:
    value = settings.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def build_screen_feed(db: Session, settings: dict[str, Any]) -> dict[str, Any]:
    now = now_bg()
    audience = str(settings.get("screen_audience") or "all")
    slides: list[dict[str, Any]] = []

    if _bool_setting(settings, "show_announcements"):
        announcements = db.query(Announcement).filter(
            Announcement.published.is_(True),
            Announcement.archived_at.is_(None),
            Announcement.publish_from <= now,
        ).order_by(Announcement.publish_from.desc()).all()
        priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
        announcements = sorted(
            (
                item
                for item in announcements
                if (item.publish_until is None or item.publish_until > now)
                and _audience_matches(item.audience, audience)
            ),
            key=lambda item: (
                priority_order.get((item.priority or "normal").casefold(), 2),
                -item.id,
            ),
        )
        for item in announcements[:12]:
            slides.append({
                "id": f"announcement:{item.id}",
                "type": "announcement",
                "title": item.title,
                "body": item.body,
                "category": item.category,
                "priority": item.priority,
                "publish_until": item.publish_until.isoformat() if item.publish_until else None,
            })

    if _bool_setting(settings, "show_events"):
        horizon = now + timedelta(days=7)
        events = db.query(Event).filter(
            Event.end_time >= now,
            Event.start_time <= horizon,
        ).order_by(Event.start_time).all()
        for item in events:
            if not _audience_matches(item.target_group, audience):
                continue
            slides.append({
                "id": f"event:{item.id}",
                "type": "event",
                "title": item.title,
                "body": item.description or "",
                "start_time": item.start_time.isoformat(),
                "end_time": item.end_time.isoformat(),
                "room": item.room,
            })

    if _bool_setting(settings, "show_substitutions"):
        substitutions = db.query(Substitution).filter(
            Substitution.date == today_bg(),
        ).order_by(Substitution.period, Substitution.class_name).all()
        for item in substitutions:
            slides.append({
                "id": f"substitution:{item.id}",
                "type": "substitution",
                "title": f"Заместване — {item.class_name}, {item.period}. час",
                "body": item.notes or "",
                "class_name": item.class_name,
                "period": item.period,
                "subject": item.subject,
                "replacement_teacher": (
                    item.replacement_teacher.full_name
                    if item.replacement_teacher
                    else None
                ),
                "room": item.room.code if item.room else None,
            })

    slides = slides[:30]
    canonical = json.dumps(slides, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    revision = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return {
        "revision": revision,
        "generated_at": now.isoformat(),
        "audience": audience,
        "slides": slides,
    }
