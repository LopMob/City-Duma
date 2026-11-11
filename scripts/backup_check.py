"""make backup-check — автоматическая проверка резервного копирования (ЛР5).

Повторяет ручной сценарий защиты ЛР3 «копия -> повреждение -> восстановление»
теми же скриптами, что `make backup` и `make restore`, но в одноразовой БД
(scripts/_scratch.py), поэтому рабочие данные не затрагиваются:

  1. схема строится миграциями (alembic upgrade head);
  2. добавляются контрольные записи;
  3. scripts/db_backup.py создаёт резервную копию;
  4. данные «портятся»: одна запись удаляется, другая изменяется;
  5. scripts/db_restore.py восстанавливает копию;
  6. проверяется, что записи и версия схемы вернулись к состоянию копии.

    python scripts/backup_check.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from _pg import REPO_ROOT
from _scratch import scratch_database, url_string
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool


class CheckFailed(Exception):
    pass


def run(url: URL, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": url_string(url)}
    result = subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        raise CheckFailed(
            f"{' '.join(args)} завершился с кодом {result.returncode}:\n"
            f"{result.stderr}"
        )


def snapshot(engine) -> tuple[list[tuple[int, str]], str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, full_name FROM deputies ORDER BY id")
        ).all()
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return [tuple(row) for row in rows], version


def run_checks(url: URL, workdir: Path) -> None:
    run(url, "-m", "alembic", "upgrade", "head")
    engine = create_engine(url, poolclass=NullPool)
    marker = uuid.uuid4().hex[:8]
    try:
        with engine.begin() as conn:
            for name in (f"Копия-А-{marker}", f"Копия-Б-{marker}"):
                conn.execute(
                    text(
                        "INSERT INTO deputies (full_name, is_active, created_at) "
                        "VALUES (:name, true, now())"
                    ),
                    {"name": name},
                )
        before = snapshot(engine)
        print(f"До копии: {before[0]}, схема {before[1]}")

        dump = workdir / "backup_check.sql"
        run(url, "scripts/db_backup.py", "--output", str(dump))
        if dump.stat().st_size == 0:
            raise CheckFailed("резервная копия пустая")

        first_id, second_id = before[0][0][0], before[0][1][0]
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM deputies WHERE id = :id"), {"id": first_id})
            conn.execute(
                text("UPDATE deputies SET full_name = 'ИСПОРЧЕНО' WHERE id = :id"),
                {"id": second_id},
            )
        damaged = snapshot(engine)
        print(f"После повреждения: {damaged[0]}")
        if damaged == before:
            raise CheckFailed("повреждение данных не удалось смоделировать")

        run(url, "scripts/db_restore.py", str(dump))
        after = snapshot(engine)
        print(f"После восстановления: {after[0]}, схема {after[1]}")
        if after != before:
            raise CheckFailed(
                f"после восстановления данные не совпали с копией: {after} != {before}"
            )
    finally:
        engine.dispose()


def main() -> None:
    try:
        with (
            scratch_database("bkpcheck") as url,
            tempfile.TemporaryDirectory() as tmp,
        ):
            run_checks(url, Path(tmp))
    except CheckFailed as exc:
        print(f"\nbackup-check НЕ ПРОЙДЕН: {exc}", file=sys.stderr)
        sys.exit(1)
    print("\nbackup-check пройден: копия создаётся и восстанавливает данные.")


if __name__ == "__main__":
    main()
