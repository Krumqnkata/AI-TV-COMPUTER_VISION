"""Persist deployment-scheduled operational job outcomes.

Revision ID: 20260728_01
Revises: 20260727_01
Create Date: 2026-07-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260728_01"
down_revision: Union[str, None] = "20260727_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "operational_job_runs"


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE_NAME in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_name", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("summary_json", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_operational_job_runs_job_name"),
        TABLE_NAME,
        ["job_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operational_job_runs_started_at"),
        TABLE_NAME,
        ["started_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operational_job_runs_status"),
        TABLE_NAME,
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE_NAME in sa.inspect(bind).get_table_names():
        op.drop_table(TABLE_NAME)
