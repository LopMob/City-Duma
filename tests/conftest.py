"""Тестовая конфигурация (ЛР3, п.6).

Тесты работают с настоящим PostgreSQL, но строго в отдельной базе:
  - имя берётся из DATABASE_URL с суффиксом `_test` (city_duma -> city_duma_test)
    либо задаётся явно через TEST_DATABASE_URL; имя обязано оканчиваться на
    `_test`, иначе тесты не стартуют — рабочая БД не может быть затёрта;
  - тестовая БД создаётся автоматически (роль должна иметь право CREATEDB);
  - схема строится миграциями Alembic (`upgrade head`), то есть тесты идут по
    той же схеме, что и в проде, включая ограничения и enum-типы;
  - перед каждым тестом таблицы очищаются (TRUNCATE ... RESTART IDENTITY),
    поэтому тесты не зависят друг от друга и id всегда начинаются с 1.

Если PostgreSQL недоступен, тесты НЕ пропускаются, а падают с понятным
сообщением (пропуск тестов запрещён — см. CONTRIBUTING.md).
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from alembic import command
from app.config import get_settings
from app.database import Base, get_db
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def _test_database_url() -> URL:
    explicit = os.environ.get("TEST_DATABASE_URL")
    url = make_url(explicit or get_settings().database_url)
    if not explicit:
        url = url.set(database=f"{url.database}_test")
    name = url.database or ""
    if not name.endswith("_test") or not _SAFE_NAME.match(name):
        pytest.exit(
            f"Имя тестовой БД '{name}' должно состоять из латиницы/цифр/_ и "
            "оканчиваться на '_test' — тесты не запускаются на рабочей БД.",
            returncode=2,
        )
    return url


def _admin_engine(url: URL):
    """Подключение к служебной БД postgres — для CREATE/DROP DATABASE."""
    return create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )


def create_database(url: URL) -> None:
    admin = _admin_engine(url)
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        admin.dispose()


def drop_database(url: URL) -> None:
    admin = _admin_engine(url)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def test_db_url() -> URL:
    url = _test_database_url()
    try:
        create_database(url)
    except OperationalError as exc:
        pytest.exit(
            "PostgreSQL недоступен или нет прав создавать тестовую БД.\n"
            f"Проверьте DATABASE_URL в .env и что сервер запущен.\n{exc.orig}",
            returncode=2,
        )
    return url


@pytest.fixture(scope="session")
def pg_engine(test_db_url):
    """Тестовая БД со схемой, собранной миграциями Alembic."""
    engine = create_engine(test_db_url, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["database_url"] = test_db_url.render_as_string(hide_password=False)
    command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


_TABLES = ", ".join(t.name for t in Base.metadata.sorted_tables)


def _truncate(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {_TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture()
def db_session(pg_engine):
    """Чистая сессия SQLAlchemy для unit-тестов app/crud.py напрямую, без
    FastAPI/HTTP — каждый тест начинает с пустых таблиц."""
    _truncate(pg_engine)
    session: Session = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(pg_engine):
    """FastAPI TestClient, подключённый к тестовой БД (HTTP + БД целиком)."""
    _truncate(pg_engine)
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=pg_engine
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def scratch_db_url(test_db_url):
    """Одноразовая пустая БД для проверки миграций с нуля; удаляется после теста."""
    base = (test_db_url.database or "")[: -len("_test")]
    url = test_db_url.set(database=f"{base}_mig_{uuid.uuid4().hex[:8]}_test")
    create_database(url)
    try:
        yield url
    finally:
        drop_database(url)
