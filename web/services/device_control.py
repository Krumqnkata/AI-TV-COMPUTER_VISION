"""Per-device enrollment, authentication, configuration and safe commands."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from engine.admin_models import (
    DeviceCommand,
    DeviceCredential,
    DeviceEnrollmentToken,
    DeviceNode,
    StaffAccount,
)
from engine.db import Camera, InteractionPoint, now_bg
from utils.config import Config
from web.services.admin_control import audit_event, get_setting


SAFE_COMMANDS: dict[str, str] = {
    "refresh_config": "Опресни конфигурацията",
    "enable": "Включи приложението",
    "disable": "Постави приложението на пауза",
    "request_diagnostics": "Изискай актуална диагностика",
    "check_connectivity": "Провери връзката със сървъра",
    "update_app": "Провери и приложи PWA обновяване",
    "clear_pwa_cache": "Изчисти PWA кеша и презареди",
    "test_camera": "Тествай камерата",
    "test_audio": "Тествай звука",
    "test_screen": "Тествай екрана",
    "restart_app": "Рестартирай приложението",
}
PWA_COMMANDS_BY_PROFILE: dict[str, frozenset[str]] = {
    "kiosk": frozenset(SAFE_COMMANDS),
    "screen": frozenset({
        "refresh_config",
        "enable",
        "disable",
        "request_diagnostics",
        "check_connectivity",
        "update_app",
        "clear_pwa_cache",
        "test_screen",
        "restart_app",
    }),
}
NODE_COMMANDS = frozenset({
    "refresh_config",
    "enable",
    "disable",
    "test_camera",
    "test_audio",
    "test_screen",
    "restart_app",
})


@dataclass(frozen=True)
class DeviceContext:
    device: DeviceNode | None
    credential: DeviceCredential | None = None
    legacy: bool = False

    @property
    def identifier(self) -> str:
        return self.device.identifier if self.device else "legacy-shared-key"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fingerprint(value: str) -> str:
    return _hash(value)[:12]


def _new_device_key() -> str:
    return f"dev_{secrets.token_urlsafe(36)}"


def create_enrollment_token(
    db: Session,
    actor: StaffAccount,
    *,
    label: str,
    device_type: str,
    expected_identifier: str | None = None,
    zone_id: str | None = None,
    screen_id: str | None = None,
    interaction_point_id: int | None = None,
    initial_config: dict[str, Any] | None = None,
    valid_minutes: int = 15,
    ip_address: str | None = None,
) -> tuple[DeviceEnrollmentToken, str]:
    valid_minutes = max(5, min(int(valid_minutes), 24 * 60))
    raw = f"enr_{secrets.token_urlsafe(32)}"
    item = DeviceEnrollmentToken(
        token_hash=_hash(raw),
        label=label.strip()[:150],
        device_type=device_type.strip()[:30],
        expected_identifier=(expected_identifier or "").strip()[:100] or None,
        zone_id=(zone_id or "").strip()[:50] or None,
        screen_id=(screen_id or "").strip()[:50] or None,
        interaction_point_id=interaction_point_id,
        initial_config_json=json.dumps(initial_config or {}, ensure_ascii=False),
        expires_at=now_bg() + timedelta(minutes=valid_minutes),
        created_by_staff_id=actor.id,
    )
    db.add(item)
    audit_event(
        db,
        "device.enrollment_created",
        f"Създаден код за сдвояване: {item.label}",
        actor=actor,
        entity_type="DeviceEnrollmentToken",
        changes={
            "device_type": item.device_type,
            "expected_identifier": item.expected_identifier,
            "interaction_point_id": item.interaction_point_id,
        },
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(item)
    return item, raw


def enroll_device(
    db: Session,
    *,
    enrollment_token: str,
    identifier: str,
    name: str,
    device_type: str,
    capabilities: list[str] | None = None,
    software_version: str | None = None,
    require_interaction_point: bool = False,
) -> tuple[DeviceNode, str]:
    now = now_bg()
    token = db.query(DeviceEnrollmentToken).filter(
        DeviceEnrollmentToken.token_hash == _hash(enrollment_token),
    ).first()
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise ValueError("Кодът за сдвояване е невалиден или изтекъл")

    identifier = identifier.strip()
    if not identifier or len(identifier) > 100:
        raise ValueError("Невалиден идентификатор на устройство")
    if token.expected_identifier and token.expected_identifier != identifier:
        raise ValueError("Кодът е издаден за друго устройство")
    if token.device_type != device_type:
        raise ValueError("Типът на устройството не съвпада с кода")

    device = db.query(DeviceNode).filter(DeviceNode.identifier == identifier).first()
    if device is None:
        device = DeviceNode(identifier=identifier, name=name.strip()[:150], device_type=device_type)
        db.add(device)
    else:
        for credential in device.credentials:
            credential.active = False
        device.name = name.strip()[:150] or device.name
        device.device_type = device_type

    point = db.get(InteractionPoint, token.interaction_point_id) if token.interaction_point_id else None
    if point is not None and not point.active:
        raise ValueError("Интерактивната точка за този код вече не е активна")
    if require_interaction_point and device_type in {"kiosk", "screen"} and point is None:
        raise ValueError("Кодът за PWA устройство няма зададена интерактивна точка")
    if point is not None and device_type in {"kiosk", "screen"} and not point.screen_id:
        raise ValueError("Интерактивната точка няма зададен screen_id")

    device.zone_id = point.zone_id if point is not None else token.zone_id
    device.screen_id = point.screen_id if point is not None else token.screen_id
    device.interaction_point_id = point.id if point is not None else None
    device.active = True
    device.status = "online"
    device.config_json = token.initial_config_json or "{}"
    device.capabilities_json = json.dumps(sorted(set(capabilities or [])), ensure_ascii=False)
    device.software_version = (software_version or "").strip()[:50] or None
    device.last_seen_at = now
    db.flush()

    if device_type == "kiosk":
        camera = device.camera
        if camera is None:
            camera = db.query(Camera).filter(
                Camera.stream_url == f"device-local://{identifier}",
            ).first()
        if camera is None:
            camera = Camera(
                name=f"CAM-DEVICE-{device.id}",
                zone_id=device.zone_id,
                interaction_point_id=device.interaction_point_id,
                stream_url=f"device-local://{identifier}",
                active=True,
            )
            db.add(camera)
            db.flush()
        else:
            camera.zone_id = device.zone_id
            camera.interaction_point_id = device.interaction_point_id
            camera.active = True
        device.camera_id = camera.id
    elif device_type == "screen":
        device.camera_id = None

    raw_key = _new_device_key()
    credential = DeviceCredential(
        device_id=device.id,
        key_hash=_hash(raw_key),
        fingerprint=_fingerprint(raw_key),
        active=True,
        created_at=now,
    )
    db.add(credential)
    token.used_at = now
    token.used_by_device_id = device.id
    db.commit()
    db.refresh(device)
    return device, raw_key


def authenticate_device(
    db: Session,
    device_identifier: str | None,
    supplied_key: str | None,
    *,
    touch: bool = True,
) -> DeviceContext | None:
    if not supplied_key:
        return None
    if device_identifier:
        credential = db.query(DeviceCredential).filter(
            DeviceCredential.key_hash == _hash(supplied_key),
            DeviceCredential.active.is_(True),
        ).first()
        now = now_bg()
        if (
            credential
            and credential.device
            and credential.device.active
            and credential.device.identifier == device_identifier
            and (credential.expires_at is None or credential.expires_at > now)
        ):
            if touch:
                credential.last_used_at = now
                db.commit()
            return DeviceContext(device=credential.device, credential=credential)

    legacy_enabled = bool(get_setting(db, "devices.legacy_shared_key_enabled"))
    if legacy_enabled and Config.DEVICE_API_KEY and hmac.compare_digest(supplied_key, Config.DEVICE_API_KEY):
        return DeviceContext(device=None, credential=None, legacy=True)
    return None


def rotate_device_key(
    db: Session,
    device: DeviceNode,
    actor: StaffAccount,
    *,
    ip_address: str | None = None,
) -> str:
    for credential in device.credentials:
        credential.active = False
    raw_key = _new_device_key()
    db.add(DeviceCredential(
        device_id=device.id,
        key_hash=_hash(raw_key),
        fingerprint=_fingerprint(raw_key),
        active=True,
    ))
    audit_event(
        db,
        "device.key_rotated",
        f"Сменен ключ на устройство: {device.name}",
        actor=actor,
        entity_type="DeviceNode",
        entity_id=device.id,
        changes={"fingerprint": _fingerprint(raw_key)},
        ip_address=ip_address,
    )
    db.commit()
    return raw_key


def update_heartbeat(
    db: Session,
    context: DeviceContext,
    *,
    status: str = "online",
    software_version: str | None = None,
    capabilities: list[str] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> DeviceNode:
    if context.device is None:
        raise ValueError("Старият общ ключ не може да изпраща управляван heartbeat")
    device = context.device
    device.last_seen_at = now_bg()
    device.status = status[:30] if status in {"online", "warning", "error", "paused"} else "online"
    if software_version is not None:
        device.software_version = software_version.strip()[:50] or None
    if capabilities is not None:
        device.capabilities_json = json.dumps(sorted(set(capabilities)), ensure_ascii=False)
    if diagnostics is not None:
        encoded_diagnostics = json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)
        if len(encoded_diagnostics) > 5_000:
            raise ValueError("Диагностичните данни са твърде големи")
        device.diagnostics_json = encoded_diagnostics
    db.commit()
    db.refresh(device)
    return device


def record_websocket_event(
    db: Session,
    device_identifier: str | None,
    *,
    connected: bool,
) -> None:
    """Persist WebSocket lifecycle timestamps without changing heartbeat truth."""
    if not device_identifier:
        return
    device = db.query(DeviceNode).filter(
        DeviceNode.identifier == device_identifier,
    ).first()
    if device is None:
        return
    now = now_bg()
    if connected:
        device.last_websocket_at = now
        device.last_seen_at = now
        if device.active and device.status == "offline":
            device.status = "online"
    else:
        device.last_websocket_disconnected_at = now
    db.commit()


def parse_device_diagnostics(device: DeviceNode) -> dict[str, Any]:
    try:
        value = json.loads(device.diagnostics_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def device_config(db: Session, context: DeviceContext) -> dict[str, Any]:
    if context.device is None:
        return {
            "legacy": True,
            "config_version": 0,
            "settings": {
                "kiosk_idle_seconds": get_setting(db, "sessions.kiosk_idle_seconds"),
                "voice_enabled": get_setting(db, "features.voice_enabled"),
            },
        }
    try:
        custom = json.loads(context.device.config_json or "{}")
    except json.JSONDecodeError:
        custom = {}
    if not isinstance(custom, dict):
        custom = {}
    try:
        capabilities = json.loads(context.device.capabilities_json or "[]")
    except json.JSONDecodeError:
        capabilities = []
    return {
        "legacy": False,
        "device_id": context.device.identifier,
        "device_name": context.device.name,
        "device_type": context.device.device_type,
        "zone_id": context.device.zone_id,
        "screen_id": context.device.screen_id,
        "camera_id": context.device.camera.name if context.device.camera else None,
        "interaction_point_id": context.device.interaction_point_id,
        "capabilities": capabilities,
        "software_version": context.device.software_version,
        "config_version": context.device.config_version,
        "settings": {
            "kiosk_idle_seconds": get_setting(db, "sessions.kiosk_idle_seconds"),
            "qr_same_camera_seconds": get_setting(db, "qr.same_camera_seconds"),
            "qr_cross_camera_seconds": get_setting(db, "qr.cross_camera_seconds"),
            "voice_enabled": get_setting(db, "features.voice_enabled"),
            **custom,
        },
    }


def update_device_config(
    db: Session,
    device: DeviceNode,
    config: dict[str, Any],
    actor: StaffAccount,
    *,
    ip_address: str | None = None,
) -> None:
    encoded = json.dumps(config, ensure_ascii=False)
    if len(encoded) > 20_000:
        raise ValueError("Конфигурацията е твърде голяма")
    device.config_json = encoded
    device.config_version += 1
    audit_event(
        db,
        "device.config_updated",
        f"Обновена конфигурация: {device.name}",
        actor=actor,
        entity_type="DeviceNode",
        entity_id=device.id,
        changes={"config_version": device.config_version, "config": config},
        ip_address=ip_address,
    )
    db.commit()


def queue_command(
    db: Session,
    device: DeviceNode,
    command: str,
    actor: StaffAccount,
    *,
    payload: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> DeviceCommand:
    if command not in SAFE_COMMANDS:
        raise ValueError("Непозволена команда")
    allowed_commands = PWA_COMMANDS_BY_PROFILE.get(
        device.device_type,
        NODE_COMMANDS,
    )
    if command not in allowed_commands:
        raise ValueError("Командата не се поддържа от този тип устройство")
    item = DeviceCommand(
        device_id=device.id,
        command=command,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
        status="pending",
        created_by_staff_id=actor.id,
    )
    db.add(item)
    audit_event(
        db,
        "device.command_queued",
        f"Изпратена команда „{SAFE_COMMANDS[command]}“ към {device.name}",
        actor=actor,
        entity_type="DeviceCommand",
        changes={"command": command, "device_id": device.id},
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(item)
    return item


def available_safe_commands(device: DeviceNode) -> dict[str, str]:
    allowed = PWA_COMMANDS_BY_PROFILE.get(device.device_type, NODE_COMMANDS)
    return {
        command: label
        for command, label in SAFE_COMMANDS.items()
        if command in allowed
    }


def pending_commands(db: Session, context: DeviceContext) -> list[DeviceCommand]:
    if context.device is None:
        return []
    commands = db.query(DeviceCommand).filter(
        DeviceCommand.device_id == context.device.id,
        DeviceCommand.status.in_(("pending", "delivered")),
    ).order_by(DeviceCommand.id).limit(20).all()
    now = now_bg()
    for item in commands:
        if item.status == "pending":
            item.status = "delivered"
            item.delivered_at = now
    db.commit()
    return commands


def acknowledge_command(
    db: Session,
    context: DeviceContext,
    command_id: int,
    *,
    success: bool,
    result: dict[str, Any] | None = None,
) -> DeviceCommand:
    if context.device is None:
        raise ValueError("Командата не принадлежи на това устройство")
    item = db.get(DeviceCommand, command_id)
    if item is None or item.device_id != context.device.id:
        raise LookupError("Командата не е намерена")
    if item.status in {"acknowledged", "failed"}:
        return item
    item.status = "acknowledged" if success else "failed"
    item.acknowledged_at = now_bg()
    item.result_json = json.dumps(result or {}, ensure_ascii=False)
    db.commit()
    db.refresh(item)
    return item


def context_allows_scope(
    context: DeviceContext,
    *,
    zone_id: str | None = None,
    screen_id: str | None = None,
    camera_identifier: str | None = None,
    interaction_point_id: int | None = None,
) -> bool:
    if context.legacy or context.device is None:
        return True
    if zone_id and context.device.zone_id != zone_id:
        return False
    if screen_id and context.device.screen_id != screen_id:
        return False
    if interaction_point_id and context.device.interaction_point_id != interaction_point_id:
        return False
    if camera_identifier and (
        context.device.camera is None
        or context.device.camera.name != camera_identifier
    ):
        return False
    return True


def revoke_device_context(db: Session, context: DeviceContext) -> None:
    """Revoke only the credential used by the current browser profile."""
    if context.legacy or context.device is None:
        return
    if context.credential is not None:
        context.credential.active = False
    context.device.status = "offline"
    db.commit()


def mark_offline_devices(db: Session) -> int:
    threshold = now_bg() - timedelta(seconds=int(get_setting(db, "devices.offline_after_seconds")))
    devices = db.query(DeviceNode).filter(
        DeviceNode.active.is_(True),
        DeviceNode.last_seen_at.is_not(None),
        DeviceNode.last_seen_at < threshold,
        DeviceNode.status != "offline",
    ).all()
    for device in devices:
        device.status = "offline"
    if devices:
        db.commit()
    return len(devices)
