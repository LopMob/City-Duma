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
