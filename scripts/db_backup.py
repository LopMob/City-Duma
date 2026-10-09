"""Резервное копирование БД приложения (ЛР3, п.11).

Делает логический дамп PostgreSQL через pg_dump (обычный SQL-файл, его можно
открыть и прочитать). Дамп содержит DROP ... IF EXISTS перед созданием
объектов (--clean --if-exists), поэтому его можно накатить поверх испорченной
БД, а не только в пустую.

Строка подключения — DATABASE_URL (окружение или .env), как у приложения.

Использование:
    python scripts/db_backup.py                 # backups/duma_<ts>.sql
    python scripts/db_backup.py --output PATH    # конкретный путь
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
from pathlib import Path

from _pg import REPO_ROOT, find_tool, libpq_args, libpq_env, postgres_url


def _timestamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%d_%H%M%S")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=None, help="Путь к файлу резервной копии"
    )
    args = parser.parse_args()

    url = postgres_url()
    backups_dir = REPO_ROOT / "backups"
    backups_dir.mkdir(exist_ok=True)
    output = args.output or backups_dir / f"duma_{_timestamp()}.sql"

    cmd = [
        find_tool("pg_dump"),
        "--no-owner",
        "--no-privileges",
        # --clean --if-exists: дамп сам удаляет существующие объекты перед
        # созданием, иначе восстановление поверх испорченной (но не пустой)
        # БД упирается в "already exists" и оставляет испорченные данные.
        "--clean",
        "--if-exists",
        # Дамп всегда в UTF-8, независимо от кодировки кластера (в русской
        # Windows это часто WIN1251) — файл читается в любом редакторе.
        "--encoding=UTF8",
        *libpq_args(url),
        "-f",
        str(output),
        url.database,
    ]
    result = subprocess.run(
        cmd,
        env=libpq_env(url),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        output.unlink(missing_ok=True)
        raise SystemExit(f"pg_dump завершился с ошибкой:\n{result.stderr}")

    print(f"Резервная копия создана: {output} ({output.stat().st_size} байт)")


if __name__ == "__main__":
    main()
