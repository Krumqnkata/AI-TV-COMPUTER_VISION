from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from engine.admin_models import StaffAccount


def session_has_permission(request: Request, permission: str) -> bool:
    return permission in set(request.session.get("permissions", []))


def require_session_permission(request: Request, permission: str) -> None:
    if not session_has_permission(request, permission):
        raise HTTPException(status_code=403, detail="Нямате право за това действие")


def current_staff(db: Session, request: Request) -> StaffAccount:
    account = db.get(StaffAccount, request.session.get("staff_id"))
    if account is None or not account.active:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Сесията е невалидна")
    return account


def request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:64]
    return request.client.host if request.client else None
