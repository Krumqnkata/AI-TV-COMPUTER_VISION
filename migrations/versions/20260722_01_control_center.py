"""Add the role-based school control centre.

Revision ID: 20260722_01
Revises: 20260722_00
Create Date: 2026-07-22

The migration is deliberately additive and check-first. Existing prototype
tables and the current admin UI data remain untouched. It is also safe for a
database where an older application version already created these tables.
"""

from typing import Sequence, Union

from alembic import op

from engine import admin_models as _admin_models  # noqa: F401
from engine.db import Base


revision: str = "20260722_01"
down_revision: Union[str, None] = "20260722_00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_TABLES = (
    "staff_permissions",
    "staff_roles",
    "staff_accounts",
    "staff_role_permissions",
    "staff_account_roles",
    "admin_audit_events",
    "system_settings",
    "encrypted_secrets",
    "archived_records",
    "class_groups",
    "group_memberships",
    "rooms",
    "announcements",
    "clubs",
    "substitutions",
    "duties",
    "school_tasks",
    "reminders",
    "directory_entries",
    "message_campaigns",
    "schedule_import_jobs",
    "import_row_errors",
    "privacy_notices",
    "privacy_cleanup_runs",
    "backup_records",
    "device_nodes",
    "device_credentials",
    "device_enrollment_tokens",
    "device_commands",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in NEW_TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(NEW_TABLES):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
