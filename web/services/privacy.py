"""Retention previews and explicit, audited privacy cleanup runs."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from engine.admin_models import (
    AdminAuditEvent,
    PrivacyCleanupRun,
    ScheduleImportJob,
    StaffAccount,
)
from engine.db import Message, SystemEvent, now_bg
from web.services.admin_control import audit_event, get_setting


def retention_preview(db: Session) -> dict[str, dict[str, Any]]:
    now = now_bg()
    event_before = now - timedelta(days=int(get_setting(db, "privacy.system_events_days")))
    audit_before = now - timedelta(days=int(get_setting(db, "privacy.audit_days")))
    message_before = now - timedelta(days=int(get_setting(db, "privacy.delivered_messages_days")))
    import_before = now - timedelta(days=int(get_setting(db, "privacy.import_jobs_days")))
    return {
        "system_events": {
            "label": "Системни събития",
            "before": event_before,
            "count": db.query(SystemEvent).filter(SystemEvent.timestamp < event_before).count(),
        },
        "admin_audit": {
            "label": "Административен одит",
            "before": audit_before,
            "count": db.query(AdminAuditEvent).filter(AdminAuditEvent.created_at < audit_before).count(),
        },
        "delivered_messages": {
            "label": "Доставени/изтекли съобщения",
            "before": message_before,
            "count": db.query(Message).filter(
                Message.status.in_(("delivered", "expired", "deleted")),
                or_(Message.delivered_at < message_before, Message.valid_until < message_before),
            ).count(),
        },
        "import_jobs": {
            "label": "Стари импортни отчети",
            "before": import_before,
            "count": db.query(ScheduleImportJob).filter(ScheduleImportJob.created_at < import_before).count(),
        },
    }


def execute_retention_cleanup(
    db: Session,
    actor: StaffAccount | None,
    *,
    ip_address: str | None = None,
) -> PrivacyCleanupRun:
    preview = retention_preview(db)
    deleted = {
        "system_events": db.query(SystemEvent).filter(
            SystemEvent.timestamp < preview["system_events"]["before"],
        ).delete(synchronize_session=False),
        "delivered_messages": db.query(Message).filter(
            Message.status.in_(("delivered", "expired", "deleted")),
            or_(
                Message.delivered_at < preview["delivered_messages"]["before"],
                Message.valid_until < preview["delivered_messages"]["before"],
            ),
        ).delete(synchronize_session=False),
        "import_jobs": db.query(ScheduleImportJob).filter(
            ScheduleImportJob.created_at < preview["import_jobs"]["before"],
        ).delete(synchronize_session=False),
        "admin_audit": db.query(AdminAuditEvent).filter(
            AdminAuditEvent.created_at < preview["admin_audit"]["before"],
        ).delete(synchronize_session=False),
    }
    run = PrivacyCleanupRun(
        mode="execute",
        status="completed",
        summary_json=json.dumps(deleted, ensure_ascii=False),
        executed_by_staff_id=actor.id if actor else None,
    )
    db.add(run)
    audit_event(
        db,
        "privacy.retention_executed",
        f"Изпълнено почистване: {sum(deleted.values())} записа",
        actor=actor,
        entity_type="PrivacyCleanupRun",
        changes=deleted,
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(run)
    return run
