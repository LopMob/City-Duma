"""Граничные случаи правила кворума (ЛР3, п.5 — мутационное тестирование).

Кворум: `required = total_members // 2 + 1`. Эти тесты закрывают именно
граничные значения, чтобы любая мутация формулы (`+1` -> ничего, `//2` ->
`/2`, `<` -> `<=` и т.п.) ломала хотя бы один тест.
"""

import pytest

from app import crud, schemas
from app.models import MeetingStatus


def _commission_with_members(db_session, n_members):
    commission = crud.create_commission(
        db_session, schemas.CommissionCreate(name=f"Комиссия на {n_members}")
    )
    deputies = [
        crud.create_deputy(db_session, schemas.DeputyCreate(full_name=f"Депутат {i}"))
        for i in range(n_members)
    ]
    for dep in deputies:
        crud.create_membership(
            db_session, commission.id, schemas.MembershipCreate(deputy_id=dep.id)
        )
    return commission, deputies


def _meeting(db_session, commission_id):
    return crud.create_meeting(
        db_session,
        schemas.MeetingCreate(
            commission_id=commission_id,
            title="Заседание",
            scheduled_at="2026-10-01T10:00:00",
        ),
    )


def _mark_present(db_session, meeting_id, deputies, count):
    for dep in deputies[:count]:
        crud.mark_attendance(
            db_session,
            meeting_id,
            schemas.AttendanceCreate(deputy_id=dep.id, status="present"),
        )


@pytest.mark.parametrize(
    "total,required",
    [
        (1, 1),  # 1 член: нужен он сам
        (2, 2),  # 2 члена: нужны оба
        (3, 2),  # 3 члена: нужно 2
        (4, 3),  # 4 члена: нужно 3 (строго больше половины, не просто половина)
        (5, 3),  # 5 членов: нужно 3
        (6, 4),  # 6 членов: нужно 4
    ],
)
def test_quorum_required_count_boundary(db_session, total, required):
    commission, deputies = _commission_with_members(db_session, total)
    meeting = _meeting(db_session, commission.id)

    # На один голос меньше требуемого — кворума быть не должно (если required > 0).
    if required > 1:
        _mark_present(db_session, meeting.id, deputies, required - 1)
        with pytest.raises(crud.ConflictError, match="кворум"):
            crud.set_meeting_status(db_session, meeting, MeetingStatus.held)

        # Добираем недостающий голос.
        crud.mark_attendance(
            db_session,
            meeting.id,
            schemas.AttendanceCreate(
                deputy_id=deputies[required - 1].id, status="present"
            ),
        )
    else:
        _mark_present(db_session, meeting.id, deputies, required)

    held = crud.set_meeting_status(db_session, meeting, MeetingStatus.held)
    assert held.status == MeetingStatus.held


def test_quorum_absent_and_excused_do_not_count_as_present(db_session):
    """Кворум считается только по статусу present — absent/excused не должны
    засчитываться, даже если отметка вообще есть."""
    commission, deputies = _commission_with_members(db_session, 4)
    meeting = _meeting(db_session, commission.id)

    crud.mark_attendance(
        db_session,
        meeting.id,
        schemas.AttendanceCreate(deputy_id=deputies[0].id, status="present"),
    )
    crud.mark_attendance(
        db_session,
        meeting.id,
        schemas.AttendanceCreate(deputy_id=deputies[1].id, status="absent"),
    )
    crud.mark_attendance(
        db_session,
        meeting.id,
        schemas.AttendanceCreate(deputy_id=deputies[2].id, status="excused"),
    )
    # present=1 из требуемых 3 — кворума нет, даже если отметок всего 3.
    with pytest.raises(crud.ConflictError, match="кворум"):
        crud.set_meeting_status(db_session, meeting, MeetingStatus.held)
