"""Восстановление БД приложения из резервной копии (ЛР3, п.11-12).

Пара к scripts/db_backup.py — накатывает SQL-дамп через psql.

Дамп сам удаляет и пересоздаёт объекты БД, поэтому всё, что было в базе
после создания копии (удалённые, испорченные, новые строки), заменяется
состоянием из дампа — именно это нужно для сценария «намеренное удаление
или изменение данных, затем восстановление» (п.12 задания).

Восстановление идёт одной транзакцией с остановкой на первой же ошибке: либо
БД полностью вернулась к состоянию из копии, либо не изменилась вовсе.

Использование:
    python scripts/db_restore.py backups/duma_20261003_120000.sql
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from _pg import find_tool, libpq_args, libpq_env, postgres_url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump_path", type=Path, help="Путь к файлу резервной копии")
    args = parser.parse_args()

    if not args.dump_path.exists():
        raise SystemExit(f"Файл резервной копии не найден: {args.dump_path}")

    url = postgres_url()
    print(
        f"Восстанавливаю БД '{url.database}' "
        f"({url.host or 'localhost'}:{url.port or 5432}) из {args.dump_path}"
    )

    cmd = [
        find_tool("psql"),
        *libpq_args(url),
        "-d",
        url.database,
        "-v",
        "ON_ERROR_STOP=1",
        "--single-transaction",
        "--quiet",
        "-f",
        str(args.dump_path),
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
        raise SystemExit(f"psql завершился с ошибкой, БД не изменена:\n{result.stderr}")

    print(f"БД восстановлена из {args.dump_path}")


if __name__ == "__main__":
    main()
