from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db

router = APIRouter(prefix="/commissions", tags=["commissions"])


@router.get("", response_model=list[schemas.CommissionRead])
def list_commissions(db: Session = Depends(get_db)):
    return crud.list_commissions(db)


@router.post("", response_model=schemas.CommissionRead, status_code=201)
def create_commission(payload: schemas.CommissionCreate, db: Session = Depends(get_db)):
    try:
        return crud.create_commission(db, payload)
    except crud.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{commission_id}", response_model=schemas.CommissionRead)
def get_commission(commission_id: int, db: Session = Depends(get_db)):
    commission = crud.get_commission(db, commission_id)
    if commission is None:
        raise HTTPException(status_code=404, detail="Комиссия не найдена")
    return commission


@router.patch("/{commission_id}", response_model=schemas.CommissionRead)
def update_commission(
    commission_id: int, payload: schemas.CommissionUpdate, db: Session = Depends(get_db)
):
    commission = crud.get_commission(db, commission_id)
    if commission is None:
        raise HTTPException(status_code=404, detail="Комиссия не найдена")
    try:
        return crud.update_commission(db, commission, payload)
    except crud.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{commission_id}", status_code=204)
def delete_commission(commission_id: int, db: Session = Depends(get_db)):
    commission = crud.get_commission(db, commission_id)
    if commission is None:
        raise HTTPException(status_code=404, detail="Комиссия не найдена")
    crud.delete_commission(db, commission)
