from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
