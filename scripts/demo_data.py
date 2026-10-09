"""Вспомогательный скрипт для показа на защите ЛР3 (не часть приложения).

Работает с БД из DATABASE_URL — той же, что и приложение, миграции, backup.

    python scripts/demo_data.py seed      # добавить двух тестовых депутатов
    python scripts/demo_data.py show      # показать содержимое таблицы deputies
    python scripts/demo_data.py delete    # "повреждение": удалить всех депутатов
    python scripts/demo_data.py spoil     # "повреждение": испортить имена

Вставка не трогает колонку email, поэтому seed работает и на схеме 0003
(до миграции 0004) — это нужно для показа миграции БД, уже содержащей данные.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

# Скрипт запускается как `python scripts/demo_data.py`, поэтому в sys.path попадает
# папка scripts/, а не корень проекта — добавляем корень, чтобы работал `import app`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["seed", "show", "delete", "spoil"])
    args = parser.parse_args()

    engine = create_engine(get_settings().database_url)
    with engine.begin() as conn:
        if args.command == "seed":
            for name in ("Тестовый Депутат", "Второй Депутат"):
                conn.execute(
                    text(
                        "INSERT INTO deputies (full_name, is_active, created_at) "
                        "VALUES (:name, true, now())"
                    ),
                    {"name": name},
                )
        elif args.command == "delete":
            conn.execute(text("DELETE FROM deputies"))
        elif args.command == "spoil":
            conn.execute(text("UPDATE deputies SET full_name = 'ИСПОРЧЕНО'"))

        result = conn.execute(text("SELECT * FROM deputies ORDER BY id"))
        columns = list(result.keys())
        rows = result.fetchall()

    print(f"колонки: {columns}")
    print(f"строк в deputies: {len(rows)}")
    for row in rows:
        print("   ", " | ".join(str(value) for value in row))
    engine.dispose()


if __name__ == "__main__":
    main()