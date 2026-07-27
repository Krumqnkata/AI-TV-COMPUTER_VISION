"""Create deterministic fixtures for the dedicated Playwright server process."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engine.admin_models import Announcement, DirectoryEntry
from engine.auth import get_password_hash
from engine.db import InteractionPoint, Person, now_bg
from tests.fixtures import seed_test_data
from web.database import SessionLocal
from web.services.admin_control import (
    ensure_admin_foundation,
    provision_staff_from_person,
    update_settings,
)
from web.services.device_control import create_enrollment_token


def main() -> None:
    admin_password = os.environ.pop("PWA_E2E_ADMIN_PASSWORD", "")
    if len(admin_password) < 20:
        raise RuntimeError("Missing secure E2E administrator fixture password")
    with SessionLocal() as db:
        seed_test_data(db)
        ensure_admin_foundation(db)
        admin_person = db.query(Person).filter(Person.role == "admin").one()
        admin_person.password_hash = get_password_hash(admin_password)
        actor = provision_staff_from_person(db, admin_person)
        actor.password_hash = admin_person.password_hash
        db.commit()
        update_settings(
            db,
            {"features.kiosk_auto_speak_answers": True},
            actor,
        )
        point = db.query(InteractionPoint).filter(
            InteractionPoint.name == "Главен вход - Екран",
        ).one()
        if actor is None:
            raise RuntimeError("Missing E2E staff fixture")

        _kiosk_token, kiosk_raw = create_enrollment_token(
            db,
            actor,
            label="Playwright kiosk",
            device_type="kiosk",
            expected_identifier=None,
            zone_id=point.zone_id,
            screen_id=point.screen_id,
            interaction_point_id=point.id,
            initial_config={
                "display_brightness": 100,
                "kiosk_idle_seconds": 15,
            },
            valid_minutes=60,
        )
        _screen_token, screen_raw = create_enrollment_token(
            db,
            actor,
            label="Playwright public screen",
            device_type="screen",
            expected_identifier=None,
            zone_id=point.zone_id,
            screen_id=point.screen_id,
            interaction_point_id=point.id,
            initial_config={
                "display_brightness": 100,
                "screen_mode": "public",
                "screen_audience": "all",
                "screen_rotation_seconds": 30,
                "show_announcements": True,
                "show_events": False,
                "show_substitutions": False,
            },
            valid_minutes=60,
        )
        db.add(Announcement(
            title="Playwright публична новина",
            body="Публичното съдържание се вижда без лични данни.",
            audience="all",
            priority="normal",
            publish_from=now_bg(),
            published=True,
        ))
        db.add(DirectoryEntry(
            kind="faq",
            name="Кога отваря библиотеката?",
            value="Библиотеката отваря в 08:15 ч.",
            details="Работно време на библиотеката.",
            sort_order=1,
            active=True,
        ))
        db.commit()
        print(json.dumps({
            "kiosk_token": kiosk_raw,
            "screen_token": screen_raw,
            "admin_username": actor.username,
        }))


if __name__ == "__main__":
    main()
