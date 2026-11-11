"""Одноразовые базы PostgreSQL для make migrate-check и make backup-check (ЛР5).

Обе проверки разрушительны (downgrade base, восстановление поверх данных),
поэтому они никогда не работают с БД из DATABASE_URL. Рядом с ней создаётся
отдельная база `<имя>_<назначение>_<случайный суффикс>_test`, а после
проверки она удаляется — даже если проверка упала. Нужна роль с правом
CREATEDB, как и для тестов (в CI это суперпользователь сервиса postgres).
"""

from __future__ import annotations

import contextlib
import re
import uuid
from collections.abc import Iterator

from _pg import postgres_url
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def _admin_engine(url: URL):
    return create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )


def url_string(url: URL) -> str:
    """Строка подключения для DATABASE_URL дочернего процесса (alembic, backup)."""
    return url.render_as_string(hide_password=False)


@contextlib.contextmanager
def scratch_database(purpose: str) -> Iterator[URL]:
    base = postgres_url()
    name = f"{base.database}_{purpose}_{uuid.uuid4().hex[:8]}_test"
    if not _SAFE_NAME.match(name):
        raise SystemExit(f"Недопустимое имя временной БД: {name!r}")
    admin = _admin_engine(base)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except OperationalError as exc:
        admin.dispose()
        raise SystemExit(
            "Не удалось создать временную БД (PostgreSQL недоступен или у роли "
            f"нет права CREATEDB).\n{exc.orig}"
        ) from exc
    print(f"Временная БД для проверки: {name}")
    try:
        yield base.set(database=name)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
        print(f"Временная БД {name} удалена")
