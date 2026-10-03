from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db

DbSession = Annotated[Session, Depends(get_db)]

router = APIRouter(prefix="/deputies", tags=["deputies"])


@router.get("", response_model=list[schemas.DeputyRead])
def list_deputies(db: DbSession):
    return crud.list_deputies(db)


@router.post("", response_model=schemas.DeputyRead, status_code=201)
def create_deputy(payload: schemas.DeputyCreate, db: DbSession):
    try:
        return crud.create_deputy(db, payload)
    except crud.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{deputy_id}", response_model=schemas.DeputyRead)
def get_deputy(deputy_id: int, db: DbSession):
    deputy = crud.get_deputy(db, deputy_id)
    if deputy is None:
        raise HTTPException(status_code=404, detail="Депутат не найден")
    return deputy


@router.patch("/{deputy_id}", response_model=schemas.DeputyRead)
def update_deputy(deputy_id: int, payload: schemas.DeputyUpdate, db: DbSession):
    deputy = crud.get_deputy(db, deputy_id)
    if deputy is None:
        raise HTTPException(status_code=404, detail="Депутат не найден")
    try:
        return crud.update_deputy(db, deputy, payload)
    except crud.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{deputy_id}", status_code=204)
def delete_deputy(deputy_id: int, db: DbSession):
    deputy = crud.get_deputy(db, deputy_id)
    if deputy is None:
        raise HTTPException(status_code=404, detail="Депутат не найден")
    crud.delete_deputy(db, deputy)
