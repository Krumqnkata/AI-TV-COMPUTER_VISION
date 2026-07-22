from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqladmin.authentication import AuthenticationBackend

from engine.admin_models import StaffAccount
from utils.config import Config
from web.admin.permissions import request_ip
from web.database import SessionLocal
from web.services.admin_control import authenticate_staff, get_setting, permissions_for_account


def _write_session(request: Request, account: StaffAccount, db, *, new_login: bool = False) -> None:
    if new_login:
        request.session.clear()
        request.session["authenticated_at"] = datetime.now(UTC).isoformat()
    request.session["staff_id"] = account.id
    request.session["display_name"] = account.display_name
    request.session["role_codes"] = [role.code for role in account.roles if role.active]
    request.session["permissions"] = sorted(permissions_for_account(account))
    request.session["school_name"] = get_setting(db, "school.name")
    request.session["accent"] = get_setting(db, "appearance.accent")
    request.session["compact"] = bool(get_setting(db, "appearance.compact"))


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        with SessionLocal() as db:
            account = authenticate_staff(db, username, password, ip_address=request_ip(request))
            if account is None:
                return False
            _write_session(request, account, db, new_login=True)
            return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        staff_id = request.session.get("staff_id")
        authenticated_at = request.session.get("authenticated_at")
        if not staff_id:
            return False
        try:
            session_started = datetime.fromisoformat(authenticated_at) if authenticated_at else datetime.now(UTC)
        except (TypeError, ValueError):
            request.session.clear()
            return False
        if session_started.tzinfo is None:
            session_started = session_started.replace(tzinfo=UTC)
        if session_started + timedelta(seconds=Config.ADMIN_SESSION_SECONDS) < datetime.now(UTC):
            request.session.clear()
            return False
        with SessionLocal() as db:
            account = db.get(StaffAccount, staff_id)
            if account is None or not account.active:
                request.session.clear()
                return False
            if account.linked_person and not account.linked_person.active:
                request.session.clear()
                return False
            _write_session(request, account, db)
            return True
