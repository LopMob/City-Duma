def create_deputy(client, name):
    return client.post("/deputies", json={"full_name": name}).json()["id"]


def create_commission(client, name):
    return client.post("/commissions", json={"name": name}).json()["id"]


def test_add_and_list_members(client):
    commission_id = create_commission(client, "Бюджетная комиссия")
    deputy_id = create_deputy(client, "Иванов Иван Иванович")

    resp = client.post(
        f"/commissions/{commission_id}/members", json={"deputy_id": deputy_id}
    )
    assert resp.status_code == 201
    assert resp.json()["is_chair"] is False

    resp = client.get(f"/commissions/{commission_id}/members")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["deputy_id"] == deputy_id


def test_same_deputy_twice_409(client):
    commission_id = create_commission(client, "Социальная комиссия")
    deputy_id = create_deputy(client, "Петров Пётр Петрович")

    client.post(f"/commissions/{commission_id}/members", json={"deputy_id": deputy_id})
    resp = client.post(
        f"/commissions/{commission_id}/members", json={"deputy_id": deputy_id}
    )
    assert resp.status_code == 409


def test_second_chair_409(client):
    commission_id = create_commission(client, "Комиссия по ЖКХ")
    first = create_deputy(client, "Сидоров Сидор Сидорович")
    second = create_deputy(client, "Кузнецов Кузьма Кузьмич")

    resp = client.post(
        f"/commissions/{commission_id}/members",
        json={"deputy_id": first, "is_chair": True},
    )
    assert resp.status_code == 201

    resp = client.post(
        f"/commissions/{commission_id}/members",
        json={"deputy_id": second, "is_chair": True},
    )
    assert resp.status_code == 409


def test_second_chair_via_patch_409(client):
    commission_id = create_commission(client, "Комиссия по транспорту")
    first = create_deputy(client, "Смирнов Семён Семёнович")
    second = create_deputy(client, "Орлов Олег Олегович")

    client.post(
        f"/commissions/{commission_id}/members",
        json={"deputy_id": first, "is_chair": True},
    )
    resp = client.post(
        f"/commissions/{commission_id}/members", json={"deputy_id": second}
    )
    membership_id = resp.json()["id"]

    resp = client.patch(f"/memberships/{membership_id}", json={"is_chair": True})
    assert resp.status_code == 409


def test_change_chair(client):
    commission_id = create_commission(client, "Комиссия по образованию")
    first = create_deputy(client, "Волков Виктор Викторович")
    second = create_deputy(client, "Зайцев Захар Захарович")

    resp = client.post(
        f"/commissions/{commission_id}/members",
        json={"deputy_id": first, "is_chair": True},
    )
    first_membership = resp.json()["id"]
    resp = client.post(
        f"/commissions/{commission_id}/members", json={"deputy_id": second}
    )
    second_membership = resp.json()["id"]

    # Снимаем старого председателя, назначаем нового.
    resp = client.patch(f"/memberships/{first_membership}", json={"is_chair": False})
    assert resp.status_code == 200
    resp = client.patch(f"/memberships/{second_membership}", json={"is_chair": True})
    assert resp.status_code == 200
    assert resp.json()["is_chair"] is True


def test_add_member_missing_deputy_404(client):
    commission_id = create_commission(client, "Комиссия по экологии")
    resp = client.post(f"/commissions/{commission_id}/members", json={"deputy_id": 999})
    assert resp.status_code == 404


def test_delete_membership(client):
    commission_id = create_commission(client, "Комиссия по культуре")
    deputy_id = create_deputy(client, "Морозов Максим Максимович")
    resp = client.post(
        f"/commissions/{commission_id}/members", json={"deputy_id": deputy_id}
    )
    membership_id = resp.json()["id"]

    resp = client.delete(f"/memberships/{membership_id}")
    assert resp.status_code == 204
    assert client.get(f"/commissions/{commission_id}/members").json() == []


def test_delete_deputy_removes_membership(client):
    commission_id = create_commission(client, "Комиссия по спорту")
    deputy_id = create_deputy(client, "Лебедев Лев Львович")
    client.post(f"/commissions/{commission_id}/members", json={"deputy_id": deputy_id})

    client.delete(f"/deputies/{deputy_id}")
    assert client.get(f"/commissions/{commission_id}/members").json() == []
