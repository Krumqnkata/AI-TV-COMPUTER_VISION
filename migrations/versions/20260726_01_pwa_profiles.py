"""Add interaction-point assignment for browser PWA enrollment.

Revision ID: 20260726_01
Revises: 20260722_01
Create Date: 2026-07-26

The migration is additive and check-first because a fresh database may already
contain the current column when the preceding metadata-driven migration builds
the control-centre tables.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260726_01"
down_revision: Union[str, None] = "20260722_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "device_enrollment_tokens"
FK_NAME = "fk_device_enrollment_tokens_interaction_point_id"


def _column_names(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE_NAME)}


def upgrade() -> None:
    bind = op.get_bind()
    if "interaction_point_id" in _column_names(bind):
        return

    with op.batch_alter_table(TABLE_NAME) as batch:
        batch.add_column(sa.Column("interaction_point_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            FK_NAME,
            "interaction_points",
            ["interaction_point_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "interaction_point_id" not in _column_names(bind):
        return

    inspector = sa.inspect(bind)
    foreign_keys = inspector.get_foreign_keys(TABLE_NAME)
    matching = next(
        (
            item["name"]
            for item in foreign_keys
            if item.get("constrained_columns") == ["interaction_point_id"] and item.get("name")
        ),
        None,
    )
    with op.batch_alter_table(TABLE_NAME) as batch:
        if matching:
            batch.drop_constraint(matching, type_="foreignkey")
        batch.drop_column("interaction_point_id")
