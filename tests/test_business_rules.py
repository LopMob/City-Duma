def _create_deputy(client, name):
    return client.post("/deputies", json={"full_name": name}).json()["id"]


def _create_commission(client, name):
    return client.post("/commissions", json={"name": name}).json()["id"]


def test_commission_cannot_have_two_chairs(client):
    commission_id = _create_commission(client, "Бюджетная комиссия")
    dep1 = _create_deputy(client, "Первый Депутат")
    dep2 = _create_deputy(client, "Второй Депутат")

    resp1 = client.post(
        f"/commissions/{commission_id}/members",
        json={"deputy_id": dep1, "is_chair": True},
    )
    assert resp1.status_code == 201

    resp2 = client.post(
        f"/commissions/{commission_id}/members",
        json={"deputy_id": dep2, "is_chair": True},
    )
    assert resp2.status_code == 409
    assert "председатель" in resp2.json()["detail"]


def test_meeting_cannot_be_held_without_quorum(client):
    commission_id = _create_commission(client, "Социальная комиссия")
    deputies = [_create_deputy(client, f"Депутат {i}") for i in range(4)]
    for dep_id in deputies:
        client.post(f"/commissions/{commission_id}/members", json={"deputy_id": dep_id})

    meeting_resp = client.post(
        "/meetings",
        json={
            "commission_id": commission_id,
            "title": "Заседание по бюджету",
            "scheduled_at": "2026-09-10T10:00:00",
        },
    )
    meeting_id = meeting_resp.json()["id"]

    client.post(
        f"/meetings/{meeting_id}/attendance",
        json={"deputy_id": deputies[0], "status": "present"},
    )

    resp = client.patch(f"/meetings/{meeting_id}/status", json={"status": "held"})
    assert resp.status_code == 409
    assert "кворум" in resp.json()["detail"].lower()

    client.post(
        f"/meetings/{meeting_id}/attendance",
        json={"deputy_id": deputies[1], "status": "present"},
    )
    client.post(
        f"/meetings/{meeting_id}/attendance",
        json={"deputy_id": deputies[2], "status": "present"},
    )

    resp = client.patch(f"/meetings/{meeting_id}/status", json={"status": "held"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "held"


def test_duplicate_attendance_is_conflict(client):
    commission_id = _create_commission(client, "Комиссия по ЖКХ")
    dep_id = _create_deputy(client, "Депутат Единственный")
    client.post(f"/commissions/{commission_id}/members", json={"deputy_id": dep_id})
    meeting_id = client.post(
        "/meetings",
        json={
            "commission_id": commission_id,
            "title": "Заседание",
            "scheduled_at": "2026-09-10T10:00:00",
        },
    ).json()["id"]

    first = client.post(
        f"/meetings/{meeting_id}/attendance",
        json={"deputy_id": dep_id, "status": "present"},
    )
    assert first.status_code == 201

    second = client.post(
        f"/meetings/{meeting_id}/attendance",
        json={"deputy_id": dep_id, "status": "absent"},
    )
    assert second.status_code == 409
