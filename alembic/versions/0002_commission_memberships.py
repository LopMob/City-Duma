"""commission memberships

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-15

"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commission_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "commission_id",
            sa.Integer(),
            sa.ForeignKey("commissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "deputy_id",
            sa.Integer(),
            sa.ForeignKey("deputies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_chair", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("joined_at", sa.Date(), nullable=False),
        sa.UniqueConstraint("commission_id", "deputy_id", name="uq_membership_pair"),
    )


def downgrade() -> None:
    op.drop_table("commission_memberships")
