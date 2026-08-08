"""Add administrator-managed jokes for successful badge scans.

Revision ID: 20260808_01
Revises: 20260728_01
Create Date: 2026-08-08
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260808_01"
down_revision: Union[str, None] = "20260728_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "badge_jokes"

INITIAL_JOKES = (
    ("Какво каза нулата на осмицата? Хубав колан!", "all"),
    ("Защо компютърът отиде на лекар? Защото беше хванал вирус.", "all"),
    ("Как се нарича мечка без зъби? Желирано мече.", "all"),
    ("Коя е любимата музика на принтера? Хартия и блус.", "all"),
    (
        "Защо учебникът по математика беше тъжен? "
        "Защото имаше твърде много задачи.",
        "student",
    ),
    ("Защо моливът закъсня за час? Защото се подостряше.", "student"),
    ("Какво прави ученикът на Луната? Учи за звездната си оценка.", "student"),
    (
        "Защо часовникът е добър ученик? "
        "Защото винаги знае кога е време за час.",
        "student",
    ),
    (
        "Защо учителят харесва Wi-Fi? "
        "Защото обича целият клас да има добра връзка.",
        "teacher",
    ),
    (
        "Днес дневникът обеща да пази тайна, но не му вярваме напълно.",
        "teacher",
    ),
    ("Кое е любимото копче на учителя? Запази промените.", "teacher"),
    (
        "Защо маркерът получи отличен? Защото винаги подчертава важното.",
        "teacher",
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE_NAME in sa.inspect(bind).get_table_names():
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=280), nullable=False),
        sa.Column("audience", sa.String(length=20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by_staff_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_staff_id"],
            ["staff_accounts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_badge_jokes_active"), TABLE_NAME, ["active"], unique=False)
    op.create_index(
        op.f("ix_badge_jokes_audience"),
        TABLE_NAME,
        ["audience"],
        unique=False,
    )

    badge_jokes = sa.table(
        TABLE_NAME,
        sa.column("text", sa.String(length=280)),
        sa.column("audience", sa.String(length=20)),
        sa.column("active", sa.Boolean()),
    )
    op.bulk_insert(
        badge_jokes,
        [
            {"text": text, "audience": audience, "active": True}
            for text, audience in INITIAL_JOKES
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE_NAME in sa.inspect(bind).get_table_names():
        op.drop_table(TABLE_NAME)
