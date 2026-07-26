"""Add persisted browser and WebSocket diagnostics to managed devices.

Revision ID: 20260727_01
Revises: 20260726_01
Create Date: 2026-07-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260727_01"
down_revision: Union[str, None] = "20260726_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "device_nodes"


def _column_names(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE_NAME)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind)
    with op.batch_alter_table(TABLE_NAME) as batch:
        if "diagnostics_json" not in columns:
            batch.add_column(
                sa.Column(
                    "diagnostics_json",
                    sa.Text(),
                    nullable=False,
                    server_default="{}",
                )
            )
        if "last_websocket_at" not in columns:
            batch.add_column(sa.Column("last_websocket_at", sa.DateTime(), nullable=True))
        if "last_websocket_disconnected_at" not in columns:
            batch.add_column(
                sa.Column(
                    "last_websocket_disconnected_at",
                    sa.DateTime(),
                    nullable=True,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind)
    with op.batch_alter_table(TABLE_NAME) as batch:
        if "last_websocket_disconnected_at" in columns:
            batch.drop_column("last_websocket_disconnected_at")
        if "last_websocket_at" in columns:
            batch.drop_column("last_websocket_at")
        if "diagnostics_json" in columns:
            batch.drop_column("diagnostics_json")
