"""Общие помощники для работы с Docker Compose (ЛР4)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT / ".env.compose"


def docker_binary() -> str:
    path = shutil.which("docker")
    if not path:
        raise SystemExit(
            "Не найден docker. Установите Docker Desktop (Windows) или Docker "
            "Engine + плагин compose (Linux) и запустите его."
        )
    return path


def compose_cmd(env_file: Path | None = None, project: str | None = None) -> list[str]:
    """Начало команды `docker compose` с нужным env-файлом и именем проекта."""
    cmd = [docker_binary(), "compose", "--env-file", str(env_file or DEFAULT_ENV_FILE)]
    if project:
        cmd += ["-p", project]
    return cmd


def run(
    cmd: list[str],
    *,
    check: bool = True,
    stdin_path: Path | None = None,
    stdout_path: Path | None = None,
    quiet: bool = False,
) -> subprocess.CompletedProcess:
    """Запуск команды. Вывод бинарный-безопасный: файлы пишутся/читаются как bytes.

    stdout_path/stdin_path нужны для дампов: перенаправление `>` в PowerShell
    портит кодировку SQL-файла, поэтому поток копирует сам Python.
    """
    stdin = stdin_path.open("rb") if stdin_path else None
    stdout = (
        stdout_path.open("wb") if stdout_path else (subprocess.PIPE if quiet else None)
    )
    try:
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE if (quiet or stdout_path or stdin_path) else None,
            check=False,
        )
    finally:
        if stdin:
            stdin.close()
        if stdout_path and stdout:
            stdout.close()
    if check and result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise SystemExit(
            f"Команда завершилась с кодом {result.returncode}: {' '.join(cmd)}\n{err}"
        )
    return result


def out(cmd: list[str]) -> str:
    """Запустить команду и вернуть stdout текстом (ошибка — SystemExit)."""
    result = run(cmd, quiet=True)
    return result.stdout.decode("utf-8", errors="replace").strip()


def log(message: str) -> None:
    print(message, flush=True)
    sys.stdout.flush()
