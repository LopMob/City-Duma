#!/usr/bin/env python3
"""Резервное копирование БД приложения (ЛР3, п.11).

Работает с обоими движками, которые использует проект:
  - SQLite (ЛР1, локальная разработка) — делает текстовый SQL-дамп через
    sqlite3.iterdump(), а не бинарную копию файла: дамп безопасен даже при
    открытом файле и человекочитаем (удобно для проверки на защите).
  - PostgreSQL (ЛР2, прод-сервер) — вызывает pg_dump (должен быть
    установлен; на app/db-сервере из ЛР2 это так, т.к. ставился
    postgresql-client вместе с сервером).

Источник строки подключения — переменная окружения DATABASE_URL (как и для
приложения/alembic), либо .env через app.config.get_settings().

Использование:
    python scripts/db_backup.py                 # backups/duma_<ts>.sql
    python scripts/db_backup.py --output PATH    # конкретный путь
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings


def _timestamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%d_%H%M%S")


def backup_sqlite(db_path: Path, output: Path) -> None:
    if not db_path.exists():
        raise SystemExit(f"Файл БД не найден: {db_path}")
    con = sqlite3.connect(db_path)
    try:
        with output.open("w", encoding="utf-8") as fh:
            for line in con.iterdump():
                fh.write(f"{line}\n")
    finally:
        con.close()


def backup_postgres(url, output: Path) -> None:
    if shutil.which("pg_dump") is None:
        raise SystemExit(
            "pg_dump не найден в PATH. На сервере из ЛР2 он ставится вместе с "
            "пакетом postgresql(-client). Установите его или делайте бэкап с "
            "той машины, где он есть."
        )
    cmd = [
        "pg_dump",
        "--no-owner",
        "--no-privileges",
        "-h",
        url.host or "localhost",
        "-p",
        str(url.port or 5432),
        "-U",
        url.username or "postgres",
        "-f",
        str(output),
        url.database,
    ]
    env = {"PGPASSWORD": url.password} if url.password else None
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"pg_dump завершился с ошибкой:\n{result.stderr}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=None, help="Путь к файлу резервной копии"
    )
    args = parser.parse_args()

    database_url = get_settings().database_url
    url = make_url(database_url)

    backups_dir = REPO_ROOT / "backups"
    backups_dir.mkdir(exist_ok=True)
    output = args.output or backups_dir / f"duma_{_timestamp()}.sql"

    if url.get_backend_name() == "sqlite":
        db_path = Path(url.database).resolve()
        backup_sqlite(db_path, output)
    elif url.get_backend_name().startswith("postgresql"):
        backup_postgres(url, output)
    else:
        raise SystemExit(f"Неподдерживаемый тип БД: {url.get_backend_name()}")

    print(f"Резервная копия создана: {output} ({output.stat().st_size} байт)")


if __name__ == "__main__":
    main()
