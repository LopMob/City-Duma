"""Интеграционные тесты миграций (ЛР3, п.8-9): миграции реально выполняются
через alembic CLI (как на защите), каждая — в своей одноразовой БД PostgreSQL
(фикстура scratch_db_url) — проверяются оба сценария, которые спросят на защите:
  - применение на чистой БД (с нуля);
  - применение на БД, уже содержащей данные (накатываем новую ревизию
    поверх существующих записей и убеждаемся, что они не потерялись).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(url, *args) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.fixture()
def scratch_engine(scratch_db_url):
    engine = create_engine(scratch_db_url, poolclass=NullPool)
    yield engine
    engine.dispose()


def test_upgrade_head_on_clean_database(scratch_db_url, scratch_engine):
    """Применение всех миграций на пустой, только что созданной БД."""
    result = _run_alembic(scratch_db_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    insp = inspect(scratch_engine)
    assert {
        "deputies",
        "commissions",
        "commission_memberships",
        "meetings",
        "attendances",
        "alembic_version",
    } <= set(insp.get_table_names())

    with scratch_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == "0004"

    # Новая колонка и уникальное ограничение из последней миграции на месте.
    assert "email" in {c["name"] for c in insp.get_columns("deputies")}
    assert "uq_deputy_email" in {
        u["name"] for u in insp.get_unique_constraints("deputies")
    }


def test_upgrade_preserves_existing_data(scratch_db_url, scratch_engine):
    """Применение новой миграции (0004) на БД, которая уже содержит данные
    (накатанную только до 0003) — старые записи должны сохраниться, новая
    колонка должна появиться как NULL у существующих строк."""
    # Шаг 1: накатываем схему только до 0003 (как будто БД давно в проде).
    result = _run_alembic(scratch_db_url, "upgrade", "0003")
    assert result.returncode == 0, result.stderr

    # Шаг 2: кладём "боевые" данные в уже существующую схему.
    with scratch_engine.begin() as conn:
        deputy_id = conn.execute(
            text(
                "INSERT INTO deputies (full_name, is_active, created_at) "
                "VALUES ('Существующий Депутат', true, now()) RETURNING id"
            )
        ).scalar()

    # Шаг 3: накатываем новую миграцию 0004 поверх уже заполненной БД.
    result = _run_alembic(scratch_db_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    # Шаг 4: старая запись на месте, новая колонка добавилась как NULL.
    with scratch_engine.connect() as conn:
        row = conn.execute(
            text("SELECT full_name, email FROM deputies WHERE id = :id"),
            {"id": deputy_id},
        ).one()
    assert row.full_name == "Существующий Депутат"
    assert row.email is None


def test_unique_email_constraint_is_enforced_after_upgrade(
    scratch_db_url, scratch_engine
):
    """Ограничение uq_deputy_email реально работает на уровне БД: дубль email
    отклоняется, а несколько депутатов без email допустимы (NULL)."""
    assert _run_alembic(scratch_db_url, "upgrade", "head").returncode == 0

    insert = text(
        "INSERT INTO deputies (full_name, is_active, created_at, email) "
        "VALUES (:name, true, now(), :email)"
    )
    with scratch_engine.begin() as conn:
        conn.execute(insert, {"name": "Один", "email": None})
        conn.execute(insert, {"name": "Два", "email": None})
        conn.execute(insert, {"name": "Три", "email": "dup@duma.example"})

    with pytest.raises(IntegrityError), scratch_engine.begin() as conn:
        conn.execute(insert, {"name": "Четыре", "email": "dup@duma.example"})


def test_downgrade_then_upgrade_is_idempotent(scratch_db_url, scratch_engine):
    """Откат последней миграции и повторное применение не должны падать
    и не должны терять данные более ранних таблиц."""
    assert _run_alembic(scratch_db_url, "upgrade", "head").returncode == 0

    with scratch_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO commissions (name, created_at) VALUES ('Комиссия', now())"
            )
        )

    result = _run_alembic(scratch_db_url, "downgrade", "-1")
    assert result.returncode == 0, result.stderr
    assert "email" not in {
        c["name"] for c in inspect(scratch_engine).get_columns("deputies")
    }

    result = _run_alembic(scratch_db_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    with scratch_engine.connect() as conn:
        name = conn.execute(text("SELECT name FROM commissions")).scalar()
    assert name == "Комиссия"
