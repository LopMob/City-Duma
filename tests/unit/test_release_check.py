"""Тесты проверки релиза (ЛР6): scripts/release_check.py.

Проверка работает с поддельным «сервером» в памяти, поэтому тесты не требуют
развёрнутого окружения: проверяется сама логика — версия, основной сценарий,
уборка тестовых записей и понятные сообщения об ошибке.
"""

import pytest

from scripts import release_check as rc

COMMIT = "0123456789abcdef0123456789abcdef01234567"


class FakeApp:
    """Минимальная имитация API «Городской Думы»."""

    def __init__(self, version="v0.6.0", commit=COMMIT, broken=None, db_up=True):
        self.version, self.commit, self.broken = version, commit, broken
        self.db_up = db_up
        self.ids = 0
        self.objects: dict[str, dict] = {}
        self.deleted: list[str] = []

    def _new(self, kind, **fields):
        self.ids += 1
        obj = {"id": self.ids, **fields}
        self.objects[f"/{kind}/{self.ids}"] = obj
        return obj

    def __call__(self, method, path, payload):
        if self.broken == path and method == "POST":
            return 500, "Internal Server Error"
        if (method, path) == ("GET", "/health"):
            return 200, {"status": "ok", "environment": "staging"}
        if (method, path) == ("GET", "/ready"):
            if self.db_up:
                return 200, {"status": "ok", "database": "ok"}
            return 503, {"status": "error", "database": "unavailable"}
        if (method, path) == ("GET", "/version"):
            return 200, {
                "version": self.version,
                "commit": self.commit,
                "environment": "staging",
            }
        if (method, path) == ("GET", "/ui/"):
            return 200, "<html></html>"
        if method == "POST" and path in ("/deputies", "/commissions", "/meetings"):
            return 201, self._new(path.strip("/"), **payload)
        if method == "POST" and path.endswith("/members"):
            return 201, {"id": 99, **payload}
        if method == "POST" and path.endswith("/attendance"):
            self.attendance = [{"id": 1, **payload}]
            return 201, self.attendance[0]
        if method == "PATCH" and path.endswith("/status"):
            return 200, {"status": payload["status"]}
        if method == "GET" and path.endswith("/attendance"):
            return 200, self.attendance
        if method == "DELETE":
            self.deleted.append(path)
            return 204, ""
        return 404, {"detail": "not found"}


def run(app, expected="v0.6.0"):
    lines = []
    rc.ReleaseCheck(app, expected, log=lines.append).run()
    return lines


def test_healthy_release_passes_and_cleans_up():
    app = FakeApp()
    lines = run(app)
    assert any("сценарий выполнен" in line for line in lines)
    assert app.deleted[0].startswith("/commissions/")
    assert any(path.startswith("/deputies/") for path in app.deleted)


@pytest.mark.parametrize("expected", ["v0.6.0", COMMIT, COMMIT[:8], ""])
def test_expected_version_matches_tag_or_commit(expected):
    run(FakeApp(), expected)


def test_wrong_running_version_fails():
    with pytest.raises(rc.CheckFailed, match="ожидалась версия v0.7.0"):
        run(FakeApp(), "v0.7.0")


def test_short_prefix_is_not_enough_to_match_commit():
    assert not rc.version_matches("0123", {"version": "x", "commit": COMMIT})


def test_broken_main_scenario_fails_but_cleans_up():
    app = FakeApp(broken="/meetings")
    with pytest.raises(rc.CheckFailed, match="создание заседания"):
        run(app)
    assert len(app.deleted) == 2  # комиссия и депутат удалены несмотря на ошибку


def test_unhealthy_app_fails():
    def call(method, path, payload):
        return 503, "Service Unavailable"

    with pytest.raises(rc.CheckFailed, match="/health"):
        run(call)


def test_release_with_unavailable_database_fails_before_version_check():
    """ЛР8, INCIDENT-001: /health жив, а БД нет — выпуск не принимается."""
    app = FakeApp(db_up=False)
    with pytest.raises(rc.CheckFailed, match="/ready"):
        run(app)
    assert app.ids == 0  # основной сценарий даже не начинался


def test_old_version_without_ready_endpoint_still_passes():
    """Откат на версию до ЛР8 (нет /ready) не должен считаться ошибкой."""

    class OldApp(FakeApp):
        def __call__(self, method, path, payload):
            if path == "/ready":
                return 404, {"detail": "Not Found"}
            return super().__call__(method, path, payload)

    lines = run(OldApp())
    assert any("/ready нет" in line for line in lines)
