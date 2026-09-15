from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, schemas


class ConflictError(Exception):
    """Нарушение бизнес-правила / уникальности (409 Conflict)."""


# ---------- Deputy ----------


def list_deputies(db: Session) -> list[models.Deputy]:
    return list(db.scalars(select(models.Deputy).order_by(models.Deputy.id)))


def get_deputy(db: Session, deputy_id: int) -> models.Deputy | None:
    return db.get(models.Deputy, deputy_id)


def create_deputy(db: Session, data: schemas.DeputyCreate) -> models.Deputy:
    deputy = models.Deputy(**data.model_dump())
    db.add(deputy)
    db.commit()
    db.refresh(deputy)
    return deputy


def update_deputy(db: Session, deputy: models.Deputy, data: schemas.DeputyUpdate) -> models.Deputy:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(deputy, field, value)
    db.commit()
    db.refresh(deputy)
    return deputy


def delete_deputy(db: Session, deputy: models.Deputy) -> None:
    db.delete(deputy)
    db.commit()


# ---------- Commission ----------


def list_commissions(db: Session) -> list[models.Commission]:
    return list(db.scalars(select(models.Commission).order_by(models.Commission.id)))


def get_commission(db: Session, commission_id: int) -> models.Commission | None:
    return db.get(models.Commission, commission_id)


def get_commission_by_name(db: Session, name: str) -> models.Commission | None:
    return db.scalar(select(models.Commission).where(models.Commission.name == name))


def create_commission(db: Session, data: schemas.CommissionCreate) -> models.Commission:
    if get_commission_by_name(db, data.name) is not None:
        raise ConflictError(f"Комиссия с названием '{data.name}' уже существует")
    commission = models.Commission(**data.model_dump())
    db.add(commission)
    db.commit()
    db.refresh(commission)
    return commission


def update_commission(
    db: Session, commission: models.Commission, data: schemas.CommissionUpdate
) -> models.Commission:
    updates = data.model_dump(exclude_unset=True)
    new_name = updates.get("name")
    if new_name and new_name != commission.name and get_commission_by_name(db, new_name):
        raise ConflictError(f"Комиссия с названием '{new_name}' уже существует")
    for field, value in updates.items():
        setattr(commission, field, value)
    db.commit()
    db.refresh(commission)
    return commission


def delete_commission(db: Session, commission: models.Commission) -> None:
    db.delete(commission)
    db.commit()


# ---------- CommissionMembership ----------

CHAIR_EXISTS = "У комиссии уже есть председатель. Сначала снимите текущего председателя"


def list_memberships(
    db: Session, commission_id: int
) -> list[models.CommissionMembership]:
    stmt = (
        select(models.CommissionMembership)
        .where(models.CommissionMembership.commission_id == commission_id)
        .order_by(models.CommissionMembership.id)
    )
    return list(db.scalars(stmt))


def get_membership(
    db: Session, membership_id: int
) -> models.CommissionMembership | None:
    return db.get(models.CommissionMembership, membership_id)


def get_chair(db: Session, commission_id: int) -> models.CommissionMembership | None:
    """Текущий председатель комиссии (или None, если его нет)."""
    return db.scalar(
        select(models.CommissionMembership).where(
            models.CommissionMembership.commission_id == commission_id,
            models.CommissionMembership.is_chair.is_(True),
        )
    )


def create_membership(
    db: Session, commission_id: int, data: schemas.MembershipCreate
) -> models.CommissionMembership:
    existing = db.scalar(
        select(models.CommissionMembership).where(
            models.CommissionMembership.commission_id == commission_id,
            models.CommissionMembership.deputy_id == data.deputy_id,
        )
    )
    if existing is not None:
        raise ConflictError("Этот депутат уже состоит в данной комиссии")

    # Правило: у комиссии может быть только один председатель.
    if data.is_chair and get_chair(db, commission_id) is not None:
        raise ConflictError(CHAIR_EXISTS)

    membership = models.CommissionMembership(
        commission_id=commission_id, **data.model_dump()
    )
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return membership


def update_membership(
    db: Session, membership: models.CommissionMembership, data: schemas.MembershipUpdate
) -> models.CommissionMembership:
    if data.is_chair:
        chair = get_chair(db, membership.commission_id)
        # Себя же повторно назначить можно, другого второго председателя — нельзя.
        if chair is not None and chair.id != membership.id:
            raise ConflictError(CHAIR_EXISTS)
    membership.is_chair = data.is_chair
    db.commit()
    db.refresh(membership)
    return membership


def delete_membership(db: Session, membership: models.CommissionMembership) -> None:
    db.delete(membership)
    db.commit()
