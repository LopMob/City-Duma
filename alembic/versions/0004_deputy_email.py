"""deputy contact email

Новая функция (ЛР3, п.10): у депутата можно указать контактный email —
для последующих уведомлений о заседаниях. Поле необязательное и уникальное
(несколько депутатов без email — нормально, SQL UNIQUE не считает NULL
дубликатом).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03

"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deputies", sa.Column("email", sa.String(length=255), nullable=True))
    op.create_unique_constraint("uq_deputy_email", "deputies", ["email"])


def downgrade() -> None:
    op.drop_constraint("uq_deputy_email", "deputies", type_="unique")
    op.drop_column("deputies", "email")
