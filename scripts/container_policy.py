"""Политики контейнерного окружения (ЛР4, п.13): чистые функции без Docker.

Каждая проверка принимает уже прочитанные данные (текст Dockerfile, словарь
compose.yaml, ...) и возвращает список нарушений — пустой список означает
«всё в порядке». Благодаря этому правила тестируются обычными unit-тестами
(tests/unit/test_container_policy.py), а make container-check лишь читает файлы
репозитория и применяет к ним эти функции.

Закреплённые запреты:
  1. тег latest (или отсутствие тега) у любого образа;
  2. запуск от root (в Dockerfile и в compose);
  3. секреты в образе (ENV/ARG с паролем, COPY .env и ключей) и литеральные
     пароли в compose;
  4. отключение healthcheck;
  5. публикация лишних портов (БД — никогда; приложению — только его порт).
"""

from __future__ import annotations

import re
from typing import Any

APP_SERVICE = "app"
DB_SERVICE = "db"
# Единственный порт контейнера, который разрешено публиковать на хост.
ALLOWED_PUBLISHED = {APP_SERVICE: {8000}}
# Служебные (разовые) сервисы: restart "no" и healthcheck не требуются.
ONE_SHOT_RESTART = {"no", None}

_SECRET_NAME = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|CREDENTIAL)", re.IGNORECASE
)
_SECRET_FILES = re.compile(
    r"(^|[\s/\"'])(\.env(\.[\w.-]+)?|[\w.-]*\.pem|[\w.-]*\.key|id_rsa[\w.-]*)"
    r"($|[\s\"'])",
    re.IGNORECASE,
)


def _logical_lines(dockerfile: str) -> list[str]:
    """Строки Dockerfile без комментариев, с объединёнными переносами `\\`."""
    lines: list[str] = []
    buffer = ""
    for raw in dockerfile.splitlines():
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        lines.append((buffer + stripped).strip())
        buffer = ""
    if buffer:
        lines.append(buffer.strip())
    return lines


def _image_problem(image: str, where: str) -> str | None:
    """Нарушение, если у образа нет явной версии или версия latest."""
    if "@sha256:" in image:  # закрепление по digest — самый строгий вариант
        return None
    name = image.rsplit("/", 1)[-1]
    if ":" not in name:
        return f"{where}: образ '{image}' без тега (по умолчанию это latest)"
    tag = name.split(":", 1)[1]
    if tag.lower() == "latest":
        return f"{where}: образ '{image}' использует тег latest"
    return None


def check_dockerfile(dockerfile: str) -> list[str]:
    problems: list[str] = []
    lines = _logical_lines(dockerfile)

    stages: set[str] = set()
    for line in lines:
        match = re.match(
            r"FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?$",
            line,
            re.IGNORECASE,
        )
        if not match:
            continue
        image, alias = match.group(1), match.group(2)
        if image in stages or image.lower() == "scratch" or "$" in image:
            pass  # промежуточный этап сборки / scratch / образ из ARG
        else:
            issue = _image_problem(image, "Dockerfile FROM")
            if issue:
                problems.append(issue)
        if alias:
            stages.add(alias)

    users = [
        line.split(None, 1)[1].strip()
        for line in lines
        if re.match(r"USER\s+\S+", line, re.IGNORECASE)
    ]
    if not users:
        problems.append("Dockerfile: нет инструкции USER — процесс будет от root")
    else:
        final_user = users[-1].split(":", 1)[0].strip().lower()
        if final_user in {"root", "0"}:
            problems.append("Dockerfile: итоговый USER — root")

    healthchecks = [
        line for line in lines if re.match(r"HEALTHCHECK\b", line, re.IGNORECASE)
    ]
    if not healthchecks:
        problems.append("Dockerfile: нет инструкции HEALTHCHECK")
    elif any(
        re.match(r"HEALTHCHECK\s+NONE\b", line, re.IGNORECASE) for line in healthchecks
    ):
        problems.append("Dockerfile: HEALTHCHECK NONE отключает проверку здоровья")

    for line in lines:
        keyword = line.split(None, 1)[0].upper() if line.split() else ""
        if keyword in {"ENV", "ARG"}:
            body = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""
            # ENV A=1 B=2 / ENV A 1 / ARG A=1 / ARG A
            pairs = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)(?:=(\S*))?", body)
            for name, value in pairs:
                if _SECRET_NAME.search(name) and value not in {"", None}:
                    problems.append(
                        f"Dockerfile: секрет в образе — {keyword} {name} со значением"
                    )
                elif keyword == "ARG" and _SECRET_NAME.search(name):
                    problems.append(
                        f"Dockerfile: ARG {name} попадёт в историю образа — "
                        "секреты передаются только при запуске"
                    )
        if keyword in {"COPY", "ADD"} and _SECRET_FILES.search(line):
            problems.append(f"Dockerfile: в образ копируется секретный файл: {line}")
    return problems


def check_dockerignore(text: str) -> list[str]:
    """В .dockerignore обязаны быть исключения для секретов и служебных папок."""
    entries = {line.strip() for line in text.splitlines() if line.strip()}
    required = {
        ".env": "файл с секретами",
        ".git": "история репозитория",
        "backups": "резервные копии БД",
    }
    problems = [
        f".dockerignore: нет записи '{entry}' ({why})"
        for entry, why in required.items()
        if entry not in entries
    ]
    if ".env.*" not in entries:
        problems.append(
            ".dockerignore: нет записи '.env.*' (варианты файла с секретами)"
        )
    return problems


def _published_container_ports(ports: list[Any]) -> list[int]:
    result: list[int] = []
    for item in ports or []:
        if isinstance(item, dict):
            target = item.get("target")
            if target is not None:
                result.append(int(target))
            continue
        # "127.0.0.1:8000:8000", "8000:8000", "8000", "8000/tcp"
        text = str(item).split("/", 1)[0]
        result.append(int(text.rsplit(":", 1)[-1]))
    return result


def check_compose(compose: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    services: dict[str, Any] = compose.get("services") or {}
    networks: dict[str, Any] = compose.get("networks") or {}
    volumes: dict[str, Any] = compose.get("volumes") or {}

    for required in (APP_SERVICE, DB_SERVICE):
        if required not in services:
            problems.append(f"compose: нет сервиса '{required}'")

    for name, svc in services.items():
        svc = svc or {}
        where = f"compose[{name}]"

        image = svc.get("image")
        if image:
            issue = _image_problem(image, where)
            if issue:
                problems.append(issue)
        else:
            problems.append(f"{where}: не задан image с закреплённой версией")

        user = str(svc.get("user", "")).split(":", 1)[0].strip().lower()
        if user in {"root", "0"}:
            problems.append(f"{where}: запуск от root (user: {svc.get('user')})")
        if svc.get("privileged"):
            problems.append(f"{where}: privileged: true")
        if svc.get("network_mode") == "host":
            problems.append(f"{where}: network_mode: host")

        one_shot = str(svc.get("restart", "no")) == "no" and name != DB_SERVICE
        health = svc.get("healthcheck")
        if not one_shot:
            if not health:
                problems.append(f"{where}: нет healthcheck")
            elif health.get("disable") or str(health.get("test", "")).strip() in (
                "NONE",
                "['NONE']",
            ):
                problems.append(f"{where}: healthcheck отключён")
            elif isinstance(health.get("test"), list) and health["test"][:1] == [
                "NONE"
            ]:
                problems.append(f"{where}: healthcheck отключён (test: NONE)")

        for limit in ("mem_limit", "cpus", "pids_limit"):
            if limit not in svc:
                problems.append(f"{where}: не задан лимит ресурсов {limit}")

        published = _published_container_ports(svc.get("ports", []))
        allowed = ALLOWED_PUBLISHED.get(name, set())
        extra = sorted(set(published) - allowed)
        if extra:
            problems.append(f"{where}: опубликованы лишние порты {extra}")
        if svc.get("expose") and name == DB_SERVICE and published:
            problems.append(f"{where}: порты БД опубликованы")

        env = svc.get("environment") or {}
        if isinstance(env, list):
            env = dict(
                item.split("=", 1) if "=" in item else (item, "") for item in env
            )
        for key, value in env.items():
            if _SECRET_NAME.search(str(key)) or str(key) == "DATABASE_URL":
                text = str(value)
                if text and not ("${" in text):
                    problems.append(
                        f"{where}: значение {key} записано литералом — "
                        "используйте ${ПЕРЕМЕННАЯ} из .env.compose"
                    )

    # БД: данные в named volume, сеть только внутренняя.
    db = services.get(DB_SERVICE) or {}
    data_mounts = [
        str(v) for v in db.get("volumes", []) if "/var/lib/postgresql" in str(v)
    ]
    if not data_mounts:
        problems.append("compose[db]: данные PostgreSQL не вынесены в volume")
    for mount in data_mounts:
        source = mount.split(":", 1)[0]
        if source.startswith((".", "/", "~")):
            problems.append("compose[db]: данные в bind-mount, нужен named volume")
        elif source not in volumes:
            problems.append(f"compose: volume '{source}' не объявлен в volumes")

    db_nets = db.get("networks") or []
    if not db_nets:
        problems.append("compose[db]: БД должна быть в явно заданной внутренней сети")
    for net in db_nets if not isinstance(db_nets, dict) else db_nets.keys():
        cfg = networks.get(net) or {}
        if not cfg.get("internal"):
            problems.append(f"compose[db]: сеть '{net}' должна быть internal: true")

    app = services.get(APP_SERVICE) or {}
    depends = app.get("depends_on") or {}
    if not isinstance(depends, dict) or (
        (depends.get(DB_SERVICE) or {}).get("condition") != "service_healthy"
    ):
        problems.append(
            "compose[app]: app должен ждать db с condition: service_healthy"
        )
    return problems


def check_requirements_sync(full: str, runtime: str) -> list[str]:
    """Версии в requirements-runtime.txt совпадают с requirements.txt."""

    def parse(text: str) -> dict[str, str]:
        pins: dict[str, str] = {}
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            name, _, version = line.partition("==")
            pins[name.strip().lower()] = version.strip()
        return pins

    full_pins, runtime_pins = parse(full), parse(runtime)
    problems = []
    for name, version in runtime_pins.items():
        if not version:
            problems.append(f"requirements-runtime.txt: {name} без точной версии (==)")
        elif full_pins.get(name) != version:
            problems.append(
                f"requirements-runtime.txt: {name}=={version}, "
                f"а в requirements.txt {full_pins.get(name)!r}"
            )
    return problems


def check_secrets_not_tracked(tracked_files: list[str]) -> list[str]:
    """Файлы с секретами (.env, .env.compose, ключи) не лежат в git."""
    allowed = {".env.example", ".env.compose.example"}
    problems = []
    for path in tracked_files:
        base = path.rsplit("/", 1)[-1]
        if base in allowed:
            continue
        if (
            base == ".env"
            or base.startswith(".env.")
            or base.endswith((".pem", ".key"))
        ):
            problems.append(f"git: в репозитории лежит файл с секретами {path}")
    return problems
