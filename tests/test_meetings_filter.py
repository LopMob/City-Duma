def test_list_meetings_filtered_by_status(client):
    commission_id = client.post(
        "/commissions", json={"name": "Комиссия по спорту"}
    ).json()["id"]

    scheduled_id = client.post(
        "/meetings",
        json={
            "commission_id": commission_id,
            "title": "Плановое заседание",
            "scheduled_at": "2026-10-01T10:00:00",
        },
    ).json()["id"]

    cancelled_id = client.post(
        "/meetings",
        json={
            "commission_id": commission_id,
            "title": "Отменённое заседание",
            "scheduled_at": "2026-10-05T10:00:00",
        },
    ).json()["id"]
    client.patch(f"/meetings/{cancelled_id}/status", json={"status": "cancelled"})

    resp = client.get("/meetings", params={"status": "scheduled"})
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()]
    assert scheduled_id in ids
    assert cancelled_id not in ids

    resp = client.get("/meetings", params={"status": "cancelled"})
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()]
    assert ids == [cancelled_id]


def test_list_meetings_invalid_status_returns_422(client):
    resp = client.get("/meetings", params={"status": "not-a-status"})
    assert resp.status_code == 422
