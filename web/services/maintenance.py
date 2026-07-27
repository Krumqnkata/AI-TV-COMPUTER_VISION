"""Audited entry points for deployment-scheduled maintenance jobs."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from engine.admin_models import OperationalJobRun
from engine.db import now_bg
from web.database import SessionLocal
from web.services.admin_control import audit_event
from web.services.backups import create_database_backup
from web.services.privacy import execute_retention_cleanup


logger = logging.getLogger(__name__)
MAINTENANCE_JOBS = ("backup", "retention")


@dataclass(frozen=True)
class MaintenanceResult:
    job_name: str
    status: str
    run_id: int
    summary: dict[str, Any]


class MaintenanceError(RuntimeError):
    def __init__(self, failed_jobs: list[str]):
        self.failed_jobs = tuple(failed_jobs)
        super().__init__(f"Maintenance failed: {', '.join(failed_jobs)}")


def _create_run(job_name: str) -> int:
    with SessionLocal() as db:
        item = OperationalJobRun(
            job_name=job_name,
            status="running",
            started_at=now_bg(),
        )
        db.add(item)
        audit_event(
            db,
            "operations.job_started",
            f"Стартирана автоматична задача: {job_name}",
            actor=None,
            entity_type="OperationalJobRun",
            changes={"job_name": job_name},
        )
        db.commit()
        db.refresh(item)
        return item.id


def _finish_run(
    run_id: int,
    *,
    status: str,
    started: float,
    summary: dict[str, Any] | None = None,
    error_type: str | None = None,
) -> None:
    with SessionLocal() as db:
        item = db.get(OperationalJobRun, run_id)
        if item is None:
            raise RuntimeError("Operational job run disappeared")
        item.status = status
        item.finished_at = now_bg()
        item.duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        item.summary_json = json.dumps(summary or {}, ensure_ascii=False, sort_keys=True)
        item.error_type = (error_type or "")[:100] or None
        audit_event(
            db,
            f"operations.job_{status}",
            (
                f"Автоматичната задача завърши: {item.job_name}"
                if status == "completed"
                else f"Автоматичната задача е неуспешна: {item.job_name}"
            ),
            actor=None,
            entity_type="OperationalJobRun",
            entity_id=item.id,
            changes={
                "job_name": item.job_name,
                "status": status,
                "duration_ms": item.duration_ms,
                "summary": summary or {},
                "error_type": error_type,
            },
        )
        db.commit()


def run_maintenance_job(job_name: str) -> MaintenanceResult:
    if job_name not in MAINTENANCE_JOBS:
        raise ValueError("Непозната maintenance задача")
    run_id = _create_run(job_name)
    started = time.perf_counter()
    try:
        with SessionLocal() as db:
            if job_name == "backup":
                record = create_database_backup(db, None)
                summary: dict[str, Any] = {
                    "backup_id": record.id,
                    "database_type": record.database_type,
                    "size_bytes": record.size_bytes,
                    "status": record.status,
                }
            else:
                cleanup = execute_retention_cleanup(db, None)
                deleted = json.loads(cleanup.summary_json or "{}")
                summary = {
                    "cleanup_run_id": cleanup.id,
                    "deleted_total": sum(
                        int(value) for value in deleted.values()
                        if isinstance(value, int)
                    ),
                    "status": cleanup.status,
                }
    except Exception as exc:
        error_type = type(exc).__name__
        _finish_run(
            run_id,
            status="failed",
            started=started,
            error_type=error_type,
        )
        logger.error(
            "Scheduled maintenance job failed",
            extra={
                "event": "operations.maintenance.failed",
                "job_name": job_name,
                "error_type": error_type,
            },
        )
        raise

    _finish_run(
        run_id,
        status="completed",
        started=started,
        summary=summary,
    )
    logger.info(
        "Scheduled maintenance job completed",
        extra={
            "event": "operations.maintenance.completed",
            "job_name": job_name,
        },
    )
    return MaintenanceResult(job_name, "completed", run_id, summary)


def run_maintenance(selection: str) -> list[MaintenanceResult]:
    jobs = MAINTENANCE_JOBS if selection == "all" else (selection,)
    results: list[MaintenanceResult] = []
    failures: list[str] = []
    for job_name in jobs:
        try:
            results.append(run_maintenance_job(job_name))
        except Exception:
            failures.append(job_name)
    if failures:
        raise MaintenanceError(failures)
    return results
