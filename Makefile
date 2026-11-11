.PHONY: setup db-init run test quality sast migrate verify backup restore \
	container-init container-up container-down container-logs container-migrate \
	container-backup container-restore container-restore-new container-lint container-check \
	reports-dir quality-report sast-report db-wait migrate-check backup-check \
	deps-audit deps-audit-report secrets image-scan guard ci-local

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

# ---- ЛР5: те же проверки, что выполняет GitLab CI (подробности — docs/CI.md) ----

REPORTS_DIR ?= reports
GUARD_BASE ?= origin/main
GUARD_ARGS ?=
GITLEAKS_IMAGE ?= zricethezav/gitleaks:v8.30.1
TRIVY_IMAGE ?= aquasec/trivy:0.71.2
IMAGE ?= city-duma:0.4.0

reports-dir:
	python -c "import pathlib; pathlib.Path('$(REPORTS_DIR)').mkdir(exist_ok=True)"

# Машиночитаемые отчёты (артефакты CI). Ничего не проверяют: падение pipeline
# определяют make quality и make sast, отчёты лишь сохраняют их результаты.
quality-report: reports-dir
	ruff check app tests scripts --exit-zero --output-format gitlab --output-file $(REPORTS_DIR)/gl-code-quality.json

sast-report: reports-dir
	bandit -q -c pyproject.toml -r app -f json -o $(REPORTS_DIR)/bandit.json --exit-zero

db-wait:
	python scripts/db_wait.py

migrate-check:
	python scripts/migrate_check.py

backup-check:
	python scripts/backup_check.py

deps-audit:
	pip-audit -r requirements.txt --desc on --progress-spinner off

deps-audit-report: reports-dir
	pip-audit -r requirements.txt --desc on --progress-spinner off -f json -o $(REPORTS_DIR)/pip-audit.json

# Поиск секретов: рабочее дерево и коммиты ветки, которых нет в main.
secrets:
	docker run --rm -v "$(CURDIR):/repo" $(GITLEAKS_IMAGE) dir --redact --verbose --config /repo/.gitleaks.toml /repo
	docker run --rm -v "$(CURDIR):/repo" $(GITLEAKS_IMAGE) git --redact --verbose --config /repo/.gitleaks.toml --log-opts="$(GUARD_BASE)..HEAD" /repo

# Анализ образа приложения: уязвимости HIGH/CRITICAL с исправлением и секреты.
image-scan:
	docker build -t $(IMAGE) .
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$(CURDIR):/repo" $(TRIVY_IMAGE) image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed --ignorefile /repo/.trivyignore $(IMAGE)

# Защита критических файлов: сравнение с main (в CI: GUARD_ARGS=--ci).
guard:
	python scripts/ci_guard.py --base $(GUARD_BASE) $(GUARD_ARGS)

# Полный прогон обязательных проверок pipeline на своей машине.
ci-local: verify migrate-check backup-check deps-audit secrets guard container-check
