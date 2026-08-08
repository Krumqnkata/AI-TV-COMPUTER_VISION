"""Select admin-curated jokes for badge greetings without an AI call."""

from __future__ import annotations

import secrets

from sqlalchemy.orm import Session

from engine.admin_models import BadgeJoke
from web.services.admin_control import get_setting


def _audiences_for_role(role: str | None) -> tuple[str, ...]:
    normalized = (role or "").strip().casefold()
    if normalized == "student":
        return ("all", "student")
    if normalized in {"teacher", "admin"}:
        return ("all", "teacher")
    return ("all",)


def select_badge_joke(db: Session, role: str | None) -> str | None:
    """Return an approved joke according to the admin-controlled frequency."""
    if not bool(get_setting(db, "features.badge_jokes_enabled")):
        return None

    frequency = int(get_setting(db, "features.badge_jokes_frequency"))
    if frequency <= 0 or secrets.randbelow(100) >= min(frequency, 100):
        return None

    jokes = db.query(BadgeJoke).filter(
        BadgeJoke.active.is_(True),
        BadgeJoke.audience.in_(_audiences_for_role(role)),
    ).all()
    if not jokes:
        return None

    joke = secrets.choice(jokes)
    return f"Шега за деня: {joke.text}"
