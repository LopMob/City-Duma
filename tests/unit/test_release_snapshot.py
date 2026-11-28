"""ЛР8, п.12: снимок состояния до/после выпуска (scripts/release_snapshot.py)."""

import json
from datetime import UTC, datetime

from scripts import release_snapshot as rs

METRICS = """# HELP http_requests_total x
# TYPE http_requests_total counter
http_requests_total{method="GET",route="/deputies",status="200"} 40
http_requests_total{method="GET",route="/deputies",status="500"} 2
app_db_up 1
duma_meetings{status="held"} 3
"""


def fake(prom_ok=True, db_up=True):
    def fetch(url):
        if url.endswith("/version"):
            return 200, '{"version": "v0.8.0", "environment": "staging"}'
        if url.endswith("/health"):
            return 200, '{"status": "ok"}'
        if url.endswith("/ready"):
            return (
                (200, '{"database": "ok"}')
                if db_up
                else (503, '{"database": "unavailable"}')
            )
        if url.endswith("/metrics"):
            return 200, METRICS
        if "/api/v1/query" in url:
            if not prom_ok:
                return 0, "connection refused"
            if "ALERTS" in url:
                result = [
                    {"metric": {"alertname": "AppDatabaseDown"}, "value": [0, "1"]}
                ]
            else:
                result = [{"metric": {}, "value": [0, "0.0476"]}]
            return 200, json.dumps({"data": {"result": result}})
        return 404, ""

    return fetch


NOW = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)


def test_snapshot_contains_version_readiness_metrics_and_alerts():
    text = rs.snapshot(fake(), "http://h:8800/", "http://h:9090", "before-v0.8.0", NOW)
    assert "# Снимок состояния: before-v0.8.0" in text
    assert '"version": "v0.8.0"' in text
    assert "/ready: HTTP 200" in text
    assert "HTTP-запросов с запуска: 42, из них 5xx: 2" in text
    assert '`duma_meetings{status="held"} 3`' in text
    assert "Сработавшие оповещения: AppDatabaseDown" in text
    assert "0.0476" in text


def test_snapshot_shows_problems_instead_of_failing():
    text = rs.snapshot(
        fake(prom_ok=False, db_up=False), "http://h", "http://p", "after", NOW
    )
    assert "/ready: HTTP 503" in text
    assert "нет данных (HTTP 0)" in text


def test_main_writes_markdown_file(tmp_path, capsys):
    code = rs.main(
        ["--url", "http://h", "--label", "after v0.8.0", "--out-dir", str(tmp_path)],
        fetch=fake(),
    )
    assert code == 0
    (saved,) = tmp_path.glob("*.md")
    assert saved.name.endswith("-after-v0.8.0.md")
    assert "Prometheus" not in saved.read_text("utf-8")
    assert "Сохранено" in capsys.readouterr().out
