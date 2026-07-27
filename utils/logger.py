"""Credential-safe structured logging and per-request log context."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from utils.config import Config


_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "school_ai_request_id",
    default=None,
)
_device_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "school_ai_device_id",
    default=None,
)
_configured = False

_EXTRA_FIELDS = (
    "event",
    "http_method",
    "http_path",
    "http_status",
    "duration_ms",
    "device_id",
    "request_id",
    "job_name",
    "command",
    "count",
    "error_type",
)


class JsonFormatter(logging.Formatter):
    """Render one compact JSON object without exception messages or tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or _request_id.get()
        device_id = getattr(record, "device_id", None) or _device_id.get()
        if request_id:
            payload["request_id"] = request_id
        if device_id:
            payload["device_id"] = device_id
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info and record.exc_info[0]:
            payload["error_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(
    *,
    log_dir: Path | None = None,
    stream: bool = True,
) -> logging.Logger:
    """Configure application and Uvicorn loggers once for JSON output."""
    global _configured
    if _configured:
        return logging.getLogger("school_ai")

    formatter = JsonFormatter()
    handlers: list[logging.Handler] = []
    if stream:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        handlers.append(stream_handler)

    target_dir = (log_dir or Config.LOGS_DIR).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(target_dir / "system.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, Config.LOG_LEVEL, logging.INFO))
    for handler in handlers:
        root.addHandler(handler)

    for name in ("school_ai", "uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # The application middleware is the single access-log source. It avoids
    # query strings and credential-bearing headers by design.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.disabled = True

    _configured = True
    return logging.getLogger("school_ai")


def bind_request_context(
    request_id: str,
    device_id: str | None,
) -> tuple[contextvars.Token, contextvars.Token]:
    return _request_id.set(request_id), _device_id.set(device_id)


def reset_request_context(tokens: tuple[contextvars.Token, contextvars.Token]) -> None:
    request_token, device_token = tokens
    _request_id.reset(request_token)
    _device_id.reset(device_token)


def log_system(message: str, level: str = "info", **extra: Any) -> None:
    logger = configure_logging()
    log_method = getattr(
        logger,
        level
        if level in {"debug", "info", "warning", "error", "critical"}
        else "info",
    )
    log_method(str(message), extra=extra or None)
