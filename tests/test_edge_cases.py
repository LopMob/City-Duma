"""Дополнительные граничные и ошибочные случаи (ЛР3, п.5):
валидация входных данных, несуществующие id, каскадное удаление.
"""


def _commission(client, name):
    return client.post("/commissions", json={"name": name}).json()["id"]


def _deputy(client, name):
    return client.post("/deputies", json={"full_name": name}).json()["id"]


def test_deputy_name_exact_min_length_is_valid(client):
    # min_length=2 — граница: ровно 2 символа должно приниматься.
    resp = client.post("/deputies", json={"full_name": "Ян"})
    assert resp.status_code == 201


def test_commission_name_exact_min_length_is_valid(client):
    resp = client.post("/commissions", json={"name": "АБ"})
    assert resp.status_code == 201


def test_commission_name_too_short_422(client):
    resp = client.post("/commissions", json={"name": "А"})
    assert resp.status_code == 422


def test_invalid_attendance_status_422(client):
    commission_id = _commission(client, "Комиссия по контролю")
    dep_id = _deputy(client, "Депутат Контрольный")
    client.post(f"/commissions/{commission_id}/members", json={"deputy_id": dep_id})
    meeting_id = client.post(
        "/meetings",
        json={
            "commission_id": commission_id,
            "title": "Заседание",
            "scheduled_at": "2026-10-01T10:00:00",
        },
    ).json()["id"]

    resp = client.post(
        f"/meetings/{meeting_id}/attendance",
        json={"deputy_id": dep_id, "status": "not-a-real-status"},
    )
    assert resp.status_code == 422


def test_invalid_meeting_status_transition_value_422(client):
    commission_id = _commission(client, "Комиссия по статусам")
    meeting_id = client.post(
        "/meetings",
        json={
            "commission_id": commission_id,
            "title": "Заседание",
            "scheduled_at": "2026-10-01T10:00:00",
        },
    ).json()["id"]

    resp = client.patch(f"/meetings/{meeting_id}/status", json={"status": "bogus"})
    assert resp.status_code == 422


def test_create_meeting_for_missing_commission_404(client):
    resp = client.post(
        "/meetings",
        json={
            "commission_id": 999,
            "title": "Заседание-призрак",
            "scheduled_at": "2026-10-01T10:00:00",
        },
    )
    assert resp.status_code == 404


def test_mark_attendance_for_missing_meeting_404(client):
    dep_id = _deputy(client, "Депутат")
    resp = client.post(
        "/meetings/999/attendance", json={"deputy_id": dep_id, "status": "present"}
    )
    assert resp.status_code == 404


def test_mark_attendance_for_missing_deputy_404(client):
    commission_id = _commission(client, "Комиссия Икс")
    meeting_id = client.post(
        "/meetings",
        json={
            "commission_id": commission_id,
            "title": "Заседание",
            "scheduled_at": "2026-10-01T10:00:00",
        },
    ).json()["id"]
    resp = client.post(
        f"/meetings/{meeting_id}/attendance",
        json={"deputy_id": 999, "status": "present"},
    )
    assert resp.status_code == 404


def test_update_attendance_via_api(client):
    commission_id = _commission(client, "Комиссия по апдейту")
    dep_id = _deputy(client, "Депутат Апдейтов")
    client.post(f"/commissions/{commission_id}/members", json={"deputy_id": dep_id})
    meeting_id = client.post(
        "/meetings",
        json={
            "commission_id": commission_id,
            "title": "Заседание",
            "scheduled_at": "2026-10-01T10:00:00",
        },
    ).json()["id"]
    attendance_id = client.post(
        f"/meetings/{meeting_id}/attendance",
        json={"deputy_id": dep_id, "status": "absent"},
    ).json()["id"]

    resp = client.patch(
        f"/meetings/{meeting_id}/attendance/{attendance_id}",
        json={"status": "present", "note": "опоздал на 10 минут"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "present"
    assert resp.json()["note"] == "опоздал на 10 минут"


def test_update_attendance_wrong_meeting_404(client):
    commission_id = _commission(client, "Комиссия А2")
    dep_id = _deputy(client, "Депутат А2")
    client.post(f"/commissions/{commission_id}/members", json={"deputy_id": dep_id})
    meeting_id = client.post(
        "/meetings",
        json={
            "commission_id": commission_id,
            "title": "Заседание",
            "scheduled_at": "2026-10-01T10:00:00",
        },
    ).json()["id"]
    attendance_id = client.post(
        f"/meetings/{meeting_id}/attendance",
        json={"deputy_id": dep_id, "status": "present"},
    ).json()["id"]

    other_meeting_id = client.post(
        "/meetings",
        json={
            "commission_id": commission_id,
            "title": "Другое заседание",
            "scheduled_at": "2026-10-02T10:00:00",
        },
    ).json()["id"]

    resp = client.patch(
        f"/meetings/{other_meeting_id}/attendance/{attendance_id}",
        json={"status": "absent"},
    )
    assert resp.status_code == 404


def test_delete_commission_cascades_meetings(client):
    commission_id = _commission(client, "Комиссия на удаление")
    meeting_id = client.post(
        "/meetings",
        json={
            "commission_id": commission_id,
            "title": "Заседание под удаление",
            "scheduled_at": "2026-10-01T10:00:00",
        },
    ).json()["id"]

    resp = client.delete(f"/commissions/{commission_id}")
    assert resp.status_code == 204

    resp = client.get(f"/meetings/{meeting_id}")
    assert resp.status_code == 404
