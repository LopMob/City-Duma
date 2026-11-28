.PHONY: setup db-init run test quality sast migrate verify backup restore \
	container-init container-up container-down container-logs container-migrate \
	container-backup container-restore container-restore-new container-lint container-check \
	reports-dir quality-report sast-report db-wait migrate-check backup-check \
	deps-audit deps-audit-report secrets image-scan guard ci-local release-check \
	infra-setup infra-lint infra-check infra-apply infra-vault \
	monitoring-up monitoring-down monitoring-check logs snapshot

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

# ---- ЛР6: проверка работающего релиза (подробности — docs/CD.md) ----

STAGING_URL ?= http://192.168.58.139:8800
EXPECT ?=

# Проверка развёрнутой версии: /health, /version (= EXPECT), основной сценарий.
release-check:
	python scripts/release_check.py --url $(STAGING_URL) --expect "$(EXPECT)"

# ---- ЛР7: управление конфигурацией, Ansible (подробности — docs/ANSIBLE.md) ----

# Окружение: staging (по умолчанию) или production.
ENV ?= staging
ANSIBLE_VENV ?= .venv-ansible
ANSIBLE_BIN ?= $(CURDIR)/$(ANSIBLE_VENV)/bin
# Файл с паролем ansible-vault (в git не попадает; в CI — защищённая переменная).
VAULT_PASS_FILE ?= $(HOME)/.vault_pass_city_duma
VAULT_FILE = inventories/$(ENV)/group_vars/$(ENV)/vault.yml
# Адреса серверов и registry по умолчанию записаны в ansible/inventories/*/host_vars
# и ansible/group_vars/all.yml. На другой машине их можно переопределить:
#   make infra-apply ENV=production HOST=10.0.0.5 SSH_USER=admin \
#        REGISTRY_IMAGE=10.0.0.2:5050/root/city-duma
HOST ?=
SSH_USER ?=
REGISTRY_IMAGE ?=
PLAYBOOK = cd ansible && TARGET_HOST="$(HOST)" TARGET_USER="$(SSH_USER)" REGISTRY_IMAGE="$(REGISTRY_IMAGE)" \
	$(ANSIBLE_BIN)/ansible-playbook -i inventories/$(ENV)/hosts.yml \
	--vault-password-file $(VAULT_PASS_FILE) site.yml

# Отдельное окружение с Ansible и ansible-lint точных версий.
infra-setup:
	python3 -m venv $(ANSIBLE_VENV)
	$(ANSIBLE_BIN)/pip install -r ansible/requirements.txt

# Проверка инфраструктурного кода: нет открытых секретов, синтаксис, ansible-lint.
infra-lint:
	$(ANSIBLE_BIN)/python scripts/vault_check.py ansible
	cd ansible && $(ANSIBLE_BIN)/ansible-playbook -i inventories/staging/hosts.yml --syntax-check site.yml
	cd ansible && $(ANSIBLE_BIN)/ansible-playbook -i inventories/production/hosts.yml --syntax-check site.yml
	cd ansible && $(ANSIBLE_BIN)/ansible-lint

# Пробный запуск: что изменилось бы на серверах, без изменений.
infra-check:
	$(PLAYBOOK) --check --diff

# Привести серверы окружения к требуемому состоянию и проверить приложение.
infra-apply:
	$(PLAYBOOK)

# Создать или изменить зашифрованные секреты окружения.
infra-vault:
	cd ansible && if [ -f $(VAULT_FILE) ]; then \
		$(ANSIBLE_BIN)/ansible-vault edit --vault-password-file $(VAULT_PASS_FILE) $(VAULT_FILE); \
	else \
		$(ANSIBLE_BIN)/ansible-vault create --vault-password-file $(VAULT_PASS_FILE) $(VAULT_FILE); \
	fi

# ---- ЛР8: наблюдаемость (подробности — docs/OBSERVABILITY.md) ----

PROMETHEUS_IMAGE ?= prom/prometheus:v3.1.0
MONITORING = docker compose -p city-duma-monitoring -f monitoring/compose.monitoring.yaml \
	--env-file monitoring/.env
PROMETHEUS_URL ?= http://127.0.0.1:9090
SNAPSHOT_LABEL ?= snapshot

# Prometheus, Grafana, node-exporter и cAdvisor рядом с локальным приложением
# (сначала make container-up). Grafana: http://127.0.0.1:3000, Prometheus: :9090.
monitoring-up:
	@test -f monitoring/.env || { echo "Нет monitoring/.env: cp monitoring/.env.example monitoring/.env и задайте пароль"; exit 1; }
	$(MONITORING) up -d --wait

monitoring-down:
	$(MONITORING) down

# То же, что job monitoring:check в CI: синтаксис и тесты правил оповещения.
monitoring-check:
	docker run --rm -v "$(CURDIR)/monitoring/prometheus:/etc/prometheus:ro" -w /etc/prometheus \
		--entrypoint promtool $(PROMETHEUS_IMAGE) check config prometheus.yml
	docker run --rm -v "$(CURDIR)/monitoring/prometheus:/etc/prometheus:ro" -w /etc/prometheus \
		--entrypoint promtool $(PROMETHEUS_IMAGE) check rules alerts.yml
	docker run --rm -v "$(CURDIR)/monitoring/prometheus:/etc/prometheus:ro" -w /etc/prometheus \
		--entrypoint promtool $(PROMETHEUS_IMAGE) test rules alerts.test.yml

# Журнал приложения (JSON, по строке на событие). Фильтр: make logs | grep '"level": "ERROR"'
logs:
	$(COMPOSE) logs app --no-log-prefix --tail=200

# Снимок состояния до/после выпуска (ЛР8, п.12): docs/releases/<время>-<метка>.md
snapshot:
	python scripts/release_snapshot.py --url $(STAGING_URL) --prometheus $(PROMETHEUS_URL) \
		--label $(SNAPSHOT_LABEL)
