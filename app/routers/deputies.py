from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db

router = APIRouter(prefix="/deputies", tags=["deputies"])


@router.get("", response_model=list[schemas.DeputyRead])
def list_deputies(db: Session = Depends(get_db)):
    return crud.list_deputies(db)


@router.post("", response_model=schemas.DeputyRead, status_code=201)
def create_deputy(payload: schemas.DeputyCreate, db: Session = Depends(get_db)):
    return crud.create_deputy(db, payload)


@router.get("/{deputy_id}", response_model=schemas.DeputyRead)
def get_deputy(deputy_id: int, db: Session = Depends(get_db)):
    deputy = crud.get_deputy(db, deputy_id)
    if deputy is None:
        raise HTTPException(status_code=404, detail="Депутат не найден")
    return deputy


@router.patch("/{deputy_id}", response_model=schemas.DeputyRead)
def update_deputy(deputy_id: int, payload: schemas.DeputyUpdate, db: Session = Depends(get_db)):
    deputy = crud.get_deputy(db, deputy_id)
    if deputy is None:
        raise HTTPException(status_code=404, detail="Депутат не найден")
    return crud.update_deputy(db, deputy, payload)


@router.delete("/{deputy_id}", status_code=204)
def delete_deputy(deputy_id: int, db: Session = Depends(get_db)):
    deputy = crud.get_deputy(db, deputy_id)
    if deputy is None:
        raise HTTPException(status_code=404, detail="Депутат не найден")
    crud.delete_deputy(db, deputy)
