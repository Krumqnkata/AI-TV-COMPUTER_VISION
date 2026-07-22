import hmac
import secrets
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from engine.csrf import CSRF_COOKIE_NAME, generate_csrf_token, verify_csrf_token
from engine.db import Person
from utils.config import Config
from web.database import get_db


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def verify_device_key_value(value: str | None) -> bool:
    expected = Config.DEVICE_API_KEY
    return bool(expected and value and hmac.compare_digest(value, expected))


def request_has_valid_device_key(request: Request) -> bool:
    return verify_device_key_value(request.headers.get("x-device-key"))


def require_device(request: Request) -> None:
    if not Config.DEVICE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DEVICE_API_KEY не е конфигуриран на сървъра",
        )
    if not request_has_valid_device_key(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалиден ключ на крайното устройство",
        )


def get_staff_session(
    request: Request,
    db: Session = Depends(get_db),
) -> Person:
    person_id = request.session.get("person_id")
    if not person_id:
        raise HTTPException(status_code=401, detail="Необходим е вход в административния панел")
    user = db.get(Person, person_id)
    if not user or not user.active or user.role not in ("admin", "teacher"):
        request.session.clear()
        raise HTTPException(status_code=401, detail="Административната сесия е невалидна")
    return user


def require_admin(user: Person = Depends(get_staff_session)) -> Person:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Само за администратори")
    return user


def require_device_or_staff(
    request: Request,
    db: Session = Depends(get_db),
) -> Person | None:
    if request_has_valid_device_key(request):
        return None
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
                if not has_device_credentials and not verify_csrf_token(request):
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


def ensure_runtime_secrets() -> None:
    if not Config.ADMIN_SECRET_KEY:
        # Development-only process secret. Production must provide a stable value
        # so sessions survive restarts.
        Config.ADMIN_SECRET_KEY = secrets.token_urlsafe(48)
