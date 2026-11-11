# Continuous Delivery (ЛР6): развёртывание и проверка релиза

Успешная сборка не равна успешному релизу. Образ, прошедший CI (ЛР5),
автоматически разворачивается в окружении **staging** и принимается только
после проверки работающей версии в этом окружении.

## 1. Схема

```
... -> publish -> deploy:staging -> verify:staging -> выпуск принят
                     |                   |
              образ из registry   контейнеры, /health, /version,
              (без сборки)        основной сценарий; иначе — не принят
                                         |
                              rollback:staging (вручную)
```

| Что | Реализация |
|---|---|
| Окружение staging | ВМ 192.168.58.139, каталог `/srv/city-duma-staging`, compose-проект `city-duma-staging`, порт **8800** |
| Доступ pipeline | учётная запись `deploy`, вход только по SSH-ключу (`restrict`, `from=` сети Docker); ключ — защищённая переменная |
| Образ | `docker pull` из registry по тегу; запуск `compose up --no-build` — на сервере ничего не собирается |
| Настройки | `/srv/city-duma-staging/.env` — только на сервере (пароль БД, порт); в образе и git их нет |
| Когда развёртывается | автоматически после успешного `publish:image` в `main`; тег `vX.Y.Z` — после ручного подтверждения |
| Версия | `GET /version` → `{"version", "commit", "environment"}`; значения вшиваются в образ при сборке |
| Проверка релиза | `deploy.sh status` (db и app healthy, migrate = 0) + `make release-check` |
| Неудачная проверка | job красный, `mark-good` не выполняется — выпуск не принят |
| Возврат | ручной `rollback:staging`: к последней проверенной версии или к `ROLLBACK_TO=<тег>` |
| Конфликты | `resource_group: staging` в CI + `flock` в `deploy.sh` |

Журнал выпусков на сервере: `/srv/city-duma-staging/state/releases.log`
(`deployed`, `verified`, `failed-health`, `rollback`), там же `current`
(последняя проверенная версия) и `previous`.

## 2. Файлы

| Файл | Назначение |
|---|---|
| `compose.deploy.yaml` | поверх `compose.yaml`: образ `${APP_IMAGE}` из registry вместо сборки, `ENVIRONMENT`, лимит памяти из `.env` |
| `deploy/staging/deploy.sh` | выполняется на сервере: `deploy`, `rollback`, `status`, `mark-good`, `history` |
| `deploy/staging/setup-server.sh` | однократная подготовка сервера: пользователь `deploy`, ключ, каталог, `.env` |
| `scripts/release_check.py` | проверка релиза: `/health`, `/version`, `/ui/`, сценарий депутат → комиссия → заседание → кворум |
| `.gitlab-ci.yml` | стадии `deploy`, `verify`; jobs `deploy:staging`, `verify:staging`, `rollback:staging` |

Коды ошибок `deploy.sh` (все — до замены контейнеров, кроме 20–21):

| Код | Причина | Работающая версия |
|---|---|---|
| 10 | в `.env` нет обязательной переменной | не тронута |
| 11 | некорректная ссылка на образ | не тронута |
| 12 | образа с таким тегом нет в registry | не тронута |
| 13 | registry недоступен / отказал в доступе | не тронута |
| 14 | уже идёт другое развёртывание | не тронута |
| 15 | нет предыдущей проверенной версии для отката | не тронута |
| 20 | приложение не стало healthy | новая версия не принята → rollback |
| 21 | контейнеры не запустились (например, миграции) | новая версия не принята → rollback |

## 3. Настройка (один раз)

Всё на ВМ с GitLab, под администратором (`vboxuser`).

**1. Ключ pipeline:**
```
ssh-keygen -t ed25519 -N "" -C gitlab-ci-deploy -f ~/deploy_key
```

**2. Окружение и учётная запись** (из клона репозитория, где есть `deploy/`):
```
sudo bash deploy/staging/setup-server.sh "$(cat ~/deploy_key.pub)"
```

**3. Доступ deploy к registry.** GitLab: *Settings → Repository → Deploy
tokens → Add token*: имя `staging-pull`, scope **read_registry**. Затем:
```
sudo -iu deploy docker login 192.168.58.139:5050 -u <username токена>
```
(пароль — значение токена). Учётные данные сохраняются у пользователя
`deploy` на сервере, в pipeline их нет.

**4. Переменные GitLab** (*Settings → CI/CD → Variables*), обе — тип **File**,
флаг **Protect variable**, без Expand:

| Key | Значение |
|---|---|
| `DEPLOY_SSH_KEY` | содержимое `~/deploy_key` (`cat ~/deploy_key`), включая строки BEGIN/END и перевод строки в конце |
| `DEPLOY_KNOWN_HOSTS` | вывод `ssh-keyscan -t ed25519 192.168.58.139` |

Protected — значит переменные получают только pipeline защищённых веток и
тегов: из MR и feature-веток развернуть ничего нельзя.

**5. Защищённые теги:** *Settings → Repository → Protected tags* → `v*`,
Allowed to create: Maintainers. Иначе pipeline тега не получит ключ.

После слияния в `main` первый pipeline сам развернёт приложение:
*Operate → Environments → staging* покажет историю выпусков, а приложение
откроется по адресу http://192.168.58.139:8800.

## 4. Работа

**Выпуск из main** — автоматически после слияния MR: `publish` →
`deploy:staging` → `verify:staging`.

**Выпуск тега:** создать тег `vX.Y.Z` от `main` (*Code → Tags*). В pipeline
тега job `deploy:staging` ждёт ручного подтверждения (▶). После него —
автоматическая проверка.

**Возврат:** в любом pipeline `main` запустить ▶ `rollback:staging`:
- без переменных — к последней проверенной версии;
- с переменной `ROLLBACK_TO` = `v0.6.0` или короткий SHA — к указанной.

Возврат проверяется так же, как обычный выпуск.

**Проверка релиза вручную** (с любой машины, где есть Python):
```
make release-check STAGING_URL=http://192.168.58.139:8800 EXPECT=v0.6.0
```

**Состояние на сервере** (из клона репозитория на ВМ — так же, как это
делает pipeline):
```
sudo -iu deploy bash -s -- status  < deploy/staging/deploy.sh
sudo -iu deploy bash -s -- history < deploy/staging/deploy.sh
```

Образы до ЛР6 (`v0.5.0` и раньше) не имеют `/version`, поэтому проверку
релиза не пройдут — возвращаться имеет смысл к версиям начиная с `v0.6.0`.
