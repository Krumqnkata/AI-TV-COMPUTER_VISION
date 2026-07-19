import os
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from typing import Optional

from engine.auth import decode_access_token

# Lazy import to avoid circular deps — get_db lives in web/server.py
def _get_db_from_server():
    from web.server import get_db
    return get_db


def _extract_token(request: Request) -> Optional[str]:
    """Extract JWT from Authorization header (Bearer) or from session storage fallback header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


def get_current_user(request: Request, db: Session = Depends(lambda: None)):
    """Dependency that extracts and validates the JWT, returns the Person record."""
    # We can't use a lambda for db — we inject it via closure trick below
    raise NotImplementedError("Use get_current_user_dep instead")


def _make_get_current_user(get_db_func):
    """Factory that produces a get_current_user dependency bound to a specific get_db."""
    def get_current_user(request: Request, db: Session = Depends(get_db_func)):
        from engine.db import Person
        token = _extract_token(request)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        payload = decode_access_token(token)
        if payload is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_id: Optional[int] = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
        user = db.query(Person).filter(Person.id == user_id).first()
        if user is None or not user.active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
        return user
    return get_current_user


def _make_require_admin(get_db_func):
    """Factory that produces a require_admin dependency bound to a specific get_db."""
    _get_current_user = _make_get_current_user(get_db_func)
    def require_admin_dep(current_user=Depends(_get_current_user)):
        if current_user.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        return current_user
    return require_admin_dep


# These will be overridden by server.py after it creates its own get_db
# We provide simple placeholder versions here that server.py will replace:
class _DeferredDep:
    """Lazy dependency that reads itself from a module-level variable."""
    def __call__(self, request: Request):
        raise HTTPException(status_code=503, detail="Auth not yet initialized")

require_admin = _DeferredDep()
