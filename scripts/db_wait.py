"""Ожидание готовности PostgreSQL из DATABASE_URL (ЛР5).

В CI сервис postgres стартует одновременно с job и первые секунды ещё
инициализирует кластер — тесты и миграции начинаются только после ответа БД.
Ожидание ограничено по времени: если БД так и не ответила, job падает.

    python scripts/db_wait.py [--timeout 60]
"""

from __future__ import annotations

import argparse
import time

from _pg import postgres_url
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    url = postgres_url()
    engine = create_engine(url, poolclass=NullPool)
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print(f"PostgreSQL {url.host}:{url.port or 5432} готов")
            return
        except OperationalError as exc:
            if time.monotonic() > deadline:
                raise SystemExit(
                    f"PostgreSQL не ответил за {args.timeout} с:\n{exc.orig}"
                ) from exc
            time.sleep(1)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
