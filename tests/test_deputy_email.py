"""Новая функция ЛР3 (п.10): контактный email депутата — необязательный,
но уникальный. Требует миграции 0004 (alembic/versions/0004_deputy_email.py).
"""


def test_create_deputy_with_email(client):
    resp = client.post(
        "/deputies",
        json={"full_name": "Белов Борис Борисович", "email": "belov@duma.example"},
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "belov@duma.example"


def test_multiple_deputies_without_email_is_allowed(client):
    # Уникальность email не должна мешать нескольким депутатам без email —
    # SQL UNIQUE не считает NULL дубликатом.
    r1 = client.post("/deputies", json={"full_name": "Депутат Один"})
    r2 = client.post("/deputies", json={"full_name": "Депутат Два"})
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["email"] is None
    assert r2.json()["email"] is None


def test_duplicate_email_on_create_409(client):
    client.post(
        "/deputies", json={"full_name": "Волков Виктор", "email": "dup@duma.example"}
    )
    resp = client.post(
        "/deputies", json={"full_name": "Зайцев Захар", "email": "dup@duma.example"}
    )
    assert resp.status_code == 409


def test_duplicate_email_on_update_409(client):
    client.post(
        "/deputies", json={"full_name": "Орлов Олег", "email": "orlov@duma.example"}
    )
    second_id = client.post("/deputies", json={"full_name": "Смирнов Семён"}).json()[
        "id"
    ]

    resp = client.patch(f"/deputies/{second_id}", json={"email": "orlov@duma.example"})
    assert resp.status_code == 409


def test_update_email_to_itself_is_ok(client):
    deputy_id = client.post(
        "/deputies", json={"full_name": "Лебедев Лев", "email": "lebedev@duma.example"}
    ).json()["id"]

    resp = client.patch(
        f"/deputies/{deputy_id}", json={"email": "lebedev@duma.example"}
    )
    assert resp.status_code == 200


def test_invalid_email_format_422(client):
    resp = client.post(
        "/deputies", json={"full_name": "Козлов Константин", "email": "not-an-email"}
    )
    assert resp.status_code == 422
