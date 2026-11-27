"""ЛР8: структурированные журналы и маскирование секретов (app/observability/logs.py)."""

import json
import logging

import pytest

from app.observability import instruments, logs


def record(message="", **extra):
    rec = logging.makeLogRecord(
        {"name": "city_duma", "levelname": "INFO", "msg": message}
    )
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def fmt(message="", **extra):
    return json.loads(logs.JsonFormatter().format(record(message, **extra)))


def test_record_is_one_line_of_json_with_required_fields():
    line = logs.JsonFormatter().format(record("готово", event="app_started"))
    assert "\n" not in line
    entry = json.loads(line)
    assert {"time", "level", "event", "request_id", "logger"} <= set(entry)
    assert entry["event"] == "app_started"
    assert entry["level"] == "INFO"
    assert entry["time"].endswith("+00:00")


def test_request_id_is_fixed_when_record_is_created():
    token = logs.request_id_var.set("req-42")
    try:
        rec = logging.getLogger("city_duma").makeRecord(
            "city_duma", logging.INFO, __file__, 1, "x", (), None
        )
    finally:
        logs.request_id_var.reset(token)
    # форматируем уже после окончания «запроса»
    assert json.loads(logs.JsonFormatter().format(rec))["request_id"] == "req-42"


@pytest.mark.parametrize(
    "text",
    [
        "postgresql+psycopg2://duma:S3cr3t-pass@db:5432/city_duma",
        "Authorization: Bearer abc.def.ghi",
        "password=S3cr3t-pass user=duma",
        '{"token": "S3cr3t-pass"}',
        "POSTGRES_PASSWORD=S3cr3t-pass",
    ],
)
def test_secrets_in_text_are_masked(text):
    masked, count = logs.redact_text(text)
    assert count >= 1
    assert "S3cr3t-pass" not in masked
    assert "abc.def.ghi" not in masked
    assert logs.MASK in masked


def test_ordinary_text_is_not_changed():
    text = "Нет кворума: присутствует 1 из 3 членов комиссии"
    assert logs.redact_text(text) == (text, 0)


def test_fields_with_secret_names_are_masked_entirely():
    entry = fmt(
        "", event="x", password="p1", api_key="k1", nested={"token": "t1", "ok": 1}
    )
    assert entry["password"] == logs.MASK
    assert entry["api_key"] == logs.MASK
    assert entry["nested"] == {"token": logs.MASK, "ok": 1}


def test_secret_in_exception_text_is_masked():
    try:
        raise RuntimeError("could not connect to postgresql://duma:S3cr3t@db/x")
    except RuntimeError:
        import sys

        rec = record("ошибка", event="unhandled_error")
        rec.exc_info = sys.exc_info()
    entry = json.loads(logs.JsonFormatter().format(rec))
    assert "S3cr3t" not in json.dumps(entry)
    assert "RuntimeError" in entry["error"]


def test_each_redaction_is_counted_in_metric():
    before = instruments.LOG_REDACTIONS.value()
    fmt("password=abc token=def")
    assert instruments.LOG_REDACTIONS.value() == before + 2


def test_setup_logging_writes_json_to_stdout(capsys):
    logs.setup_logging("INFO")
    try:
        logs.get_logger().info("проверка", extra={"event": "test_event"})
        out = capsys.readouterr().out.strip().splitlines()[-1]
        assert json.loads(out)["event"] == "test_event"
        assert logging.getLogger("uvicorn.access").disabled
    finally:
        logging.getLogger().handlers[:] = []
