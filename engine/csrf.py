import secrets
import hmac
from fastapi import Request
from typing import Optional

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"

def generate_csrf_token() -> str:
    """Generate a secure random CSRF token."""
    return secrets.token_urlsafe(32)

def _get_token_from_request(request: Request) -> Optional[str]:
    # Prefer header, fallback to cookie
    token = request.headers.get(CSRF_HEADER_NAME)
    if not token:
        token = request.cookies.get(CSRF_COOKIE_NAME)
    return token

def verify_csrf_token(request: Request) -> bool:
    """Validate CSRF token from request.
    Returns True if token exists and matches the cookie value.
    """
    token = _get_token_from_request(request)
    if not token:
        return False
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not cookie_token:
        return False
    return hmac.compare_digest(token, cookie_token)
