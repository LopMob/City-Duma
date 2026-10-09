"""Общие помощники для скриптов работы с PostgreSQL (backup / restore / init)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from sqlalchemy.engine import URL, make_url

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings


def postgres_url() -> URL:
    """URL рабочей БД из DATABASE_URL / .env (PostgreSQL — единственная СУБД)."""
    url = make_url(get_settings().database_url)
    if not url.get_backend_name().startswith("postgresql") or not url.database:
        raise SystemExit("DATABASE_URL должен указывать на базу PostgreSQL.")
    return url


def find_tool(name: str) -> str:
    """Путь к клиентской утилите PostgreSQL (pg_dump, psql).

    Порядок поиска: переменная окружения (PG_DUMP / PSQL) -> PATH -> типичная
    папка установки PostgreSQL в Windows (в PATH она по умолчанию не попадает).
    """
    override = os.environ.get(name.upper())
    if override and Path(override).exists():
        return override
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL"
        candidates = sorted(
            base.glob(f"*/bin/{name}.exe"),
            key=lambda p: [int(x) for x in p.parts[-3].split(".") if x.isdigit()],
            reverse=True,
        )
        if candidates:
            return str(candidates[0])
    raise SystemExit(
        f"{name} не найден. Установите клиент PostgreSQL (в Windows он идёт в "
        f"составе установщика сервера) и добавьте папку bin в PATH либо "
        f"задайте переменную окружения {name.upper()} с полным путём к {name}."
    )


def libpq_args(url: URL) -> list[str]:
    return [
        "-h",
        url.host or "localhost",
        "-p",
        str(url.port or 5432),
        "-U",
        url.username or "postgres",
    ]


def libpq_env(url: URL) -> dict[str, str]:
    """Окружение процесса с паролем для утилит libpq (пароль не попадает в argv)."""
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    return env
