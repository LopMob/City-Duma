"""deputy contact email

Новая функция (ЛР3, п.10): у депутата можно указать контактный email —
для последующих уведомлений о заседаниях. Поле необязательное и уникальное
(несколько депутатов без email — нормально, SQL UNIQUE не считает NULL
дубликатом ни в SQLite, ни в PostgreSQL).

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
    # batch_alter_table: на SQLite ALTER TABLE не умеет добавлять constraint
    # напрямую (пересоздаёт таблицу через copy-and-move); на PostgreSQL та же
    # команда выполняется как обычный ALTER TABLE ADD COLUMN/ADD CONSTRAINT.
    with op.batch_alter_table("deputies") as batch_op:
        batch_op.add_column(sa.Column("email", sa.String(length=255), nullable=True))
        batch_op.create_unique_constraint("uq_deputy_email", ["email"])


def downgrade() -> None:
    with op.batch_alter_table("deputies") as batch_op:
        batch_op.drop_constraint("uq_deputy_email", type_="unique")
        batch_op.drop_column("email")
