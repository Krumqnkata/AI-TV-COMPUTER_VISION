from __future__ import annotations

import secrets
import threading
import time
from typing import Any


_store: dict[str, tuple[Any, float, int]] = {}
_lock = threading.Lock()
_TTL_SECONDS = 300


def store_once(value: Any, staff_id: int) -> str:
    ticket = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _lock:
        for key, (_value, created, _staff_id) in list(_store.items()):
            if now - created > _TTL_SECONDS:
                _store.pop(key, None)
        _store[ticket] = (value, now, staff_id)
    return ticket


def pop_once(ticket: str, staff_id: int) -> Any | None:
    with _lock:
        entry = _store.pop(ticket, None)
    if entry is None:
        return None
    value, created, owner_id = entry
    if owner_id != staff_id or time.monotonic() - created > _TTL_SECONDS:
        return None
    return value
