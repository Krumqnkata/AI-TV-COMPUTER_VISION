"""Permission-aware SQLAdmin model views with Bulgarian labels and auditing."""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqladmin import ModelView
from wtforms import SelectField

from engine.admin_models import (
    AdminAuditEvent,
    Announcement,
    ArchivedRecord,
    BackupRecord,
    ClassGroup,
    Club,
    DirectoryEntry,
    Duty,
    GroupMembership,
    MessageCampaign,
    PrivacyCleanupRun,
    PrivacyNotice,
    Reminder,
    Room,
    ScheduleImportJob,
    SchoolTask,
    StaffPermission,
    StaffRole,
    Substitution,
    DeviceNode,
)
from engine.db import Badge, Camera, Event, InteractionPoint, Message, Person, SystemEvent, Timetable
from web.admin.permissions import current_staff, request_ip, session_has_permission
from web.database import SessionLocal
from web.services.admin_control import archive_model, audit_event, model_snapshot


def _date_time(model, attribute):
    name = attribute if isinstance(attribute, str) else attribute.key
    value = getattr(model, name, None)
    return value.strftime("%d.%m.%Y %H:%M") if value else "—"


def _yes_no(model, attribute):
    name = attribute if isinstance(attribute, str) else attribute.key
    return "Да" if getattr(model, name, False) else "Не"


class PermissionedModelView(ModelView):
    view_permission = "dashboard.view"
    manage_permission: str | None = None
    archive_on_delete = False
    can_view_details = True
    page_size = 50

    def is_accessible(self, request: Request) -> bool:
        return session_has_permission(request, self.view_permission) or bool(
            self.manage_permission and session_has_permission(request, self.manage_permission)
        )

    def _can_manage(self, request: Request) -> bool:
        return bool(self.manage_permission and session_has_permission(request, self.manage_permission))

    async def check_can_edit(self, request: Request, model) -> bool:
        return bool(self.can_edit and self._can_manage(request))

    async def check_can_delete(self, request: Request, model) -> bool:
        return bool(self.can_delete and self._can_manage(request))

    async def insert_model(self, request: Request, data: dict):
        if not self._can_manage(request):
            raise HTTPException(status_code=403, detail="Нямате право да създавате записи")
        return await super().insert_model(request, data)

    async def update_model(self, request: Request, pk: str, data: dict):
        if not self._can_manage(request):
            raise HTTPException(status_code=403, detail="Нямате право да редактирате записи")
        return await super().update_model(request, pk, data)

    async def delete_model(self, request: Request, pk):
        if not self._can_manage(request):
            raise HTTPException(status_code=403, detail="Нямате право да изтривате записи")
        return await super().delete_model(request, pk)

    async def on_model_change(self, data, model, is_created, request: Request) -> None:
        if is_created and hasattr(model, "created_by_staff_id"):
            data["created_by_staff_id"] = request.session.get("staff_id")

    async def after_model_change(self, data, model, is_created, request: Request) -> None:
        with SessionLocal() as db:
            actor = current_staff(db, request)
            audit_event(
                db,
                "record.created" if is_created else "record.updated",
                f"{'Създаден' if is_created else 'Обновен'} запис: {self.name}",
                actor=actor,
                entity_type=type(model).__name__,
                entity_id=getattr(model, "id", None),
                changes=model_snapshot(model),
                ip_address=request_ip(request),
            )
            db.commit()

    async def after_model_delete(self, model, request: Request) -> None:
        with SessionLocal() as db:
            actor = current_staff(db, request)
            if self.archive_on_delete:
                archive_model(db, model, actor, reason="Изтрито през административния панел")
            else:
                audit_event(
                    db,
                    "record.deleted",
                    f"Изтрит запис: {self.name}",
                    actor=actor,
                    entity_type=type(model).__name__,
                    entity_id=getattr(model, "id", None),
                    changes=model_snapshot(model),
                    ip_address=request_ip(request),
                )
            db.commit()


class PersonAdmin(PermissionedModelView, model=Person):
    name = "Човек"
    name_plural = "Ученици и служители"
    category = "Хора"
    icon = "fa-solid fa-users"
    view_permission = "people.view"
    manage_permission = "people.manage"
    can_export = True
    column_list = [Person.full_name, Person.role, Person.class_name, Person.active, Person.created_at]
    column_searchable_list = [Person.full_name, Person.class_name]
    column_sortable_list = [Person.full_name, Person.role, Person.active]
    form_excluded_list = [Person.created_at, Person.password_hash]
    column_details_exclude_list = [Person.password_hash]
    column_labels = {
        Person.full_name: "Пълно име", Person.role: "Тип профил", Person.class_name: "Клас",
        Person.active: "Активен", Person.created_at: "Добавен на",
    }
    column_formatters = {Person.active: _yes_no, Person.created_at: _date_time}
    form_overrides = {"role": SelectField}
    form_args = {"role": {"choices": [("student", "Ученик"), ("teacher", "Учител"), ("admin", "Администратор"), ("guest", "Гост")]}}


class ClassGroupAdmin(PermissionedModelView, model=ClassGroup):
    name = "Клас/група"
    name_plural = "Класове и групи"
    category = "Хора"
    icon = "fa-solid fa-people-group"
    view_permission = "people.view"
    manage_permission = "people.manage"
    column_list = [ClassGroup.name, ClassGroup.grade, ClassGroup.active, ClassGroup.description]
    column_labels = {ClassGroup.name: "Име", ClassGroup.grade: "Випуск", ClassGroup.active: "Активна", ClassGroup.description: "Бележка"}
    column_formatters = {ClassGroup.active: _yes_no}


class GroupMembershipAdmin(PermissionedModelView, model=GroupMembership):
    name = "Участник в група"
    name_plural = "Участници в групи"
    category = "Хора"
    icon = "fa-solid fa-user-group"
    view_permission = "people.view"
    manage_permission = "people.manage"
    column_list = [GroupMembership.group, GroupMembership.person, GroupMembership.created_at]
    form_columns = [GroupMembership.group, GroupMembership.person]
    column_labels = {GroupMembership.group: "Група", GroupMembership.person: "Човек", GroupMembership.created_at: "Добавен"}
    column_formatters = {GroupMembership.created_at: _date_time}


class BadgeAdmin(PermissionedModelView, model=Badge):
    name = "QR бадж"
    name_plural = "QR баджове"
    category = "Хора"
    icon = "fa-solid fa-id-badge"
    view_permission = "people.view"
    manage_permission = "badges.manage"
    can_create = False
    can_delete = False
    can_export = True
    column_list = [Badge.person, Badge.status, Badge.created_at]
    form_columns = [Badge.person, Badge.status]
    column_details_exclude_list = [Badge.token_hash]
    column_labels = {Badge.person: "Собственик", Badge.status: "Статус", Badge.created_at: "Издаден"}
    column_formatters = {Badge.created_at: _date_time}
    form_overrides = {"status": SelectField}
    form_args = {"status": {"choices": [("active", "Активен"), ("lost", "Изгубен"), ("disabled", "Спрян")]}}


class TimetableAdmin(PermissionedModelView, model=Timetable):
    name = "Учебен час"
    name_plural = "Разписание"
    category = "Учебен процес"
    icon = "fa-solid fa-calendar-week"
    view_permission = "schedule.view"
    manage_permission = "schedule.manage"
    can_export = True
    column_list = [Timetable.person, Timetable.date, Timetable.period, Timetable.start_time, Timetable.subject, Timetable.class_name, Timetable.room]
    form_columns = [Timetable.person, Timetable.date, Timetable.period, Timetable.start_time, Timetable.end_time, Timetable.subject, Timetable.class_name, Timetable.room]
    column_labels = {
        Timetable.person: "Човек", Timetable.date: "Дата", Timetable.period: "Час №",
        Timetable.start_time: "Начало", Timetable.end_time: "Край", Timetable.subject: "Предмет",
        Timetable.class_name: "Клас", Timetable.room: "Кабинет",
    }


class SubstitutionAdmin(PermissionedModelView, model=Substitution):
    name = "Заместване"
    name_plural = "Замествания"
    category = "Учебен процес"
    icon = "fa-solid fa-person-chalkboard"
    view_permission = "schedule.view"
    manage_permission = "schedule.manage"
    archive_on_delete = True
    column_list = [Substitution.date, Substitution.period, Substitution.class_name, Substitution.subject, Substitution.original_teacher, Substitution.replacement_teacher, Substitution.room]
    form_columns = [Substitution.date, Substitution.period, Substitution.class_name, Substitution.subject, Substitution.original_teacher, Substitution.replacement_teacher, Substitution.room, Substitution.notes]


class DutyAdmin(PermissionedModelView, model=Duty):
    name = "Дежурство"
    name_plural = "Дежурства"
    category = "Учебен процес"
    icon = "fa-solid fa-clipboard-check"
    view_permission = "schedule.view"
    manage_permission = "schedule.manage"
    archive_on_delete = True
    column_list = [Duty.person, Duty.date, Duty.start_time, Duty.end_time, Duty.location]
    form_columns = [Duty.person, Duty.date, Duty.start_time, Duty.end_time, Duty.location, Duty.notes]


class RoomAdmin(PermissionedModelView, model=Room):
    name = "Помещение"
    name_plural = "Кабинети и помещения"
    category = "Учебен процес"
    icon = "fa-solid fa-door-open"
    view_permission = "schedule.view"
    manage_permission = "schedule.manage"
    column_list = [Room.code, Room.name, Room.floor, Room.wing, Room.active]
    column_searchable_list = [Room.code, Room.name]
    column_formatters = {Room.active: _yes_no}


class EventAdmin(PermissionedModelView, model=Event):
    name = "Събитие"
    name_plural = "Събития"
    category = "Съдържание"
    icon = "fa-solid fa-calendar-days"
    view_permission = "content.view"
    manage_permission = "content.manage"
    archive_on_delete = True
    column_list = [Event.title, Event.start_time, Event.end_time, Event.target_group, Event.room]
    column_searchable_list = [Event.title, Event.target_group]
    column_formatters = {Event.start_time: _date_time, Event.end_time: _date_time}


class AnnouncementAdmin(PermissionedModelView, model=Announcement):
    name = "Обява"
    name_plural = "Новини и обяви"
    category = "Съдържание"
    icon = "fa-solid fa-bullhorn"
    view_permission = "content.view"
    manage_permission = "content.manage"
    archive_on_delete = True
    column_list = [Announcement.title, Announcement.category, Announcement.audience, Announcement.priority, Announcement.publish_from, Announcement.publish_until, Announcement.published]
    form_excluded_list = [Announcement.created_by, Announcement.created_by_staff_id, Announcement.created_at, Announcement.archived_at]
    column_formatters = {Announcement.publish_from: _date_time, Announcement.publish_until: _date_time, Announcement.published: _yes_no}


class ClubAdmin(PermissionedModelView, model=Club):
    name = "Клуб"
    name_plural = "Клубове"
    category = "Съдържание"
    icon = "fa-solid fa-palette"
    view_permission = "content.view"
    manage_permission = "content.manage"
    archive_on_delete = True
    column_list = [Club.name, Club.schedule_text, Club.room, Club.advisor, Club.active]
    form_columns = [Club.name, Club.description, Club.schedule_text, Club.room, Club.advisor, Club.active]
    column_formatters = {Club.active: _yes_no}


class SchoolTaskAdmin(PermissionedModelView, model=SchoolTask):
    name = "Задача"
    name_plural = "Задачи"
    category = "Съдържание"
    icon = "fa-solid fa-list-check"
    view_permission = "content.view"
    manage_permission = "content.manage"
    archive_on_delete = True
    column_list = [SchoolTask.title, SchoolTask.due_at, SchoolTask.audience, SchoolTask.assigned_person, SchoolTask.group, SchoolTask.status]
    form_excluded_list = [SchoolTask.created_at]
    column_formatters = {SchoolTask.due_at: _date_time}


class ReminderAdmin(PermissionedModelView, model=Reminder):
    name = "Напомняне"
    name_plural = "Напомняния"
    category = "Съдържание"
    icon = "fa-solid fa-bell"
    view_permission = "content.view"
    manage_permission = "content.manage"
    archive_on_delete = True
    column_list = [Reminder.person, Reminder.group, Reminder.text, Reminder.remind_at, Reminder.zone_id, Reminder.status]
    form_excluded_list = [Reminder.created_at, Reminder.delivered_at]
    column_formatters = {Reminder.remind_at: _date_time}


class DirectoryEntryAdmin(PermissionedModelView, model=DirectoryEntry):
    name = "Запис в указателя"
    name_plural = "Училищен указател"
    category = "Съдържание"
    icon = "fa-solid fa-address-book"
    view_permission = "content.view"
    manage_permission = "content.manage"
    archive_on_delete = True
    column_list = [DirectoryEntry.kind, DirectoryEntry.name, DirectoryEntry.value, DirectoryEntry.sort_order, DirectoryEntry.active]
    column_searchable_list = [DirectoryEntry.name, DirectoryEntry.value]
    column_formatters = {DirectoryEntry.active: _yes_no}


class MessageAdmin(PermissionedModelView, model=Message):
    name = "Съобщение"
    name_plural = "Виртуална поща"
    category = "Комуникация"
    icon = "fa-solid fa-envelope"
    view_permission = "messages.view"
    manage_permission = "messages.manage"
    can_delete = False
    column_list = [Message.sender, Message.recipient, Message.text, Message.status, Message.valid_until, Message.delivered_at]
    form_columns = [Message.sender, Message.recipient, Message.text, Message.valid_until, Message.status]
    column_formatters = {Message.valid_until: _date_time, Message.delivered_at: _date_time}


class MessageCampaignAdmin(PermissionedModelView, model=MessageCampaign):
    name = "Кампания"
    name_plural = "Групови съобщения"
    category = "Комуникация"
    icon = "fa-solid fa-paper-plane"
    view_permission = "messages.view"
    manage_permission = "messages.manage"
    archive_on_delete = True
    column_list = [MessageCampaign.title, MessageCampaign.group, MessageCampaign.sender, MessageCampaign.status, MessageCampaign.recipient_count, MessageCampaign.valid_until]
    form_excluded_list = [MessageCampaign.created_by, MessageCampaign.created_by_staff_id, MessageCampaign.created_at, MessageCampaign.recipient_count]
    column_formatters = {MessageCampaign.valid_until: _date_time}


class InteractionPointAdmin(PermissionedModelView, model=InteractionPoint):
    name = "Интерактивна точка"
    name_plural = "Интерактивни точки"
    category = "Устройства"
    icon = "fa-solid fa-display"
    view_permission = "devices.view"
    manage_permission = "devices.manage"
    column_list = [InteractionPoint.name, InteractionPoint.zone_id, InteractionPoint.type, InteractionPoint.screen_id, InteractionPoint.active]
    column_formatters = {InteractionPoint.active: _yes_no}


class CameraAdmin(PermissionedModelView, model=Camera):
    name = "Камера"
    name_plural = "Камери"
    category = "Устройства"
    icon = "fa-solid fa-video"
    view_permission = "devices.view"
    manage_permission = "devices.manage"
    column_list = [Camera.name, Camera.zone_id, Camera.interaction_point, Camera.active]
    form_columns = [Camera.name, Camera.zone_id, Camera.interaction_point, Camera.stream_url, Camera.active]
    column_formatters = {Camera.active: _yes_no}


class DeviceNodeAdmin(PermissionedModelView, model=DeviceNode):
    name = "Устройство"
    name_plural = "Регистрирани устройства"
    category = "Устройства"
    icon = "fa-solid fa-microchip"
    view_permission = "devices.view"
    manage_permission = "devices.manage"
    can_create = can_edit = can_delete = False
    column_list = [DeviceNode.name, DeviceNode.identifier, DeviceNode.device_type, DeviceNode.zone_id, DeviceNode.screen_id, DeviceNode.status, DeviceNode.software_version, DeviceNode.last_seen_at]
    column_formatters = {DeviceNode.last_seen_at: _date_time}


class PrivacyNoticeAdmin(PermissionedModelView, model=PrivacyNotice):
    name = "Политика"
    name_plural = "Известия за поверителност"
    category = "Поверителност"
    icon = "fa-solid fa-shield-halved"
    view_permission = "privacy.view"
    manage_permission = "privacy.manage"
    archive_on_delete = True
    column_list = [PrivacyNotice.version, PrivacyNotice.title, PrivacyNotice.published, PrivacyNotice.active, PrivacyNotice.created_at]
    column_formatters = {PrivacyNotice.published: _yes_no, PrivacyNotice.active: _yes_no, PrivacyNotice.created_at: _date_time}


class ArchivedRecordAdmin(PermissionedModelView, model=ArchivedRecord):
    name = "Архивиран запис"
    name_plural = "Архивирани записи"
    category = "Поверителност"
    icon = "fa-solid fa-box-archive"
    view_permission = "privacy.view"
    can_create = can_edit = can_delete = False
    column_list = [ArchivedRecord.entity_type, ArchivedRecord.label, ArchivedRecord.reason, ArchivedRecord.archived_by, ArchivedRecord.archived_at]
    column_details_exclude_list = [ArchivedRecord.snapshot_json]
    column_formatters = {ArchivedRecord.archived_at: _date_time}


class ReadOnlyAuditView(PermissionedModelView):
    can_create = can_edit = can_delete = False
    page_size = 100


class AdminAuditAdmin(ReadOnlyAuditView, model=AdminAuditEvent):
    name = "Одитно събитие"
    name_plural = "Административен одит"
    category = "Достъп и одит"
    icon = "fa-solid fa-user-shield"
    view_permission = "audit.view"
    column_list = [AdminAuditEvent.created_at, AdminAuditEvent.actor, AdminAuditEvent.action, AdminAuditEvent.summary, AdminAuditEvent.ip_address]
    column_default_sort = [(AdminAuditEvent.created_at, True)]
    column_details_exclude_list = [AdminAuditEvent.changes_json]
    column_formatters = {AdminAuditEvent.created_at: _date_time}


class SystemEventAdmin(ReadOnlyAuditView, model=SystemEvent):
    name = "Системно събитие"
    name_plural = "Технически журнал"
    category = "Достъп и одит"
    icon = "fa-solid fa-wave-square"
    view_permission = "audit.view"
    column_list = [SystemEvent.timestamp, SystemEvent.event_type, SystemEvent.person, SystemEvent.camera, SystemEvent.interaction_point]
    column_default_sort = [(SystemEvent.timestamp, True)]
    column_details_exclude_list = [SystemEvent.metadata_json]
    column_formatters = {SystemEvent.timestamp: _date_time}


class StaffRoleAdmin(ReadOnlyAuditView, model=StaffRole):
    name = "Роля"
    name_plural = "Роли и права"
    category = "Достъп и одит"
    icon = "fa-solid fa-key"
    view_permission = "staff.view"
    column_list = [StaffRole.name, StaffRole.code, StaffRole.description, StaffRole.active]
    column_formatters = {StaffRole.active: _yes_no}


class StaffPermissionAdmin(ReadOnlyAuditView, model=StaffPermission):
    name = "Право"
    name_plural = "Каталог на правата"
    category = "Достъп и одит"
    icon = "fa-solid fa-list"
    view_permission = "staff.view"
    column_list = [StaffPermission.category, StaffPermission.name, StaffPermission.code, StaffPermission.description]


class ScheduleImportJobAdmin(ReadOnlyAuditView, model=ScheduleImportJob):
    name = "Импорт"
    name_plural = "История на импортите"
    category = "Достъп и одит"
    icon = "fa-solid fa-file-import"
    view_permission = "schedule.view"
    column_list = [ScheduleImportJob.created_at, ScheduleImportJob.file_name, ScheduleImportJob.mode, ScheduleImportJob.status, ScheduleImportJob.valid_rows, ScheduleImportJob.invalid_rows, ScheduleImportJob.created_by]
    column_details_exclude_list = [ScheduleImportJob.result_json]
    column_formatters = {ScheduleImportJob.created_at: _date_time}


class BackupRecordAdmin(ReadOnlyAuditView, model=BackupRecord):
    name = "Резервно копие"
    name_plural = "История на архивите"
    category = "Достъп и одит"
    icon = "fa-solid fa-database"
    view_permission = "backups.view"
    column_list = [BackupRecord.created_at, BackupRecord.file_name, BackupRecord.status, BackupRecord.size_bytes, BackupRecord.created_by, BackupRecord.verified_at]
    column_details_exclude_list = [BackupRecord.storage_path, BackupRecord.sha256]
    column_formatters = {BackupRecord.created_at: _date_time, BackupRecord.verified_at: _date_time}


class PrivacyCleanupRunAdmin(ReadOnlyAuditView, model=PrivacyCleanupRun):
    name = "Почистване"
    name_plural = "История на retention"
    category = "Достъп и одит"
    icon = "fa-solid fa-broom"
    view_permission = "privacy.view"
    column_list = [PrivacyCleanupRun.created_at, PrivacyCleanupRun.mode, PrivacyCleanupRun.status, PrivacyCleanupRun.executed_by]
    column_details_exclude_list = [PrivacyCleanupRun.summary_json]
    column_formatters = {PrivacyCleanupRun.created_at: _date_time}


MODEL_VIEWS = [
    PersonAdmin, ClassGroupAdmin, GroupMembershipAdmin, BadgeAdmin,
    TimetableAdmin, SubstitutionAdmin, DutyAdmin, RoomAdmin,
    EventAdmin, AnnouncementAdmin, ClubAdmin, SchoolTaskAdmin, ReminderAdmin, DirectoryEntryAdmin,
    MessageAdmin, MessageCampaignAdmin,
    InteractionPointAdmin, CameraAdmin, DeviceNodeAdmin,
    PrivacyNoticeAdmin, ArchivedRecordAdmin,
    AdminAuditAdmin, SystemEventAdmin, StaffRoleAdmin, StaffPermissionAdmin,
    ScheduleImportJobAdmin, BackupRecordAdmin, PrivacyCleanupRunAdmin,
]
