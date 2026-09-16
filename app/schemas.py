from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import AttendanceStatus, MeetingStatus

# ---------- Deputy ----------


class DeputyBase(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    party: str | None = Field(default=None, max_length=120)
    election_district: str | None = Field(default=None, max_length=120)
    is_active: bool = True


class DeputyCreate(DeputyBase):
    pass


class DeputyUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    party: str | None = Field(default=None, max_length=120)
    election_district: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None


class DeputyRead(DeputyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


# ---------- Commission ----------


class CommissionBase(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    description: str | None = None


class CommissionCreate(CommissionBase):
    pass


class CommissionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = None


class CommissionRead(CommissionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


# ---------- CommissionMembership ----------


class MembershipCreate(BaseModel):
    deputy_id: int
    is_chair: bool = False


class MembershipUpdate(BaseModel):
    is_chair: bool


class MembershipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    commission_id: int
    deputy_id: int
    is_chair: bool
    joined_at: date


# ---------- Meeting ----------


class MeetingCreate(BaseModel):
    commission_id: int | None = None
    title: str = Field(min_length=2, max_length=255)
    scheduled_at: datetime
    agenda: str | None = None


class MeetingStatusUpdate(BaseModel):
    status: MeetingStatus


class MeetingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    commission_id: int | None
    title: str
    scheduled_at: datetime
    status: MeetingStatus
    agenda: str | None


# ---------- Attendance ----------


class AttendanceCreate(BaseModel):
    deputy_id: int
    status: AttendanceStatus
    note: str | None = None


class AttendanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meeting_id: int
    deputy_id: int
    status: AttendanceStatus
    note: str | None
