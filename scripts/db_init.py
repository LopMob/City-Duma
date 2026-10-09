"""Первоначальное создание баз данных PostgreSQL для разработки (ЛР3, п.1).

Создаёт, если их ещё нет:
  - рабочую БД из DATABASE_URL (например, city_duma);
  - тестовую БД <имя>_test (её же создают сами тесты, здесь — для порядка).

Роль из DATABASE_URL должна иметь право CREATEDB (для локальной разработки
подойдёт стандартная роль postgres). Таблицы создаёт `make migrate`.

Использование:
    python scripts/db_init.py
"""

from __future__ import annotations

from _pg import postgres_url
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool


def ensure_database(admin, name: str) -> None:
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
        ).scalar()
        if exists:
            print(f"БД '{name}' уже существует")
        else:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
            print(f"БД '{name}' создана")


def main() -> None:
    url = postgres_url()
    admin = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        for name in (url.database, f"{url.database}_test"):
            ensure_database(admin, name)
    except OperationalError as exc:
        raise SystemExit(
            "Не удалось подключиться к PostgreSQL или нет права CREATEDB.\n"
            f"Проверьте DATABASE_URL в .env и что сервер запущен.\n{exc.orig}"
        ) from exc
    finally:
        admin.dispose()


if __name__ == "__main__":
    main()
