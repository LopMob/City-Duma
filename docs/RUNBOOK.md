# Диагностика и восстановление (ЛР8, п.9)

Краткая инструкция дежурного: что делать, когда сработало оповещение или
пользователи жалуются. Устройство журналов и метрик — [`OBSERVABILITY.md`](OBSERVABILITY.md).

## 0. Подготовка: имена и адреса

На сервере окружения, под пользователем `deploy`:
```
sudo -iu deploy
P=city-duma-staging          # production: city-duma-production
APP=$P-app-1                 # контейнер приложения
DB=$P-db-1                   # контейнер PostgreSQL
DIR=/srv/$P                  # каталог окружения (.env, state/, deploy.sh)
```

| Что | staging | production |
|---|---|---|
| Приложение | http://192.168.58.139:8800 | http://192.168.58.140:8000 |
| Grafana (панель «Городская Дума») | http://192.168.58.139:3000 | http://192.168.58.140:3000 |
| Prometheus (вкладка *Alerts*) | http://192.168.58.139:9090 | http://192.168.58.140:9090 |

## 1. Общий порядок

1. **Что сломалось.** Откройте панель Grafana, строку «Доступность»: какие
   оповещения сработали, жив ли процесс (`up`), доступна ли БД (`app_db_up`).
2. **Какой компонент.** Сопоставьте признаки:

   | Признак | Компонент |
   |---|---|
   | `up{job="app"}` = 0 | контейнер приложения / сеть Docker |
   | `up` = 1, `app_db_up` = 0, `/ready` → 503 | PostgreSQL или связь с ним |
   | `up` = 1, `/ready` = 200, но снаружи не открывается | порт, firewall, сеть хоста |
   | растут 5xx, есть `app_unhandled_exceptions_total` | код новой версии |
   | перезапуски, память у лимита | ресурсы контейнера (OOM) |
   | мало места на диске | хост |

3. **Подтвердите причину журналом.** Каждая строка журнала — JSON:
   ```
   docker logs --since 15m $APP 2>&1 | grep '"level": "ERROR"'
   docker logs --since 15m $APP 2>&1 | grep '"event": "db_unavailable"'
   # всё по одному запросу (id пользователь видит в заголовке X-Request-ID и в ответе 500):
   docker logs --since 1h $APP 2>&1 | grep '"request_id": "<id>"'
   ```
   С `jq` (`sudo apt install -y jq`) удобнее:
   `docker logs --since 15m $APP 2>&1 | jq -R 'fromjson? | select(.result != "success")'`.
4. **Подтвердите метрикой.** График на панели или запрос в Prometheus. Причина
   подтверждена, когда журнал и метрика говорят одно и то же.
5. **Восстановите** (разделы ниже).
6. **Проверьте выпуск:** `make release-check STAGING_URL=<адрес> EXPECT=<версия>`
   и снимок `make snapshot SNAPSHOT_LABEL=after-recovery`.
7. **Запишите инцидент** по образцу [`incidents/INCIDENT-001`](incidents/INCIDENT-001-db-down-health-ok.md)
   и решите, какой барьер добавить, чтобы такое обнаруживалось раньше.

Если конфигурация сервера могла поменяться (права, `.env`, firewall), быстрее
всего вернуть её Ansible (ЛР7): `make infra-check ENV=...` покажет, что
отличается, а `make infra-apply ENV=...` восстановит.

## 2. Оповещения

<a id="AppDown"></a>
### AppDown — приложение не отвечает

Prometheus больше минуты не получает метрики с `app:8000`.

```
docker ps -a --filter name=$APP --format '{{.Names}} {{.Status}}'
docker logs --tail 50 $APP
docker inspect $APP --format 'OOM={{.State.OOMKilled}} exit={{.State.ExitCode}} restarts={{.RestartCount}}'
```
- контейнер остановлен → `docker start $APP`;
- падает при запуске (в журнале ошибка конфигурации) → верните настройки:
  `make infra-apply ENV=...`;
- `OOM=true` → см. [AppMemoryNearLimit](#AppMemoryNearLimit);
- новая версия не запускается → откат: pipeline `main` → ▶ `rollback:staging`.

<a id="AppDatabaseDown"></a>
### AppDatabaseDown — нет связи с базой данных

Процесс жив, `/health` = 200, но `/ready` = 503, `app_db_up` = 0; в журнале
`"event": "db_unavailable"`, API отвечает 500.

```
docker ps -a --filter name=$DB --format '{{.Names}} {{.Status}}'
docker logs --tail 30 $DB
curl -s http://127.0.0.1:${APP_PORT:-8800}/ready
```
- БД остановлена → `docker start $DB`, через 10–20 секунд `/ready` = 200
  (соединения восстанавливаются сами, `pool_pre_ping`);
- в журнале БД `password authentication failed` → неверный пароль в `.env`:
  `make infra-apply ENV=...` (пароль из vault);
- в журнале БД `No space left on device` → см. [HostDiskSpaceLow](#HostDiskSpaceLow).

<a id="AppHighErrorRate"></a>
### AppHighErrorRate — больше 5% ответов 5xx

```
docker logs --since 10m $APP 2>&1 | grep '"result": "server_error"\|"result": "exception"'
```
На панели «Ответы по кодам» и «p95 по маршрутам» видно, какой адрес ломается.
Ошибки начались сразу после выпуска → откат (`rollback:staging`), затем
исправление через MR. Ошибки вместе с `AppDatabaseDown` → чините БД.

<a id="AppUnhandledExceptions"></a>
### AppUnhandledExceptions — необработанные исключения

Ответ 500 с `request_id`; полный стек — в журнале:
```
docker logs --since 10m $APP 2>&1 | grep '"result": "exception"'
```
Это ошибка кода: заведите задачу, напишите тест, который её воспроизводит,
исправьте через MR. Если ошибок много — откатите версию.

<a id="AppSlowResponses"></a>
### AppSlowResponses — медленные ответы

Панель «p95 по маршрутам» показывает медленный адрес; «CPU контейнеров» и
«Память приложения» — не упёрлось ли в лимиты (`cpus: 0.5`). Частая причина —
медленная или перегруженная БД: `docker stats --no-stream`.

<a id="AppRestarted"></a>
### AppRestarted — приложение перезапустилось

```
docker inspect $APP --format 'OOM={{.State.OOMKilled}} restarts={{.RestartCount}} started={{.State.StartedAt}}'
docker logs --since 20m $APP 2>&1 | grep '"event": "app_st'
```
`app_stopping` перед `app_started` — штатная остановка (выпуск, `docker restart`).
Без `app_stopping` — процесс убит: `OOM=true` → [AppMemoryNearLimit](#AppMemoryNearLimit),
иначе ищите ошибку в журнале перед перезапуском.

<a id="AppMemoryNearLimit"></a>
### AppMemoryNearLimit — память у лимита

```
docker stats --no-stream $APP
grep APP_MEM_LIMIT $DIR/.env
sudo dmesg | grep -i -E 'oom|killed process' | tail
```
Лимит изменён вручную → `make infra-apply ENV=...` вернёт значение из
`app_mem_limit` (ЛР7). Память растёт сама при обычной нагрузке → увеличьте
`app_mem_limit` в `ansible/inventories/<окр>/group_vars/<окр>/main.yml` через MR.

<a id="SecretInLogs"></a>
### SecretInLogs — в журнал пытались записать секрет

Значение уже замаскировано (`***`) и в журнал не попало. Найдите, откуда:
```
docker logs --since 15m $APP 2>&1 | grep -F '***'
```
Секрет пришёл в адресе запроса (кто-то отправил пароль в URL) — сообщите
отправителю и смените секрет. Секрет передал код в журнал — исправьте код
через MR и добавьте случай в `tests/unit/test_observability_logs.py`.

<a id="HostDiskSpaceLow"></a>
### HostDiskSpaceLow — мало места на диске

```
df -h /
sudo du -xh --max-depth=2 /var | sort -h | tail -15
docker system df
```
Освободите место: удалите лишнее (старые файлы, `sudo journalctl --vacuum-size=200M`),
неиспользуемые образы — `docker image prune -a` (осторожно: только не нужные
для отката). Данные PostgreSQL (`docker volume`) не удаляйте. Затем проверьте
`/ready` и журнал БД.

<a id="MonitoringTargetDown"></a>
### MonitoringTargetDown — не собираются метрики хоста или контейнеров

```
cd /srv/city-duma-monitoring && docker compose -p city-duma-monitoring ps
docker compose -p city-duma-monitoring logs --tail 30 node-exporter cadvisor
```
Перезапуск: `docker compose -p city-duma-monitoring up -d --wait` (или
`make infra-apply ENV=...`). На работу приложения это не влияет — но
панель «Ресурсы» в это время неполная.

## 3. Сценарии защиты: как вызвать, обнаружить и исправить

Полный сценарий с ожидаемым результатом — [`LR8_DEFENSE.md`](LR8_DEFENSE.md).

| № | Сценарий | Как вызвать | Обнаружение | Восстановление |
|---|---|---|---|---|
| 1 | Остановка БД | `docker stop $DB` | AppDatabaseDown, `/ready` 503, журнал `db_unavailable` | `docker start $DB` |
| 2 | Неверная конфигурация | `sed -i 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=wrong/' $DIR/.env` и `$DIR/deploy.sh deploy $(cat $DIR/state/current)` | выпуск остановлен (`deploy.sh`), AppDatabaseDown; журнал БД `password authentication failed` | `make infra-apply ENV=...` |
| 3 | Нехватка места на диске | `fallocate` до 95% (см. LR8_DEFENSE) | HostDiskSpaceLow, панель «Свободно на диске» | удалить файл, `df -h` |
| 4 | Перезапуск контейнера | `docker restart $APP` | AppRestarted, события `app_stopping`/`app_started` | не требуется; причина — `docker inspect` |
| 5 | Превышение памяти | `APP_MEM_LIMIT=20m` в `.env` + повторное развёртывание | AppRestarted / AppDown, `OOMKilled=true` | `make infra-apply ENV=...` |
| 6 | Ошибка новой версии | MR с ошибкой в правиле кворума | `make test` и CI красные; если дошло до staging — `verify:staging` красный, 5xx | откат `rollback:staging`, исправление через MR |
| 7 | Нарушение прав доступа | `sudo chmod 000 $DIR/.env` | `deploy.sh` останавливается, `make infra-check` показывает расхождение | `make infra-apply ENV=...` |
| 8 | Недоступный порт | правило `DROP` на порт приложения в iptables | снаружи `release-check` не проходит, а `up` = 1 — сломана сеть, не приложение; `city-duma-firewall check` | `sudo city-duma-firewall apply` или `make infra-apply` |
| 9 | Ошибка служебной проверки | `docker stop $DB` и `make release-check` | проверка релиза падает на шаге `/ready` | восстановить БД, повторить проверку |
| 10 | Утечка секрета в журнал | `curl '<адрес>/deputies/password=Secret123'` | SecretInLogs; в журнале `password=***`, `grep Secret123` пусто | найти источник, сменить секрет |
