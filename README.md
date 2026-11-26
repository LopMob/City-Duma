# Городская Дума — учёт депутатов, комиссий, заседаний и посещаемости

Минимальное веб-приложение (HTTP API + реляционная БД) для учёта
депутатов городской думы, комиссий, их председателей, заседаний и
посещаемости. Разработано командой из трёх человек как сквозной проект (ЛР1).

## Технологии

- Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic
- PostgreSQL (единственная поддерживаемая СУБД; драйвер psycopg2)
- pytest + pytest-cov, ruff, black, bandit

## Разделение работы по команде

| Участник | Задача |
|---|---|
| [LopMob](https://github.com/LopMob) | Депутаты и комиссии |
| [NoName1226](https://github.com/NoName1226) | Состав комиссий и председатели |
| [stepanstoliarzh](https://github.com/stepanstoliarzh) | Заседания и посещаемость |

Подробнее — в [`docs/TASKS.md`](docs/TASKS.md), критерии приёмки по
каждой задаче — в [`docs/tasks/`](docs/tasks/). Каждая задача сделана в
отдельной ветке и влита в `main` через отдельный Pull Request.

## Веб-интерфейс

Обязателен по заданию ЛР1. Лёгкий статический дашборд без сборки —
`http://localhost:8000/` (редиректит на `/ui/`): сводка и разделы
«Депутаты», «Комиссии», «Состав комиссий», «Заседания и посещаемость».
Подробности — [`docs/UI.md`](docs/UI.md).

## Сущности и бизнес-правила

Пять сущностей: депутаты, комиссии, состав комиссий (с председателем),
заседания, посещаемость. Бизнес-правила:


- у комиссии не больше одного председатель;
- депутат не может быть дважды включён в одну комиссию;
- заседание комиссии нельзя провести без кворума (присутствует больше
  половины членов комиссии);
- на одном заседании у депутата только одна отметка посещаемости.

Модель данных — [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md), API —
[`docs/API.md`](docs/API.md), техническое задание —
[`docs/TZ.md`](docs/TZ.md).

## Быстрый старт

Нужен установленный и запущенный PostgreSQL (локально или на сервере).

```
make setup      # venv + зависимости + .env из .env.example (Linux)
                # затем в .env указать пароль PostgreSQL в DATABASE_URL
make db-init    # создать рабочую БД и тестовую <имя>_test
make migrate    # применить миграции (создать таблицы)
make run        # запустить сервер на http://localhost:8000
```

В Windows `make setup` заменяется созданием venv в IDE и
`pip install -r requirements.txt`; остальные команды те же.

Проверка: `curl http://localhost:8000/health`.
Интерактивная документация: `http://localhost:8000/docs`.

## Команды разработки

```
make setup     # первоначальная настройка
make run       # локальный запуск
make db-init   # создать рабочую и тестовую БД PostgreSQL
make test      # автоматические тесты (нужен запущенный PostgreSQL)
make quality   # форматирование и линтер (ruff + black)
make sast      # статический анализ безопасности (bandit)
make migrate   # применение миграций
make backup    # резервная копия БД (pg_dump)
make restore FILE=backups/duma_<ts>.sql   # восстановление (psql)
make verify    # quality + sast + test, обязателен перед PR
make container-check   # проверка контейнерного окружения (Docker), см. ниже
```

## Запуск в контейнерах (ЛР4)

Приложение и PostgreSQL запускаются в Docker Compose: БД во внутренней сети без
публикации порта, данные в volume, миграции отдельным контейнером, приложение
от непривилегированного пользователя. Нужен Docker (Docker Desktop / Engine).

```
make container-up          # создать .env.compose (случайный пароль), собрать и запустить
make container-logs        # логи
make container-backup      # резервная копия БД из контейнера
make container-restore-new FILE=backups/container_duma_<ts>.sql   # восстановить в НОВЫЙ volume
make container-check       # автоматическая проверка окружения и запретов
make container-down        # остановить (данные в volume сохраняются)
```

Приложение: `http://localhost:8000` (только с этого компьютера). Подробности,
лимиты ресурсов и сценарий показа — [`docs/CONTAINER.md`](docs/CONTAINER.md).

## Continuous Integration (ЛР5)

Каждый push, merge request и изменение `main` проходят pipeline GitLab CI/CD
([`.gitlab-ci.yml`](.gitlab-ci.yml)): стиль → тесты → миграции → резервное
копирование → безопасность (bandit, pip-audit, gitleaks) → сборка образа →
`make container-check` и анализ образа (trivy) → защита критических файлов →
публикация образа с тегом commit SHA. Образ публикуется, только если прошло всё.

CI выполняет те же make-команды, что и разработчик; полный прогон локально:

```
make migrate-check   # миграции: одна голова, БД с данными, откат/накат
make backup-check    # копия -> повреждение -> восстановление (во временной БД)
make deps-audit      # уязвимости зависимостей (pip-audit)
make secrets         # поиск секретов (gitleaks, нужен Docker)
make image-scan      # анализ образа (trivy, нужен Docker)
make guard           # что из критических файлов изменено и нет ли ослаблений
make ci-local        # всё перечисленное + verify + container-check
```

Как устроен pipeline и как настроить GitLab — [`docs/CI.md`](docs/CI.md);
сценарий защиты — [`docs/LR5_DEFENSE.md`](docs/LR5_DEFENSE.md).

## Continuous Delivery (ЛР6)

После успешной публикации образ из `main` автоматически разворачивается в
окружении staging (http://192.168.58.139:8800) и принимается только после
проверки работающего релиза: контейнеры, `/health`, `/version` и основной
сценарий. Теги `vX.Y.Z` разворачиваются по ручному подтверждению, возврат
предыдущей версии — ручной job `rollback:staging`.

```
make release-check STAGING_URL=http://192.168.58.139:8800 EXPECT=v0.6.0
```

Устройство и настройка — [`docs/CD.md`](docs/CD.md), сценарий защиты —
[`docs/LR6_DEFENSE.md`](docs/LR6_DEFENSE.md).

## Управление конфигурацией (ЛР7)

Серверы окружений staging и production настраиваются кодом (Ansible,
каталог `ansible/`): пакеты, пользователи, SSH, Docker, iptables, настройки
и запуск приложения с проверкой после настройки. Повторный запуск ничего не
меняет, ручные правки откатывает. Секреты — только в зашифрованных `vault.yml`.

```
make infra-setup                     # Ansible в .venv-ansible
make infra-lint                      # секреты, синтаксис, ansible-lint
make infra-apply ENV=production      # привести сервер к нужному состоянию
```

Устройство и настройка — [`docs/ANSIBLE.md`](docs/ANSIBLE.md), сценарий защиты —
[`docs/LR7_DEFENSE.md`](docs/LR7_DEFENSE.md).

## Git-процесс

Правила ветвления, коммитов и приёмки PR — в
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Структура репозитория

```
app/            — исходный код приложения (FastAPI, модели, схемы, роутеры)
alembic/        — миграции схемы БД
tests/          — автоматические тесты (unit, API, интеграционные)
scripts/        — резервное копирование/восстановление БД, init, демо-данные,
                  проверки CI (migrate-check, backup-check, ci_guard)
.gitlab/        — CODEOWNERS (критические файлы) и шаблон merge request
.gitlab-ci.yml  — pipeline GitLab CI/CD (ЛР5–7)
ansible/        — управление конфигурацией серверов (ЛР7)
docs/           — ТЗ, модель данных, разделение задач по команде
Makefile        — единый командный интерфейс проекта
```
