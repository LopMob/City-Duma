#!/usr/bin/env python3
"""Восстановление БД приложения из резервной копии (ЛР3, п.11-12).

Пара к scripts/db_backup.py — читает тот же формат (текстовый SQL-дамп).

Использование:
    python scripts/db_restore.py backups/duma_20261003_120000.sql

ВНИМАНИЕ: для SQLite текущий файл БД пересоздаётся с нуля (все данные,
которых нет в дампе, будут потеряны) — это намеренно, см. п.12 задания
("восстановление после намеренного удаления/изменения").
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings


def restore_sqlite(db_path: Path, dump_path: Path) -> None:
    if db_path.exists():
        # Старый файл в сторону, а не удаляем — на всякий случай оставляем
        # возможность вручную откатиться, если восстановление пойдёт не так.
        backup_of_old = db_path.with_suffix(db_path.suffix + ".before_restore")
        shutil.move(str(db_path), str(backup_of_old))
        print(f"Текущий файл БД сохранён как {backup_of_old}")

    con = sqlite3.connect(db_path)
    try:
        sql = dump_path.read_text(encoding="utf-8")
        con.executescript(sql)
        con.commit()
    finally:
        con.close()


def restore_postgres(url, dump_path: Path) -> None:
    if shutil.which("psql") is None:
        raise SystemExit("psql не найден в PATH — нужен клиент PostgreSQL.")
    cmd = [
        "psql",
        "-h",
        url.host or "localhost",
        "-p",
        str(url.port or 5432),
        "-U",
        url.username or "postgres",
        "-d",
        url.database,
        "-f",
        str(dump_path),
    ]
    env = {"PGPASSWORD": url.password} if url.password else None
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"psql завершился с ошибкой:\n{result.stderr}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump_path", type=Path, help="Путь к файлу резервной копии")
    args = parser.parse_args()

    if not args.dump_path.exists():
        raise SystemExit(f"Файл резервной копии не найден: {args.dump_path}")

    database_url = get_settings().database_url
    url = make_url(database_url)

    if url.get_backend_name() == "sqlite":
        db_path = Path(url.database).resolve()
        restore_sqlite(db_path, args.dump_path)
    elif url.get_backend_name().startswith("postgresql"):
        restore_postgres(url, args.dump_path)
    else:
        raise SystemExit(f"Неподдерживаемый тип БД: {url.get_backend_name()}")

    print(f"БД восстановлена из {args.dump_path}")


if __name__ == "__main__":
    main()
