import hmac
import logging
import re
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from engine.csrf import CSRF_COOKIE_NAME, generate_csrf_token, verify_csrf_token
from engine.admin_models import StaffAccount
from engine.db import Person
from utils.config import Config
from utils.logger import bind_request_context, reset_request_context
from web.database import get_db
from web.services.admin_control import (
    has_permission,
    permissions_for_account,
    provision_staff_from_person,
)
from web.services.device_control import DeviceContext, authenticate_device
from web.services.metrics import metrics_registry


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PWA_PROFILE_COOKIES = {
    "kiosk": ("school_ai_kiosk_device_id", "school_ai_kiosk_device_key"),
    "screen": ("school_ai_screen_device_id", "school_ai_screen_device_key"),
}
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
_DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
request_logger = logging.getLogger("school_ai.http")


def verify_device_key_value(value: str | None) -> bool:
    expected = Config.DEVICE_API_KEY
    return bool(expected and value and hmac.compare_digest(value, expected))


def request_has_valid_device_key(request: Request) -> bool:
    return verify_device_key_value(request.headers.get("x-device-key"))


def require_device(
    request: Request,
    db: Session = Depends(get_db),
) -> DeviceContext:
    context = authenticate_device(
        db,
        request.headers.get("x-device-id"),
        request.headers.get("x-device-key"),
    )
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалиден ключ на крайното устройство",
        )
    return context


def get_profile_device_context(
    request: Request,
    db: Session,
    profile: str,
) -> DeviceContext | None:
    """Authenticate one browser PWA profile without exposing its secret to JS."""
    if profile not in PWA_PROFILE_COOKIES:
        return None
    identifier_cookie, key_cookie = PWA_PROFILE_COOKIES[profile]
    device_id = request.headers.get("x-device-id") or request.cookies.get(identifier_cookie)
    device_key = request.headers.get("x-device-key") or request.cookies.get(key_cookie)
    context = authenticate_device(db, device_id, device_key)
    if context is None or context.device is None or context.device.device_type != profile:
        return None
    return context


def require_profile_device(profile: str):
    def dependency(
        request: Request,
        db: Session = Depends(get_db),
    ) -> DeviceContext:
        context = get_profile_device_context(request, db, profile)
        if context is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Устройството не е сдвоено или ключът му е невалиден",
            )
        return context

    return dependency


def get_staff_session(
    request: Request,
    db: Session = Depends(get_db),
) -> StaffAccount:
    staff_id = request.session.get("staff_id")
    account = db.get(StaffAccount, staff_id) if staff_id else None
    if account is None and request.session.get("person_id"):
        person = db.get(Person, request.session["person_id"])
        if person and person.active and person.role in ("admin", "teacher") and person.password_hash:
            account = provision_staff_from_person(db, person)
            db.commit()
            db.refresh(account)
    if account is None:
        raise HTTPException(status_code=401, detail="Необходим е вход в административния панел")

    authenticated_at = request.session.get("authenticated_at")
    if authenticated_at:
        try:
            session_started = datetime.fromisoformat(authenticated_at)
            if session_started.tzinfo is None:
                session_started = session_started.replace(tzinfo=UTC)
            expired = session_started + timedelta(seconds=Config.ADMIN_SESSION_SECONDS) < datetime.now(UTC)
        except (TypeError, ValueError):
            expired = True
    else:
        expired = False  # compatibility with sessions issued before staff migration
    if not account.active or expired:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Административната сесия е невалидна")
    request.session["staff_id"] = account.id
    request.session["role_codes"] = [role.code for role in account.roles if role.active]
    request.session["permissions"] = sorted(permissions_for_account(account))
    return account


def require_admin(user: StaffAccount = Depends(get_staff_session)) -> StaffAccount:
    if not any(role.code in {"superadmin", "school_admin"} and role.active for role in user.roles):
        raise HTTPException(status_code=403, detail="Само за администратори")
    return user


def require_permission(permission_code: str):
    def dependency(user: StaffAccount = Depends(get_staff_session)) -> StaffAccount:
        if not has_permission(user, permission_code):
            raise HTTPException(status_code=403, detail="Нямате право за това действие")
        return user
    return dependency


def require_device_or_staff(
    request: Request,
    db: Session = Depends(get_db),
) -> StaffAccount | DeviceContext:
    context = authenticate_device(
        db,
        request.headers.get("x-device-id"),
        request.headers.get("x-device-key"),
    )
    if context is not None:
        return context
    return get_staff_session(request, db)


def _same_origin(request: Request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return False
    parsed = urlsplit(source)
    return parsed.netloc == request.headers.get("host")


def install_security_middleware(app) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        pwa_page = (
            request.url.path in {"/pair", "/kiosk", "/screen", "/kiosk-sw.js"}
            or request.url.path.startswith("/static/pwa/")
            or request.url.path.startswith("/manifest-")
        )
        if pwa_page:
            camera_policy = "camera=(self)" if request.url.path in {"/pair", "/kiosk"} else "camera=()"
            response.headers["Permissions-Policy"] = (
                f"{camera_policy}, microphone=(), geolocation=()"
            )
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
                "img-src 'self' data: blob:; media-src 'self' blob:; "
                "style-src 'self'; script-src 'self'; worker-src 'self'; "
                "manifest-src 'self'; connect-src 'self' ws: wss:"
            )
        else:
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' "
                "https://cdn.tailwindcss.com; script-src 'self' 'unsafe-inline' "
                "https://cdn.tailwindcss.com; connect-src 'self' ws: wss:"
            )
        return response

    @app.middleware("http")
    async def csrf_protection(request: Request, call_next):
        if request.method in UNSAFE_METHODS:
            if request.url.path.startswith("/admin"):
                if not _same_origin(request):
                    return JSONResponse({"detail": "Invalid request origin"}, status_code=403)
            elif request.url.path.startswith("/api"):
                has_device_credentials = request.headers.get("x-device-key") is not None
                is_enrollment = request.url.path == "/api/devices/enroll"
                if not has_device_credentials and not is_enrollment and not verify_csrf_token(request):
                    return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)

        response = await call_next(request)
        if request.method in ("GET", "HEAD") and not request.cookies.get(CSRF_COOKIE_NAME):
            response.set_cookie(
                key=CSRF_COOKIE_NAME,
                value=generate_csrf_token(),
                httponly=False,
                secure=Config.COOKIE_SECURE,
                samesite="strict",
            )
        return response

    @app.middleware("http")
    async def request_observability(request: Request, call_next):
        supplied_request_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if _REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else uuid.uuid4().hex
        )
        supplied_device_id = request.headers.get("x-device-id")
        profile = (
            "kiosk"
            if (
                request.url.path == "/kiosk"
                or request.url.path.startswith("/api/kiosk/")
            )
            else "screen"
            if (
                request.url.path == "/screen"
                or request.url.path.startswith("/api/screen/")
            )
            else None
        )
        if not supplied_device_id and profile:
            identifier_cookie, _key_cookie = PWA_PROFILE_COOKIES[profile]
            supplied_device_id = request.cookies.get(identifier_cookie)
        device_id = (
            supplied_device_id
            if supplied_device_id and _DEVICE_ID_PATTERN.fullmatch(supplied_device_id)
            else None
        )
        tokens = bind_request_context(request_id, device_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            request_logger.error(
                "HTTP request failed",
                extra={
                    "event": "http.request.failed",
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "http_status": 500,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        finally:
            duration = time.perf_counter() - started
            metrics_registry.record_http(request.method, status_code, duration)
            request_logger.info(
                "HTTP request completed",
                extra={
                    "event": "http.request.completed",
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "http_status": status_code,
                    "duration_ms": round(duration * 1000, 2),
                },
            )
            reset_request_context(tokens)


def ensure_runtime_secrets() -> None:
    if not Config.ADMIN_SECRET_KEY:
        # Development-only process secret. Production must provide a stable value
        # so sessions survive restarts.
        Config.ADMIN_SECRET_KEY = secrets.token_urlsafe(48)
        Config.ADMIN_SECRET_IS_EPHEMERAL = True
