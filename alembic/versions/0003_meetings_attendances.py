"""meetings and attendance

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-16

"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meetings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "commission_id",
            sa.Integer(),
            sa.ForeignKey("commissions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("scheduled", "held", "cancelled", name="meetingstatus"),
            nullable=False,
            server_default="scheduled",
        ),
        sa.Column("agenda", sa.Text(), nullable=True),
    )

    op.create_table(
        "attendances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "deputy_id",
            sa.Integer(),
            sa.ForeignKey("deputies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("present", "absent", "excused", name="attendancestatus"),
            nullable=False,
        ),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.UniqueConstraint("meeting_id", "deputy_id", name="uq_attendance_pair"),
    )


def downgrade() -> None:
    op.drop_table("attendances")
    op.drop_table("meetings")
    sa.Enum(name="attendancestatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="meetingstatus").drop(op.get_bind(), checkfirst=True)
