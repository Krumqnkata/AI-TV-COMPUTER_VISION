"""Operational health, warnings and the background device monitor."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from engine.admin_models import (
    BackupRecord,
    DeviceCommand,
    DeviceNode,
    OperationalJobRun,
)
from engine.db import DeliveryReceipt, now_bg
from utils.config import Config
from web.database import SessionLocal, db_engine, schema_revisions
from web.services.admin_control import get_setting
from web.services.device_control import mark_offline_devices


logger = logging.getLogger(__name__)
MONITOR_INTERVAL_SECONDS = 15


@dataclass(frozen=True)
class OperationalWarning:
    code: str
    severity: str
    title: str
    detail: str
    action_url: str


class OperationsMonitor:
    """One idempotent monitor task per application process."""

    def __init__(self, interval_seconds: int = MONITOR_INTERVAL_SECONDS):
        self.interval_seconds = max(1, int(interval_seconds))
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._state_lock = Lock()
        self._running = False
        self._started_at: datetime | None = None
        self._last_run_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None
        self._last_offline_count = 0

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        with self._state_lock:
            self._running = True
            self._started_at = now_bg()
            self._last_error = None
        self._task = asyncio.create_task(
            self._run(),
            name="school-ai-operations-monitor",
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        try:
            await task
        finally:
            with self._state_lock:
                self._running = False
            self._task = None
            self._stop_event = None

    async def _run(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            run_at = now_bg()
            try:
                offline_count = await asyncio.to_thread(run_operations_cycle)
            except Exception as exc:  # pragma: no cover - exercised through state contract
                logger.exception("Operations monitor cycle failed")
                with self._state_lock:
                    self._last_run_at = run_at
                    self._last_error = type(exc).__name__
            else:
                with self._state_lock:
                    self._last_run_at = run_at
                    self._last_success_at = now_bg()
                    self._last_error = None
                    self._last_offline_count = offline_count

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.interval_seconds,
                )
            except TimeoutError:
                continue

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "running": self._running,
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
                "last_success_at": (
                    self._last_success_at.isoformat()
                    if self._last_success_at
                    else None
                ),
                "last_error": self._last_error,
                "last_offline_count": self._last_offline_count,
                "interval_seconds": self.interval_seconds,
            }


operations_monitor = OperationsMonitor()


def run_operations_cycle() -> int:
    """Run one monitor cycle independently of any admin page request."""
    with SessionLocal() as db:
        return mark_offline_devices(db)


def readiness_report() -> dict[str, Any]:
    """Return a credential-safe readiness report."""
    database_ok = False
    schema_ok = False
    current: tuple[str, ...] = ()
    expected: tuple[str, ...] = ()
    try:
        with db_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        database_ok = True
        current, expected = schema_revisions(db_engine)
        schema_ok = set(current) == set(expected)
    except Exception as exc:
        logger.warning("Readiness database check failed: %s", type(exc).__name__)

    monitor = operations_monitor.snapshot()
    monitor_ok = bool(monitor["running"] and monitor["last_error"] is None)
    ready = database_ok and schema_ok and monitor_ok
    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "checked_at": now_bg().isoformat(),
        "database_backend": make_url(str(db_engine.url)).get_backend_name(),
        "checks": {
            "database": {"ok": database_ok},
            "migrations": {
                "ok": schema_ok,
                "current": list(current),
                "expected": list(expected),
            },
            "operations_monitor": {
                "ok": monitor_ok,
                "last_success_at": monitor["last_success_at"],
                "last_error": monitor["last_error"],
            },
        },
    }


def collect_operational_warnings(
    db: Session,
    *,
    current_time: datetime | None = None,
) -> list[OperationalWarning]:
    """Build actionable warnings from current persisted operational state."""
    now = current_time or now_bg()
    warnings: list[OperationalWarning] = []
    offline_seconds = int(get_setting(db, "devices.offline_after_seconds"))
    ack_seconds = int(get_setting(db, "operations.ack_warning_seconds"))
    backup_hours = int(get_setting(db, "operations.backup_warning_hours"))
    disk_min_free_mb = int(get_setting(db, "operations.disk_min_free_mb"))

    heartbeat_threshold = now - timedelta(seconds=offline_seconds)
    devices = db.query(DeviceNode).filter(
        DeviceNode.active.is_(True),
    ).order_by(DeviceNode.name).all()
    for device in devices:
        if device.last_seen_at is None:
            warnings.append(OperationalWarning(
                code=f"heartbeat-never-{device.id}",
                severity="warning",
                title=f"{device.name}: липсва heartbeat",
                detail="Устройството е активно, но още не се е свързвало със сървъра.",
                action_url="/admin/diagnostics",
            ))
        elif device.last_seen_at < heartbeat_threshold:
            age_seconds = max(0, int((now - device.last_seen_at).total_seconds()))
            warnings.append(OperationalWarning(
                code=f"heartbeat-stale-{device.id}",
                severity="error" if age_seconds >= offline_seconds * 2 else "warning",
                title=f"{device.name}: няма текущ heartbeat",
                detail=f"Последният контакт е преди {age_seconds} секунди.",
                action_url="/admin/diagnostics",
            ))

    ack_threshold = now - timedelta(seconds=ack_seconds)
    stale_commands_query = db.query(DeviceCommand).filter(
        DeviceCommand.status.in_(("pending", "delivered")),
        DeviceCommand.created_at < ack_threshold,
    )
    stale_command_count = stale_commands_query.count()
    if stale_command_count:
        oldest = stale_commands_query.order_by(DeviceCommand.created_at).first()
        assert oldest is not None
        warnings.append(OperationalWarning(
            code="commands-without-ack",
            severity="warning",
            title="Команди без ACK",
            detail=(
                f"{stale_command_count} команди чакат потвърждение; "
                f"най-старата е от {oldest.created_at.strftime('%d.%m %H:%M:%S')}."
            ),
            action_url="/admin/diagnostics",
        ))

    stale_deliveries_query = db.query(DeliveryReceipt).filter(
        DeliveryReceipt.status != "acknowledged",
        DeliveryReceipt.created_at < ack_threshold,
    )
    stale_delivery_count = stale_deliveries_query.count()
    if stale_delivery_count:
        oldest = stale_deliveries_query.order_by(DeliveryReceipt.created_at).first()
        assert oldest is not None
        warnings.append(OperationalWarning(
            code="deliveries-without-ack",
            severity="error",
            title="Доставки без ACK",
            detail=(
                f"{stale_delivery_count} доставки чакат потвърждение; "
                f"най-старата е от {oldest.created_at.strftime('%d.%m %H:%M:%S')}."
            ),
            action_url="/admin/diagnostics",
        ))

    last_backup = db.query(BackupRecord).filter(
        BackupRecord.status == "verified",
    ).order_by(BackupRecord.created_at.desc()).first()
    backup_threshold = now - timedelta(hours=backup_hours)
    if last_backup is None:
        warnings.append(OperationalWarning(
            code="backup-missing",
            severity="error",
            title="Няма проверен backup",
            detail="Създайте и проверете първото резервно копие на базата.",
            action_url="/admin/backups",
        ))
    elif last_backup.created_at < backup_threshold:
        age_hours = max(0, int((now - last_backup.created_at).total_seconds() // 3600))
        warnings.append(OperationalWarning(
            code="backup-stale",
            severity="warning",
            title="Backup-ът е стар",
            detail=(
                f"Последното проверено копие е на {age_hours} часа "
                f"(праг: {backup_hours} часа)."
            ),
            action_url="/admin/backups",
        ))

    disk = disk_space_report()
    if disk["available"] and disk["free_mb"] < disk_min_free_mb:
        warnings.append(OperationalWarning(
            code="disk-space-low",
            severity="error",
            title="Недостатъчно свободно дисково място",
            detail=(
                f"Свободни са {disk['free_mb']} MB при backup директорията "
                f"(праг: {disk_min_free_mb} MB)."
            ),
            action_url="/admin/diagnostics",
        ))
    elif not disk["available"]:
        warnings.append(OperationalWarning(
            code="disk-space-check-failed",
            severity="warning",
            title="Дисковото място не може да бъде проверено",
            detail="Проверете дали backup директорията и нейният родител са достъпни.",
            action_url="/admin/diagnostics",
        ))

    if bool(get_setting(db, "operations.maintenance_enabled")):
        maintenance_hours = int(
            get_setting(db, "operations.maintenance_warning_hours"),
        )
        maintenance_threshold = now - timedelta(hours=maintenance_hours)
        for job_name, job_label in (
            ("backup", "автоматичен backup"),
            ("retention", "retention cleanup"),
        ):
            latest = db.query(OperationalJobRun).filter(
                OperationalJobRun.job_name == job_name,
            ).order_by(OperationalJobRun.started_at.desc()).first()
            if latest is None:
                warnings.append(OperationalWarning(
                    code=f"maintenance-never-{job_name}",
                    severity="error",
                    title=f"Не е изпълняван {job_label}",
                    detail="Проверете дали school-ai-maintenance.timer е активен.",
                    action_url="/admin/diagnostics",
                ))
                continue
            if latest.status == "failed":
                warnings.append(OperationalWarning(
                    code=f"maintenance-failed-{job_name}",
                    severity="error",
                    title=f"Неуспешен {job_label}",
                    detail=(
                        f"Последният опит е приключил с {latest.error_type or 'грешка'} "
                        f"на {latest.started_at.strftime('%d.%m %H:%M:%S')}."
                    ),
                    action_url="/admin/diagnostics",
                ))
                continue
            effective_time = latest.finished_at or latest.started_at
            if latest.status == "running" and latest.started_at < maintenance_threshold:
                warnings.append(OperationalWarning(
                    code=f"maintenance-stuck-{job_name}",
                    severity="error",
                    title=f"Блокирал {job_label}",
                    detail=(
                        f"Задачата е в състояние running повече от "
                        f"{maintenance_hours} часа."
                    ),
                    action_url="/admin/diagnostics",
                ))
            elif effective_time < maintenance_threshold:
                age_hours = max(
                    0,
                    int((now - effective_time).total_seconds() // 3600),
                )
                warnings.append(OperationalWarning(
                    code=f"maintenance-stale-{job_name}",
                    severity="warning",
                    title=f"Закъснял {job_label}",
                    detail=(
                        f"Последното изпълнение е преди {age_hours} часа "
                        f"(праг: {maintenance_hours} часа)."
                    ),
                    action_url="/admin/diagnostics",
                ))

    return warnings


def disk_space_report() -> dict[str, Any]:
    """Inspect the filesystem hosting backups without creating directories."""
    target = Config.BACKUP_DIR
    while not target.exists() and target.parent != target:
        target = target.parent
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        logger.warning(
            "Disk space check failed",
            extra={
                "event": "operations.disk_check_failed",
                "error_type": type(exc).__name__,
            },
        )
        return {
            "available": False,
            "path": str(Config.BACKUP_DIR),
            "free_mb": 0,
            "total_mb": 0,
            "used_percent": None,
        }
    total_mb = usage.total // (1024 * 1024)
    free_mb = usage.free // (1024 * 1024)
    used_percent = (
        round((usage.used / usage.total) * 100, 1)
        if usage.total
        else 0.0
    )
    return {
        "available": True,
        "path": str(Config.BACKUP_DIR),
        "free_mb": free_mb,
        "total_mb": total_mb,
        "used_percent": used_percent,
    }
