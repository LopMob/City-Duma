"""Создание .env.compose для контейнерного окружения (ЛР4).

Берёт .env.compose.example, подставляет случайный пароль БД и сохраняет как
.env.compose. Если файл уже есть — ничего не меняет (чтобы не потерять пароль
от существующего volume). Файл в git не попадает (.gitignore).
"""

from __future__ import annotations

import secrets

from _compose import DEFAULT_ENV_FILE, REPO_ROOT


def main() -> None:
    if DEFAULT_ENV_FILE.exists():
        print(f"{DEFAULT_ENV_FILE.name} уже существует — оставляю без изменений.")
        return
    example = (REPO_ROOT / ".env.compose.example").read_text(encoding="utf-8")
    lines = []
    for line in example.splitlines():
        if line.startswith("POSTGRES_PASSWORD="):
            line = f"POSTGRES_PASSWORD={secrets.token_hex(16)}"
        lines.append(line)
    DEFAULT_ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Создан {DEFAULT_ENV_FILE.name} со случайным паролем БД.")


if __name__ == "__main__":
    main()
