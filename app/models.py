import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MeetingStatus(enum.StrEnum):
    scheduled = "scheduled"
    held = "held"
    cancelled = "cancelled"


class AttendanceStatus(enum.StrEnum):
    present = "present"
    absent = "absent"
    excused = "excused"


class Deputy(Base):
    """Депутат городской думы."""

    __tablename__ = "deputies"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    party: Mapped[str | None] = mapped_column(String(120), nullable=True)
    election_district: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    memberships: Mapped[list["CommissionMembership"]] = relationship(
        back_populates="deputy", cascade="all, delete-orphan"
    )
    attendances: Mapped[list["Attendance"]] = relationship(
        back_populates="deputy", cascade="all, delete-orphan"
    )


class Commission(Base):
    """Комиссия городской думы (например, бюджетная, социальная)."""

    __tablename__ = "commissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    memberships: Mapped[list["CommissionMembership"]] = relationship(
        back_populates="commission", cascade="all, delete-orphan"
    )
    meetings: Mapped[list["Meeting"]] = relationship(
        back_populates="commission", cascade="all, delete-orphan"
    )


class CommissionMembership(Base):
    """Членство депутата в комиссии. is_chair=True — председатель комиссии.

    Правило: у комиссии не может быть больше одного председателя
    одновременно (проверяется в app/crud.py).
    """

    __tablename__ = "commission_memberships"
    __table_args__ = (
        UniqueConstraint("commission_id", "deputy_id", name="uq_membership_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    commission_id: Mapped[int] = mapped_column(
        ForeignKey("commissions.id", ondelete="CASCADE"), nullable=False
    )
    deputy_id: Mapped[int] = mapped_column(
        ForeignKey("deputies.id", ondelete="CASCADE"), nullable=False
    )
    is_chair: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    joined_at: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)

    commission: Mapped["Commission"] = relationship(back_populates="memberships")
    deputy: Mapped["Deputy"] = relationship(back_populates="memberships")


class Meeting(Base):
    """Заседание комиссии (или пленарное заседание думы, commission_id=NULL)."""

    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True)
    commission_id: Mapped[int | None] = mapped_column(
        ForeignKey("commissions.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[MeetingStatus] = mapped_column(
        Enum(MeetingStatus), default=MeetingStatus.scheduled, nullable=False
    )
    agenda: Mapped[str | None] = mapped_column(Text, nullable=True)

    commission: Mapped["Commission | None"] = relationship(back_populates="meetings")
    attendances: Mapped[list["Attendance"]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan"
    )


class Attendance(Base):
    """Отметка посещаемости депутата на заседании."""

    __tablename__ = "attendances"
    __table_args__ = (
        UniqueConstraint("meeting_id", "deputy_id", name="uq_attendance_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE")
    )
    deputy_id: Mapped[int] = mapped_column(
        ForeignKey("deputies.id", ondelete="CASCADE")
    )
    status: Mapped[AttendanceStatus] = mapped_column(
        Enum(AttendanceStatus), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    meeting: Mapped["Meeting"] = relationship(back_populates="attendances")
    deputy: Mapped["Deputy"] = relationship(back_populates="attendances")
