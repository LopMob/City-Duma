from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

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

    # NB: связи `memberships` и `attendances` (back_populates) добавляются
    # в задачах #2 и #3 (см. docs/tasks/), когда появятся сами таблицы
    # commission_memberships и attendances. Не добавляйте relationship()
    # на несуществующую модель — приложение не запустится.


class Commission(Base):
    """Комиссия городской думы (например, бюджетная, социальная)."""

    __tablename__ = "commissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # NB: связи `memberships` и `meetings` добавляются в задачах #2 и #3.
