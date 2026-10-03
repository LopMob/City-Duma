"""Unit-тесты бизнес-логики app/crud.py напрямую, в обход HTTP/FastAPI.

В отличие от tests/test_*.py (интеграционные — идут через TestClient,
роутеры и реальный движок SQLAlchemy), здесь вызываются функции crud.py
напрямую с SQLAlchemy Session. Это даёт две вещи:
  - быстрая, точечная проверка самой бизнес-логики без HTTP-слоя;
  - покрытие веток crud.py, до которых роутеры не достают напрямую
    (например ConflictError до того, как роутер обернёт её в HTTPException).
"""

import pytest

from app import crud, schemas
from app.models import MeetingStatus

# ---------- Deputy ----------


def test_create_get_update_delete_deputy(db_session):
    deputy = crud.create_deputy(
        db_session, schemas.DeputyCreate(full_name="Иванов И.И.")
    )
    assert deputy.id is not None
    assert deputy.is_active is True

    fetched = crud.get_deputy(db_session, deputy.id)
    assert fetched is not None
    assert fetched.full_name == "Иванов И.И."

    updated = crud.update_deputy(
        db_session, deputy, schemas.DeputyUpdate(is_active=False)
    )
    assert updated.is_active is False

    crud.delete_deputy(db_session, updated)
    assert crud.get_deputy(db_session, deputy.id) is None


def test_get_missing_deputy_returns_none(db_session):
    assert crud.get_deputy(db_session, 999) is None


# ---------- Commission ----------


def test_create_commission_duplicate_name_raises_conflict(db_session):
    crud.create_commission(db_session, schemas.CommissionCreate(name="Бюджетная"))
    with pytest.raises(crud.ConflictError):
        crud.create_commission(db_session, schemas.CommissionCreate(name="Бюджетная"))


def test_update_commission_rename_to_existing_name_raises_conflict(db_session):
    crud.create_commission(db_session, schemas.CommissionCreate(name="Комиссия А"))
    b = crud.create_commission(db_session, schemas.CommissionCreate(name="Комиссия Б"))

    with pytest.raises(crud.ConflictError):
        crud.update_commission(
            db_session, b, schemas.CommissionUpdate(name="Комиссия А")
        )


def test_update_commission_rename_to_same_name_is_noop_ok(db_session):
    a = crud.create_commission(db_session, schemas.CommissionCreate(name="Комиссия В"))
    # Переименование "в саму себя" не должно считаться конфликтом.
    updated = crud.update_commission(
        db_session, a, schemas.CommissionUpdate(name="Комиссия В")
    )
    assert updated.name == "Комиссия В"


# ---------- CommissionMembership ----------


def _deputy(db_session, name="Депутат"):
    return crud.create_deputy(db_session, schemas.DeputyCreate(full_name=name))


def _commission(db_session, name="Комиссия"):
    return crud.create_commission(db_session, schemas.CommissionCreate(name=name))


def test_create_membership_duplicate_raises_conflict(db_session):
    commission = _commission(db_session)
    deputy = _deputy(db_session)
    crud.create_membership(
        db_session, commission.id, schemas.MembershipCreate(deputy_id=deputy.id)
    )
    with pytest.raises(crud.ConflictError):
        crud.create_membership(
            db_session, commission.id, schemas.MembershipCreate(deputy_id=deputy.id)
        )


def test_create_membership_second_chair_raises_conflict(db_session):
    commission = _commission(db_session)
    dep1 = _deputy(db_session, "Первый")
    dep2 = _deputy(db_session, "Второй")

    crud.create_membership(
        db_session,
        commission.id,
        schemas.MembershipCreate(deputy_id=dep1.id, is_chair=True),
    )
    with pytest.raises(crud.ConflictError):
        crud.create_membership(
            db_session,
            commission.id,
            schemas.MembershipCreate(deputy_id=dep2.id, is_chair=True),
        )


def test_get_chair_returns_none_when_no_chair(db_session):
    commission = _commission(db_session)
    assert crud.get_chair(db_session, commission.id) is None


def test_update_membership_reassign_chair_to_self_is_ok(db_session):
    commission = _commission(db_session)
    dep = _deputy(db_session)
    membership = crud.create_membership(
        db_session,
        commission.id,
        schemas.MembershipCreate(deputy_id=dep.id, is_chair=True),
    )
    # Повторно назначить председателем того же члена — не конфликт.
    updated = crud.update_membership(
        db_session, membership, schemas.MembershipUpdate(is_chair=True)
    )
    assert updated.is_chair is True


def test_update_membership_second_chair_raises_conflict(db_session):
    commission = _commission(db_session)
    dep1 = _deputy(db_session, "Первый")
    dep2 = _deputy(db_session, "Второй")
    crud.create_membership(
        db_session,
        commission.id,
        schemas.MembershipCreate(deputy_id=dep1.id, is_chair=True),
    )
    m2 = crud.create_membership(
        db_session, commission.id, schemas.MembershipCreate(deputy_id=dep2.id)
    )
    with pytest.raises(crud.ConflictError):
        crud.update_membership(db_session, m2, schemas.MembershipUpdate(is_chair=True))


def test_delete_membership(db_session):
    commission = _commission(db_session)
    dep = _deputy(db_session)
    membership = crud.create_membership(
        db_session, commission.id, schemas.MembershipCreate(deputy_id=dep.id)
    )
    crud.delete_membership(db_session, membership)
    assert crud.list_memberships(db_session, commission.id) == []


# ---------- Meeting / quorum ----------


def _meeting(db_session, commission_id=None, title="Заседание"):
    return crud.create_meeting(
        db_session,
        schemas.MeetingCreate(
            commission_id=commission_id,
            title=title,
            scheduled_at="2026-10-01T10:00:00",
        ),
    )


def test_quorum_not_enforced_for_plenary_meeting(db_session):
    # commission_id=None — пленарное заседание, правило о кворуме не применяется.
    meeting = _meeting(db_session, commission_id=None)
    held = crud.set_meeting_status(db_session, meeting, MeetingStatus.held)
    assert held.status == MeetingStatus.held


def test_quorum_fails_with_zero_members(db_session):
    commission = _commission(db_session)
    meeting = _meeting(db_session, commission_id=commission.id)
    with pytest.raises(crud.ConflictError, match="нет ни одного члена"):
        crud.set_meeting_status(db_session, meeting, MeetingStatus.held)


# ---------- Attendance ----------


def test_mark_attendance_duplicate_raises_conflict(db_session):
    commission = _commission(db_session)
    dep = _deputy(db_session)
    crud.create_membership(
        db_session, commission.id, schemas.MembershipCreate(deputy_id=dep.id)
    )
    meeting = _meeting(db_session, commission_id=commission.id)

    crud.mark_attendance(
        db_session,
        meeting.id,
        schemas.AttendanceCreate(deputy_id=dep.id, status="present"),
    )
    with pytest.raises(crud.ConflictError):
        crud.mark_attendance(
            db_session,
            meeting.id,
            schemas.AttendanceCreate(deputy_id=dep.id, status="absent"),
        )


def test_list_get_update_attendance(db_session):
    commission = _commission(db_session)
    dep = _deputy(db_session)
    crud.create_membership(
        db_session, commission.id, schemas.MembershipCreate(deputy_id=dep.id)
    )
    meeting = _meeting(db_session, commission_id=commission.id)

    attendance = crud.mark_attendance(
        db_session,
        meeting.id,
        schemas.AttendanceCreate(deputy_id=dep.id, status="absent"),
    )

    listed = crud.list_attendance(db_session, meeting.id)
    assert [a.id for a in listed] == [attendance.id]

    fetched = crud.get_attendance(db_session, attendance.id)
    assert fetched is not None and fetched.status.value == "absent"

    updated = crud.update_attendance(
        db_session, fetched, schemas.AttendanceUpdate(status="present", note="опоздал")
    )
    assert updated.status.value == "present"
    assert updated.note == "опоздал"
