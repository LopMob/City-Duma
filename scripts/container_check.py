"""make container-check — автоматическая проверка контейнерного окружения (ЛР4).

Два этапа:

1. СТАТИКА (без Docker): правила из scripts/container_policy.py применяются к
   Dockerfile, .dockerignore, compose.yaml, requirements и списку файлов git.
   Запрещены: тег latest, запуск от root, секреты в образе, отключённый
   healthcheck, лишние опубликованные порты (п.13 задания).

2. ДИНАМИКА (нужен запущенный Docker): окружение реально собирается и
   запускается в ОТДЕЛЬНОМ compose-проекте `city-duma-check` (со своим volume
   и случайным паролем — рабочие данные в `city-duma` не затрагиваются).
   Проверяются: сборка и запуск, healthy, миграции, пользователь процесса,
   порт БД не опубликован, лимиты, сохранность данных после пересоздания
   контейнеров и после пересборки образа, корректная остановка по SIGTERM,
   резервная копия и восстановление в новый volume.

Любое нарушение — код возврата 1. Ничего не пропускается молча: если Docker
недоступен, динамический этап считается проваленным.

    python scripts/container_check.py              # статика + динамика
    python scripts/container_check.py --static-only
"""

from __future__ import annotations

import argparse
import json
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import yaml
from _compose import REPO_ROOT, compose_cmd, docker_binary, log, out, run
from container_db import backup, restore, wait_healthy
from container_policy import (
    check_compose,
    check_dockerfile,
    check_dockerignore,
    check_requirements_sync,
    check_secrets_not_tracked,
)

PROJECT = "city-duma-check"
STOP_GRACE_SECONDS = 20


# ----------------------------------------------------------------- статика --


def _git_tracked() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    return result.stdout.splitlines() if result.returncode == 0 else []


def static_checks() -> list[str]:
    problems: list[str] = []
    read = lambda name: (REPO_ROOT / name).read_text(encoding="utf-8")
    for name in (
        "Dockerfile",
        ".dockerignore",
        "compose.yaml",
        "requirements-runtime.txt",
    ):
        if not (REPO_ROOT / name).exists():
            problems.append(f"нет файла {name}")
    if problems:
        return problems
    problems += check_dockerfile(read("Dockerfile"))
    problems += check_dockerignore(read(".dockerignore"))
    problems += check_compose(yaml.safe_load(read("compose.yaml")))
    problems += check_requirements_sync(
        read("requirements.txt"), read("requirements-runtime.txt")
    )
    problems += check_secrets_not_tracked(_git_tracked())
    return problems


# ---------------------------------------------------------------- динамика --


class Env:
    """Изолированное окружение проверки: свой проект, volume, порт и пароль."""

    def __init__(self, tmp: Path) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.env_file = tmp / ".env.check"
        self.env_file.write_text(
            "POSTGRES_USER=duma_check\n"
            f"POSTGRES_PASSWORD={secrets.token_hex(16)}\n"
            "POSTGRES_DB=city_duma_check\n"
            f"APP_PORT={self.port}\n"
            "APP_BIND=127.0.0.1\n"
            "ENVIRONMENT=container-check\n",
            encoding="utf-8",
        )

    def compose(self, *args: str, check: bool = True, quiet: bool = True):
        return run(
            [*compose_cmd(self.env_file, PROJECT), *args], check=check, quiet=quiet
        )

    def container_id(self, service: str, all_states: bool = False) -> str:
        args = ["ps", "-q", service] if not all_states else ["ps", "-a", "-q", service]
        ids = out([*compose_cmd(self.env_file, PROJECT), *args]).split()
        if not ids:
            raise AssertionError(f"контейнер сервиса {service} не найден")
        return ids[0]

    def inspect(self, container: str, template: str) -> str:
        return out([docker_binary(), "inspect", "-f", template, container])

    def wait_all(self) -> None:
        for service in ("db", "app"):
            wait_healthy(service, self.env_file, PROJECT, timeout=180)

    def http(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, None

    def deputy_names(self) -> list[str]:
        status, data = self.http("GET", "/deputies")
        if status != 200:
            raise AssertionError(f"GET /deputies вернул {status}")
        return [item["full_name"] for item in data]


def dynamic_checks() -> list[str]:
    failures: list[str] = []

    def step(title: str, func) -> bool:
        try:
            detail = func()
            log(f"  [OK]   {title}" + (f" — {detail}" if detail else ""))
            return True
        except (AssertionError, SystemExit, Exception) as exc:  # noqa: BLE001
            message = f"{title}: {exc}"
            failures.append(message)
            log(f"  [FAIL] {message}")
            return False

    try:
        docker_binary()
        run([docker_binary(), "info"], quiet=True)
    except SystemExit as exc:
        return [f"Docker недоступен: {exc}"]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        env = Env(tmp)
        marker = f"Проверка-{uuid.uuid4().hex[:8]}"

        def compose_valid():
            env.compose("config", "-q")

        def build_and_start():
            run(
                [*compose_cmd(env.env_file, PROJECT), "up", "-d", "--build"],
                quiet=False,
            )
            env.wait_all()

        def migrations_done():
            cid = env.container_id("migrate", all_states=True)
            state = env.inspect(cid, "{{.State.Status}} {{.State.ExitCode}}")
            assert state == "exited 0", f"миграции завершились так: {state}"
            return "alembic upgrade head выполнен, код 0"

        def process_user():
            uid = env.compose("exec", "-T", "app", "id", "-u").stdout.decode().strip()
            assert uid != "0", "процесс приложения запущен от root"
            status = env.compose(
                "exec", "-T", "app", "cat", "/proc/1/status"
            ).stdout.decode()
            pid1_uid = next(
                line.split()[1]
                for line in status.splitlines()
                if line.startswith("Uid:")
            )
            assert pid1_uid != "0", "PID 1 (uvicorn) работает от root"
            cfg_user = env.inspect(env.container_id("app"), "{{.Config.User}}")
            assert cfg_user not in {"", "0", "root"}, f"USER образа: {cfg_user!r}"
            return f"uid={uid}, PID 1 uid={pid1_uid}"

        def db_not_published():
            ports = env.inspect(
                env.container_id("db"), "{{json .NetworkSettings.Ports}}"
            )
            bindings = [v for v in json.loads(ports).values() if v]
            assert not bindings, f"порт БД опубликован на хост: {ports}"
            result = env.compose("port", "db", "5432", check=False)
            assert (
                result.returncode != 0 or not result.stdout.strip()
            ), "docker compose port db показывает проброс"
            return "порт 5432 наружу не опубликован"

        def resource_limits():
            for service in ("app", "db"):
                cid = env.container_id(service)
                mem = int(env.inspect(cid, "{{.HostConfig.Memory}}"))
                cpus = int(env.inspect(cid, "{{.HostConfig.NanoCpus}}"))
                pids = int(env.inspect(cid, "{{.HostConfig.PidsLimit}}"))
                assert (
                    mem > 0 and cpus > 0 and pids > 0
                ), f"{service}: лимиты не применены"
            return "memory, cpus, pids заданы у app и db"

        def api_works():
            assert env.http("GET", "/health")[0] == 200, "/health не 200"
            status, _ = env.http("POST", "/deputies", {"full_name": marker})
            assert status == 201, f"POST /deputies вернул {status}"
            assert marker in env.deputy_names()

        def survives_recreate():
            env.compose("down")  # без -v: volume остаётся
            run([*compose_cmd(env.env_file, PROJECT), "up", "-d"], quiet=False)
            env.wait_all()
            assert (
                marker in env.deputy_names()
            ), "данные пропали после пересоздания контейнеров"
            return "после down/up запись на месте"

        def survives_image_update():
            before = env.container_id("app")
            run(
                [
                    *compose_cmd(env.env_file, PROJECT),
                    "up",
                    "-d",
                    "--build",
                    "--force-recreate",
                ],
                quiet=False,
            )
            env.wait_all()
            assert env.container_id("app") != before, "контейнер не был пересоздан"
            assert (
                marker in env.deputy_names()
            ), "данные пропали после обновления образа"
            return "после пересборки образа и пересоздания запись на месте"

        def graceful_stop():
            cid = env.container_id("app")
            started = time.monotonic()
            env.compose("stop", "-t", str(STOP_GRACE_SECONDS), "app")
            elapsed = time.monotonic() - started
            code = env.inspect(cid, "{{.State.ExitCode}}")
            logs = env.compose("logs", "--no-color", "app").stdout.decode(
                "utf-8", "replace"
            )
            # 0 - штатный выход uvicorn (он PID 1); 143 = 128 + SIGTERM - тоже штатное
            # завершение по сигналу. 137 = SIGKILL: не успел, остановка некорректна.
            assert code in {
                "0",
                "143",
            }, f"код выхода {code} (137 = убит SIGKILL, остановка некорректна)"
            assert elapsed < STOP_GRACE_SECONDS - 1, f"остановка заняла {elapsed:.1f} с"
            assert (
                "Shutting down" in logs or "Finished server process" in logs
            ), "в логах нет сообщений uvicorn о штатном завершении"
            run([*compose_cmd(env.env_file, PROJECT), "up", "-d"], quiet=False)
            env.wait_all()
            return f"SIGTERM → штатный выход, код {code}, за {elapsed:.1f} с"

        def restore_into_new_volume():
            dump = backup(env.env_file, PROJECT, tmp / "check.sql")
            volume = f"{PROJECT}_pgdata"
            created_before = out(
                [docker_binary(), "volume", "inspect", "-f", "{{.CreatedAt}}", volume]
            )
            restore(dump, env.env_file, PROJECT, new_volume=True)
            env.wait_all()
            created_after = out(
                [docker_binary(), "volume", "inspect", "-f", "{{.CreatedAt}}", volume]
            )
            assert created_before != created_after, "volume не был пересоздан"
            assert marker in env.deputy_names(), "после восстановления записи нет"
            version = (
                env.compose(
                    "exec",
                    "-T",
                    "db",
                    "sh",
                    "-c",
                    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc '
                    '"SELECT version_num FROM alembic_version"',
                )
                .stdout.decode()
                .strip()
            )
            assert (
                version == "0004"
            ), f"версия миграций после восстановления: {version!r}"
            return "данные и версия миграции 0004 вернулись в новый volume"

        try:
            if step(
                "compose.yaml корректен (docker compose config)", compose_valid
            ) and step(
                "сборка образа и запуск окружения, db/app healthy", build_and_start
            ):
                step("миграции выполнены в контейнере", migrations_done)
                step("процесс приложения — не root", process_user)
                step("порт БД не опубликован", db_not_published)
                step("лимиты ресурсов применены", resource_limits)
                if step("приложение отвечает, запись в БД создана", api_works):
                    step(
                        "данные переживают пересоздание контейнеров", survives_recreate
                    )
                    step("данные переживают обновление образа", survives_image_update)
                    step("остановка по SIGTERM корректна", graceful_stop)
                    step(
                        "копия → восстановление в новый volume", restore_into_new_volume
                    )
        finally:
            env.compose("down", "-v", "--remove-orphans", check=False)
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--static-only", action="store_true", help="только проверка файлов, без Docker"
    )
    args = parser.parse_args()

    log("== Статические проверки (Dockerfile, compose.yaml, запреты) ==")
    problems = static_checks()
    for problem in problems:
        log(f"  [FAIL] {problem}")
    if not problems:
        log(
            "  [OK]   запреты latest / root / секреты / healthcheck / лишние порты соблюдены"
        )

    if not args.static_only:
        log("== Динамические проверки (реальный запуск в Docker) ==")
        problems += dynamic_checks()

    if problems:
        log(f"\ncontainer-check НЕ ПРОЙДЕН: нарушений — {len(problems)}")
        sys.exit(1)
    log("\ncontainer-check пройден: контейнерное окружение соответствует требованиям.")


if __name__ == "__main__":
    main()
