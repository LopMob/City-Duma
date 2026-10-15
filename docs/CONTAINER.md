# Контейнерное окружение (ЛР4)

Приложение «Городская Дума» и PostgreSQL запускаются в Docker Compose.
Локальная работа из ЛР3 (`make run`, `make verify`, `make backup`) остаётся как
была — контейнерные команды начинаются с `container-` и не пересекаются с ней.

## Состав

| Файл | Назначение |
|---|---|
| `Dockerfile` | Образ приложения: закреплённая версия Python, зависимости из `requirements-runtime.txt`, пользователь `app` (UID 10001), `HEALTHCHECK`, `STOPSIGNAL SIGTERM` |
| `.dockerignore` | В образ не попадают `.env*`, `.git`, `backups`, тесты, документация |
| `compose.yaml` | Сервисы `db`, `migrate`, `app`, сети `backend` (internal) и `frontend`, volume `pgdata` |
| `.env.compose` | Пароль БД и порт — создаётся `make container-init`, в git не попадает |
| `scripts/container_db.py` | Резервное копирование и восстановление внутри контейнера |
| `scripts/container_check.py`, `scripts/container_policy.py` | `make container-check` и правила запретов |

```
 хост ──127.0.0.1:8000──► app ──(сеть backend, internal)──► db ──► volume pgdata
                          ▲                                  (порт 5432 наружу НЕ опубликован)
        migrate (разово: alembic upgrade head, затем завершается)
```

## Соответствие заданию

| № | Требование | Как выполнено |
|---|---|---|
| 1 | Dockerfile и .dockerignore | `Dockerfile`, `.dockerignore` |
| 2 | Воспроизводимая сборка | образы `python:3.11.13-slim-bookworm` и `postgres:16.10-alpine` закреплены точной версией; Python-зависимости — точные версии `==` (`requirements-runtime.txt` синхронизирован с `requirements.txt`, это проверяет `container-check`); слой зависимостей кешируется |
| 3 | Не root | `USER 10001:10001`; в compose `cap_drop: [ALL]`, `no-new-privileges`, `read_only` файловая система (запись только в `tmpfs /tmp`) |
| 4 | Настройки через переменные окружения | `DATABASE_URL`, `ENVIRONMENT` задаёт compose из `.env.compose`; приложение читает их через `pydantic-settings`; в образе секретов нет |
| 5 | Корректное завершение | `CMD` в exec-форме (uvicorn — PID 1 и сам получает `SIGTERM`), `STOPSIGNAL SIGTERM`, `--timeout-graceful-shutdown 15`, `stop_grace_period: 20s` |
| 6 | Compose для приложения и БД | `compose.yaml` |
| 7 | Сеть, volume, healthcheck, порядок | сеть `backend` с `internal: true`; named volume `pgdata`; healthcheck у `db` (`pg_isready`) и `app` (`/health`); `app` ждёт `db: service_healthy` и `migrate: service_completed_successfully` |
| 8 | Порт БД не публикуется | у `db` нет `ports`; опубликован только `127.0.0.1:8000 → app` |
| 9 | Данные после пересоздания/обновления образа | volume `pgdata` не удаляется при `down`/`--force-recreate`; проверяет `container-check` |
| 10 | Миграции, backup, restore в контейнерах | `make container-migrate`, `make container-backup`, `make container-restore`, `make container-restore-new` |
| 11 | Лимиты ресурсов | см. ниже |
| 12 | `make container-check` | `scripts/container_check.py` |
| 13 | Запреты | `CONTRIBUTING.md` + автоматические правила `scripts/container_policy.py` |
| 14 | Изменения только после container-check | `CONTRIBUTING.md`, п. 2 и Definition of Done |

## Лимиты ресурсов и обоснование

| Сервис | memory | cpus | pids | Почему |
|---|---|---|---|---|
| `app` | 256 МиБ | 0.5 | 100 | uvicorn + SQLAlchemy в покое занимают порядка 100 МиБ; 256 МиБ — запас на нагрузку. Утечка памяти или «форк-бомба» упрётся в лимит и не заберёт хост |
| `db` | 512 МиБ | 1.0 | 200 | `shared_buffers` по умолчанию 128 МиБ плюс память на соединения; для учебных объёмов данных этого достаточно с запасом |
| `migrate` | 256 МиБ | 0.5 | 100 | разовая задача, живёт секунды |

Значения скорректируйте по `docker stats`, если нагрузка изменится.

## Первый запуск

```
make container-up       # создаёт .env.compose, собирает образ, запускает db → migrate → app
docker compose --env-file .env.compose ps
curl http://localhost:8000/health
```

`make container-down` останавливает окружение, данные остаются в volume.
Чтобы стереть данные, нужно явно удалить volume: `docker compose --env-file .env.compose down -v`.

## Что проверяет `make container-check`

Статика (без Docker, `make container-lint`): правила запретов применяются к `Dockerfile`,
`.dockerignore`, `compose.yaml`, `requirements-*.txt` и списку файлов git.

Динамика (нужен Docker; окружение поднимается в отдельном проекте `city-duma-check` со своим
volume, паролем и портом — рабочие данные не затрагиваются):

1. сборка образа и запуск, `db` и `app` в статусе `healthy`;
2. миграции выполнены контейнером `migrate` (код выхода 0);
3. процесс приложения запущен не от root (`id -u` и UID PID 1);
4. порт БД не опубликован на хост;
5. лимиты ресурсов применены;
6. приложение отвечает, запись в БД создаётся;
7. запись сохранилась после `down` → `up` (пересоздание контейнеров);
8. запись сохранилась после пересборки образа (`up --build --force-recreate`);
9. остановка по SIGTERM штатная: код выхода 0 (или 143), быстрее `stop_grace_period`, в логах `Shutting down`;
10. резервная копия → `down -v` → восстановление в новый volume: запись и версия миграции 0004 на месте.

Если Docker недоступен, динамические проверки считаются проваленными, а не пропущенными.

## Запреты (п. 13) и где они закреплены

| Запрет | Правило в `container_policy.py` |
|---|---|
| `latest` или образ без тега | `FROM` в Dockerfile и `image:` в compose |
| запуск от root | нет `USER` / `USER root` / `user: root` / `privileged` |
| секреты в образе | `ENV`/`ARG` с `PASSWORD`/`TOKEN`/`SECRET`…, `COPY .env`; пароли литералами в compose; `.env*` в git |
| отключение healthcheck | `HEALTHCHECK NONE`, `healthcheck: disable`, отсутствие healthcheck |
| лишние порты | любой `ports` у `db`; у остальных — кроме 8000 приложения |

Каждое правило покрыто тестом, который намеренно ломает конфигурацию:
`tests/unit/test_container_policy.py`.

## Резервная копия и восстановление

```
make container-backup                                          # backups/container_duma_<время>.sql
make container-restore FILE=backups/container_duma_<время>.sql        # в работающую БД
make container-restore-new FILE=backups/container_duma_<время>.sql   # в НОВЫЙ volume
```

`pg_dump` и `psql` запускаются внутри контейнера `db` (`docker compose exec`), на хосте
клиентские утилиты PostgreSQL не нужны, пароль в командную строку не попадает.
Копия делается с `--clean --if-exists`, восстановление — `psql -v ON_ERROR_STOP=1
--single-transaction`: либо БД целиком вернулась к состоянию копии, либо не изменилась.
`restore-new` удаляет контейнеры и volume (`down -v`), поднимает пустую БД на новом
volume, накатывает копию и запускает приложение.
