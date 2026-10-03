"""Интеграционные тесты миграций (ЛР3, п.8-9): миграции реально выполняются
через alembic CLI (как на защите), против временного файла SQLite —
проверяются оба сценария, которые спросят на защите:
  - применение на чистой БД (с нуля);
  - применение на БД, уже содержащей данные (накатываем новую ревизию
    поверх существующих записей и убеждаемся, что они не потерялись).
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(*args, db_path: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.fixture()
def tmp_db_path(tmp_path):
    return tmp_path / "migration_test.db"


def test_upgrade_head_on_clean_database(tmp_db_path):
    """Применение всех миграций на пустой, только что созданной БД."""
    result = _run_alembic("upgrade", "head", db_path=tmp_db_path)
    assert result.returncode == 0, result.stderr

    con = sqlite3.connect(tmp_db_path)
    tables = {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "deputies",
        "commissions",
        "commission_memberships",
        "meetings",
        "attendances",
        "alembic_version",
    } <= tables

    version = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert version == "0004"

    # Новая колонка из последней миграции должна присутствовать.
    columns = {row[1] for row in con.execute("PRAGMA table_info(deputies)")}
    assert "email" in columns
    con.close()


def test_upgrade_preserves_existing_data(tmp_db_path):
    """Применение новой миграции (0004) на БД, которая уже содержит данные
    (накатанную только до 0003) — старые записи должны сохраниться, новая
    колонка должна появиться как NULL у существующих строк."""
    # Шаг 1: накатываем схему только до 0003 (как будто БД давно в проде).
    result = _run_alembic("upgrade", "0003", db_path=tmp_db_path)
    assert result.returncode == 0, result.stderr

    # Шаг 2: вручную кладём "боевые" данные в уже существующую схему.
    con = sqlite3.connect(tmp_db_path)
    con.execute(
        "INSERT INTO deputies (full_name, is_active, created_at) "
        "VALUES ('Существующий Депутат', 1, '2026-09-01 10:00:00')"
    )
    con.commit()
    deputy_id = con.execute(
        "SELECT id FROM deputies WHERE full_name='Существующий Депутат'"
    ).fetchone()[0]
    con.close()

    # Шаг 3: накатываем новую миграцию 0004 поверх уже заполненной БД.
    result = _run_alembic("upgrade", "head", db_path=tmp_db_path)
    assert result.returncode == 0, result.stderr

    # Шаг 4: старая запись на месте, новая колонка добавилась как NULL.
    con = sqlite3.connect(tmp_db_path)
    row = con.execute(
        "SELECT full_name, email FROM deputies WHERE id=?", (deputy_id,)
    ).fetchone()
    assert row is not None
    assert row[0] == "Существующий Депутат"
    assert row[1] is None
    con.close()


def test_downgrade_then_upgrade_is_idempotent(tmp_db_path):
    """Откат последней миграции и повторное применение не должны падать
    и не должны терять данные более ранних таблиц."""
    result = _run_alembic("upgrade", "head", db_path=tmp_db_path)
    assert result.returncode == 0, result.stderr

    con = sqlite3.connect(tmp_db_path)
    con.execute(
        "INSERT INTO commissions (name, created_at) VALUES ('Комиссия', '2026-09-01')"
    )
    con.commit()
    con.close()

    result = _run_alembic("downgrade", "-1", db_path=tmp_db_path)
    assert result.returncode == 0, result.stderr

    result = _run_alembic("upgrade", "head", db_path=tmp_db_path)
    assert result.returncode == 0, result.stderr

    con = sqlite3.connect(tmp_db_path)
    name = con.execute("SELECT name FROM commissions").fetchone()[0]
    assert name == "Комиссия"
    con.close()
