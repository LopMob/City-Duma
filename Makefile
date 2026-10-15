.PHONY: setup db-init run test quality sast migrate verify backup restore \
	container-init container-up container-down container-logs container-migrate \
	container-backup container-restore container-restore-new container-lint container-check

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

# ---- ЛР4: контейнерное окружение (подробности — docs/CONTAINER.md) ----

COMPOSE = docker compose --env-file .env.compose

container-init:
	python scripts/container_init.py

container-up: container-init
	$(COMPOSE) up -d --build

container-down:
	$(COMPOSE) down

container-logs:
	$(COMPOSE) logs --tail=100

container-migrate:
	$(COMPOSE) run --rm migrate

container-backup:
	python scripts/container_db.py backup $(if $(OUTPUT),--output $(OUTPUT),)

container-restore:
ifeq ($(strip $(FILE)),)
	$(error Использование: make container-restore FILE=backups/container_duma_<timestamp>.sql)
endif
	python scripts/container_db.py restore $(FILE)

container-restore-new:
ifeq ($(strip $(FILE)),)
	$(error Использование: make container-restore-new FILE=backups/container_duma_<timestamp>.sql)
endif
	python scripts/container_db.py restore $(FILE) --new-volume

container-lint:
	python scripts/container_check.py --static-only

container-check:
	python scripts/container_check.py
