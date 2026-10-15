"""Тесты правил контейнерного окружения (ЛР4, п.13).

Реальные файлы репозитория обязаны проходить все правила, а каждое нарушение
из списка запретов (latest, root, секреты в образе, отключённый healthcheck,
лишние порты и др.) обязано обнаруживаться — это проверка самих проверок.
Docker для этих тестов не нужен.
"""

import copy
from pathlib import Path

import pytest
import yaml

from scripts import container_policy as policy

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def test_repository_files_pass_all_policies():
    full = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    runtime = (ROOT / "requirements-runtime.txt").read_text(encoding="utf-8")
    assert policy.check_dockerfile(DOCKERFILE) == []
    assert policy.check_dockerignore(DOCKERIGNORE) == []
    assert policy.check_compose(COMPOSE) == []
    assert policy.check_requirements_sync(full, runtime) == []


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("FROM python:3.11.13-slim-bookworm", "FROM python:latest", "latest"),
        ("FROM python:3.11.13-slim-bookworm", "FROM python", "без тега"),
        ("USER 10001:10001\n", "", "нет инструкции USER"),
        ("USER 10001:10001", "USER root", "root"),
        ("USER 10001:10001", "USER 0", "root"),
        ("HEALTHCHECK --interval", "#HEALTHCHECK --interval", "HEALTHCHECK"),
        ("ENV PYTHONDONTWRITEBYTECODE=1", "ENV DB_PASSWORD=hunter2", "секрет"),
        ("ENV PYTHONDONTWRITEBYTECODE=1", "ARG API_TOKEN", "ARG"),
        ("COPY app ./app", "COPY .env ./", "секретный файл"),
    ],
)
def test_dockerfile_violations_are_detected(old, new, expected):
    assert old in DOCKERFILE
    problems = policy.check_dockerfile(DOCKERFILE.replace(old, new, 1))
    assert any(expected in problem for problem in problems), problems


def test_dockerfile_healthcheck_none_is_detected():
    broken = DOCKERFILE + "\nHEALTHCHECK NONE\n"
    assert any("NONE" in p for p in policy.check_dockerfile(broken))


def test_multistage_build_stage_reference_is_not_treated_as_image():
    text = (
        "FROM python:3.11.13-slim AS build\nFROM build\n"
        "USER 1000\nHEALTHCHECK CMD true\n"
    )
    assert policy.check_dockerfile(text) == []


def _compose(mutate):
    data = copy.deepcopy(COMPOSE)
    mutate(data)
    return policy.check_compose(data)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d["services"]["db"].update(image="postgres:latest"), "latest"),
        (lambda d: d["services"]["db"].update(image="postgres"), "без тега"),
        (lambda d: d["services"]["app"].update(user="root"), "root"),
        (lambda d: d["services"]["app"].update(user="0:0"), "root"),
        (lambda d: d["services"]["db"].update(ports=["5432:5432"]), "лишние порты"),
        (
            lambda d: d["services"]["app"].update(ports=["8000:8000", "9000:9000"]),
            "лишние порты",
        ),
        (
            lambda d: d["services"]["app"].update(healthcheck={"disable": True}),
            "healthcheck отключён",
        ),
        (
            lambda d: d["services"]["db"].update(healthcheck={"test": ["NONE"]}),
            "healthcheck отключён",
        ),
        (lambda d: d["services"]["app"].pop("healthcheck"), "нет healthcheck"),
        (lambda d: d["services"]["db"].pop("mem_limit"), "mem_limit"),
        (lambda d: d["services"]["app"].pop("cpus"), "cpus"),
        (lambda d: d["services"]["app"].update(privileged=True), "privileged"),
        (lambda d: d["services"]["app"].update(network_mode="host"), "host"),
        (
            lambda d: d["services"]["db"]["environment"].update(
                POSTGRES_PASSWORD="hunter2"
            ),
            "литералом",
        ),
        (
            lambda d: d["services"]["app"]["environment"].update(
                DATABASE_URL="postgresql://u:p@db/x"
            ),
            "литералом",
        ),
        (lambda d: d["networks"]["backend"].update(internal=False), "internal"),
        (
            lambda d: d["services"]["db"].update(
                volumes=["./data:/var/lib/postgresql/data"]
            ),
            "bind-mount",
        ),
        (lambda d: d["services"]["db"].pop("volumes"), "volume"),
        (
            lambda d: d["services"]["app"].update(depends_on={"db": {}}),
            "service_healthy",
        ),
    ],
)
def test_compose_violations_are_detected(mutate, expected):
    problems = _compose(mutate)
    assert any(expected in problem for problem in problems), problems


def test_one_shot_migrate_service_does_not_need_healthcheck():
    assert "healthcheck" not in COMPOSE["services"]["migrate"]
    assert policy.check_compose(COMPOSE) == []


def test_dockerignore_must_exclude_secrets_and_git():
    broken = DOCKERIGNORE.replace(".env\n", "", 1)
    problems = policy.check_dockerignore(broken)
    assert any("'.env'" in p for p in problems)
    assert any(
        "'.git'" in p for p in policy.check_dockerignore("backups\n.env\n.env.*\n")
    )


def test_requirements_must_stay_in_sync():
    full = "fastapi==1.0.0\nuvicorn[standard]==2.0.0\n"
    assert policy.check_requirements_sync(full, "fastapi==1.0.0\n") == []
    assert policy.check_requirements_sync(full, "fastapi==1.0.1\n")
    assert policy.check_requirements_sync(full, "fastapi\n")
    assert policy.check_requirements_sync(full, "unknown==1\n")


def test_secret_files_must_not_be_tracked_in_git():
    ok = [".env.example", ".env.compose.example", "app/main.py"]
    assert policy.check_secrets_not_tracked(ok) == []
    bad = policy.check_secrets_not_tracked([".env", ".env.compose", "keys/server.pem"])
    assert len(bad) == 3
