from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


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

    # NB: связь `attendances` добавляется в задаче #3 (см. docs/tasks/).


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

    # NB: связь `meetings` добавляется в задаче #3.


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
