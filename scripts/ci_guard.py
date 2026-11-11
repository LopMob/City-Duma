"""make guard — защита критических файлов приёмки (ЛР5, п.13–15).

Что считается критическим, решает .gitlab/CODEOWNERS ЦЕЛЕВОЙ ветки (main),
а не ветки автора. Скрипт сравнивает текущее дерево с базой (merge-base с
main) и сообщает о двух видах находок:

  * КРИТИЧЕСКИЕ ИЗМЕНЕНИЯ — какие защищённые файлы затронуты и кто их владелец;
  * ОСЛАБЛЕНИЕ ПРОВЕРОК — то, что запрещено CONTRIBUTING.md без обоснования:
    удалённые тесты и тесты с меньшим числом проверок, новые skip/xfail,
    снижение --cov-fail-under, новые исключения bandit/ruff/coverage,
    новые # noqa / # nosec, удалённые команды make-целей и jobs pipeline,
    allow_failure / when: manual / `|| true` у обязательных проверок,
    пропавшие обязательные jobs.

Режимы:
  make guard                  локально перед push: ослабление — код 1,
                              критические изменения — предупреждение.
  make guard GUARD_ARGS=--ci  в GitLab CI (job guard:critical-files).
      В merge request любая находка требует одобрения владельца из CODEOWNERS,
      который не автор MR, данного ПОСЛЕ последнего push. Одобрения читаются
      через API токеном GUARD_API_TOKEN (scope read_api). Нет токена или API
      недоступно — job падает (fail closed). Job CI запускает копию этого
      скрипта из ЦЕЛЕВОЙ ветки (git show origin/main:scripts/ci_guard.py),
      а не из ветки автора: MR не может «подкрутить» проверку под себя —
      правка guard вступает в силу только после слияния.

Скрипт не импортирует код приложения: нужны только стандартная библиотека,
git и PyYAML.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import io
import json
import os
import re
import subprocess
import sys
import tokenize
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import tomllib
import yaml

SELF_PATH = "scripts/ci_guard.py"
CODEOWNERS_LOCATIONS = (".gitlab/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
CI_FILE = ".gitlab-ci.yml"
ZERO_SHA = "0" * 40

# Обязательные jobs pipeline: каждый обязан существовать, падать при ошибке
# (без allow_failure) и запускаться автоматически (без when: manual / never).
REQUIRED_CI_JOBS = (
    "quality",
    "test",
    "migrations",
    "backup",
    "sast",
    "deps-audit",
    "secrets",
    "build:image",
    "container-check",
    "image-scan",
    "guard:critical-files",
    "publish:image",
)
PUBLISH_JOB = "publish:image"

# make-цели, из которых нельзя молча убирать команды и зависимости.
GUARDED_MAKE_TARGETS = (
    "verify",
    "quality",
    "sast",
    "test",
    "migrate",
    "migrate-check",
    "backup-check",
    "deps-audit",
    "secrets",
    "container-check",
    "guard",
)

CI_RESERVED_KEYS = {
    "workflow",
    "stages",
    "variables",
    "default",
    "include",
    "image",
    "services",
    "cache",
    "before_script",
    "after_script",
}

SKIP_MARKERS = re.compile(
    r"pytest\.mark\.(skip|skipif|xfail)\b"
    r"|pytest\.(skip|xfail|importorskip)\s*\("
    r"|unittest\.skip"
    r"|\bpytest_collection_modifyitems\b"
    r"|\bpytest_ignore_collect\b"
)
SUPPRESSIONS = re.compile(
    r"#\s*(noqa|nosec|type:\s*ignore|pragma:\s*no\s*cover|fmt:\s*off)",
    re.IGNORECASE,
)
IGNORE_ERRORS = re.compile(r"\|\|\s*(true|:|exit\s+0)\b|;\s*true\s*$|^\s*-\S")
PYTEST_FORBIDDEN_OPTS = (
    "--deselect",
    "--ignore",
    "--ignore-glob",
    "-k",
    "-m",
    "--co",
    "--collect-only",
    "--lf",
    "--last-failed",
    "--sw",
    "--stepwise",
    "-p",
)
COV_FAIL_UNDER = re.compile(r"--cov-fail-under[= ](\d+(?:\.\d+)?)")
ROLE_LEVELS = {"developer": 30, "maintainer": 40, "owner": 50}
CHECKED_CODE_DIRS = ("app/", "scripts/", "tests/", "alembic/")


# ------------------------------------------------------------- CODEOWNERS --


@dataclass
class Rule:
    pattern: str
    owners: list[str]
    section: str = ""


def parse_codeowners(text: str) -> list[Rule]:
    rules: list[Rule] = []
    section, section_owners = "", []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        header = re.match(r"^\^?\[([^\]]+)\](?:\[\d+\])?\s*(.*)$", line)
        if header:
            section = header.group(1).strip()
            section_owners = header.group(2).split()
            continue
        parts = re.split(r"(?<!\\)\s+", line)
        pattern = parts[0].replace("\\ ", " ")
        owners = [p for p in parts[1:] if "@" in p] or list(section_owners)
        rules.append(Rule(pattern, owners, section))
    return rules


def pattern_matches(pattern: str, path: str) -> bool:
    """Соответствие пути шаблону CODEOWNERS (упрощённая семантика .gitignore)."""
    if pattern in {"*", "/*", "/**", "**"}:
        return True
    dir_only = pattern.endswith("/")
    anchored = pattern.startswith("/")
    body = pattern.strip("/")
    if not body:
        return True
    parts = path.split("/")
    if anchored or "/" in body:
        candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    else:
        candidates = parts
    last = len(candidates) - 1
    for index, candidate in enumerate(candidates):
        if dir_only and index == last:
            continue
        if fnmatch.fnmatchcase(candidate, body):
            return True
    return False


def owners_for(path: str, rules: list[Rule]) -> list[str] | None:
    """Владельцы файла: в каждой секции побеждает последнее совпавшее правило."""
    per_section: dict[str, list[str]] = {}
    for rule in rules:
        if pattern_matches(rule.pattern, path):
            per_section[rule.section] = rule.owners
    if not per_section:
        return None
    owners: list[str] = []
    for section_owners in per_section.values():
        owners += [o for o in section_owners if o not in owners]
    return owners


# ------------------------------------------------------------------ тесты --


# В Python 3.12+ содержимое f-строк — отдельные токены FSTRING_MIDDLE.
_STRING_TOKENS = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}


def split_python(source: str) -> tuple[str, list[str]]:
    """(код без строк и комментариев, список комментариев).

    Маркеры skip и подавления анализаторов ищутся только в настоящем коде и
    комментариях — упоминание «# noqa» в строке или docstring не считается.
    """
    code: list[str] = []
    comments: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                comments.append(token.string)
            elif token.type not in _STRING_TOKENS:
                code.append(token.string)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return source, source.splitlines()
    return re.sub(r"\s*\.\s*", ".", " ".join(code)), comments


def _check_count(node: ast.AST) -> int:
    count = 0
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assert):
            count += 1
        elif isinstance(sub, ast.Call):
            func = sub.func
            name = func.attr if isinstance(func, ast.Attribute) else ""
            if name in {"raises", "warns"} or name.startswith("assert_"):
                count += 1
    return count


def collect_tests(sources: dict[str, str]) -> dict[str, int]:
    """{'tests/x.py::test_a': число проверок} для функций и методов test*."""
    tests: dict[str, int] = {}
    for path, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tests[f"{path}::<синтаксическая ошибка>"] = -1
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test"):
                    tests[f"{path}::{node.name}"] = _check_count(node)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                for item in node.body:
                    if isinstance(
                        item, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ) and item.name.startswith("test"):
                        key = f"{path}::{node.name}.{item.name}"
                        tests[key] = _check_count(item)
    return tests


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        path.startswith("tests/")
        and name.endswith(".py")
        and (name.startswith("test_") or name.endswith("_test.py"))
    )


def test_findings(
    base_sources: dict[str, str], head_sources: dict[str, str]
) -> list[str]:
    findings: list[str] = []
    base = collect_tests({p: s for p, s in base_sources.items() if is_test_file(p)})
    head = collect_tests({p: s for p, s in head_sources.items() if is_test_file(p)})
    head_names = {key.split("::", 1)[1] for key in head}

    for key in sorted(base):
        name = key.split("::", 1)[1]
        if key not in head and name not in head_names:
            findings.append(f"удалён тест {key}")
    for key in sorted(head):
        if head[key] == -1:
            findings.append(f"файл тестов не разбирается: {key.split('::')[0]}")
        elif key in base and head[key] < base[key]:
            findings.append(
                f"в тесте {key} меньше проверок: {base[key]} -> {head[key]}"
            )
    if len(head) < len(base):
        findings.append(f"число тестов уменьшилось: {len(base)} -> {len(head)}")

    def markers(sources: dict[str, str]) -> int:
        total = 0
        for path, text in sources.items():
            if path.startswith("tests/") and path.endswith(".py"):
                code, _ = split_python(text)
                total += len(SKIP_MARKERS.findall(code))
        return total

    before, after = markers(base_sources), markers(head_sources)
    if after > before:
        findings.append(
            f"добавлены пропуски тестов (skip/xfail/deselect): {before} -> {after}"
        )
    return findings


def suppression_findings(
    base_sources: dict[str, str], head_sources: dict[str, str]
) -> list[str]:
    def count(sources: dict[str, str]) -> int:
        total = 0
        for path, text in sources.items():
            if path.endswith(".py") and path.startswith(CHECKED_CODE_DIRS):
                _, comments = split_python(text)
                total += sum(len(SUPPRESSIONS.findall(c)) for c in comments)
        return total

    before, after = count(base_sources), count(head_sources)
    if after > before:
        return [
            (
                "добавлены подавления анализаторов (# noqa, # nosec, # type: ignore, "
                f"# pragma: no cover): {before} -> {after} — нужно обоснование в MR"
            )
        ]
    return []


# -------------------------------------------------------------- pyproject --


def _dig(data: dict, dotted: str):
    node = data
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _as_set(value) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {f"{k}={v}" for k, v in value.items()}
    return {str(item) for item in value}


def _addopts_tokens(data: dict) -> list[str]:
    value = _dig(data, "tool.pytest.ini_options.addopts") or ""
    if isinstance(value, list):
        value = " ".join(value)
    return value.split()


def _fail_under(data: dict) -> float | None:
    match = COV_FAIL_UNDER.search(" ".join(_addopts_tokens(data)))
    values = [float(match.group(1))] if match else []
    report = _dig(data, "tool.coverage.report.fail_under")
    if report is not None:
        values.append(float(report))
    return max(values) if values else None


# Списки, рост которых означает новые исключения из проверок.
_GROWING_LISTS = {
    "tool.bandit.skips": "новые пропуски правил bandit",
    "tool.bandit.exclude_dirs": "bandit исключает новые каталоги",
    "tool.coverage.run.omit": "файлы исключены из покрытия",
    "tool.coverage.report.exclude_lines": "строки исключены из покрытия",
    "tool.coverage.report.exclude_also": "строки исключены из покрытия",
    "tool.ruff.ignore": "ruff игнорирует новые правила",
    "tool.ruff.extend-ignore": "ruff игнорирует новые правила",
    "tool.ruff.lint.ignore": "ruff игнорирует новые правила",
    "tool.ruff.lint.extend-ignore": "ruff игнорирует новые правила",
    "tool.ruff.lint.per-file-ignores": "ruff игнорирует правила в файлах",
    "tool.ruff.per-file-ignores": "ruff игнорирует правила в файлах",
    "tool.ruff.exclude": "ruff исключает файлы",
    "tool.ruff.extend-exclude": "ruff исключает файлы",
    "tool.black.exclude": "black исключает файлы",
    "tool.black.extend-exclude": "black исключает файлы",
    "tool.black.force-exclude": "black исключает файлы",
    "tool.pytest.ini_options.filterwarnings": "подавлены предупреждения pytest",
}
# Списки, сокращение которых означает, что проверяется меньше.
_SHRINKING_LISTS = {
    "tool.ruff.select": "ruff проверяет меньше правил",
    "tool.ruff.lint.select": "ruff проверяет меньше правил",
    "tool.ruff.lint.extend-select": "ruff проверяет меньше правил",
    "tool.coverage.run.source": "покрытие измеряется по меньшему объёму кода",
    "tool.pytest.ini_options.testpaths": "pytest собирает тесты из меньшего числа мест",
}


def pyproject_findings(base_text: str | None, head_text: str | None) -> list[str]:
    if base_text is None:
        return []
    if head_text is None:
        return ["удалён pyproject.toml с настройками тестов и анализаторов"]
    try:
        base, head = tomllib.loads(base_text), tomllib.loads(head_text)
    except tomllib.TOMLDecodeError as exc:
        return [f"pyproject.toml не разбирается: {exc}"]
    findings: list[str] = []

    before, after = _fail_under(base), _fail_under(head)
    if before is not None and (after is None or after < before):
        findings.append(
            f"снижен порог покрытия --cov-fail-under: {before:g} -> "
            f"{'нет' if after is None else f'{after:g}'}"
        )

    base_opts, head_opts = _addopts_tokens(base), _addopts_tokens(head)
    removed = [t for t in base_opts if t not in head_opts]
    if removed:
        findings.append(f"из addopts pytest удалено: {' '.join(removed)}")
    added = [t for t in head_opts if t not in base_opts]
    forbidden = [
        t
        for t in added
        if any(t == opt or t.startswith(f"{opt}=") for opt in PYTEST_FORBIDDEN_OPTS)
    ]
    if forbidden:
        findings.append(f"в addopts pytest добавлен отбор тестов: {' '.join(added)}")

    for dotted, message in _GROWING_LISTS.items():
        grown = _as_set(_dig(head, dotted)) - _as_set(_dig(base, dotted))
        if grown:
            findings.append(f"{message} ({dotted}): {', '.join(sorted(grown))}")
    for dotted, message in _SHRINKING_LISTS.items():
        old = _dig(base, dotted)
        if old is None:
            continue
        lost = _as_set(old) - _as_set(_dig(head, dotted))
        if lost:
            findings.append(f"{message} ({dotted}): убрано {', '.join(sorted(lost))}")
    return findings


# --------------------------------------------------------------- Makefile --


def parse_makefile(text: str) -> dict[str, tuple[list[str], list[str]]]:
    """{цель: (зависимости, строки рецепта)} — без раскрытия переменных."""
    targets: dict[str, tuple[list[str], list[str]]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("\t"):
            if current is not None and line.strip():
                targets[current][1].append(line.strip())
            continue
        match = re.match(r"^([A-Za-z0-9_.%-]+)\s*:(?![=:])\s*(.*)$", line)
        if match and match.group(1) != ".PHONY":
            current = match.group(1)
            prereqs = match.group(2).split("#", 1)[0].split()
            targets[current] = (prereqs, [])
        elif line.strip() and not line.lstrip().startswith(("#", "ifeq", "ifneq")):
            if not line.lstrip().startswith(("endif", "else", "$(error")):
                current = None
    return targets


def makefile_findings(base_text: str | None, head_text: str | None) -> list[str]:
    if base_text is None:
        return []
    if head_text is None:
        return ["удалён Makefile — единый командный интерфейс проекта"]
    base, head = parse_makefile(base_text), parse_makefile(head_text)
    findings: list[str] = []
    for target in GUARDED_MAKE_TARGETS:
        if target not in base:
            continue
        if target not in head:
            findings.append(f"удалена make-цель {target}")
            continue
        (base_deps, base_cmds), (head_deps, head_cmds) = base[target], head[target]
        lost_deps = [d for d in base_deps if d not in head_deps]
        if lost_deps:
            findings.append(f"из make {target} убраны шаги: {' '.join(lost_deps)}")
        lost_cmds = [c for c in base_cmds if c not in head_cmds]
        for command in lost_cmds:
            findings.append(f"из make {target} удалена или изменена команда: {command}")
        for command in head_cmds:
            if command not in base_cmds and IGNORE_ERRORS.search(command):
                findings.append(
                    f"в make {target} ошибка команды игнорируется: {command}"
                )
    return findings


# -------------------------------------------------------------- pipeline --


def load_ci(text: str | None) -> dict | None:
    if text is None:
        return None
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def _merge(base: dict, extra: dict) -> dict:
    result = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve_job(name: str, data: dict, depth: int = 0) -> dict:
    """Job с учётом extends (шаблоны применяются по порядку, затем сам job)."""
    job = data.get(name) or {}
    if not isinstance(job, dict) or depth > 10:
        return {}
    extends = job.get("extends") or []
    if isinstance(extends, str):
        extends = [extends]
    resolved: dict = {}
    for parent in extends:
        resolved = _merge(resolved, resolve_job(parent, data, depth + 1))
    own = {k: v for k, v in job.items() if k != "extends"}
    return _merge(resolved, own)


def _flatten(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()]
    items: list[str] = []
    for item in value:
        items += _flatten(item)
    return items


def ci_jobs(data: dict) -> list[str]:
    return [
        key
        for key, value in data.items()
        if key not in CI_RESERVED_KEYS
        and not key.startswith(".")
        and isinstance(value, dict)
    ]


def ci_invariant_findings(head: dict | None) -> list[str]:
    """Свойства pipeline, которые обязаны выполняться всегда (не только в diff)."""
    if head is None:
        return [f"нет {CI_FILE} — pipeline удалён"]
    findings: list[str] = []
    jobs = set(ci_jobs(head))
    stages = head.get("stages") or []
    for name in REQUIRED_CI_JOBS:
        if name not in jobs:
            findings.append(f"в pipeline нет обязательного job {name}")
            continue
        job = resolve_job(name, head)
        if job.get("allow_failure") not in (None, False):
            findings.append(f"job {name}: allow_failure — падение не остановит CI")
        if job.get("when") in {"manual", "never", "delayed"}:
            findings.append(f"job {name}: when: {job['when']} — проверка не запустится")
        for rule in job.get("rules") or []:
            if isinstance(rule, dict) and (
                rule.get("when") in {"manual", "never"} or rule.get("allow_failure")
            ):
                findings.append(f"job {name}: rules отключают или ослабляют проверку")
                break
        for command in _flatten(job.get("script")):
            if IGNORE_ERRORS.search(command):
                findings.append(f"job {name}: ошибка игнорируется: {command}")
    publish = resolve_job(PUBLISH_JOB, head) if PUBLISH_JOB in jobs else {}
    if publish:
        if publish.get("needs") is not None:
            findings.append(
                f"job {PUBLISH_JOB}: needs позволяет публиковать, не дожидаясь "
                "всех проверок"
            )
        if stages and publish.get("stage") != stages[-1]:
            findings.append(f"job {PUBLISH_JOB} должен быть в последней стадии")
    return findings


def ci_diff_findings(base: dict | None, head: dict | None) -> list[str]:
    if base is None or head is None:
        return []
    findings: list[str] = []
    head_jobs = set(ci_jobs(head))
    for name in ci_jobs(base):
        if name not in head_jobs:
            findings.append(f"из pipeline удалён job {name}")
            continue
        base_script = _flatten(resolve_job(name, base).get("script"))
        head_script = _flatten(resolve_job(name, head).get("script"))
        for command in base_script:
            if command not in head_script:
                findings.append(f"job {name}: удалена или изменена команда: {command}")
    if base.get("workflow") != head.get("workflow"):
        findings.append("изменены правила запуска pipeline (workflow)")
    return findings


# -------------------------------------------------------------------- git --


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def git_ok(*args: str) -> bool:
    return (
        subprocess.run(["git", *args], capture_output=True, check=False).returncode == 0
    )


def git_show(ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def read_head(path: str) -> str | None:
    file = Path(path)
    return (
        file.read_text(encoding="utf-8", errors="replace") if file.is_file() else None
    )


def changed_paths(base: str) -> list[str]:
    """Изменённые файлы (рабочее дерево против базы), для переименований — оба."""
    paths: list[str] = []
    output = git("diff", "--name-status", "-M", base, "--")
    for line in output.splitlines():
        parts = line.split("\t")
        for path in parts[1:]:
            if path not in paths:
                paths.append(path)
    untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
    return paths + [p for p in untracked if p not in paths]


def sources_at(ref: str | None, prefixes: tuple[str, ...]) -> dict[str, str]:
    if ref is None:
        files = git(
            "ls-files", "--cached", "--others", "--exclude-standard", *prefixes
        ).splitlines()
        return {p: text for p in files if (text := read_head(p)) is not None}
    files = git("ls-tree", "-r", "--name-only", ref, "--", *prefixes).splitlines()
    return {p: text for p in files if (text := git_show(ref, p)) is not None}


def codeowners_at(ref: str | None) -> tuple[list[Rule], str]:
    for location in CODEOWNERS_LOCATIONS:
        text = read_head(location) if ref is None else git_show(ref, location)
        if text is not None:
            return parse_codeowners(text), location
    return [], ""


def fetch_branch(branch: str) -> None:
    git(
        "fetch",
        "--no-tags",
        "--quiet",
        "origin",
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        check=False,
    )


# ------------------------------------------------------------------- отчёт --


@dataclass
class Report:
    base: str
    critical: dict[str, list[str]] = field(default_factory=dict)
    weakening: list[str] = field(default_factory=list)
    owners_pool: list[str] = field(default_factory=list)
    codeowners_source: str = ""

    @property
    def clean(self) -> bool:
        return not self.critical and not self.weakening


def analyze(base: str, rules_ref: str | None) -> Report:
    rules, location = codeowners_at(rules_ref)
    source = f"{location} из {rules_ref}" if location else ""
    if not rules:
        rules, location = codeowners_at(None)
        source = f"{location} из текущей ветки (в базе его ещё нет)" if location else ""
    report = Report(base=base, codeowners_source=source)
    for rule in rules:
        report.owners_pool += [o for o in rule.owners if o not in report.owners_pool]

    for path in changed_paths(base):
        owners = owners_for(path, rules)
        if owners is not None:
            report.critical[path] = owners

    prefixes = CHECKED_CODE_DIRS
    base_sources, head_sources = sources_at(base, prefixes), sources_at(None, prefixes)
    report.weakening += test_findings(base_sources, head_sources)
    report.weakening += suppression_findings(base_sources, head_sources)
    report.weakening += pyproject_findings(
        git_show(base, "pyproject.toml"), read_head("pyproject.toml")
    )
    report.weakening += makefile_findings(
        git_show(base, "Makefile"), read_head("Makefile")
    )
    try:
        base_ci = load_ci(git_show(base, CI_FILE))
        head_ci = load_ci(read_head(CI_FILE))
    except yaml.YAMLError as exc:
        report.weakening.append(f"{CI_FILE} не разбирается: {exc}")
    else:
        report.weakening += ci_diff_findings(base_ci, head_ci)
        if base_ci is not None or head_ci is not None:
            report.weakening += ci_invariant_findings(head_ci)
    return report


def print_report(report: Report) -> None:
    print(f"База сравнения: {report.base}")
    if report.codeowners_source:
        print(f"Критические файлы: {report.codeowners_source}")
    else:
        print("ВНИМАНИЕ: файл CODEOWNERS не найден — критические файлы не заданы")
    if report.critical:
        print("\nКРИТИЧЕСКИЕ ИЗМЕНЕНИЯ (нужно одобрение владельца):")
        for path, owners in report.critical.items():
            print(f"  * {path}  — {' '.join(owners) or 'владелец не указан'}")
    if report.weakening:
        print("\nОСЛАБЛЕНИЕ ПРОВЕРОК:")
        for item in report.weakening:
            print(f"  ! {item}")
    if report.clean:
        print("\nКритические файлы не затронуты, ослаблений проверок нет.")


# ------------------------------------------------------------- GitLab API --


class Api:
    def __init__(self, env: dict[str, str]) -> None:
        self.base = f"{env['CI_API_V4_URL']}/projects/{env['CI_PROJECT_ID']}"
        self.token = env.get("GUARD_API_TOKEN", "")

    def get(self, path: str):
        request = urllib.request.Request(
            f"{self.base}{path}", headers={"PRIVATE-TOKEN": self.token}
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _owner_matches(owner: str, user: dict, levels: dict[int, int]) -> bool:
    if owner.startswith("@@"):
        need = ROLE_LEVELS.get(owner[2:].lower())
        return need is not None and levels.get(user["id"], 0) >= need
    return owner.lstrip("@").lower() == user["username"].lower()


# Системные заметки MR: GitLab пишет их при каждом push и одобрении. По ним
# определяется порядок событий, даже если в API одобрений нет approved_at.
NOTES_QUERY = "sort=desc&order_by=created_at&per_page=100"
_PUSH_NOTE = re.compile(r"^(added \d+ (new )?commits?|добавил[аи]? \d+)", re.IGNORECASE)
_APPROVE_NOTE = re.compile(r"^(approved this merge request|одобрил)", re.IGNORECASE)


def _system_notes(events, pattern: re.Pattern, username: str | None = None):
    for note in events or []:
        if not isinstance(note, dict) or not note.get("system"):
            continue
        if not pattern.match((note.get("body") or "").strip()):
            continue
        if username and (note.get("author") or {}).get("username") != username:
            continue
        if moment := _parse_time(note.get("created_at")):
            yield moment


def last_push_time(mr: dict, versions, events, head_sha: str) -> datetime | None:
    """Время последнего push в MR: версия diff, заметка «added N commits», создание MR."""
    moments = [
        moment
        for v in versions or []
        if v.get("head_commit_sha") == head_sha
        and (moment := _parse_time(v.get("created_at")))
    ]
    moments += list(_system_notes(events, _PUSH_NOTE))
    if created := _parse_time(mr.get("created_at")):
        moments.append(created)
    return max(moments, default=None)


def approval_time(item: dict, events) -> datetime | None:
    """Время одобрения: approved_at из API или последняя заметка «approved»."""
    username = (item.get("user") or {}).get("username", "")
    moments = list(_system_notes(events, _APPROVE_NOTE, username))
    if approved := _parse_time(item.get("approved_at")):
        moments.append(approved)
    return max(moments, default=None)


def check_mr_approvals(report: Report, env: dict[str, str]) -> tuple[bool, list[str]]:
    """Есть ли действующие одобрения владельцев для всех находок в MR."""
    if not env.get("GUARD_API_TOKEN"):
        return False, [
            (
                "не задана переменная GUARD_API_TOKEN (Settings > CI/CD > Variables) — "
                "одобрения проверить нельзя, изменение не принимается"
            )
        ]
    api, iid = Api(env), env["CI_MERGE_REQUEST_IID"]
    try:
        mr = api.get(f"/merge_requests/{iid}")
        approvals = api.get(f"/merge_requests/{iid}/approvals")
        versions = api.get(f"/merge_requests/{iid}/versions")
        events = api.get(f"/merge_requests/{iid}/notes?{NOTES_QUERY}")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return False, [f"GitLab API недоступно: {exc}"]

    author = mr.get("author", {}).get("username", "")
    pushed_at = last_push_time(mr, versions, events, env.get("CI_COMMIT_SHA", ""))

    notes: list[str] = []
    valid: list[dict] = []
    for item in approvals.get("approved_by") or []:
        user = item.get("user") or {}
        name = user.get("username", "")
        approved_at = approval_time(item, events)
        if name == author:
            notes.append(f"одобрение автора MR @{author} не учитывается")
        elif approved_at is None or pushed_at is None or approved_at <= pushed_at:
            # Fail closed: если нельзя доказать, что одобрение дано после
            # последнего push, оно считается устаревшим.
            notes.append(
                f"одобрение @{name} дано до последнего push — нужно одобрить заново"
            )
        else:
            valid.append(user)

    levels: dict[int, int] = {}
    if any(o.startswith("@@") for o in report.owners_pool):
        for user in valid:
            try:
                member = api.get(f"/members/all/{user['id']}")
                levels[user["id"]] = int(member.get("access_level", 0))
            except (urllib.error.URLError, TimeoutError, ValueError):
                levels[user["id"]] = 0

    def approved_by_owner(owners: list[str]) -> list[str]:
        return [
            f"@{u['username']}"
            for u in valid
            if any(_owner_matches(o, u, levels) for o in owners)
        ]

    ok = True
    for path, owners in report.critical.items():
        who = approved_by_owner(owners)
        if who:
            notes.append(f"{path}: одобрено {' '.join(who)}")
        else:
            ok = False
            notes.append(f"{path}: нет одобрения владельца ({' '.join(owners)})")
    if report.weakening:
        who = approved_by_owner(report.owners_pool)
        if who:
            notes.append(f"ослабление проверок осознанно одобрено: {' '.join(who)}")
        else:
            ok = False
            notes.append("ослабление проверок не одобрено владельцем из CODEOWNERS")
    return ok, notes


def commit_came_from_mr(env: dict[str, str]) -> bool | None:
    if not env.get("GUARD_API_TOKEN"):
        return None
    try:
        mrs = Api(env).get(f"/repository/commits/{env['CI_COMMIT_SHA']}/merge_requests")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    return any(mr.get("state") == "merged" for mr in mrs)


# ------------------------------------------------------------------ режимы --


def resolve_ci_base(env: dict[str, str]) -> tuple[str | None, str | None, str]:
    """(база diff, ref доверенной версии guard и CODEOWNERS, описание)."""
    if env.get("CI_COMMIT_TAG"):
        return None, None, f"тег {env['CI_COMMIT_TAG']}"
    default = env.get("CI_DEFAULT_BRANCH", "main")
    if env.get("CI_MERGE_REQUEST_IID"):
        target = env.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", default)
        fetch_branch(target)
        base = (
            env.get("CI_MERGE_REQUEST_DIFF_BASE_SHA")
            or git("merge-base", f"origin/{target}", "HEAD").strip()
        )
        return (
            base,
            f"origin/{target}",
            f"MR !{env['CI_MERGE_REQUEST_IID']} -> {target}",
        )
    branch = env.get("CI_COMMIT_BRANCH", "")
    if branch == default:
        before = env.get("CI_COMMIT_BEFORE_SHA", "")
        if (
            before
            and before != ZERO_SHA
            and git_ok("cat-file", "-e", f"{before}^{{commit}}")
        ):
            return before, before, f"push в {default}"
        if git_ok("rev-parse", "--verify", "-q", "HEAD~1"):
            return "HEAD~1", "HEAD~1", f"push в {default}"
        return None, None, f"первый коммит {default}"
    fetch_branch(default)
    base = git("merge-base", f"origin/{default}", "HEAD", check=False).strip()
    return base or None, f"origin/{default}", f"ветка {branch} -> {default}"


def note_modified_guard(trusted_ref: str | None) -> None:
    """Сообщает, если ветка меняет сам guard: в CI выполняется версия из main."""
    running = Path(__file__).resolve()
    branch_copy = Path(SELF_PATH).resolve()
    if running == branch_copy or not trusted_ref:
        return
    if read_head(SELF_PATH) != running.read_text(encoding="utf-8"):
        print(
            f"{SELF_PATH} изменён в этой ветке — выполняется доверенная версия из "
            f"{trusted_ref}; новая версия заработает после слияния."
        )


def run_local(base: str) -> int:
    if not git_ok("rev-parse", "--verify", "-q", f"{base}^{{commit}}"):
        print(f"Нет ссылки {base}: выполните `git fetch origin` или задайте GUARD_BASE")
        return 2
    merge_base = git("merge-base", base, "HEAD", check=False).strip() or base
    report = analyze(merge_base, base)
    print_report(report)
    if report.weakening:
        print("\nguard: найдено ослабление проверок — в MR потребуется одобрение.")
        return 1
    if report.critical:
        print("\nguard: в MR потребуется одобрение владельцев критических файлов.")
    return 0


def run_ci(env: dict[str, str]) -> int:
    os.chdir(git("rev-parse", "--show-toplevel").strip())
    base, trusted_ref, description = resolve_ci_base(env)
    note_modified_guard(trusted_ref)
    print(f"Режим CI: {description}")
    if base is None:
        print(
            "Сравнивать не с чем — проверяются только обязательные свойства pipeline."
        )
        findings = ci_invariant_findings(load_ci(read_head(CI_FILE)))
        for item in findings:
            print(f"  ! {item}")
        return 1 if findings else 0

    report = analyze(base, trusted_ref)
    print_report(report)
    if report.clean:
        return 0

    if env.get("CI_MERGE_REQUEST_IID"):
        ok, notes = check_mr_approvals(report, env)
        print("\nОдобрения:")
        for note in notes:
            print(f"  - {note}")
        if ok:
            print("\nguard пройден: изменения согласованы владельцами.")
            return 0
        print(
            "\nguard НЕ ПРОЙДЕН: изменение критических файлов не согласовано.\n"
            "Владелец (не автор MR) нажимает Approve в merge request, затем "
            "Retry этого job (или Run pipeline)."
        )
        return 1

    if env.get("CI_COMMIT_BRANCH") == env.get("CI_DEFAULT_BRANCH", "main"):
        came = commit_came_from_mr(env)
        if came is False:
            print("\nguard НЕ ПРОЙДЕН: критические файлы изменены в main без MR.")
            return 1
        if came is None:
            print("\nВНИМАНИЕ: не удалось проверить, что коммит пришёл через MR.")
        print("\nИзменения приняты через MR — согласование выполнено там.")
        return 0

    if report.weakening:
        print("\nguard НЕ ПРОЙДЕН: ослабление проверок вне merge request.")
        return 1
    print("\nОткройте merge request: там потребуется одобрение владельцев.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ci", action="store_true", help="режим GitLab CI")
    parser.add_argument(
        "--base", default="origin/main", help="с чем сравнивать (локальный режим)"
    )
    args = parser.parse_args()
    code = run_ci(dict(os.environ)) if args.ci else run_local(args.base)
    sys.exit(code)


if __name__ == "__main__":
    main()
