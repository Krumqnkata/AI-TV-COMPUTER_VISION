"""Administrative identity, permissions, settings, secrets and audit helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, inspect
from sqlalchemy.orm import Session

from engine.admin_models import (
    AdminAuditEvent,
    ArchivedRecord,
    EncryptedSecret,
    StaffAccount,
    StaffPermission,
    StaffRole,
    SystemSetting,
)
from engine.auth import verify_password
from engine.db import Person, now_bg
from utils.config import Config


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    category: str
    label: str
    description: str
    value_type: str
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    restart_required: bool = False


PERMISSION_DEFINITIONS: tuple[tuple[str, str, str, str], ...] = (
    ("dashboard.view", "Преглед на таблото", "Начало", "Здраве на системата и обобщения."),
    ("people.view", "Преглед на хора", "Хора", "Ученици, учители, класове и баджове."),
    ("people.manage", "Управление на хора", "Хора", "Създаване и редактиране на профили и класове."),
    ("badges.manage", "Управление на QR баджове", "Хора", "Издаване, спиране и повторно издаване."),
    ("schedule.view", "Преглед на разписание", "Учебен процес", "Разписание, замествания и дежурства."),
    ("schedule.manage", "Управление на разписание", "Учебен процес", "Редакция на учебни записи."),
    ("schedule.import", "Импорт на разписание", "Учебен процес", "CSV/XLSX преглед и импорт."),
    ("content.view", "Преглед на съдържание", "Съдържание", "Новини, събития, задачи и указател."),
    ("content.manage", "Управление на съдържание", "Съдържание", "Публикуване и архивиране."),
    ("messages.view", "Преглед на съобщения", "Комуникация", "Виртуална поща и кампании."),
    ("messages.manage", "Управление на съобщения", "Комуникация", "Създаване и прекратяване на съобщения."),
    ("devices.view", "Преглед на устройства", "Устройства", "Състояние, версии и конфигурация."),
    ("devices.manage", "Управление на устройства", "Устройства", "Сдвояване, ключове и безопасни команди."),
    ("assistant.view", "Преглед на AI настройките", "Асистент", "Активен доставчик и функции."),
    ("assistant.manage", "Управление на AI настройките", "Асистент", "Модел, поведение и защитени ключове."),
    ("privacy.view", "Преглед на поверителност", "Поверителност", "Срокове и предварителен преглед."),
    ("privacy.manage", "Управление на поверителност", "Поверителност", "Почистване и политики."),
    ("backups.view", "Преглед на архиви", "Система", "Състояние и проверка на резервни копия."),
    ("backups.manage", "Управление на архиви", "Система", "Създаване и изтегляне на резервни копия."),
    ("settings.view", "Преглед на настройки", "Система", "Оперативни настройки без deployment тайни."),
    ("settings.manage", "Управление на настройки", "Система", "Промяна на безопасни настройки."),
    ("staff.view", "Преглед на служебни профили", "Достъп", "Профили, роли и права."),
    ("staff.manage", "Управление на служебни профили", "Достъп", "Създаване, спиране и роли."),
    ("audit.view", "Преглед на одит", "Достъп", "Следа на административните действия."),
)

ALL_PERMISSION_CODES = frozenset(item[0] for item in PERMISSION_DEFINITIONS)

ROLE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "superadmin": {
        "name": "Главен администратор",
        "description": "Пълен достъп до контролния център.",
        "permissions": ALL_PERMISSION_CODES,
    },
    "school_admin": {
        "name": "Училищен администратор",
        "description": "Ежедневно управление без служебни профили и защитени AI ключове.",
        "permissions": ALL_PERMISSION_CODES - {"staff.manage", "assistant.manage"},
    },
    "teacher_editor": {
        "name": "Учител редактор",
        "description": "Разписание, съдържание и комуникация.",
        "permissions": {
            "dashboard.view", "people.view", "schedule.view", "schedule.manage",
            "schedule.import", "content.view", "content.manage", "messages.view",
            "messages.manage", "devices.view", "assistant.view",
        },
    },
    "technical_operator": {
        "name": "Технически оператор",
        "description": "Устройства, архиви и диагностика без лични съобщения.",
        "permissions": {
            "dashboard.view", "devices.view", "devices.manage", "backups.view",
            "backups.manage", "settings.view", "audit.view", "privacy.view",
        },
    },
}


SETTING_DEFINITIONS: tuple[SettingDefinition, ...] = (
    SettingDefinition("school.name", "Училище", "Име на училището", "Показва се в панела и на екраните.", "string", "Училищен AI асистент"),
    SettingDefinition("school.subtitle", "Училище", "Кратко описание", "Помага на потребителите да разпознаят системата.", "string", "Информационен и комуникационен център"),
    SettingDefinition("appearance.accent", "Външен вид", "Основен цвят", "Цвят за бутони и активни елементи.", "color", "#2563eb"),
    SettingDefinition("appearance.compact", "Външен вид", "Компактен режим", "Показва повече редове на големи екрани.", "boolean", False),
    SettingDefinition("sessions.kiosk_idle_seconds", "Сесии", "Неактивност на киоск", "След колко секунди киоск сесията приключва.", "integer", 60, 15, 900),
    SettingDefinition("qr.same_camera_seconds", "QR разпознаване", "Повторение от същата камера", "Прозорец за игнориране на повторно сканиране.", "integer", 10, 1, 120),
    SettingDefinition("qr.cross_camera_seconds", "QR разпознаване", "Повторение от друга камера", "Прозорец за избор на по-увереното засичане.", "integer", 5, 1, 60),
    SettingDefinition("messages.default_valid_hours", "Съобщения", "Стандартна валидност", "Часове, през които ново съобщение чака доставка.", "integer", 24, 1, 720),
    SettingDefinition("dashboard.refresh_seconds", "Табло", "Опресняване на таблото", "Интервал за автоматично обновяване.", "integer", 30, 10, 300),
    SettingDefinition("devices.offline_after_seconds", "Устройства", "Праг за офлайн", "Устройство без heartbeat се показва като офлайн.", "integer", 90, 30, 3600),
    SettingDefinition("operations.ack_warning_seconds", "Наблюдение", "Праг за липсващ ACK", "След колко секунди непотвърдена команда или доставка да стане предупреждение.", "integer", 120, 30, 86400),
    SettingDefinition("operations.backup_warning_hours", "Наблюдение", "Праг за стар backup", "След колко часа последното проверено копие да се счита за старо.", "integer", 24, 1, 720),
    SettingDefinition("operations.disk_min_free_mb", "Наблюдение", "Минимално свободно дисково място", "Предупреждение, когато свободното място при backup директорията падне под този праг.", "integer", 2048, 128, 1048576),
    SettingDefinition("operations.maintenance_enabled", "Наблюдение", "Следене на автоматичната поддръжка", "Включете след инсталиране и стартиране на school-ai-maintenance.timer.", "boolean", False),
    SettingDefinition("operations.maintenance_warning_hours", "Наблюдение", "Праг за периодичните задачи", "След колко часа без успешно backup/retention изпълнение да има предупреждение.", "integer", 30, 1, 720),
    SettingDefinition("devices.legacy_shared_key_enabled", "Устройства", "Стар общ ключ", "Временно допуска DEVICE_API_KEY за стари клиенти.", "boolean", bool(Config.DEVICE_API_KEY)),
    SettingDefinition("assistant.provider", "AI асистент", "Доставчик", "Правила, Gemini или локален Ollama.", "choice", "rules", choices=("rules", "gemini", "ollama")),
    SettingDefinition("assistant.gemini_model", "AI асистент", "Gemini модел", "Идентификаторът се използва само когато доставчикът е Gemini.", "string", Config.GEMINI_MODEL_ID),
    SettingDefinition("assistant.ollama_model", "AI асистент", "Ollama модел", "Името на локалния модел се използва само когато доставчикът е Ollama.", "string", Config.OLLAMA_MODEL),
    SettingDefinition("assistant.temperature", "AI асистент", "Креативност", "По-ниската стойност дава по-предвидими отговори.", "number", Config.AI_TEMPERATURE, 0, 2),
    SettingDefinition("assistant.external_calls_per_minute", "AI асистент", "Заявки в минута", "Максимален брой външни AI заявки за един доставчик и процес.", "integer", 20, 1, 120),
    SettingDefinition("assistant.circuit_failure_threshold", "AI асистент", "Праг за временно спиране", "Последователни грешки преди временно блокиране на външния доставчик.", "integer", 3, 1, 10),
    SettingDefinition("assistant.circuit_reset_seconds", "AI асистент", "Възстановяване след грешки", "Секунди преди нов опит след отваряне на защитния прекъсвач.", "integer", 60, 10, 600),
    SettingDefinition("features.voice_enabled", "Функции", "Гласов асистент", "Разрешава гласови заявки на устройствата.", "boolean", True),
    SettingDefinition("features.kiosk_auto_speak_answers", "Функции", "Автоматично прочитане в киоска", "Автоматично прочита само неповерителните отговори; личните изискват действие от потребителя.", "boolean", False),
    SettingDefinition("features.public_stats_enabled", "Функции", "Публична статистика", "Показва обобщени данни на началния екран.", "boolean", True),
    SettingDefinition("privacy.system_events_days", "Поверителност", "Системни събития", "Срок за съхранение в дни.", "integer", 90, 7, 3650),
    SettingDefinition("privacy.audit_days", "Поверителност", "Административен одит", "Срок за съхранение в дни.", "integer", 365, 30, 3650),
    SettingDefinition("privacy.delivered_messages_days", "Поверителност", "Доставени съобщения", "Кога доставените съобщения да се архивират и изтрият.", "integer", 30, 1, 3650),
    SettingDefinition("privacy.import_jobs_days", "Поверителност", "Импортни отчети", "Срок за отчети и грешки от импорт.", "integer", 30, 1, 3650),
)

SETTING_MAP = {definition.key: definition for definition in SETTING_DEFINITIONS}

SECRET_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("gemini.api_key", "AI асистент", "Gemini API ключ"),
    ("ollama.api_key", "AI асистент", "Ollama/API gateway ключ"),
)
SECRET_MAP = {key: (category, label) for key, category, label in SECRET_DEFINITIONS}

_SENSITIVE_PARTS = ("password", "secret", "token", "api_key", "key_hash", "ciphertext")


def _redact(value: Any, key: str = "") -> Any:
    if any(part in key.lower() for part in _SENSITIVE_PARTS):
        return "[скрито]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def audit_event(
    db: Session,
    action: str,
    summary: str,
    *,
    actor: StaffAccount | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    changes: Any = None,
    ip_address: str | None = None,
) -> AdminAuditEvent:
    event = AdminAuditEvent(
        actor_staff_id=actor.id if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        summary=summary[:500],
        changes_json=json.dumps(_redact(changes), ensure_ascii=False, default=str) if changes is not None else None,
        ip_address=(ip_address or "")[:64] or None,
    )
    db.add(event)
    return event


def _upsert_permissions_and_roles(db: Session) -> None:
    permissions: dict[str, StaffPermission] = {}
    for code, name, category, description in PERMISSION_DEFINITIONS:
        item = db.query(StaffPermission).filter(StaffPermission.code == code).first()
        if item is None:
            item = StaffPermission(code=code, name=name, category=category, description=description)
            db.add(item)
        else:
            item.name, item.category, item.description = name, category, description
        permissions[code] = item
    db.flush()

    for code, definition in ROLE_DEFINITIONS.items():
        role = db.query(StaffRole).filter(StaffRole.code == code).first()
        if role is None:
            role = StaffRole(code=code)
            db.add(role)
        role.name = definition["name"]
        role.description = definition["description"]
        role.system_role = True
        role.active = True
        role.permissions = [permissions[value] for value in sorted(definition["permissions"])]
    db.flush()


def _ensure_settings(db: Session) -> None:
    existing = {item.key: item for item in db.query(SystemSetting).all()}
    migrated_defaults: dict[str, Any] = {}
    legacy_model = existing.get("assistant.model")
    if legacy_model is not None:
        try:
            legacy_value = json.loads(legacy_model.value_json)
        except (TypeError, json.JSONDecodeError):
            legacy_value = None
        if isinstance(legacy_value, str) and legacy_value.strip():
            provider = "rules"
            provider_item = existing.get("assistant.provider")
            if provider_item is not None:
                try:
                    provider = str(json.loads(provider_item.value_json))
                except (TypeError, json.JSONDecodeError):
                    provider = "rules"
            target = (
                "assistant.ollama_model"
                if provider == "ollama"
                else "assistant.gemini_model"
            )
            migrated_defaults[target] = legacy_value.strip()

    for definition in SETTING_DEFINITIONS:
        item = existing.get(definition.key)
        if item is None:
            default = migrated_defaults.get(definition.key, definition.default)
            item = SystemSetting(
                key=definition.key,
                value_json=json.dumps(default, ensure_ascii=False),
            )
            db.add(item)
        item.category = definition.category
        item.label = definition.label
        item.description = definition.description
        item.value_type = definition.value_type
        item.restart_required = definition.restart_required


def _role_for_person(person: Person) -> str:
    return "superadmin" if person.role == "admin" else "teacher_editor"


def _unique_username(db: Session, preferred: str) -> str:
    base = preferred.strip()[:100] or "staff"
    candidate = base
    suffix = 1
    while db.query(StaffAccount).filter(func.lower(StaffAccount.username) == candidate.lower()).first():
        suffix += 1
        tail = f"-{suffix}"
        candidate = f"{base[:100 - len(tail)]}{tail}"
    return candidate


def provision_staff_from_person(db: Session, person: Person) -> StaffAccount:
    account = db.query(StaffAccount).filter(StaffAccount.linked_person_id == person.id).first()
    if account is None:
        account = StaffAccount(
            username=_unique_username(db, person.full_name),
            display_name=person.full_name,
            linked_person_id=person.id,
            password_hash=person.password_hash or "",
            active=person.active,
        )
        db.add(account)
    role = db.query(StaffRole).filter(StaffRole.code == _role_for_person(person)).one()
    if role not in account.roles:
        account.roles.append(role)
    return account


def ensure_admin_foundation(db: Session) -> None:
    """Idempotently install system roles/settings and bridge legacy staff."""
    _upsert_permissions_and_roles(db)
    _ensure_settings(db)
    db.flush()
    legacy_people = db.query(Person).filter(
        Person.role.in_(("admin", "teacher")),
        Person.active.is_(True),
        Person.password_hash.is_not(None),
    ).all()
    for person in legacy_people:
        provision_staff_from_person(db, person)
    db.commit()


def permissions_for_account(account: StaffAccount) -> set[str]:
    return {
        permission.code
        for role in account.roles
        if role.active
        for permission in role.permissions
    }


def has_permission(account: StaffAccount | None, code: str) -> bool:
    return bool(account and account.active and code in permissions_for_account(account))


def authenticate_staff(
    db: Session,
    username: str,
    password: str,
    *,
    ip_address: str | None = None,
) -> StaffAccount | None:
    """Authenticate a staff account with lockout and legacy migration."""
    normalized = username.strip()
    # SQLite's lower() handles ASCII only. Try an exact match first so legacy
    # Bulgarian full names remain valid login identifiers.
    account = db.query(StaffAccount).filter(StaffAccount.username == normalized).first()
    if account is None:
        account = db.query(StaffAccount).filter(func.lower(StaffAccount.username) == normalized.lower()).first()
    now = now_bg()

    if account is None:
        person = db.query(Person).filter(Person.full_name == normalized).first()
        if person is None:
            person = db.query(Person).filter(func.lower(Person.full_name) == normalized.lower()).first()
        if (
            person
            and person.active
            and person.role in ("admin", "teacher")
            and person.password_hash
            and verify_password(password, person.password_hash)
        ):
            _upsert_permissions_and_roles(db)
            account = provision_staff_from_person(db, person)
            db.flush()
        else:
            return None

    if not account.active:
        return None
    if account.locked_until and account.locked_until > now:
        audit_event(db, "auth.login_locked", "Отказан вход за временно заключен профил", actor=account, ip_address=ip_address)
        db.commit()
        return None
    if account.locked_until and account.locked_until <= now:
        account.locked_until = None
        account.failed_login_count = 0

    if not account.password_hash or not verify_password(password, account.password_hash):
        account.failed_login_count += 1
        if account.failed_login_count >= Config.ADMIN_LOGIN_MAX_FAILURES:
            account.locked_until = now + timedelta(minutes=Config.ADMIN_LOGIN_LOCK_MINUTES)
            account.failed_login_count = 0
        audit_event(db, "auth.login_failed", "Неуспешен опит за вход", actor=account, ip_address=ip_address)
        db.commit()
        return None

    linked = account.linked_person
    if linked and not linked.active:
        return None
    account.failed_login_count = 0
    account.locked_until = None
    account.last_login_at = now
    audit_event(db, "auth.login", "Успешен вход в административния панел", actor=account, ip_address=ip_address)
    db.commit()
    db.refresh(account)
    return account


def validate_setting(key: str, raw_value: Any) -> Any:
    definition = SETTING_MAP.get(key)
    if definition is None:
        raise ValueError("Непозната настройка")
    if definition.value_type == "boolean":
        if isinstance(raw_value, bool):
            value = raw_value
        elif str(raw_value).strip().lower() in {"1", "true", "on", "yes", "да"}:
            value = True
        elif str(raw_value).strip().lower() in {"0", "false", "off", "no", "не", ""}:
            value = False
        else:
            raise ValueError("Очаква се Да или Не")
    elif definition.value_type == "integer":
        value = int(raw_value)
    elif definition.value_type == "number":
        value = float(raw_value)
    else:
        value = str(raw_value).strip()
        if len(value) > 500:
            raise ValueError("Стойността е твърде дълга")
        if definition.value_type == "color":
            if len(value) != 7 or not value.startswith("#") or any(c not in "0123456789abcdefABCDEF" for c in value[1:]):
                raise ValueError("Цветът трябва да е във формат #RRGGBB")
        if definition.choices and value not in definition.choices:
            raise ValueError("Невалиден избор")

    if isinstance(value, (int, float)):
        if definition.minimum is not None and value < definition.minimum:
            raise ValueError(f"Минималната стойност е {definition.minimum:g}")
        if definition.maximum is not None and value > definition.maximum:
            raise ValueError(f"Максималната стойност е {definition.maximum:g}")
    return value


def get_setting(db: Session, key: str) -> Any:
    definition = SETTING_MAP.get(key)
    if definition is None:
        raise KeyError(key)
    item = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if item is None:
        return definition.default
    try:
        return validate_setting(key, json.loads(item.value_json))
    except (ValueError, TypeError, json.JSONDecodeError):
        return definition.default


def settings_catalog(db: Session) -> list[dict[str, Any]]:
    values = {item.key: item for item in db.query(SystemSetting).all()}
    result = []
    for definition in SETTING_DEFINITIONS:
        item = values.get(definition.key)
        result.append({
            "definition": definition,
            "value": get_setting(db, definition.key),
            "updated_at": item.updated_at if item else None,
        })
    return result


def update_settings(
    db: Session,
    values: dict[str, Any],
    actor: StaffAccount,
    *,
    ip_address: str | None = None,
) -> dict[str, Any]:
    validated = {key: validate_setting(key, value) for key, value in values.items()}
    changes: dict[str, Any] = {}
    for key, value in validated.items():
        definition = SETTING_MAP[key]
        item = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        previous = get_setting(db, key)
        if item is None:
            item = SystemSetting(key=key)
            db.add(item)
        item.category = definition.category
        item.label = definition.label
        item.description = definition.description
        item.value_type = definition.value_type
        item.value_json = json.dumps(value, ensure_ascii=False)
        item.restart_required = definition.restart_required
        item.updated_by_staff_id = actor.id
        if previous != value:
            changes[key] = {"old": previous, "new": value}
    if changes:
        audit_event(
            db,
            "settings.updated",
            f"Променени настройки: {len(changes)}",
            actor=actor,
            entity_type="SystemSetting",
            changes=changes,
            ip_address=ip_address,
        )
    db.commit()
    return validated


def _master_key() -> bytes:
    if not Config.SETTINGS_MASTER_KEY and Config.ADMIN_SECRET_IS_EPHEMERAL:
        raise RuntimeError("Конфигурирайте SETTINGS_MASTER_KEY преди запис на защитени стойности")
    source = Config.SETTINGS_MASTER_KEY or Config.ADMIN_SECRET_KEY
    if not source:
        raise RuntimeError("SETTINGS_MASTER_KEY не е конфигуриран")
    return hashlib.sha256(source.encode("utf-8")).digest()


def save_secret(
    db: Session,
    key: str,
    plaintext: str,
    actor: StaffAccount,
    *,
    ip_address: str | None = None,
) -> EncryptedSecret:
    if key not in SECRET_MAP:
        raise ValueError("Непозната защитена настройка")
    value = plaintext.strip()
    if not value:
        raise ValueError("Стойността не може да е празна")
    nonce = os.urandom(12)
    ciphertext = nonce + AESGCM(_master_key()).encrypt(nonce, value.encode("utf-8"), key.encode("utf-8"))
    category, label = SECRET_MAP[key]
    item = db.query(EncryptedSecret).filter(EncryptedSecret.key == key).first()
    if item is None:
        item = EncryptedSecret(key=key)
        db.add(item)
    item.category = category
    item.label = label
    item.ciphertext = base64.urlsafe_b64encode(ciphertext).decode("ascii")
    item.fingerprint = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    item.updated_by_staff_id = actor.id
    audit_event(
        db,
        "secret.updated",
        f"Обновена защитена настройка: {label}",
        actor=actor,
        entity_type="EncryptedSecret",
        entity_id=key,
        changes={"fingerprint": item.fingerprint},
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(item)
    return item


def read_secret(db: Session, key: str) -> str | None:
    item = db.query(EncryptedSecret).filter(EncryptedSecret.key == key).first()
    if item is None:
        return None
    payload = base64.urlsafe_b64decode(item.ciphertext.encode("ascii"))
    nonce, encrypted = payload[:12], payload[12:]
    return AESGCM(_master_key()).decrypt(nonce, encrypted, key.encode("utf-8")).decode("utf-8")


def secret_catalog(db: Session) -> list[dict[str, Any]]:
    existing = {item.key: item for item in db.query(EncryptedSecret).all()}
    return [
        {
            "key": key,
            "category": category,
            "label": label,
            "configured": key in existing,
            "fingerprint": existing[key].fingerprint if key in existing else None,
            "updated_at": existing[key].updated_at if key in existing else None,
        }
        for key, category, label in SECRET_DEFINITIONS
    ]


def model_snapshot(model: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in inspect(model).mapper.column_attrs:
        key = column.key
        result[key] = _redact(getattr(model, key, None), key)
    return result


def archive_model(
    db: Session,
    model: Any,
    actor: StaffAccount,
    *,
    reason: str | None = None,
) -> ArchivedRecord:
    snapshot = model_snapshot(model)
    label = str(model)
    record = ArchivedRecord(
        entity_type=type(model).__name__,
        entity_id=str(snapshot.get("id", "")),
        label=label[:255],
        snapshot_json=json.dumps(snapshot, ensure_ascii=False, default=str),
        reason=(reason or "")[:255] or None,
        archived_by_staff_id=actor.id,
    )
    db.add(record)
    audit_event(
        db,
        "record.archived",
        f"Архивиран запис: {label}"[:500],
        actor=actor,
        entity_type=type(model).__name__,
        entity_id=snapshot.get("id"),
    )
    return record


def apply_role_codes(db: Session, account: StaffAccount, role_codes: Iterable[str]) -> None:
    codes = {value for value in role_codes if value in ROLE_DEFINITIONS}
    account.roles = db.query(StaffRole).filter(StaffRole.code.in_(codes)).all() if codes else []
