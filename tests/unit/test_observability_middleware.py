"""ЛР8: журнал и метрики каждого запроса (app/observability/middleware.py).

Проверяется на «голом» ASGI-приложении: без FastAPI и базы данных.
"""

import asyncio
import json
import logging

import pytest

from app.observability import instruments as m
from app.observability import logs
from app.observability.middleware import ObservabilityMiddleware


class Route:
    def __init__(self, path):
        self.path = path


def make_app(status=200, route="/deputies/{deputy_id}", fail=False):
    async def app(scope, receive, send):
        scope["route"] = Route(route)  # так делает FastAPI после маршрутизации
        logs.get_logger().info("внутри", extra={"event": "inner"})
        if fail:
            raise RuntimeError("postgresql://duma:S3cr3t@db/x недоступна")
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    return app


def call(app, path="/deputies/7", headers=()):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"token=abc",
        "headers": list(headers),
        "client": ("10.0.0.1", 5000),
    }
    asyncio.run(ObservabilityMiddleware(app)(scope, receive, send))
    return sent


@pytest.fixture
def records(caplog):
    caplog.set_level(logging.DEBUG, logger="city_duma")
    return caplog


def request_entries(caplog):
    fmt = logs.JsonFormatter()
    return [
        json.loads(fmt.format(r))
        for r in caplog.records
        if getattr(r, "event", "") == "http_request"
    ]


def header(sent, name):
    start = next(msg for msg in sent if msg["type"] == "http.response.start")
    return dict(start["headers"]).get(name)


def test_request_is_logged_with_all_fields(records):
    sent = call(make_app())
    (entry,) = request_entries(records)
    assert entry["method"] == "GET"
    assert entry["path"] == "/deputies/7"
    assert entry["route"] == "/deputies/{deputy_id}"
    assert entry["status"] == 200
    assert entry["result"] == "success"
    assert entry["duration_ms"] >= 0
    assert entry["request_id"] == header(sent, b"x-request-id").decode()
    assert "token=abc" not in json.dumps(entry)


def test_request_id_from_header_is_kept_and_shared_with_inner_logs(records):
    call(make_app(), headers=[(b"x-request-id", b"abc-123")])
    ids = {getattr(r, "request_id", None) or "-" for r in records.records}
    fmt = logs.JsonFormatter()
    assert {json.loads(fmt.format(r))["request_id"] for r in records.records} == {
        "abc-123"
    }
    assert ids  # записи есть


def test_invalid_request_id_is_replaced():
    sent = call(make_app(), headers=[(b"x-request-id", b"bad id\nwith newline")])
    assert header(sent, b"x-request-id") != b"bad id\nwith newline"


def test_metrics_count_requests_by_route_template():
    before = m.HTTP_REQUESTS.value(
        method="GET", route="/deputies/{deputy_id}", status="404"
    )
    call(make_app(status=404))
    call(make_app(status=404), path="/deputies/8")
    after = m.HTTP_REQUESTS.value(
        method="GET", route="/deputies/{deputy_id}", status="404"
    )
    assert after == before + 2
    assert m.HTTP_DURATION.count(method="GET", route="/deputies/{deputy_id}") >= 2
    assert m.HTTP_IN_PROGRESS.value() == 0


def test_client_and_server_errors_have_results(records):
    call(make_app(status=422))
    call(make_app(status=503))
    results = [(e["status"], e["result"]) for e in request_entries(records)]
    assert results == [(422, "client_error"), (503, "server_error")]


def test_unhandled_exception_returns_500_with_request_id_and_no_secret(records):
    before = m.UNHANDLED.value(route="/boom")
    sent = call(make_app(route="/boom", fail=True), path="/boom")
    start = sent[0]
    assert start["status"] == 500
    body = json.loads(sent[1]["body"])
    assert body["request_id"] == header(sent, b"x-request-id").decode()
    (entry,) = request_entries(records)
    assert entry["result"] == "exception"
    assert "S3cr3t" not in json.dumps(entry)
    assert m.UNHANDLED.value(route="/boom") == before + 1


def test_static_files_and_unknown_paths_get_fixed_labels():
    async def static(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    before_ui = m.HTTP_REQUESTS.value(method="GET", route="/ui", status="200")
    call(static, path="/ui/app.js")
    assert (
        m.HTTP_REQUESTS.value(method="GET", route="/ui", status="200") == before_ui + 1
    )
    before = m.HTTP_REQUESTS.value(method="GET", route="unmatched", status="200")
    call(static, path="/random/path")
    assert (
        m.HTTP_REQUESTS.value(method="GET", route="unmatched", status="200")
        == before + 1
    )


def test_health_checks_are_quiet_and_metrics_endpoint_is_not_measured(records):
    before = m.HTTP_REQUESTS.value(method="GET", route="/metrics", status="200")
    call(make_app(route="/metrics"), path="/metrics")
    call(make_app(route="/health"), path="/health")
    levels = {e["route"]: e["level"] for e in request_entries(records)}
    assert levels == {"/metrics": "DEBUG", "/health": "DEBUG"}
    assert m.HTTP_REQUESTS.value(method="GET", route="/metrics", status="200") == before


def test_non_http_scopes_pass_through():
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    asyncio.run(ObservabilityMiddleware(app)({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]
