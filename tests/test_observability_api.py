"""ЛР8: журналы, метрики и проверка готовности на настоящем API и БД."""

import logging
import re

from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.main import app


def metric(text: str, sample: str) -> float:
    """Значение строки метрики, например 'app_db_up' или 'x{a="b"}'."""
    for line in text.splitlines():
        if line.startswith(sample + " "):
            return float(line.split()[-1])
    raise AssertionError(f"нет метрики {sample}")


def test_metrics_endpoint_exposes_prometheus_text(client):
    client.get("/deputies")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "# TYPE http_requests_total counter" in body
    assert (
        metric(body, 'http_requests_total{method="GET",route="/deputies",status="200"}')
        >= 1
    )
    assert metric(body, "app_db_up") == 1
    assert metric(body, 'duma_meetings{status="held"}') == 0
    assert 'app_info{version="' in body


def test_route_template_is_used_instead_of_real_address(client):
    client.get("/deputies/987654")
    body = client.get("/metrics").text
    assert 'route="/deputies/{deputy_id}",status="404"' in body
    assert "987654" not in body


def test_quorum_rejection_is_counted(client):
    before = metric(client.get("/metrics").text, "duma_quorum_rejections_total")
    commission_id = client.post("/commissions", json={"name": "Комиссия"}).json()["id"]
    for i in range(2):
        dep = client.post("/deputies", json={"full_name": f"Депутат {i}"}).json()["id"]
        client.post(f"/commissions/{commission_id}/members", json={"deputy_id": dep})
    meeting_id = client.post(
        "/meetings",
        json={
            "commission_id": commission_id,
            "title": "Без кворума",
            "scheduled_at": "2026-12-01T10:00:00",
        },
    ).json()["id"]
    resp = client.patch(f"/meetings/{meeting_id}/status", json={"status": "held"})
    assert resp.status_code == 409
    body = client.get("/metrics").text
    assert metric(body, "duma_quorum_rejections_total") == before + 1
    assert metric(body, 'duma_meetings{status="scheduled"}') == 1


def test_ready_checks_database(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}


class BrokenSession:
    """Сессия, у которой «упала» база данных."""

    def execute(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    def close(self):
        pass


def test_ready_and_metrics_report_database_down(client):
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = BrokenSession
    try:
        resp = client.get("/ready")
        assert resp.status_code == 503
        assert resp.json() == {"status": "error", "database": "unavailable"}
        body = client.get("/metrics").text
        assert metric(body, "app_db_up") == 0
        # liveness при этом жив: процесс работает, не работает зависимость
        assert client.get("/health").status_code == 200
    finally:
        app.dependency_overrides[get_db] = previous


def test_request_id_is_returned_and_kept(client):
    assert (
        client.get("/health", headers={"X-Request-ID": "test-123"}).headers[
            "x-request-id"
        ]
        == "test-123"
    )
    generated = client.get("/health").headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", generated)


def test_each_request_is_logged_as_structured_event(client, caplog):
    caplog.set_level(logging.INFO, logger="city_duma")
    client.get("/deputies?token=secret-value", headers={"X-Request-ID": "log-1"})
    (rec,) = [r for r in caplog.records if getattr(r, "event", "") == "http_request"]
    assert rec.request_id == "log-1"
    assert rec.route == "/deputies"
    assert rec.status == 200
    assert rec.result == "success"
    assert "secret-value" not in rec.path
