"""Guided SQLAdmin workflows for the school control centre."""

from __future__ import annotations

import base64
import io
import json
import secrets
from datetime import timedelta

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func
from sqladmin import BaseView, expose
from starlette.concurrency import run_in_threadpool

from engine.admin_models import (
    AdminAuditEvent,
    BackupRecord,
    DeviceCommand,
    DeviceEnrollmentToken,
    DeviceNode,
    OperationalJobRun,
    ScheduleImportJob,
    StaffAccount,
    StaffRole,
)
from engine.auth import get_password_hash
from engine.db import (
    Badge,
    Event,
    InteractionPoint,
    Message,
    Person,
    SystemEvent,
    Timetable,
    hash_token,
    now_bg,
    today_bg,
)
from utils.config import Config
from web.admin.permissions import current_staff, request_ip, require_session_permission, session_has_permission
from web.admin.reveal import pop_once, store_once
from web.connections import connection_manager
from web.database import SessionLocal
from web.services.admin_control import (
    ROLE_DEFINITIONS,
    SETTING_DEFINITIONS,
    apply_role_codes,
    audit_event,
    get_setting,
    save_secret,
    secret_catalog,
    settings_catalog,
    update_settings,
)
from web.services.backups import (
    backup_media_type,
    create_database_backup,
    downloadable_backup_path,
    verify_backup,
)
from web.services.device_control import (
    SAFE_COMMANDS,
    available_safe_commands,
    create_enrollment_token,
    mark_offline_devices,
    parse_device_diagnostics,
    queue_command,
    rotate_device_key,
)
from web.services.imports import execute_schedule_import, preview_schedule_import, timetable_csv_template
from web.services.ai_runtime import assistant_runtime_status, probe_ai_connection
from web.services.operations import (
    collect_operational_warnings,
    disk_space_report,
    operations_monitor,
    readiness_report,
)
from web.services.metrics import metrics_registry, operational_metric_values
from web.services.privacy import execute_retention_cleanup, retention_preview


def _redirect(path: str, *, ok: str | None = None, error: str | None = None) -> RedirectResponse:
    from urllib.parse import urlencode

    params = urlencode({key: value for key, value in {"ok": ok, "error": error}.items() if value})
    return RedirectResponse(path + (f"?{params}" if params else ""), status_code=303)


def _probe_and_audit_assistant(
    actor_id: int,
    ip_address: str | None,
) -> dict:
    """Run the external probe outside the event loop using its own DB session."""
    with SessionLocal() as db:
        actor = db.get(StaffAccount, actor_id)
        if actor is None:
            raise HTTPException(status_code=401, detail="Сесията е невалидна")
        result = probe_ai_connection(db)
        audit_event(
            db,
            "assistant.connection_tested",
            (
                "Успешен тест на AI доставчик"
                if result["ok"]
                else "Неуспешен тест на AI доставчик"
            ),
            actor=actor,
            entity_type="AssistantProvider",
            entity_id=result["provider"],
            changes={
                "provider": result["provider"],
                "outcome": result["outcome"],
            },
            ip_address=ip_address,
        )
        db.commit()
        return result


def _qr_data_uri(value: str) -> str:
    import qrcode

    image = qrcode.make(value)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


class PermissionedBaseView(BaseView):
    permission = "dashboard.view"

    def is_accessible(self, request: Request) -> bool:
        return session_has_permission(request, self.permission)

    def guard(self, request: Request, permission: str | None = None) -> None:
        require_session_permission(request, permission or self.permission)


class DashboardView(PermissionedBaseView):
    name = "Начало"
    icon = "fa-solid fa-house"
    permission = "dashboard.view"

    @expose("/dashboard", methods=["GET"])
    async def dashboard(self, request: Request):
        self.guard(request)
        with SessionLocal() as db:
            mark_offline_devices(db)
            today = today_bg()
            operational_warnings = collect_operational_warnings(db)
            context = {
                "title": "Контролен център",
                "subtitle": "Всичко важно за днешния ден на едно място",
                "school_name": get_setting(db, "school.name"),
                "counts": {
                    "people": db.query(Person).filter(Person.active.is_(True)).count(),
                    "students": db.query(Person).filter(Person.active.is_(True), Person.role == "student").count(),
                    "teachers": db.query(Person).filter(Person.active.is_(True), Person.role == "teacher").count(),
                    "messages": db.query(Message).filter(Message.status == "active", Message.valid_until > now_bg()).count(),
                    "lessons": db.query(Timetable).filter(Timetable.date == today).count(),
                    "events": db.query(Event).filter(func.date(Event.start_time) == today).count(),
                },
                "devices": db.query(DeviceNode).order_by(DeviceNode.status, DeviceNode.name).all(),
                "online_devices": db.query(DeviceNode).filter(DeviceNode.active.is_(True), DeviceNode.status != "offline").count(),
                "device_total": db.query(DeviceNode).filter(DeviceNode.active.is_(True)).count(),
                "screens_connected": connection_manager.count(),
                "recent_audit": db.query(AdminAuditEvent).order_by(AdminAuditEvent.created_at.desc()).limit(6).all(),
                "last_backup": db.query(BackupRecord).order_by(BackupRecord.created_at.desc()).first(),
                "failed_imports": db.query(ScheduleImportJob).filter(ScheduleImportJob.status.in_(("failed", "rejected"))).count(),
                "master_key_configured": bool(Config.SETTINGS_MASTER_KEY or Config.ADMIN_SECRET_KEY),
                "refresh_seconds": int(get_setting(db, "dashboard.refresh_seconds")),
                "operational_warnings": operational_warnings,
            }
            return await self.templates.TemplateResponse(request, "admin/dashboard.html", context)

    @expose("/dashboard/health", methods=["GET"])
    async def dashboard_health(self, request: Request):
        self.guard(request)
        with SessionLocal() as db:
            mark_offline_devices(db)
            operational_warnings = collect_operational_warnings(db)
            return await self.templates.TemplateResponse(request, "admin/fragments/dashboard_health.html", {
                "last_backup": db.query(BackupRecord).order_by(BackupRecord.created_at.desc()).first(),
                "failed_imports": db.query(ScheduleImportJob).filter(ScheduleImportJob.status.in_(("failed", "rejected"))).count(),
                "master_key_configured": bool(Config.SETTINGS_MASTER_KEY or Config.ADMIN_SECRET_KEY),
                "online_devices": db.query(DeviceNode).filter(DeviceNode.active.is_(True), DeviceNode.status != "offline").count(),
                "device_total": db.query(DeviceNode).filter(DeviceNode.active.is_(True)).count(),
                "refresh_seconds": int(get_setting(db, "dashboard.refresh_seconds")),
                "operational_warnings": operational_warnings,
            })


class DiagnosticsView(PermissionedBaseView):
    name = "Диагностика"
    category = "Устройства"
    icon = "fa-solid fa-stethoscope"
    permission = "devices.view"

    @expose("/diagnostics", methods=["GET"])
    async def diagnostics(self, request: Request):
        self.guard(request)
        websocket_connections = await connection_manager.snapshot()
        connections_by_device: dict[str, list[dict]] = {}
        for connection in websocket_connections:
            identifier = connection.get("device_id")
            if identifier:
                connections_by_device.setdefault(identifier, []).append(connection)

        with SessionLocal() as db:
            mark_offline_devices(db)
            warnings = collect_operational_warnings(db)
            device_rows = []
            for device in db.query(DeviceNode).order_by(
                DeviceNode.active.desc(),
                DeviceNode.name,
            ).all():
                try:
                    capabilities = json.loads(device.capabilities_json or "[]")
                except (json.JSONDecodeError, TypeError):
                    capabilities = []
                if not isinstance(capabilities, list):
                    capabilities = []
                connections = connections_by_device.get(device.identifier, [])
                contacts = [
                    value
                    for value in (
                        device.last_seen_at,
                        device.last_websocket_at,
                        device.last_websocket_disconnected_at,
                    )
                    if value is not None
                ]
                device_rows.append({
                    "device": device,
                    "diagnostics": parse_device_diagnostics(device),
                    "capabilities": capabilities,
                    "connections": connections,
                    "last_contact": max(contacts) if contacts else None,
                    "unacknowledged_commands": db.query(DeviceCommand).filter(
                        DeviceCommand.device_id == device.id,
                        DeviceCommand.status.in_(("pending", "delivered")),
                    ).count(),
                })

            health = readiness_report()
            process_metrics = metrics_registry.snapshot()
            operational_metrics = operational_metric_values(db)
            request_count = int(process_metrics["http_duration_count"])
            average_latency_ms = (
                round(
                    float(process_metrics["http_duration_sum"])
                    / request_count
                    * 1000,
                    2,
                )
                if request_count
                else 0
            )
            return await self.templates.TemplateResponse(
                request,
                "admin/diagnostics.html",
                {
                    "title": "Диагностика",
                    "subtitle": "Камера, браузър, WebSocket, heartbeat и ACK на едно място",
                    "health": health,
                    "monitor": operations_monitor.snapshot(),
                    "warnings": warnings,
                    "device_rows": device_rows,
                    "websocket_connections": websocket_connections,
                    "process_metrics": process_metrics,
                    "operational_metrics": operational_metrics,
                    "average_latency_ms": average_latency_ms,
                    "disk": disk_space_report(),
                    "job_runs": db.query(OperationalJobRun).order_by(
                        OperationalJobRun.started_at.desc(),
                    ).limit(10).all(),
                    "refresh_seconds": int(get_setting(db, "dashboard.refresh_seconds")),
                },
            )


class SettingsView(PermissionedBaseView):
    name = "Настройки"
    category = "Система"
    icon = "fa-solid fa-sliders"
    permission = "settings.view"

    @expose("/settings", methods=["GET"])
    async def settings(self, request: Request):
        self.guard(request)
        with SessionLocal() as db:
            catalog = settings_catalog(db)
            grouped: dict[str, list[dict]] = {}
            for item in catalog:
                grouped.setdefault(item["definition"].category, []).append(item)
            return await self.templates.TemplateResponse(request, "admin/settings.html", {
                "title": "Настройки",
                "subtitle": "Безопасни оперативни настройки; deployment тайните остават извън панела",
                "groups": grouped,
                "secrets": secret_catalog(db),
                "assistant_runtime": assistant_runtime_status(db),
                "can_manage": session_has_permission(request, "settings.manage"),
                "can_manage_ai": session_has_permission(request, "assistant.manage"),
                "ok": request.query_params.get("ok"),
                "error": request.query_params.get("error"),
            })

    @expose("/settings/save", methods=["POST"])
    async def save_settings(self, request: Request):
        self.guard(request, "settings.manage")
        form = await request.form()
        values = {}
        for definition in SETTING_DEFINITIONS:
            if definition.key.startswith("assistant.") and not session_has_permission(request, "assistant.manage"):
                continue
            if definition.value_type == "boolean":
                values[definition.key] = form.get(definition.key, "false")
            elif definition.key in form:
                values[definition.key] = form[definition.key]
        try:
            with SessionLocal() as db:
                actor = current_staff(db, request)
                update_settings(db, values, actor, ip_address=request_ip(request))
        except (ValueError, TypeError) as exc:
            return _redirect("/admin/settings", error=str(exc))
        return _redirect("/admin/settings", ok="Настройките са запазени.")

    @expose("/settings/secret", methods=["POST"])
    async def save_protected_secret(self, request: Request):
        self.guard(request, "assistant.manage")
        form = await request.form()
        try:
            with SessionLocal() as db:
                actor = current_staff(db, request)
                save_secret(
                    db,
                    str(form.get("key", "")),
                    str(form.get("value", "")),
                    actor,
                    ip_address=request_ip(request),
                )
        except (ValueError, RuntimeError) as exc:
            return _redirect("/admin/settings", error=str(exc))
        return _redirect("/admin/settings", ok="Защитената стойност е обновена и няма да бъде показвана.")

    @expose("/settings/assistant/test", methods=["POST"])
    async def test_assistant_connection(self, request: Request):
        self.guard(request, "assistant.manage")
        with SessionLocal() as db:
            actor = current_staff(db, request)
            actor_id = actor.id
        result = await run_in_threadpool(
            _probe_and_audit_assistant,
            actor_id,
            request_ip(request),
        )
        return _redirect(
            "/admin/settings",
            ok=result["message"] if result["ok"] else None,
            error=None if result["ok"] else result["message"],
        )


class StaffManagementView(PermissionedBaseView):
    name = "Служебни профили"
    category = "Достъп и одит"
    icon = "fa-solid fa-user-lock"
    permission = "staff.view"

    @expose("/staff", methods=["GET"])
    async def staff(self, request: Request):
        self.guard(request)
        with SessionLocal() as db:
            return await self.templates.TemplateResponse(request, "admin/staff.html", {
                "title": "Служебни профили",
                "subtitle": "Отделни входове за хората, които управляват системата",
                "accounts": db.query(StaffAccount).order_by(StaffAccount.active.desc(), StaffAccount.display_name).all(),
                "roles": db.query(StaffRole).filter(StaffRole.active.is_(True)).order_by(StaffRole.name).all(),
                "can_manage": session_has_permission(request, "staff.manage"),
                "current_staff_id": request.session.get("staff_id"),
                "ok": request.query_params.get("ok"),
                "error": request.query_params.get("error"),
            })

    @expose("/staff/save", methods=["POST"])
    async def save_staff(self, request: Request):
        self.guard(request, "staff.manage")
        form = await request.form()
        try:
            account_id = int(form.get("account_id") or 0)
        except (TypeError, ValueError):
            account_id = 0
        username = str(form.get("username", "")).strip()
        display_name = str(form.get("display_name", "")).strip()
        email = str(form.get("email", "")).strip() or None
        password = str(form.get("password", ""))
        role_codes = form.getlist("roles")
        active = form.get("active") == "on"
        if not username or not display_name or not role_codes:
            return _redirect("/admin/staff", error="Потребителското име, показваното име и поне една роля са задължителни.")
        if not account_id and len(password) < 12:
            return _redirect("/admin/staff", error="Новият профил изисква парола с поне 12 знака.")

        with SessionLocal() as db:
            actor = current_staff(db, request)
            account = db.get(StaffAccount, account_id) if account_id else None
            collision = db.query(StaffAccount).filter(
                func.lower(StaffAccount.username) == username.lower(),
                StaffAccount.id != (account.id if account else 0),
            ).first()
            if collision:
                return _redirect("/admin/staff", error="Това потребителско име вече се използва.")
            if account is None:
                account = StaffAccount(username=username[:100], display_name=display_name[:120], password_hash=get_password_hash(password))
                db.add(account)
                db.flush()
            if account.id == actor.id and not active:
                return _redirect("/admin/staff", error="Не можете да деактивирате собствения си профил.")
            currently_super = any(role.code == "superadmin" for role in account.roles)
            removing_super = currently_super and "superadmin" not in role_codes
            if removing_super or (currently_super and not active):
                other_superadmins = db.query(StaffAccount).filter(
                    StaffAccount.id != account.id,
                    StaffAccount.active.is_(True),
                    StaffAccount.roles.any(StaffRole.code == "superadmin"),
                ).count()
                if other_superadmins == 0:
                    return _redirect("/admin/staff", error="Системата трябва да има поне един активен главен администратор.")
            account.username = username[:100]
            account.display_name = display_name[:120]
            account.email = email[:255] if email else None
            account.active = active
            account.force_password_change = form.get("force_password_change") == "on"
            if password:
                if len(password) < 12:
                    return _redirect("/admin/staff", error="Паролата трябва да е поне 12 знака.")
                account.password_hash = get_password_hash(password)
            apply_role_codes(db, account, role_codes)
            account.failed_login_count = 0
            account.locked_until = None
            audit_event(
                db,
                "staff.saved",
                f"Запазен служебен профил: {account.display_name}",
                actor=actor,
                entity_type="StaffAccount",
                entity_id=account.id,
                changes={"username": account.username, "roles": role_codes, "active": active, "password_changed": bool(password)},
                ip_address=request_ip(request),
            )
            db.commit()
        return _redirect("/admin/staff", ok="Служебният профил е запазен.")


class BadgeWorkflowView(PermissionedBaseView):
    name = "Издаване на бадж"
    category = "Хора"
    icon = "fa-solid fa-qrcode"
    permission = "badges.manage"

    @expose("/badges/issue", methods=["GET"])
    async def issue_page(self, request: Request):
        self.guard(request)
        with SessionLocal() as db:
            return await self.templates.TemplateResponse(request, "admin/badges.html", {
                "title": "Издаване на QR бадж",
                "subtitle": "QR токенът се показва само веднъж",
                "people": db.query(Person).filter(Person.active.is_(True)).order_by(Person.full_name).all(),
                "badges": db.query(Badge).order_by(Badge.created_at.desc()).limit(100).all(),
                "error": request.query_params.get("error"),
            })

    @expose("/badges/issue", methods=["POST"])
    async def issue_badge(self, request: Request):
        self.guard(request)
        form = await request.form()
        try:
            person_id = int(form.get("person_id") or 0)
        except (TypeError, ValueError):
            person_id = 0
        with SessionLocal() as db:
            actor = current_staff(db, request)
            person = db.get(Person, person_id)
            if person is None or not person.active:
                return _redirect("/admin/badges/issue", error="Изберете активен човек.")
            for old in db.query(Badge).filter(Badge.person_id == person.id, Badge.status == "active").all():
                old.status = "disabled"
            raw = f"SCH-{secrets.token_hex(16).upper()}"
            badge = Badge(person_id=person.id, token_hash=hash_token(raw), status="active", created_at=now_bg())
            db.add(badge)
            db.flush()
            audit_event(db, "badge.issued", f"Издаден QR бадж за {person.full_name}", actor=actor, entity_type="Badge", entity_id=badge.id, ip_address=request_ip(request))
            db.commit()
            ticket = store_once({"kind": "badge", "name": person.full_name, "value": raw, "qr": _qr_data_uri(raw)}, actor.id)
        return RedirectResponse(f"/admin/reveal/{ticket}", status_code=303)


class DeviceManagementView(PermissionedBaseView):
    name = "Управление на устройства"
    category = "Устройства"
    icon = "fa-solid fa-satellite-dish"
    permission = "devices.view"

    @expose("/devices", methods=["GET"])
    async def devices(self, request: Request):
        self.guard(request)
        with SessionLocal() as db:
            mark_offline_devices(db)
            devices = db.query(DeviceNode).order_by(DeviceNode.active.desc(), DeviceNode.name).all()
            device_configs = {}
            for device in devices:
                try:
                    parsed = json.loads(device.config_json or "{}")
                except json.JSONDecodeError:
                    parsed = {}
                if not isinstance(parsed, dict):
                    parsed = {}
                parsed.setdefault("display_brightness", 100)
                parsed.setdefault("kiosk_idle_seconds", 60)
                parsed.setdefault("screen_mode", "public")
                parsed.setdefault("screen_audience", "all")
                parsed.setdefault("screen_rotation_seconds", 12)
                parsed.setdefault("show_announcements", True)
                parsed.setdefault("show_events", True)
                parsed.setdefault("show_substitutions", True)
                device_configs[device.id] = parsed
            all_points = db.query(InteractionPoint).order_by(InteractionPoint.name).all()
            points = [
                point for point in all_points
                if point.active
            ]
            command_history: dict[int, list[dict]] = {}
            for command in db.query(DeviceCommand).order_by(
                DeviceCommand.created_at.desc(),
            ).limit(100).all():
                rows = command_history.setdefault(command.device_id, [])
                if len(rows) < 5:
                    rows.append({
                        "command": command,
                        "label": SAFE_COMMANDS.get(command.command, command.command),
                    })
            return await self.templates.TemplateResponse(request, "admin/devices.html", {
                "title": "Устройства",
                "subtitle": "Сдвояване и управление на киоски и информационни екрани",
                "devices": devices,
                "tokens": db.query(DeviceEnrollmentToken).order_by(DeviceEnrollmentToken.created_at.desc()).limit(10).all(),
                "interaction_points": points,
                "point_names": {point.id: point.name for point in all_points},
                "device_configs": device_configs,
                "available_commands": {
                    device.id: available_safe_commands(device)
                    for device in devices
                },
                "command_history": command_history,
                "can_manage": session_has_permission(request, "devices.manage"),
                "now": now_bg(),
                "ok": request.query_params.get("ok"),
                "error": request.query_params.get("error"),
            })

    @expose("/devices/pair", methods=["POST"])
    async def pair(self, request: Request):
        self.guard(request, "devices.manage")
        form = await request.form()
        try:
            valid_minutes = int(form.get("valid_minutes") or 15)
            device_type = str(form.get("device_type", "kiosk")).strip()
            if device_type not in {"kiosk", "screen"}:
                raise ValueError("От този екран могат да се сдвояват само PWA киоск или екран.")
            point_id = int(form.get("interaction_point_id") or 0)
            brightness = max(10, min(100, int(form.get("display_brightness") or 100)))
            rotation_seconds = max(5, min(60, int(form.get("screen_rotation_seconds") or 12)))
            initial_config = {
                "display_brightness": brightness,
                "kiosk_idle_seconds": max(15, min(600, int(form.get("kiosk_idle_seconds") or 60))),
            }
            if device_type == "screen":
                screen_mode = str(form.get("screen_mode") or "public")
                if screen_mode not in {"public", "paired"}:
                    raise ValueError("Невалиден режим на екрана.")
                initial_config.update({
                    "screen_mode": screen_mode,
                    "screen_audience": str(form.get("screen_audience") or "all").strip()[:100] or "all",
                    "screen_rotation_seconds": rotation_seconds,
                    "show_announcements": form.get("show_announcements") == "on",
                    "show_events": form.get("show_events") == "on",
                    "show_substitutions": form.get("show_substitutions") == "on",
                })
            with SessionLocal() as db:
                actor = current_staff(db, request)
                point = db.get(InteractionPoint, point_id)
                if point is None or not point.active:
                    raise ValueError("Изберете активна интерактивна точка.")
                if not point.screen_id:
                    raise ValueError("Избраната точка няма screen_id. Добавете го преди сдвояването.")
                token, raw = create_enrollment_token(
                    db,
                    actor,
                    label=str(form.get("label", "Ново устройство")),
                    device_type=device_type,
                    expected_identifier=str(form.get("identifier", "")) or None,
                    zone_id=point.zone_id,
                    screen_id=point.screen_id,
                    interaction_point_id=point.id,
                    initial_config=initial_config,
                    valid_minutes=valid_minutes,
                    ip_address=request_ip(request),
                )
                ticket = store_once({
                    "kind": "enrollment",
                    "name": token.label,
                    "value": raw,
                    "qr": _qr_data_uri(raw),
                    "expires_at": token.expires_at,
                    "profile": device_type,
                    "interaction_point": point.name,
                }, actor.id)
        except (ValueError, TypeError) as exc:
            return _redirect("/admin/devices", error=str(exc))
        return RedirectResponse(f"/admin/reveal/{ticket}", status_code=303)

    @expose("/devices/{device_id}/save", methods=["POST"])
    async def save_device(self, request: Request):
        self.guard(request, "devices.manage")
        form = await request.form()
        with SessionLocal() as db:
            actor = current_staff(db, request)
            device = db.get(DeviceNode, int(request.path_params["device_id"]))
            if device is None:
                raise HTTPException(status_code=404)
            device.name = str(form.get("name", device.name)).strip()[:150] or device.name
            if device.device_type in {"kiosk", "screen"}:
                try:
                    point_id = int(form.get("interaction_point_id") or 0)
                except (TypeError, ValueError):
                    return _redirect("/admin/devices", error="Изберете валидна интерактивна точка.")
                point = db.get(InteractionPoint, point_id)
                if point is None or not point.active or not point.screen_id:
                    return _redirect(
                        "/admin/devices",
                        error="PWA устройството изисква активна точка със зададен screen_id.",
                    )
                device.interaction_point_id = point.id
                device.zone_id = point.zone_id
                device.screen_id = point.screen_id
                if device.camera is not None:
                    device.camera.interaction_point_id = point.id
                    device.camera.zone_id = point.zone_id
            device.active = form.get("active") == "on"
            if not device.active:
                device.status = "offline"
            try:
                config = json.loads(device.config_json or "{}")
            except json.JSONDecodeError:
                config = {}
            if not isinstance(config, dict):
                config = {}
            try:
                config["display_brightness"] = max(10, min(100, int(form.get("display_brightness") or 100)))
                config["kiosk_idle_seconds"] = max(
                    15,
                    min(600, int(form.get("kiosk_idle_seconds") or 60)),
                )
                if device.device_type == "screen":
                    screen_mode = str(form.get("screen_mode") or "public")
                    if screen_mode not in {"public", "paired"}:
                        raise ValueError("Невалиден режим на екрана.")
                    config.update({
                        "screen_mode": screen_mode,
                        "screen_audience": str(form.get("screen_audience") or "all").strip()[:100] or "all",
                        "screen_rotation_seconds": max(
                            5,
                            min(60, int(form.get("screen_rotation_seconds") or 12)),
                        ),
                        "show_announcements": form.get("show_announcements") == "on",
                        "show_events": form.get("show_events") == "on",
                        "show_substitutions": form.get("show_substitutions") == "on",
                    })
            except (TypeError, ValueError) as exc:
                return _redirect(
                    "/admin/devices",
                    error=str(exc) if str(exc) else "Проверете числовите настройки на устройството.",
                )
            device.config_json = json.dumps(config, ensure_ascii=False)
            device.config_version += 1
            audit_event(
                db,
                "device.saved",
                f"Обновено устройство: {device.name}",
                actor=actor,
                entity_type="DeviceNode",
                entity_id=device.id,
                changes={
                    "interaction_point_id": device.interaction_point_id,
                    "zone_id": device.zone_id,
                    "screen_id": device.screen_id,
                    "active": device.active,
                    "config_version": device.config_version,
                    "config": config,
                },
                ip_address=request_ip(request),
            )
            db.commit()
        return _redirect("/admin/devices", ok="Устройството е обновено.")

    @expose("/devices/{device_id}/command", methods=["POST"])
    async def command(self, request: Request):
        self.guard(request, "devices.manage")
        form = await request.form()
        with SessionLocal() as db:
            actor = current_staff(db, request)
            device = db.get(DeviceNode, int(request.path_params["device_id"]))
            if device is None:
                raise HTTPException(status_code=404)
            try:
                item = queue_command(db, device, str(form.get("command", "")), actor, ip_address=request_ip(request))
            except ValueError as exc:
                return _redirect("/admin/devices", error=str(exc))
            delivered = await connection_manager.send_to_device(
                {
                    "type": "device_command_available",
                    "data": {"command_id": item.id},
                },
                device.identifier,
            )
        delivery_note = (
            "Устройството е известено веднага по WebSocket."
            if delivered
            else "Командата ще бъде взета при следващото polling свързване."
        )
        return _redirect(
            "/admin/devices",
            ok=f"Командата №{item.id} чака ACK. {delivery_note}",
        )

    @expose("/devices/{device_id}/rotate", methods=["POST"])
    async def rotate(self, request: Request):
        self.guard(request, "devices.manage")
        with SessionLocal() as db:
            actor = current_staff(db, request)
            device = db.get(DeviceNode, int(request.path_params["device_id"]))
            if device is None:
                raise HTTPException(status_code=404)
            raw = rotate_device_key(db, device, actor, ip_address=request_ip(request))
            ticket = store_once({"kind": "device_key", "name": device.name, "value": raw}, actor.id)
        return RedirectResponse(f"/admin/reveal/{ticket}", status_code=303)


class ScheduleImportView(PermissionedBaseView):
    name = "Импорт на разписание"
    category = "Учебен процес"
    icon = "fa-solid fa-file-arrow-up"
    permission = "schedule.import"

    @expose("/schedule-import", methods=["GET"])
    async def import_page(self, request: Request):
        self.guard(request)
        with SessionLocal() as db:
            return await self.templates.TemplateResponse(request, "admin/import.html", {
                "title": "Импорт на разписание",
                "subtitle": "Първо преглед, после потвърждение — без скрити промени",
                "jobs": db.query(ScheduleImportJob).order_by(ScheduleImportJob.created_at.desc()).limit(20).all(),
                "ok": request.query_params.get("ok"),
                "error": request.query_params.get("error"),
            })

    @expose("/schedule-import/preview", methods=["POST"])
    async def import_preview(self, request: Request):
        self.guard(request)
        form = await request.form()
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            return _redirect("/admin/schedule-import", error="Изберете CSV или XLSX файл.")
        content = await upload.read()
        try:
            with SessionLocal() as db:
                actor = current_staff(db, request)
                preview = preview_schedule_import(db, upload.filename, content)
                ticket = store_once({"file_name": upload.filename, "content": content}, actor.id)
                return await self.templates.TemplateResponse(request, "admin/import_preview.html", {
                    "title": "Преглед на импорта",
                    "subtitle": "Проверете валидните редове и грешките преди запис",
                    "preview": preview,
                    "ticket": ticket,
                    "file_name": upload.filename,
                })
        except (ValueError, RuntimeError) as exc:
            return _redirect("/admin/schedule-import", error=str(exc))

    @expose("/schedule-import/execute", methods=["POST"])
    async def import_execute(self, request: Request):
        self.guard(request)
        form = await request.form()
        with SessionLocal() as db:
            actor = current_staff(db, request)
            payload = pop_once(str(form.get("ticket", "")), actor.id)
            if payload is None:
                return _redirect("/admin/schedule-import", error="Прегледът е изтекъл. Качете файла отново.")
            try:
                job = execute_schedule_import(
                    db,
                    payload["file_name"],
                    payload["content"],
                    str(form.get("mode", "upsert")),
                    actor,
                    allow_partial=form.get("allow_partial") == "on",
                    ip_address=request_ip(request),
                )
            except (ValueError, RuntimeError) as exc:
                return _redirect("/admin/schedule-import", error=str(exc))
        if job.status == "rejected":
            return _redirect("/admin/schedule-import", error="Импортът е отказан заради грешки. Данните не са променени.")
        return _redirect("/admin/schedule-import", ok="Разписанието е импортирано успешно.")

    @expose("/schedule-import/template", methods=["GET"])
    async def import_template(self, request: Request):
        self.guard(request)
        return Response(
            timetable_csv_template(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="timetable-template.csv"'},
        )


class PrivacyView(PermissionedBaseView):
    name = "Поверителност и срокове"
    category = "Поверителност"
    icon = "fa-solid fa-user-shield"
    permission = "privacy.view"

    @expose("/privacy", methods=["GET"])
    async def privacy(self, request: Request):
        self.guard(request)
        with SessionLocal() as db:
            return await self.templates.TemplateResponse(request, "admin/privacy.html", {
                "title": "Поверителност и срокове",
                "subtitle": "Предварителен преглед преди необратимо почистване",
                "preview": retention_preview(db),
                "can_manage": session_has_permission(request, "privacy.manage"),
                "ok": request.query_params.get("ok"),
                "error": request.query_params.get("error"),
            })

    @expose("/privacy/cleanup", methods=["POST"])
    async def cleanup(self, request: Request):
        self.guard(request, "privacy.manage")
        form = await request.form()
        if str(form.get("confirmation", "")).strip().upper() != "ИЗТРИЙ":
            return _redirect("/admin/privacy", error="За потвърждение напишете ИЗТРИЙ.")
        with SessionLocal() as db:
            actor = current_staff(db, request)
            run = execute_retention_cleanup(db, actor, ip_address=request_ip(request))
            summary = json.loads(run.summary_json)
        return _redirect("/admin/privacy", ok=f"Почистването завърши: {sum(summary.values())} премахнати записа.")


class BackupView(PermissionedBaseView):
    name = "Резервни копия"
    category = "Система"
    icon = "fa-solid fa-database"
    permission = "backups.view"

    @expose("/backups", methods=["GET"])
    async def backups(self, request: Request):
        self.guard(request)
        with SessionLocal() as db:
            return await self.templates.TemplateResponse(request, "admin/backups.html", {
                "title": "Резервни копия",
                "subtitle": "Проверими PostgreSQL/SQLite архиви със SHA-256",
                "records": db.query(BackupRecord).order_by(BackupRecord.created_at.desc()).all(),
                "can_manage": session_has_permission(request, "backups.manage"),
                "ok": request.query_params.get("ok"),
                "error": request.query_params.get("error"),
            })

    @expose("/backups/generate", methods=["POST"])
    async def create_backup(self, request: Request):
        self.guard(request, "backups.manage")
        try:
            with SessionLocal() as db:
                actor = current_staff(db, request)
                record = create_database_backup(db, actor, ip_address=request_ip(request))
        except RuntimeError as exc:
            return _redirect("/admin/backups", error=str(exc))
        return _redirect("/admin/backups", ok=f"Създадено и проверено копие: {record.file_name}")

    @expose("/backups/{backup_id}/verify", methods=["POST"])
    async def verify(self, request: Request):
        self.guard(request, "backups.manage")
        with SessionLocal() as db:
            record = db.get(BackupRecord, int(request.path_params["backup_id"]))
            if record is None:
                raise HTTPException(status_code=404)
            valid = verify_backup(db, record)
        return _redirect("/admin/backups", ok="Копието е валидно." if valid else None, error=None if valid else "Копието е повредено или липсва.")

    @expose("/backups/{backup_id}/download", methods=["GET"])
    async def download(self, request: Request):
        self.guard(request, "backups.view")
        with SessionLocal() as db:
            record = db.get(BackupRecord, int(request.path_params["backup_id"]))
            if record is None:
                raise HTTPException(status_code=404)
            try:
                path = downloadable_backup_path(record)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return FileResponse(path, filename=record.file_name, media_type=backup_media_type(record))


class RevealView(PermissionedBaseView):
    name = ""
    permission = "dashboard.view"
    is_visible = lambda self, request: False

    @expose("/reveal/{ticket}", methods=["GET"])
    async def reveal(self, request: Request):
        self.guard(request)
        payload = pop_once(request.path_params["ticket"], int(request.session["staff_id"]))
        if payload is None:
            return await self.templates.TemplateResponse(request, "admin/reveal.html", {
                "title": "Стойността вече не е налична",
                "subtitle": "За сигурност всяка чувствителна стойност се показва само веднъж",
                "payload": None,
            }, status_code=410)
        return await self.templates.TemplateResponse(request, "admin/reveal.html", {
            "title": "Запазете стойността сега",
            "subtitle": "След напускане на страницата тя не може да бъде възстановена",
            "payload": payload,
        })


CONTROL_VIEWS = [
    DashboardView,
    BadgeWorkflowView,
    ScheduleImportView,
    DeviceManagementView,
    DiagnosticsView,
    SettingsView,
    PrivacyView,
    BackupView,
    StaffManagementView,
    RevealView,
]
