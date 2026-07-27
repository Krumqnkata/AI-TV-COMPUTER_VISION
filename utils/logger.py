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
_QUIET_CONSOLE_PATH_PREFIXES = (
    "/static/",
    "/manifest-",
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


class ConsoleFormatter(logging.Formatter):
    """Render compact local-time records without exception text or tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        method = getattr(record, "http_method", None)
        path = getattr(record, "http_path", None)
        status = getattr(record, "http_status", None)
        duration_ms = getattr(record, "duration_ms", None)
        if method and path and status is not None:
            message = f"{method} {path} -> {status}"
            if duration_ms is not None:
                message += f" ({duration_ms} ms)"
        else:
            message = record.getMessage()

        context: list[str] = []
        request_id = getattr(record, "request_id", None) or _request_id.get()
        device_id = getattr(record, "device_id", None) or _device_id.get()
        if request_id:
            context.append(f"req={request_id[:8]}")
        if device_id:
            context.append(f"device={device_id}")
        for field, label in (
            ("event", "event"),
            ("job_name", "job"),
            ("command", "command"),
            ("count", "count"),
        ):
            value = getattr(record, field, None)
            if value is not None:
                context.append(f"{label}={value}")
        error_type = getattr(record, "error_type", None)
        if not error_type and record.exc_info and record.exc_info[0]:
            error_type = record.exc_info[0].__name__
        if error_type:
            context.append(f"error={error_type}")

        suffix = f" | {' '.join(context)}" if context else ""
        return (
            f"[{timestamp}] {record.levelname:<8} {record.name} | "
            f"{message}{suffix}"
        )


class ConsoleNoiseFilter(logging.Filter):
    """Hide successful static asset requests only from the local console."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "school_ai.http" or record.levelno >= logging.WARNING:
            return True
        status = getattr(record, "http_status", None)
        if not isinstance(status, int) or not 200 <= status < 400:
            return True
        path = str(getattr(record, "http_path", ""))
        is_quiet_asset = (
            path.startswith(_QUIET_CONSOLE_PATH_PREFIXES)
            or path.endswith("-sw.js")
            or path in {"/favicon.ico", "/favicon.png"}
        )
        return not is_quiet_asset


def configure_logging(
    *,
    log_dir: Path | None = None,
    stream: bool = True,
) -> logging.Logger:
    """Configure readable local output plus a complete JSON Lines log file."""
    global _configured
    if _configured:
        return logging.getLogger("school_ai")

    json_formatter = JsonFormatter()
    handlers: list[logging.Handler] = []
    if stream:
        stream_handler = logging.StreamHandler(sys.stdout)
        if Config.LOG_FORMAT == "json":
            stream_handler.setFormatter(json_formatter)
        else:
            stream_handler.setFormatter(ConsoleFormatter())
            stream_handler.addFilter(ConsoleNoiseFilter())
        handlers.append(stream_handler)

    target_dir = (log_dir or Config.LOGS_DIR).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(target_dir / "system.log", encoding="utf-8")
    file_handler.setFormatter(json_formatter)
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
