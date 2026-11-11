"""Тесты защиты критических файлов (ЛР5, п.13–15): scripts/ci_guard.py.

Проверяются сами проверки: реальные файлы репозитория не дают ложных
срабатываний, а каждая попытка ослабить приёмку — удалить тест, убрать
проверки из теста, добавить skip, снизить порог покрытия, отключить правило
анализатора, убрать команду из make verify, сделать job необязательным —
обнаруживается. Отдельно проверяется логика одобрений MR (без сети).
"""

import copy
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import ci_guard as guard

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
CI = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"))
CODEOWNERS = (ROOT / ".gitlab" / "CODEOWNERS").read_text(encoding="utf-8")

BASE_TEST = """
import pytest


def test_quorum():
    assert 1 + 1 == 2
    assert 2 * 2 == 4


def test_conflict():
    with pytest.raises(ValueError):
        int("x")
"""


# ------------------------------------------------------------- CODEOWNERS --


def test_codeowners_marks_acceptance_files_as_critical():
    rules = guard.parse_codeowners(CODEOWNERS)
    for path in (
        "tests/unit/test_quorum_boundaries.py",
        "Makefile",
        "pyproject.toml",
        ".gitlab-ci.yml",
        ".gitlab/CODEOWNERS",
        "scripts/ci_guard.py",
        "scripts/container_policy.py",
    ):
        assert guard.owners_for(path, rules), f"{path} должен быть критическим"
    for path in ("app/main.py", "app/routers/meetings.py", "docs/API.md"):
        assert guard.owners_for(path, rules) is None, f"{path} не критический"


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("/tests/", "tests/unit/test_a.py", True),
        ("/tests/", "app/tests/test_a.py", False),
        ("/Makefile", "Makefile", True),
        ("/Makefile", "docs/Makefile", False),
        ("*.toml", "config/app.toml", True),
        ("docs/", "src/docs/readme.md", True),
        ("docs/", "docs", False),
        ("*", "anything/at/all.py", True),
    ],
)
def test_pattern_matching(pattern, path, expected):
    assert guard.pattern_matches(pattern, path) is expected


def test_last_matching_rule_wins_and_sections_combine():
    rules = guard.parse_codeowners(
        "[Backend] @team\n"
        "/app/ @alice\n"
        "/app/secure.py @bob\n"
        "[Security]\n"
        "/app/secure.py @@maintainer\n"
    )
    assert guard.owners_for("app/main.py", rules) == ["@alice"]
    assert guard.owners_for("app/secure.py", rules) == ["@bob", "@@maintainer"]


# ------------------------------------------------------------------ тесты --


def test_unchanged_tests_have_no_findings():
    sources = {"tests/test_x.py": BASE_TEST}
    assert guard.test_findings(sources, dict(sources)) == []


def test_removed_test_is_detected():
    head = BASE_TEST.replace("def test_conflict", "def helper_conflict")
    findings = guard.test_findings(
        {"tests/test_x.py": BASE_TEST}, {"tests/test_x.py": head}
    )
    assert any("удалён тест tests/test_x.py::test_conflict" in f for f in findings)
    assert any("число тестов уменьшилось" in f for f in findings)


def test_moved_test_is_not_reported_as_removed():
    findings = guard.test_findings(
        {"tests/test_x.py": BASE_TEST}, {"tests/unit/test_x.py": BASE_TEST}
    )
    assert findings == []


def test_weakened_assertions_are_detected():
    head = BASE_TEST.replace("    assert 2 * 2 == 4\n", "")
    findings = guard.test_findings(
        {"tests/test_x.py": BASE_TEST}, {"tests/test_x.py": head}
    )
    assert findings == ["в тесте tests/test_x.py::test_quorum меньше проверок: 2 -> 1"]


def test_removed_pytest_raises_is_detected():
    head = BASE_TEST.replace(
        '    with pytest.raises(ValueError):\n        int("x")', "    pass"
    )
    findings = guard.test_findings(
        {"tests/test_x.py": BASE_TEST}, {"tests/test_x.py": head}
    )
    assert any("test_conflict меньше проверок" in f for f in findings)


@pytest.mark.parametrize(
    "decorator",
    [
        "@pytest.mark.skip(reason='later')",
        "@pytest.mark.skipif(True, reason='x')",
        "@pytest.mark.xfail",
    ],
)
def test_new_skip_marker_is_detected(decorator):
    head = BASE_TEST.replace("def test_quorum", f"{decorator}\ndef test_quorum")
    findings = guard.test_findings(
        {"tests/test_x.py": BASE_TEST}, {"tests/test_x.py": head}
    )
    assert any("добавлены пропуски тестов" in f for f in findings)


def test_deselect_hook_in_conftest_is_detected():
    hook = "def pytest_collection_modifyitems(items):\n    items.clear()\n"
    findings = guard.test_findings(
        {"tests/conftest.py": ""}, {"tests/conftest.py": hook}
    )
    assert any("добавлены пропуски тестов" in f for f in findings)


def test_skip_text_inside_string_is_not_a_marker():
    head = BASE_TEST + '\n\ndef test_doc():\n    assert "pytest.mark.skip" != ""\n'
    findings = guard.test_findings(
        {"tests/test_x.py": BASE_TEST}, {"tests/test_x.py": head}
    )
    assert findings == []


def test_new_suppression_comment_is_detected():
    base = {"app/x.py": "value = eval('1')\n"}
    head = {"app/x.py": "value = eval('1')  # nosec B307\n"}
    findings = guard.suppression_findings(base, head)
    assert findings and "0 -> 1" in findings[0]


def test_suppression_text_in_string_is_ignored():
    head = {"scripts/x.py": 'HELP = "используйте # noqa осторожно"\n'}
    assert guard.suppression_findings({"scripts/x.py": ""}, head) == []


# -------------------------------------------------------------- pyproject --


def test_repository_pyproject_has_no_findings():
    assert guard.pyproject_findings(PYPROJECT, PYPROJECT) == []


def test_lowered_coverage_threshold_is_detected():
    head = PYPROJECT.replace("--cov-fail-under=40", "--cov-fail-under=30")
    findings = guard.pyproject_findings(PYPROJECT, head)
    assert any("снижен порог покрытия" in f and "40 -> 30" in f for f in findings)


def test_removed_coverage_threshold_is_detected():
    head = PYPROJECT.replace(" --cov-fail-under=40", "")
    findings = guard.pyproject_findings(PYPROJECT, head)
    assert any("снижен порог покрытия" in f for f in findings)


def test_new_bandit_skip_is_detected():
    head = PYPROJECT.replace("skips = []", 'skips = ["B608"]')
    findings = guard.pyproject_findings(PYPROJECT, head)
    assert any("bandit" in f and "B608" in f for f in findings)


def test_test_selection_in_addopts_is_detected():
    head = PYPROJECT.replace('addopts = "', 'addopts = "-k not_slow ')
    findings = guard.pyproject_findings(PYPROJECT, head)
    assert any("отбор тестов" in f for f in findings)


def test_new_ruff_ignore_is_detected():
    head = PYPROJECT + '\n[tool.ruff.lint]\nignore = ["F401"]\n'
    findings = guard.pyproject_findings(PYPROJECT, head)
    assert any("ruff" in f and "F401" in f for f in findings)


# --------------------------------------------------------------- Makefile --


def test_repository_makefile_has_no_findings():
    assert guard.makefile_findings(MAKEFILE, MAKEFILE) == []


def test_step_removed_from_verify_is_detected():
    head = MAKEFILE.replace("verify: quality sast test", "verify: quality test")
    findings = guard.makefile_findings(MAKEFILE, head)
    assert findings == ["из make verify убраны шаги: sast"]


def test_ignored_error_in_make_target_is_detected():
    head = MAKEFILE.replace("\tpytest -q\n", "\tpytest -q || true\n")
    findings = guard.makefile_findings(MAKEFILE, head)
    assert any("ошибка команды игнорируется" in f for f in findings)
    assert any("удалена или изменена команда: pytest -q" in f for f in findings)


def test_removed_make_target_is_detected():
    head = MAKEFILE.replace("migrate-check:", "migrate-chek:")
    findings = guard.makefile_findings(MAKEFILE, head)
    assert "удалена make-цель migrate-check" in findings


# -------------------------------------------------------------- pipeline --


def test_repository_pipeline_satisfies_invariants():
    assert guard.ci_invariant_findings(CI) == []
    assert guard.ci_diff_findings(CI, CI) == []


def test_pipeline_has_every_required_job():
    assert set(guard.REQUIRED_CI_JOBS) <= set(guard.ci_jobs(CI))


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda ci: ci["test"].update(allow_failure=True), "allow_failure"),
        (lambda ci: ci["secrets"].update(when="manual"), "when: manual"),
        (lambda ci: ci.pop("image-scan"), "нет обязательного job image-scan"),
        (lambda ci: ci["publish:image"].update(needs=["build:image"]), "needs"),
        (
            lambda ci: ci["test"].update(script=["make test || true"]),
            "ошибка игнорируется",
        ),
        (
            lambda ci: ci["sast"].update(rules=[{"when": "never"}]),
            "rules отключают",
        ),
        (
            lambda ci: ci["image-scan"].update(stage="verify"),
            "publish:image должен быть позже проверки image-scan",
        ),
        (lambda ci: ci.pop("verify:staging"), "нет обязательного job verify:staging"),
    ],
)
def test_weakened_pipeline_is_detected(change, expected):
    head = copy.deepcopy(CI)
    change(head)
    findings = guard.ci_invariant_findings(head) + guard.ci_diff_findings(CI, head)
    assert any(expected in f for f in findings), findings


def test_allow_failure_inherited_from_template_is_detected():
    head = copy.deepcopy(CI)
    head[".python"]["allow_failure"] = True
    findings = guard.ci_invariant_findings(head)
    assert any("job quality: allow_failure" in f for f in findings)


def test_removed_command_in_job_is_detected():
    head = copy.deepcopy(CI)
    head["migrations"]["script"] = ["make migrate"]
    findings = guard.ci_diff_findings(CI, head)
    assert any("make migrate-check" in f for f in findings)


# -------------------------------------------------------------- одобрения --


def _fake_api(monkeypatch, responses):
    monkeypatch.setattr(guard.Api, "get", lambda self, path: responses[path])


ENV = {
    "GUARD_API_TOKEN": "token",
    "CI_API_V4_URL": "http://gitlab.local/api/v4",
    "CI_PROJECT_ID": "7",
    "CI_MERGE_REQUEST_IID": "5",
    "CI_COMMIT_SHA": "abc",
}


NOTES = f"/merge_requests/5/notes?{guard.NOTES_QUERY}"


def _responses(approved_by, pushed_at="2026-11-10T10:00:00Z", notes=(), versions=None):
    return {
        "/merge_requests/5": {
            "author": {"username": "stepan"},
            "created_at": "2026-11-10T08:00:00Z",
        },
        "/merge_requests/5/approvals": {"approved_by": approved_by},
        "/merge_requests/5/versions": (
            versions
            if versions is not None
            else [{"head_commit_sha": "abc", "created_at": pushed_at}]
        ),
        NOTES: list(notes),
        "/members/all/2": {"access_level": 40},
        "/members/all/3": {"access_level": 30},
    }


def _note(body, at, username="stepan"):
    return {
        "system": True,
        "body": body,
        "created_at": at,
        "author": {"username": username},
    }


def _report(owners=("@lopmob",)):
    return guard.Report(
        base="main",
        critical={"tests/test_x.py": list(owners)},
        owners_pool=list(owners),
    )


def _approval(user_id, username, at="2026-11-10T11:00:00Z"):
    return {"user": {"id": user_id, "username": username}, "approved_at": at}


def test_owner_approval_after_push_is_accepted(monkeypatch):
    _fake_api(monkeypatch, _responses([_approval(2, "LopMob")]))
    ok, notes = guard.check_mr_approvals(_report(), ENV)
    assert ok, notes


def test_author_cannot_approve_own_critical_change(monkeypatch):
    _fake_api(monkeypatch, _responses([_approval(1, "stepan")]))
    ok, notes = guard.check_mr_approvals(_report(("@stepan",)), ENV)
    assert not ok
    assert any("автора MR" in n for n in notes)


def test_approval_before_last_push_is_stale(monkeypatch):
    stale = _approval(2, "LopMob", at="2026-11-10T09:00:00Z")
    _fake_api(monkeypatch, _responses([stale]))
    ok, notes = guard.check_mr_approvals(_report(), ENV)
    assert not ok
    assert any("до последнего push" in n for n in notes)


def test_non_owner_approval_is_not_enough(monkeypatch):
    _fake_api(monkeypatch, _responses([_approval(3, "random")]))
    ok, _ = guard.check_mr_approvals(_report(), ENV)
    assert not ok


def test_role_owner_requires_maintainer_level(monkeypatch):
    _fake_api(monkeypatch, _responses([_approval(3, "dev")]))
    assert not guard.check_mr_approvals(_report(("@@maintainer",)), ENV)[0]
    _fake_api(monkeypatch, _responses([_approval(2, "lead")]))
    assert guard.check_mr_approvals(_report(("@@maintainer",)), ENV)[0]


def _plain(user_id, username):
    """Одобрение без approved_at (так отвечают некоторые версии GitLab)."""
    return {"user": {"id": user_id, "username": username}}


def test_approval_time_taken_from_system_note(monkeypatch):
    notes = [
        _note("added 1 commit", "2026-11-10T10:00:00Z"),
        _note("approved this merge request", "2026-11-10T11:00:00Z", "LopMob"),
    ]
    _fake_api(monkeypatch, _responses([_plain(2, "LopMob")], notes=notes))
    ok, notes_out = guard.check_mr_approvals(_report(), ENV)
    assert ok, notes_out


def test_new_push_after_approval_makes_it_stale(monkeypatch):
    # Пустой коммит может не создать новую версию diff — push виден по заметке.
    notes = [
        _note("added 1 commit", "2026-11-10T12:00:00Z"),
        _note("approved this merge request", "2026-11-10T11:00:00Z", "LopMob"),
    ]
    responses = _responses([_plain(2, "LopMob")], notes=notes, versions=[])
    _fake_api(monkeypatch, responses)
    ok, notes_out = guard.check_mr_approvals(_report(), ENV)
    assert not ok
    assert any("до последнего push" in n for n in notes_out)


def test_approval_with_unknown_time_fails_closed(monkeypatch):
    _fake_api(monkeypatch, _responses([_plain(2, "LopMob")]))
    ok, notes_out = guard.check_mr_approvals(_report(), ENV)
    assert not ok
    assert any("до последнего push" in n for n in notes_out)


def test_missing_token_fails_closed():
    env = {k: v for k, v in ENV.items() if k != "GUARD_API_TOKEN"}
    ok, notes = guard.check_mr_approvals(_report(), env)
    assert not ok
    assert "GUARD_API_TOKEN" in notes[0]


# ------------------------------------------------- сквозной прогон на git --


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_analyze_on_real_git_history(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / ".gitlab").mkdir()
    (repo / ".gitlab" / "CODEOWNERS").write_text("/tests/ @lopmob\n", "utf-8")
    (repo / "tests" / "test_x.py").write_text(BASE_TEST, "utf-8")
    (repo / "README.md").write_text("demo\n", "utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "ci@example.com")
    _git(repo, "config", "user.name", "CI")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")

    (repo / "tests" / "test_x.py").write_text(
        BASE_TEST.replace("def test_conflict", "def _disabled_conflict"), "utf-8"
    )
    (repo / "README.md").write_text("changed\n", "utf-8")
    monkeypatch.chdir(repo)

    report = guard.analyze("main", "main")
    assert report.critical == {"tests/test_x.py": ["@lopmob"]}
    assert any("удалён тест" in f for f in report.weakening)
    assert guard.run_local("main") == 1
