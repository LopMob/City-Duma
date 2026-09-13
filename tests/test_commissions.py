def test_create_and_get_commission(client):
    resp = client.post("/commissions", json={"name": "Бюджетная комиссия"})
    assert resp.status_code == 201
    commission_id = resp.json()["id"]

    resp = client.get(f"/commissions/{commission_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Бюджетная комиссия"


def test_duplicate_commission_name_409(client):
    client.post("/commissions", json={"name": "Социальная комиссия"})
    resp = client.post("/commissions", json={"name": "Социальная комиссия"})
    assert resp.status_code == 409


def test_rename_commission_conflict_409(client):
    client.post("/commissions", json={"name": "Комиссия А"})
    resp = client.post("/commissions", json={"name": "Комиссия Б"})
    commission_b_id = resp.json()["id"]

    resp = client.patch(f"/commissions/{commission_b_id}", json={"name": "Комиссия А"})
    assert resp.status_code == 409


def test_get_missing_commission_404(client):
    resp = client.get("/commissions/999")
    assert resp.status_code == 404


def test_delete_commission(client):
    resp = client.post("/commissions", json={"name": "Временная комиссия"})
    commission_id = resp.json()["id"]

    resp = client.delete(f"/commissions/{commission_id}")
    assert resp.status_code == 204
    assert client.get(f"/commissions/{commission_id}").status_code == 404
