"""Тесты проверки открытых секретов Ansible (ЛР7): scripts/vault_check.py."""

from scripts import vault_check as vc

ENCRYPTED = "$ANSIBLE_VAULT;1.1;AES256\n6162636465\n"


def write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_encrypted_vault_is_ok(tmp_path):
    write(tmp_path, "inventories/staging/group_vars/staging/vault.yml", ENCRYPTED)
    assert vc.find_problems(tmp_path) == []


def test_plain_vault_is_rejected(tmp_path):
    write(
        tmp_path,
        "inventories/staging/group_vars/staging/vault.yml",
        "vault_postgres_password: secret\n",
    )
    problems = vc.find_problems(tmp_path)
    assert len(problems) == 1
    assert "не зашифрован" in problems[0]


def test_secret_outside_vault_is_rejected(tmp_path):
    write(tmp_path, "group_vars/all.yml", "vault_registry_token: abc\n")
    problems = vc.find_problems(tmp_path)
    assert len(problems) == 1
    assert "вне vault.yml" in problems[0]


def test_reference_to_secret_is_allowed(tmp_path):
    write(
        tmp_path,
        "inventories/prod/group_vars/prod/main.yml",
        'app_postgres_password: "{{ vault_postgres_password }}"\n',
    )
    assert vc.find_problems(tmp_path) == []


def test_example_file_is_ignored(tmp_path):
    write(tmp_path, "vault.yml.example", "vault_postgres_password: change-me\n")
    assert vc.find_problems(tmp_path) == []


def test_main_exit_codes(tmp_path, capsys):
    assert vc.main(["vault_check", str(tmp_path)]) == 0
    write(tmp_path, "vault.yml", "plain: text\n")
    assert vc.main(["vault_check", str(tmp_path)]) == 1
    assert vc.main(["vault_check", str(tmp_path / "missing")]) == 1
    assert "[FAIL]" in capsys.readouterr().out
