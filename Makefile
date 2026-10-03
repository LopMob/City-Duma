.PHONY: setup run test quality sast migrate verify backup restore

setup:
	python3 -m venv venv
	./venv/bin/pip install -r requirements.txt
	cp -n .env.example .env || true

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -q

quality:
	ruff check app tests
	black --check app tests

# SAST (ЛР3, п.7): статический анализ кода на безопасность. -q тише, но
# ненулевой exit-код при находках всё равно проваливает make verify.
sast:
	bandit -q -c pyproject.toml -r app

migrate:
	alembic upgrade head

# ЛР3, п.11: создать резервную копию текущей БД (путь можно переопределить
# переменной OUTPUT, иначе backups/duma_<timestamp>.sql).
backup:
	python scripts/db_backup.py $(if $(OUTPUT),--output $(OUTPUT),)

# ЛР3, п.11-12: восстановить БД из резервной копии. Обязателен FILE=путь.
restore:
ifeq ($(strip $(FILE)),)
	$(error Использование: make restore FILE=backups/duma_<timestamp>.sql)
endif
	python scripts/db_restore.py $(FILE)

# ЛР3, п.13: единая команда — весь обязательный набор локальных проверок
# (форматирование/линтер + SAST + тесты с порогом покрытия). Назначение
# каждой из них разобрано в docs/VERIFY.md.
verify: quality sast test
