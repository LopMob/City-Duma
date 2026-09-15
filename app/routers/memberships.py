from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db

router = APIRouter(prefix="/memberships", tags=["memberships"])


@router.patch("/{membership_id}", response_model=schemas.MembershipRead)
def update_membership(
    membership_id: int, payload: schemas.MembershipUpdate, db: Session = Depends(get_db)
):
    membership = crud.get_membership(db, membership_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="Членство в комиссии не найдено")
    try:
        return crud.update_membership(db, membership, payload)
    except crud.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{membership_id}", status_code=204)
def delete_membership(membership_id: int, db: Session = Depends(get_db)):
    membership = crud.get_membership(db, membership_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="Членство в комиссии не найдено")
    crud.delete_membership(db, membership)
