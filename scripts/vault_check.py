"""Проверка, что секреты Ansible не лежат в репозитории открытым текстом (ЛР7).

Секреты окружений хранятся только в файлах vault.yml, и каждый такой файл
обязан быть зашифрован ansible-vault (первая строка — $ANSIBLE_VAULT;...).
Кроме того, секретные переменные vault_* нельзя задавать нигде, кроме vault.yml
(образцы *.example не считаются: в них заглушки).

    python scripts/vault_check.py [каталог]     # по умолчанию ansible/

Код возврата 0 — открытых секретов нет, 1 — найдены нарушения.
"""

import re
import sys
from pathlib import Path

VAULT_HEADER = "$ANSIBLE_VAULT;"
VAULT_FILE = "vault.yml"
SECRET_ASSIGNMENT = re.compile(r"^\s*vault_[A-Za-z0-9_]+\s*:", re.MULTILINE)
YAML_SUFFIXES = {".yml", ".yaml"}


def is_encrypted(text: str) -> bool:
    return text.lstrip().startswith(VAULT_HEADER)


def check_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.name == VAULT_FILE:
        if not is_encrypted(text):
            return [f"{path}: файл секретов не зашифрован (ansible-vault encrypt)"]
        return []
    if path.suffix in YAML_SUFFIXES and SECRET_ASSIGNMENT.search(text):
        return [f"{path}: секрет vault_* задан вне vault.yml"]
    return []


def find_problems(root: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".venv" not in path.parts:
            problems.extend(check_file(path))
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("ansible")
    if not root.is_dir():
        print(f"нет каталога {root}")
        return 1
    problems = find_problems(root)
    for problem in problems:
        print(f"[FAIL] {problem}")
    if problems:
        return 1
    print(f"[OK] открытых секретов в {root} нет")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
