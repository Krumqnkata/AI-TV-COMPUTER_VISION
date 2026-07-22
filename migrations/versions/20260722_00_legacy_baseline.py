"""Create the original QR-assistant schema.

Revision ID: 20260722_00
Revises: None
Create Date: 2026-07-22

The revision is check-first so it can adopt an existing prototype database
without replacing tables or data. Databases already stamped at 20260722_01
remain valid because this revision is now an ancestor of that revision.
"""

from typing import Sequence, Union

from alembic import op

from engine import admin_models as _admin_models  # noqa: F401
from engine.db import Base


revision: str = "20260722_00"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_TABLES = (
    "persons",
    "badges",
    "interaction_points",
    "cameras",
    "messages",
    "timetable",
    "events",
    "system_events",
    "delivery_receipts",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in LEGACY_TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(LEGACY_TABLES):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
