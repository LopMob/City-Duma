from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db
from app.models import MeetingStatus

DbSession = Annotated[Session, Depends(get_db)]

router = APIRouter(prefix="/meetings", tags=["Заседания"])


def _get_meeting_or_404(db: Session, meeting_id: int):
    meeting = crud.get_meeting(db, meeting_id)
    if meeting is None:
        raise HTTPException(
            status_code=404, detail=f"Заседание id={meeting_id} не найдено"
        )
    return meeting


@router.post("", response_model=schemas.MeetingRead, status_code=201)
def create_meeting(data: schemas.MeetingCreate, db: DbSession):
    if (
        data.commission_id is not None
        and crud.get_commission(db, data.commission_id) is None
    ):
        raise HTTPException(
            status_code=404, detail=f"Комиссия id={data.commission_id} не найдена"
        )
    return crud.create_meeting(db, data)


@router.get("", response_model=list[schemas.MeetingRead])
def list_meetings(
    db: DbSession,
    commission_id: Annotated[int | None, Query()] = None,
    status: Annotated[MeetingStatus | None, Query()] = None,
):
    return crud.list_meetings(db, commission_id, status)


@router.get("/{meeting_id}", response_model=schemas.MeetingRead)
def get_meeting(meeting_id: int, db: DbSession):
    return _get_meeting_or_404(db, meeting_id)


@router.patch("/{meeting_id}/status", response_model=schemas.MeetingRead)
def set_meeting_status(
    meeting_id: int, data: schemas.MeetingStatusUpdate, db: DbSession
):
    meeting = _get_meeting_or_404(db, meeting_id)
    return crud.set_meeting_status(db, meeting, data.status)


@router.post(
    "/{meeting_id}/attendance", response_model=schemas.AttendanceRead, status_code=201
)
def mark_attendance(meeting_id: int, data: schemas.AttendanceCreate, db: DbSession):
    _get_meeting_or_404(db, meeting_id)
    if crud.get_deputy(db, data.deputy_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Депутат id={data.deputy_id} не найден"
        )
    return crud.mark_attendance(db, meeting_id, data)


@router.get("/{meeting_id}/attendance", response_model=list[schemas.AttendanceRead])
def list_attendance(meeting_id: int, db: DbSession):
    _get_meeting_or_404(db, meeting_id)
    return crud.list_attendance(db, meeting_id)


@router.patch(
    "/{meeting_id}/attendance/{attendance_id}", response_model=schemas.AttendanceRead
)
def update_attendance(
    meeting_id: int, attendance_id: int, data: schemas.AttendanceUpdate, db: DbSession
):
    _get_meeting_or_404(db, meeting_id)
    attendance = crud.get_attendance(db, attendance_id)
    if attendance is None or attendance.meeting_id != meeting_id:
        raise HTTPException(
            status_code=404,
            detail=f"Отметка посещаемости id={attendance_id} не найдена",
        )
    return crud.update_attendance(db, attendance, data)
