# ЛР6: соответствие заданию и сценарий защиты

Устройство и настройка — [`docs/CD.md`](CD.md).

## Соответствие пунктам задания

| № | Требование | Где реализовано |
|---|---|---|
| 1 | Отдельное тестовое окружение | staging: `/srv/city-duma-staging`, compose-проект `city-duma-staging`, порт 8800, своя БД и volume |
| 2 | Доступ pipeline через отдельную учётную запись | пользователь `deploy`, только SSH-ключ (`restrict`, `from=`), ключ — Protected-переменная `DEPLOY_SSH_KEY` |
| 3 | Образ из registry, без пересборки | `deploy.sh`: `docker pull` + `compose up --no-build`; `compose.deploy.yaml` — `image: ${APP_IMAGE}` |
| 4 | Настройки отдельно от образа | `/srv/city-duma-staging/.env` только на сервере; доступ к registry — deploy token у пользователя `deploy` |
| 5 | Автоматическое развёртывание после публикации | `deploy:staging` в стадии после `publish`, для `main` — автоматически |
| 6 | Ограничение веткой / тегом / подтверждением | только `main` (авто) и теги `vX.Y.Z` (ручное подтверждение); ключ доступен только защищённым ref |
| 7 | Проверка контейнеров, служебного адреса, версии, сценария | `verify:staging`: `deploy.sh status` + `make release-check` |
| 8 | `/version` | `app/main.py`, версия и коммит вшиваются в образ (`--build-arg`) |
| 9 | Выпуск не успешен при падении проверки | `mark-good` только после проверки; иначе job красный, `current` не меняется |
| 10 | Развёртывание тега и ручной возврат | pipeline тега (▶ deploy), `rollback:staging` с `ROLLBACK_TO` |
| 11 | Нет одновременных конфликтующих развёртываний | `resource_group: staging` + `flock` в `deploy.sh` (код 14) |
| 12 | Сквозной выпуск функции | `/version`: задача ISSUE-6 → ветка → `make verify` → MR и CI → образ → staging → проверка → тег `v0.6.0` |
| 13 | Неудачный выпуск и возврат | сценарии 3–4 ниже + `rollback:staging` |

## Подготовка

1. Последний pipeline `main` зелёный, `verify:staging` пройден,
   http://192.168.58.139:8800/version показывает текущую версию.
2. Открыты: *Build → Pipelines*, *Operate → Environments → staging*,
   терминал ВМ в каталоге клона репозитория (`~/city-duma`).
3. Есть хотя бы две проверенные версии (например, `v0.6.0` и более новая),
   чтобы было куда возвращаться.

Во всех сценариях, где проверка падает до замены контейнеров, сначала
покажите `/version`, а после ошибки — что версия не изменилась.

## 1. Неверный тег

Pipeline `main` → ▶ `rollback:staging` → Variables: `ROLLBACK_TO` = `v9.9.9`.
Ожидается: `образ ...:v9.9.9 не найден в registry: неверный тег? Работающая
версия не тронута` (код 12). `/version` — прежняя.

## 2. Отсутствующая переменная

На ВМ закомментировать пароль БД в настройках окружения:
```
sudo sed -i 's/^POSTGRES_PASSWORD=/#&/' /srv/city-duma-staging/.env
```
Retry `deploy:staging` в последнем pipeline `main`. Ожидается: `в .env не
задана переменная: POSTGRES_PASSWORD — развёртывание остановлено, работающая
версия не тронута` (код 10). Вернуть:
```
sudo sed -i 's/^#POSTGRES_PASSWORD=/POSTGRES_PASSWORD=/' /srv/city-duma-staging/.env
```
Вариант для CI: временно переименовать переменную `DEPLOY_SSH_KEY` в GitLab —
job сразу сообщит, что защищённой переменной нет.

## 3. Неуспешный healthcheck

Задать окружению заведомо недостаточный лимит памяти:
```
echo 'APP_MEM_LIMIT=20m' | sudo tee -a /srv/city-duma-staging/.env
```
Retry `deploy:staging`. Приложение не может запуститься, `deploy.sh` ждёт
healthy 120 с, выводит журнал контейнера и падает: `проверка здоровья не
пройдена — выпуск НЕ принят` (код 20); `verify:staging` не выполняется.
Убрать строку и вернуть версию (сценарий 6):
```
sudo sed -i '/^APP_MEM_LIMIT=/d' /srv/city-duma-staging/.env
```

## 4. Приложение запускается, но основной сценарий не работает

Повредить окружение — отобрать у приложения право добавлять депутатов:
```
sudo docker exec city-duma-staging-db-1 psql -U duma -d city_duma -c "REVOKE INSERT ON deputies FROM duma;"
```
Retry `verify:staging`. Контейнеры healthy, `/health` и `/version` в порядке,
но сценарий падает: `создание депутата: ожидался HTTP 201, получен 500` —
выпуск не принят. Вернуть права:
```
sudo docker exec city-duma-staging-db-1 psql -U duma -d city_duma -c "GRANT INSERT ON deputies TO duma;"
```

## 5. Недоступный registry

```
sudo gitlab-ctl stop registry
```
▶ `rollback:staging` с `ROLLBACK_TO` = тег предыдущей версии (или Retry
`deploy:staging`). Ожидается: `registry недоступен ... Работающая версия не
тронута` (код 13). Вернуть: `sudo gitlab-ctl start registry`.

## 6. Возврат предыдущей версии

После сценария 3 (или любого неудачного выпуска) — ▶ `rollback:staging` без
переменных. `deploy.sh` берёт последнюю проверенную версию (`state/current`),
разворачивает её и проверка релиза проходит. В *Operate → Environments →
staging* виден новый деплой, на сервере — запись `rollback` в журнале:
```
sudo -iu deploy bash -s -- history < deploy/staging/deploy.sh
```

## Вопросы

- **Чем выпуск отличается от сборки?** Образ может собраться и пройти тесты,
  но не работать в окружении (настройки, БД, ресурсы). Поэтому версия
  принимается только после проверки в staging.
- **Почему образ не собирается на сервере?** Иначе в staging работал бы не
  тот образ, который проверил CI; тег связывает выпуск с коммитом.
- **Почему проверка неверного тега/registry/переменной не роняет сервис?**
  Все такие проверки в `deploy.sh` выполняются до замены контейнеров.
- **Как исключены параллельные выпуски?** `resource_group` ставит jobs в
  очередь, `flock` на сервере защищает и от ручного запуска.
