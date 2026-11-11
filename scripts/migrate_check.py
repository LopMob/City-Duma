"""make migrate-check — автоматическая проверка миграций Alembic (ЛР5).

Все шаги выполняются тем же alembic CLI, что и `make migrate`, но в
одноразовой БД (scripts/_scratch.py) — рабочая БД не затрагивается:

  1. у миграций ровно одна голова (нет «развилки» после параллельных веток);
  2. upgrade head на чистой БД, затем current == head;
  3. downgrade -1, в схему предыдущей версии добавляется запись, upgrade head —
     последняя миграция применяется к БД, уже содержащей данные, и не теряет их;
  4. downgrade base -> upgrade head — все миграции обратимы и повторяемы.

Любая ошибка — код возврата 1 с выводом alembic (в CI pipeline останавливается).

    python scripts/migrate_check.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

from _pg import REPO_ROOT
from _scratch import scratch_database, url_string
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool


class CheckFailed(Exception):
    pass


def alembic(url: URL, *args: str) -> str:
    env = {**os.environ, "DATABASE_URL": url_string(url)}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise CheckFailed(
            f"alembic {' '.join(args)} завершился с кодом {result.returncode}:\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout


def revisions(output: str) -> list[str]:
    """Ревизии из вывода `alembic heads` / `alembic current` ('0004 (head)')."""
    found = []
    for line in output.splitlines():
        parts = line.split()
        if parts and not line.startswith(("INFO", "WARNING", "ERROR")):
            found.append(parts[0])
    return found


def step(title: str) -> None:
    print(f"\n== {title}", flush=True)


def run_checks(url: URL) -> None:
    step("1. Одна голова миграций")
    heads = revisions(alembic(url, "heads"))
    if len(heads) != 1:
        raise CheckFailed(f"ожидалась одна голова миграций, найдено: {heads}")
    head = heads[0]
    print(f"   head = {head}")

    step("2. upgrade head на чистой БД")
    alembic(url, "upgrade", "head")
    current = revisions(alembic(url, "current"))
    if current != [head]:
        raise CheckFailed(f"после upgrade head current = {current}, ожидалось {head}")
    print(f"   current = {head}")

    step("3. Последняя миграция на БД с данными")
    engine = create_engine(url, poolclass=NullPool)
    marker = f"migrate-check-{uuid.uuid4().hex[:8]}"
    try:
        alembic(url, "downgrade", "-1")
        if "deputies" in inspect(engine).get_table_names():
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO deputies (full_name, is_active, created_at) "
                        "VALUES (:name, true, now())"
                    ),
                    {"name": marker},
                )
            alembic(url, "upgrade", "head")
            with engine.connect() as conn:
                count = conn.execute(
                    text("SELECT count(*) FROM deputies WHERE full_name = :name"),
                    {"name": marker},
                ).scalar()
            if count != 1:
                raise CheckFailed("данные потеряны после upgrade head")
            print("   запись, созданная до последней миграции, сохранилась")
        else:
            alembic(url, "upgrade", "head")
            print("   таблицы deputies до последней миграции нет — шаг пропущен")
    finally:
        engine.dispose()

    step("4. downgrade base -> upgrade head")
    alembic(url, "downgrade", "base")
    if revisions(alembic(url, "current")):
        raise CheckFailed("после downgrade base в alembic_version осталась ревизия")
    alembic(url, "upgrade", "head")
    if revisions(alembic(url, "current")) != [head]:
        raise CheckFailed("повторный upgrade head не дошёл до head")
    print("   все миграции откатываются и накатываются повторно")


def main() -> None:
    try:
        with scratch_database("migcheck") as url:
            run_checks(url)
    except CheckFailed as exc:
        print(f"\nmigrate-check НЕ ПРОЙДЕН: {exc}", file=sys.stderr)
        sys.exit(1)
    print("\nmigrate-check пройден.")


if __name__ == "__main__":
    main()
