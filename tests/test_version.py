"""ЛР6: адрес /version — по нему после развёртывания проверяется,
какая версия приложения на самом деле запущена."""

from app.config import Settings


def test_version_endpoint_returns_version_and_commit(client):
    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"version", "commit", "environment"}
    assert body["version"]
    assert body["commit"]


def test_version_comes_from_environment(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v0.6.0")
    monkeypatch.setenv("GIT_COMMIT", "0123456789abcdef0123456789abcdef01234567")
    settings = Settings()
    assert settings.app_version == "v0.6.0"
    assert settings.git_commit.startswith("01234567")


def test_version_defaults_for_local_run(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.app_version == "dev"
    assert settings.git_commit == "unknown"
