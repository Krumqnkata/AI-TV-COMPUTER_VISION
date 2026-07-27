"""Small in-process metrics registry with a Prometheus text export."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock

from sqlalchemy.orm import Session

from engine.admin_models import DeviceCommand, DeviceNode
from engine.db import DeliveryReceipt, now_bg
from web.connections import connection_manager
from web.services.admin_control import get_setting


def _status_class(status_code: int) -> str:
    value = max(0, min(int(status_code), 999))
    return f"{value // 100}xx"


class MetricsRegistry:
    """Thread-safe process metrics without request, person or device labels."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._http_requests: dict[tuple[str, str], int] = defaultdict(int)
        self._http_errors = 0
        self._http_duration_sum = 0.0
        self._http_duration_count = 0
        self._http_duration_max = 0.0
        self._qr_results: dict[str, int] = defaultdict(int)

    def record_http(self, method: str, status_code: int, duration_seconds: float) -> None:
        method_label = method.upper() if method.upper() in {
            "GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS",
        } else "OTHER"
        duration = max(0.0, float(duration_seconds))
        with self._lock:
            self._http_requests[(method_label, _status_class(status_code))] += 1
            if int(status_code) >= 400:
                self._http_errors += 1
            self._http_duration_sum += duration
            self._http_duration_count += 1
            self._http_duration_max = max(self._http_duration_max, duration)

    def record_qr_result(self, result: str) -> None:
        label = result if result in {"success", "ignored", "error"} else "error"
        with self._lock:
            self._qr_results[label] += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "http_requests": dict(self._http_requests),
                "http_errors": self._http_errors,
                "http_duration_sum": self._http_duration_sum,
                "http_duration_count": self._http_duration_count,
                "http_duration_max": self._http_duration_max,
                "qr_results": dict(self._qr_results),
            }

    def reset(self) -> None:
        """Test-only friendly reset; production never resets the registry."""
        with self._lock:
            self._http_requests.clear()
            self._http_errors = 0
            self._http_duration_sum = 0.0
            self._http_duration_count = 0
            self._http_duration_max = 0.0
            self._qr_results.clear()


metrics_registry = MetricsRegistry()


def operational_metric_values(db: Session) -> dict[str, int]:
    """Return low-cardinality gauges computed from persisted operational state."""
    from datetime import timedelta

    now = now_bg()
    ack_threshold = now - timedelta(
        seconds=int(get_setting(db, "operations.ack_warning_seconds")),
    )
    return {
        "active_websockets": connection_manager.count(),
        "offline_devices": db.query(DeviceNode).filter(
            DeviceNode.active.is_(True),
            DeviceNode.status == "offline",
        ).count(),
        "delayed_command_acks": db.query(DeviceCommand).filter(
            DeviceCommand.status.in_(("pending", "delivered")),
            DeviceCommand.created_at < ack_threshold,
        ).count(),
        "delayed_delivery_acks": db.query(DeliveryReceipt).filter(
            DeliveryReceipt.status != "acknowledged",
            DeliveryReceipt.created_at < ack_threshold,
        ).count(),
    }


def render_prometheus_metrics(db: Session) -> str:
    process = metrics_registry.snapshot()
    gauges = operational_metric_values(db)
    lines = [
        "# HELP school_ai_http_requests_total HTTP requests handled by this process.",
        "# TYPE school_ai_http_requests_total counter",
    ]
    for (method, status_class), value in sorted(process["http_requests"].items()):
        lines.append(
            f'school_ai_http_requests_total{{method="{method}",status_class="{status_class}"}} {value}',
        )
    lines.extend([
        "# HELP school_ai_http_errors_total HTTP responses with status 4xx or 5xx.",
        "# TYPE school_ai_http_errors_total counter",
        f"school_ai_http_errors_total {process['http_errors']}",
        "# HELP school_ai_http_request_duration_seconds Request duration summary for this process.",
        "# TYPE school_ai_http_request_duration_seconds summary",
        f"school_ai_http_request_duration_seconds_sum {process['http_duration_sum']:.6f}",
        f"school_ai_http_request_duration_seconds_count {process['http_duration_count']}",
        "# HELP school_ai_http_request_duration_seconds_max Longest observed request.",
        "# TYPE school_ai_http_request_duration_seconds_max gauge",
        f"school_ai_http_request_duration_seconds_max {process['http_duration_max']:.6f}",
        "# HELP school_ai_qr_results_total QR processing outcomes.",
        "# TYPE school_ai_qr_results_total counter",
    ])
    for result in ("success", "ignored", "error"):
        lines.append(
            f'school_ai_qr_results_total{{result="{result}"}} {process["qr_results"].get(result, 0)}',
        )
    lines.extend([
        "# HELP school_ai_active_websockets Active device WebSocket connections in this process.",
        "# TYPE school_ai_active_websockets gauge",
        f"school_ai_active_websockets {gauges['active_websockets']}",
        "# HELP school_ai_offline_devices Active devices currently marked offline.",
        "# TYPE school_ai_offline_devices gauge",
        f"school_ai_offline_devices {gauges['offline_devices']}",
        "# HELP school_ai_delayed_command_acks Commands beyond the configured ACK threshold.",
        "# TYPE school_ai_delayed_command_acks gauge",
        f"school_ai_delayed_command_acks {gauges['delayed_command_acks']}",
        "# HELP school_ai_delayed_delivery_acks Deliveries beyond the configured ACK threshold.",
        "# TYPE school_ai_delayed_delivery_acks gauge",
        f"school_ai_delayed_delivery_acks {gauges['delayed_delivery_acks']}",
    ])
    return "\n".join(lines) + "\n"
