"""Administrative control-centre models.

These tables are deliberately additive: the QR prototype tables in
``engine.db`` remain compatible while the admin panel gains proper staff
accounts, permissions, operational settings, content and device management.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from engine.db import Base, now_bg


staff_account_roles = Table(
    "staff_account_roles",
    Base.metadata,
    Column("staff_account_id", ForeignKey("staff_accounts.id", ondelete="CASCADE"), primary_key=True),
    Column("staff_role_id", ForeignKey("staff_roles.id", ondelete="CASCADE"), primary_key=True),
)

staff_role_permissions = Table(
    "staff_role_permissions",
    Base.metadata,
    Column("staff_role_id", ForeignKey("staff_roles.id", ondelete="CASCADE"), primary_key=True),
    Column("staff_permission_id", ForeignKey("staff_permissions.id", ondelete="CASCADE"), primary_key=True),
)


class StaffAccount(Base):
    __tablename__ = "staff_accounts"

    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    display_name = Column(String(120), nullable=False)
    email = Column(String(255), nullable=True)
    password_hash = Column(String(255), nullable=False)
    linked_person_id = Column(Integer, ForeignKey("persons.id", ondelete="SET NULL"), nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    force_password_change = Column(Boolean, default=False, nullable=False)
    failed_login_count = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now_bg, nullable=False)
    updated_at = Column(DateTime, default=now_bg, onupdate=now_bg, nullable=False)

    linked_person = relationship("Person")
    roles = relationship("StaffRole", secondary=staff_account_roles, back_populates="accounts")

    def __repr__(self):
        return f"{self.display_name} ({self.username})"


class StaffRole(Base):
    __tablename__ = "staff_roles"

    id = Column(Integer, primary_key=True)
    code = Column(String(60), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    system_role = Column(Boolean, default=False, nullable=False)
    active = Column(Boolean, default=True, nullable=False)

    accounts = relationship("StaffAccount", secondary=staff_account_roles, back_populates="roles")
    permissions = relationship("StaffPermission", secondary=staff_role_permissions, back_populates="roles")

    def __repr__(self):
        return self.name


class StaffPermission(Base):
    __tablename__ = "staff_permissions"

    id = Column(Integer, primary_key=True)
    code = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    category = Column(String(60), nullable=False)
    description = Column(Text, nullable=True)

    roles = relationship("StaffRole", secondary=staff_role_permissions, back_populates="permissions")

    def __repr__(self):
        return self.name


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"

    id = Column(Integer, primary_key=True)
    actor_staff_id = Column(Integer, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(100), nullable=False, index=True)
    entity_type = Column(String(100), nullable=True, index=True)
    entity_id = Column(String(100), nullable=True)
    summary = Column(String(500), nullable=False)
    changes_json = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=now_bg, nullable=False, index=True)

    actor = relationship("StaffAccount")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True)
    key = Column(String(120), unique=True, nullable=False, index=True)
    category = Column(String(60), nullable=False)
    label = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)
    value_type = Column(String(30), nullable=False)
    value_json = Column(Text, nullable=False)
    restart_required = Column(Boolean, default=False, nullable=False)
    updated_by_staff_id = Column(Integer, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime, default=now_bg, onupdate=now_bg, nullable=False)

    updated_by = relationship("StaffAccount")


class EncryptedSecret(Base):
    __tablename__ = "encrypted_secrets"

    id = Column(Integer, primary_key=True)
    key = Column(String(120), unique=True, nullable=False, index=True)
    category = Column(String(60), nullable=False)
    label = Column(String(160), nullable=False)
    ciphertext = Column(Text, nullable=False)
    fingerprint = Column(String(20), nullable=False)
    updated_by_staff_id = Column(Integer, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime, default=now_bg, onupdate=now_bg, nullable=False)

    updated_by = relationship("StaffAccount")


class ArchivedRecord(Base):
    """Immutable snapshot created before a panel record is removed."""

    __tablename__ = "archived_records"

    id = Column(Integer, primary_key=True)
    entity_type = Column(String(100), nullable=False, index=True)
    entity_id = Column(String(100), nullable=False)
    label = Column(String(255), nullable=False)
    snapshot_json = Column(Text, nullable=False)
    reason = Column(String(255), nullable=True)
    archived_by_staff_id = Column(Integer, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True)
    archived_at = Column(DateTime, default=now_bg, nullable=False, index=True)

    archived_by = relationship("StaffAccount")


class ClassGroup(Base):
    __tablename__ = "class_groups"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False, index=True)
    grade = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=now_bg, nullable=False)

    memberships = relationship("GroupMembership", back_populates="group", cascade="all, delete-orphan")

    def __repr__(self):
        return self.name


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    __table_args__ = (UniqueConstraint("group_id", "person_id", name="uq_group_person"),)

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("class_groups.id", ondelete="CASCADE"), nullable=False)
    person_id = Column(Integer, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=now_bg, nullable=False)

    group = relationship("ClassGroup", back_populates="memberships")
    person = relationship("Person")


class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True)
    code = Column(String(30), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    floor = Column(String(50), nullable=True)
    wing = Column(String(80), nullable=True)
    directions = Column(Text, nullable=True)
    active = Column(Boolean, default=True, nullable=False)

    def __repr__(self):
        return f"{self.code} — {self.name}"


class Announcement(Base):
    __tablename__ = "announcements"

    id = Column(Integer, primary_key=True)
    title = Column(String(180), nullable=False)
    body = Column(Text, nullable=False)
    category = Column(String(30), default="news", nullable=False)
    audience = Column(String(100), default="all", nullable=False)
    priority = Column(String(20), default="normal", nullable=False)
    publish_from = Column(DateTime, default=now_bg, nullable=False)
    publish_until = Column(DateTime, nullable=True)
    published = Column(Boolean, default=False, nullable=False)
    archived_at = Column(DateTime, nullable=True)
    created_by_staff_id = Column(Integer, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=now_bg, nullable=False)

    created_by = relationship("StaffAccount")


class Club(Base):
    __tablename__ = "clubs"

    id = Column(Integer, primary_key=True)
    name = Column(String(150), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    schedule_text = Column(String(255), nullable=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True)
    advisor_person_id = Column(Integer, ForeignKey("persons.id", ondelete="SET NULL"), nullable=True)
    active = Column(Boolean, default=True, nullable=False)

    room = relationship("Room")
    advisor = relationship("Person")


class Substitution(Base):
    __tablename__ = "substitutions"

    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, index=True)
    period = Column(Integer, nullable=False)
    class_name = Column(String(20), nullable=False)
    subject = Column(String(100), nullable=True)
    original_teacher_id = Column(Integer, ForeignKey("persons.id", ondelete="SET NULL"), nullable=True)
    replacement_teacher_id = Column(Integer, ForeignKey("persons.id", ondelete="SET NULL"), nullable=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="SET NULL"), nullable=True)
    notes = Column(Text, nullable=True)

    original_teacher = relationship("Person", foreign_keys=[original_teacher_id])
    replacement_teacher = relationship("Person", foreign_keys=[replacement_teacher_id])
    room = relationship("Room")


class Duty(Base):
    __tablename__ = "duties"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False, index=True)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    location = Column(String(150), nullable=False)
    notes = Column(Text, nullable=True)

    person = relationship("Person")


class SchoolTask(Base):
    __tablename__ = "school_tasks"

    id = Column(Integer, primary_key=True)
    title = Column(String(180), nullable=False)
    description = Column(Text, nullable=True)
    due_at = Column(DateTime, nullable=True)
    audience = Column(String(100), default="all", nullable=False)
    assigned_person_id = Column(Integer, ForeignKey("persons.id", ondelete="SET NULL"), nullable=True)
    group_id = Column(Integer, ForeignKey("class_groups.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(20), default="active", nullable=False)
    created_at = Column(DateTime, default=now_bg, nullable=False)

    assigned_person = relationship("Person")
    group = relationship("ClassGroup")


class Reminder(Base):
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.id", ondelete="CASCADE"), nullable=True)
    group_id = Column(Integer, ForeignKey("class_groups.id", ondelete="CASCADE"), nullable=True)
    text = Column(String(500), nullable=False)
    remind_at = Column(DateTime, nullable=False, index=True)
    zone_id = Column(String(50), nullable=True)
    repeat_rule = Column(String(100), nullable=True)
    status = Column(String(20), default="pending", nullable=False)
    delivered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now_bg, nullable=False)

    person = relationship("Person")
    group = relationship("ClassGroup")


class DirectoryEntry(Base):
    __tablename__ = "directory_entries"

    id = Column(Integer, primary_key=True)
    kind = Column(String(30), nullable=False)
    name = Column(String(150), nullable=False)
    value = Column(String(255), nullable=True)
    details = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)
    active = Column(Boolean, default=True, nullable=False)


class MessageCampaign(Base):
    __tablename__ = "message_campaigns"

    id = Column(Integer, primary_key=True)
    title = Column(String(180), nullable=False)
    text = Column(String(500), nullable=False)
    group_id = Column(Integer, ForeignKey("class_groups.id", ondelete="SET NULL"), nullable=True)
    sender_person_id = Column(Integer, ForeignKey("persons.id", ondelete="SET NULL"), nullable=True)
    valid_until = Column(DateTime, nullable=False)
    status = Column(String(20), default="draft", nullable=False)
    recipient_count = Column(Integer, default=0, nullable=False)
    created_by_staff_id = Column(Integer, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=now_bg, nullable=False)

    group = relationship("ClassGroup")
    sender = relationship("Person")
    created_by = relationship("StaffAccount")


class ScheduleImportJob(Base):
    __tablename__ = "schedule_import_jobs"

    id = Column(Integer, primary_key=True)
    file_name = Column(String(255), nullable=False)
    mode = Column(String(30), default="upsert", nullable=False)
    status = Column(String(30), default="pending", nullable=False)
    total_rows = Column(Integer, default=0, nullable=False)
    valid_rows = Column(Integer, default=0, nullable=False)
    invalid_rows = Column(Integer, default=0, nullable=False)
    created_by_staff_id = Column(Integer, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=now_bg, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    result_json = Column(Text, nullable=True)

    created_by = relationship("StaffAccount")
    errors = relationship("ImportRowError", back_populates="job", cascade="all, delete-orphan")


class ImportRowError(Base):
    __tablename__ = "import_row_errors"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("schedule_import_jobs.id", ondelete="CASCADE"), nullable=False)
    row_number = Column(Integer, nullable=False)
    field_name = Column(String(100), nullable=True)
    message = Column(String(500), nullable=False)
    row_json = Column(Text, nullable=True)

    job = relationship("ScheduleImportJob", back_populates="errors")


class PrivacyNotice(Base):
    __tablename__ = "privacy_notices"

    id = Column(Integer, primary_key=True)
    version = Column(String(30), unique=True, nullable=False)
    title = Column(String(180), nullable=False)
    content = Column(Text, nullable=False)
    published = Column(Boolean, default=False, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=now_bg, nullable=False)


class PrivacyCleanupRun(Base):
    __tablename__ = "privacy_cleanup_runs"

    id = Column(Integer, primary_key=True)
    mode = Column(String(20), nullable=False)
    status = Column(String(30), nullable=False)
    summary_json = Column(Text, nullable=False)
    executed_by_staff_id = Column(Integer, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=now_bg, nullable=False, index=True)

    executed_by = relationship("StaffAccount")


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id = Column(Integer, primary_key=True)
    file_name = Column(String(255), nullable=False)
    storage_path = Column(String(500), nullable=False)
    database_type = Column(String(30), nullable=False)
    status = Column(String(30), default="created", nullable=False)
    size_bytes = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)
    created_by_staff_id = Column(Integer, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=now_bg, nullable=False)
    verified_at = Column(DateTime, nullable=True)

    created_by = relationship("StaffAccount")


class DeviceNode(Base):
    __tablename__ = "device_nodes"

    id = Column(Integer, primary_key=True)
    identifier = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(150), nullable=False)
    device_type = Column(String(30), nullable=False)
    zone_id = Column(String(50), nullable=True)
    screen_id = Column(String(50), nullable=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)
    interaction_point_id = Column(Integer, ForeignKey("interaction_points.id", ondelete="SET NULL"), nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    status = Column(String(30), default="offline", nullable=False)
    config_json = Column(Text, default="{}", nullable=False)
    config_version = Column(Integer, default=1, nullable=False)
    capabilities_json = Column(Text, default="[]", nullable=False)
    diagnostics_json = Column(Text, default="{}", nullable=False)
    software_version = Column(String(50), nullable=True)
    last_seen_at = Column(DateTime, nullable=True, index=True)
    last_websocket_at = Column(DateTime, nullable=True)
    last_websocket_disconnected_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now_bg, nullable=False)

    camera = relationship("Camera")
    interaction_point = relationship("InteractionPoint")
    credentials = relationship("DeviceCredential", back_populates="device", cascade="all, delete-orphan")
    commands = relationship("DeviceCommand", back_populates="device", cascade="all, delete-orphan")

    def __repr__(self):
        return f"{self.name} ({self.identifier})"


class DeviceCredential(Base):
    __tablename__ = "device_credentials"

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("device_nodes.id", ondelete="CASCADE"), nullable=False)
    key_hash = Column(String(64), unique=True, nullable=False, index=True)
    fingerprint = Column(String(20), nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=now_bg, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)

    device = relationship("DeviceNode", back_populates="credentials")


class DeviceEnrollmentToken(Base):
    __tablename__ = "device_enrollment_tokens"

    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    label = Column(String(150), nullable=False)
    device_type = Column(String(30), nullable=False)
    expected_identifier = Column(String(100), nullable=True)
    zone_id = Column(String(50), nullable=True)
    screen_id = Column(String(50), nullable=True)
    interaction_point_id = Column(
        Integer,
        ForeignKey("interaction_points.id", ondelete="SET NULL"),
        nullable=True,
    )
    initial_config_json = Column(Text, default="{}", nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    used_by_device_id = Column(Integer, ForeignKey("device_nodes.id", ondelete="SET NULL"), nullable=True)
    created_by_staff_id = Column(Integer, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=now_bg, nullable=False)

    created_by = relationship("StaffAccount")
    used_by_device = relationship("DeviceNode")
    interaction_point = relationship("InteractionPoint")


class DeviceCommand(Base):
    __tablename__ = "device_commands"

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("device_nodes.id", ondelete="CASCADE"), nullable=False)
    command = Column(String(50), nullable=False)
    payload_json = Column(Text, default="{}", nullable=False)
    status = Column(String(30), default="pending", nullable=False, index=True)
    created_by_staff_id = Column(Integer, ForeignKey("staff_accounts.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=now_bg, nullable=False)
    delivered_at = Column(DateTime, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    result_json = Column(Text, nullable=True)

    device = relationship("DeviceNode", back_populates="commands")
    created_by = relationship("StaffAccount")
