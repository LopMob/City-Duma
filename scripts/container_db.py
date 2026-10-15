"""Резервное копирование и восстановление БД в контейнерной среде (ЛР4, п.10).

Работает с контейнером `db` из compose.yaml: pg_dump/psql запускаются ВНУТРИ
контейнера (`docker compose exec`), поэтому на хосте не нужны клиентские утилиты
PostgreSQL, а пароль БД не попадает в командную строку — контейнер берёт его из
собственных переменных окружения.

    python scripts/container_db.py backup [--output FILE]
    python scripts/container_db.py restore FILE              # в работающую БД
    python scripts/container_db.py restore FILE --new-volume # в НОВЫЙ volume

--new-volume: контейнеры и volume с данными удаляются (down -v), поднимается
чистая БД на новом volume, в неё накатывается копия, затем запускается всё
окружение. Именно этот сценарий — «восстановление БД в новый volume».
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

from _compose import DEFAULT_ENV_FILE, REPO_ROOT, compose_cmd, log, out, run

BACKUP_DIR = REPO_ROOT / "backups"
DUMP_MARKER = b"PostgreSQL database dump"

# Команды выполняются внутри контейнера db: ${...} раскрывает sh контейнера.
DUMP_SH = (
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
    "--no-owner --no-privileges --clean --if-exists --encoding=UTF8"
)
RESTORE_SH = (
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
    "-v ON_ERROR_STOP=1 --single-transaction --quiet"
)


def wait_healthy(
    service: str, env_file: Path, project: str | None, timeout: int = 120
) -> None:
    """Ждёт статуса healthy у сервиса (по встроенному healthcheck)."""
    deadline = time.monotonic() + timeout
    last = "unknown"
    while time.monotonic() < deadline:
        ids = out([*compose_cmd(env_file, project), "ps", "-q", service]).split()
        if ids:
            last = out(
                [
                    compose_cmd()[0],
                    "inspect",
                    "-f",
                    "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                    ids[0],
                ]
            )
            if last == "healthy":
                return
            if last in {"exited", "dead"}:
                break
        time.sleep(2)
    raise SystemExit(
        f"Сервис {service} не стал healthy за {timeout} с (статус: {last})"
    )


def backup(
    env_file: Path = DEFAULT_ENV_FILE,
    project: str | None = None,
    output: Path | None = None,
) -> Path:
    if output is None:
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d_%H%M%S")
        output = BACKUP_DIR / f"container_duma_{stamp}.sql"
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [*compose_cmd(env_file, project), "exec", "-T", "db", "sh", "-c", DUMP_SH]
    result = run(cmd, check=False, stdout_path=output)
    if result.returncode != 0:
        output.unlink(missing_ok=True)
        err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise SystemExit(f"pg_dump завершился с ошибкой ({result.returncode}): {err}")
    if DUMP_MARKER not in output.read_bytes()[:2000]:
        output.unlink(missing_ok=True)
        raise SystemExit("Резервная копия пуста или повреждена — файл удалён.")
    log(f"Резервная копия создана: {output} ({output.stat().st_size} байт)")
    return output


def restore(
    dump: Path,
    env_file: Path = DEFAULT_ENV_FILE,
    project: str | None = None,
    new_volume: bool = False,
) -> None:
    if not dump.exists():
        raise SystemExit(f"Файл резервной копии не найден: {dump}")
    base = compose_cmd(env_file, project)
    if new_volume:
        log("Удаляю контейнеры и volume с данными (down -v) ...")
        run([*base, "down", "-v", "--remove-orphans"])
        log("Поднимаю БД на новом пустом volume ...")
        run([*base, "up", "-d", "db"])
    wait_healthy("db", env_file, project)
    log(f"Восстанавливаю БД из {dump} ...")
    cmd = [*base, "exec", "-T", "db", "sh", "-c", RESTORE_SH]
    result = run(cmd, check=False, stdin_path=dump, stdout_path=None, quiet=True)
    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise SystemExit(
            f"Восстановление не выполнено (psql код {result.returncode}), БД не изменена:\n{err}"
        )
    if new_volume:
        log("Запускаю приложение (миграции пройдут как no-op) ...")
        run([*base, "up", "-d"])
    log("БД восстановлена.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--project", default=None, help="имя compose-проекта")
    sub = parser.add_subparsers(dest="command", required=True)
    p_backup = sub.add_parser("backup", help="сделать резервную копию")
    p_backup.add_argument("--output", type=Path, default=None)
    p_restore = sub.add_parser("restore", help="восстановить из копии")
    p_restore.add_argument("dump_path", type=Path)
    p_restore.add_argument("--new-volume", action="store_true")
    args = parser.parse_args()

    if not args.env_file.exists():
        raise SystemExit("Нет .env.compose — выполните make container-init")
    if args.command == "backup":
        backup(args.env_file, args.project, args.output)
    else:
        restore(args.dump_path, args.env_file, args.project, args.new_volume)


if __name__ == "__main__":
    sys.exit(main())
