.PHONY: setup db-init run test quality sast migrate verify backup restore

setup:
	python3 -m venv venv
	./venv/bin/pip install -r requirements.txt
	cp -n .env.example .env || true

db-init:
	python scripts/db_init.py

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -q

quality:
	ruff check app tests scripts
	black --check app tests scripts

sast:
	bandit -q -c pyproject.toml -r app

migrate:
	alembic upgrade head

backup:
	python scripts/db_backup.py $(if $(OUTPUT),--output $(OUTPUT),)

restore:
ifeq ($(strip $(FILE)),)
	$(error Использование: make restore FILE=backups/duma_<timestamp>.sql)
endif
	python scripts/db_restore.py $(FILE)
verify: quality sast test
