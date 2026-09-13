def test_create_and_get_deputy(client):
    resp = client.post("/deputies", json={"full_name": "Иванов Иван Иванович"})
    assert resp.status_code == 201
    deputy_id = resp.json()["id"]

    resp = client.get(f"/deputies/{deputy_id}")
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Иванов Иван Иванович"
    assert resp.json()["is_active"] is True


def test_list_deputies(client):
    client.post("/deputies", json={"full_name": "Петров Пётр Петрович"})
    resp = client.get("/deputies")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_missing_deputy_404(client):
    resp = client.get("/deputies/999")
    assert resp.status_code == 404


def test_create_deputy_name_too_short_422(client):
    resp = client.post("/deputies", json={"full_name": "А"})
    assert resp.status_code == 422


def test_update_deputy(client):
    resp = client.post("/deputies", json={"full_name": "Сидоров Сидор Сидорович"})
    deputy_id = resp.json()["id"]

    resp = client.patch(f"/deputies/{deputy_id}", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


def test_delete_deputy(client):
    resp = client.post("/deputies", json={"full_name": "Кузнецов Кузьма Кузьмич"})
    deputy_id = resp.json()["id"]

    resp = client.delete(f"/deputies/{deputy_id}")
    assert resp.status_code == 204

    resp = client.get(f"/deputies/{deputy_id}")
    assert resp.status_code == 404
