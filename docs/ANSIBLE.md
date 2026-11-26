# Управление конфигурацией (ЛР7): Ansible

Сервер приложения больше не настраивается руками и одноразовыми скриптами:
его требуемое состояние описано кодом в каталоге `ansible/`. Один запуск
приводит чистую Linux-машину к рабочему состоянию, повторный — ничего не
меняет, а ручная правка управляемого параметра откатывается следующим запуском.

```
make infra-apply ENV=staging       # или ENV=production
```

Сценарий защиты — [`docs/LR7_DEFENSE.md`](LR7_DEFENSE.md).

## 1. Схема

```
машина управления (клон репозитория + Ansible)
        |  SSH по ключу, sudo
        v
 base -> docker -> firewall -> app -> проверка /health, /version, /ui/
```

| Что | Реализация |
|---|---|
| Окружение staging | узел `staging-1`, ВМ 192.168.58.139 (GitLab, registry, runner из ЛР5–6), администратор `vboxuser`, порт **8800** |
| Окружение production | узел `prod-1`, отдельная **чистая** ВМ 192.168.58.140 (Debian 12 без графики), администратор `admin`, порт **8000** |
| Машина управления | любой Linux с клоном репозитория, например ВМ с GitLab; в CI — job runner'а |
| Образ | из registry `192.168.58.139:5050/root/city-duma`, на сервере не собирается |
| Секреты | `vault.yml` каждого окружения, зашифрован `ansible-vault` |
| Проверка | в конце каждого запуска, снаружи — с машины управления |

Адреса и пользователи выше — значения по умолчанию. На другой машине их
можно переопределить при запуске, ничего не меняя в коде:
```
make infra-apply ENV=production HOST=10.0.0.5 SSH_USER=admin \
     REGISTRY_IMAGE=10.0.0.2:5050/root/city-duma
```

## 2. Файлы

| Файл | Назначение |
|---|---|
| `ansible/ansible.cfg` | настройки Ansible: инвентарь по умолчанию, роли, pipelining |
| `ansible/requirements.txt` | точные версии `ansible-core` и `ansible-lint` |
| `ansible/.ansible-lint` | профиль `production` |
| `ansible/site.yml` | плейбук: проверка секретов и ОС, роли по порядку |
| `ansible/group_vars/all.yml` | **общие** параметры |
| `ansible/inventories/<окр>/hosts.yml` | узлы окружения |
| `ansible/inventories/<окр>/group_vars/<окр>/main.yml` | параметры **окружения** |
| `ansible/inventories/<окр>/group_vars/<окр>/vault.yml` | **секретные** параметры (зашифрован; образец — `vault.yml.example`) |
| `ansible/inventories/<окр>/host_vars/<узел>.yml` | параметры **узла** |
| `ansible/roles/{base,docker,firewall,app}` | роли |
| `scripts/vault_check.py` | проверка, что секретов в открытом виде нет |

Каждый следующий уровень параметров переопределяет предыдущий: значения ролей
по умолчанию → общие → окружения и секреты → узла. Секреты называются
`vault_*`, задаются только в `vault.yml`, а в остальных файлах на них
ссылаются: `app_postgres_password: "{{ vault_postgres_password }}"`.

## 3. Роли

| Роль | Что делает |
|---|---|
| `base` | пакеты; администратор в группе `sudo` с ключами; SSH: вход под root и по паролю запрещён (шаблон `sshd_config.d/10-city-duma.conf`, проверка `sshd -t` до применения) |
| `docker` | Docker Engine и Compose из официального репозитория; `daemon.json` (registry по HTTP, ограничение журналов) |
| `firewall` | iptables (требование ЛР2): шаблон скрипта правил + служба systemd, которая применяет их при загрузке |
| `app` | пользователь `deploy` (без пароля, ключ pipeline с `restrict,from=`), каталог окружения с правами 750, `.env` 600 из шаблона, файлы compose и `deploy.sh`, доступ к registry; запуск и проверка приложения |

### Firewall

Скрипт `/usr/local/sbin/city-duma-firewall` меняет только свои цепочки:

- `CITY-DUMA-INPUT` (из `INPUT`): разрешены loopback, ответы на свои
  соединения, ICMP, SSH из сети сервера и с машины управления, порты из
  `firewall_open_ports`. Всё остальное записывается в журнал
  (`city-duma-drop:`) и отбрасывается;
- `CITY-DUMA-DOCKER` (из `DOCKER-USER`): порты контейнеров идут мимо `INPUT`,
  поэтому снаружи пропускается только опубликованный порт приложения.
  Порт БД не публикуется вообще.

`city-duma-firewall check` сравнивает действующие правила с применёнными.
Ansible запускает его при каждом прогоне, поэтому ручное `iptables -D ...`
обнаруживается и исправляется.

На staging ограничение `DOCKER-USER` выключено: GitLab и runner там сами
публикуют порты через Docker. Интерфейсы Docker (`docker0`, `br-+`) там
доверенные, потому что jobs ходят на хост за SSH (`deploy`) и в registry.
`daemon.json` на staging не перезаписывается: он уже настроен под GitLab.

### Приложение

Работающую версию Ansible **не меняет**: на staging её обновляет CI (ЛР6).
Если приложение не запущено (чистая машина, контейнеры удалены), роль
поднимает последний проверенный релиз (`state/current`), а если его нет —
`app_version`. Для этого вызывается тот же `deploy.sh`, что и в pipeline.
При изменении `.env` или compose-файлов контейнеры пересоздаются с тем же образом.

В конце роль проверяет приложение **с машины управления**: `/health`,
`/version` (окружение и версия) и `/ui/`. Если проверка не прошла, запуск
красный. Так заодно видно, что firewall пропускает клиентов.

## 4. Требования к инфраструктурному коду

1. **Лишних портов нет.** Снаружи доступны SSH (только из сети администрирования)
   и порт приложения. Порт БД не публикуется. Остальное отбрасывается.
2. **Права минимальные.** Приложение работает в контейнере от UID 10001 под
   учётной записью `deploy` без пароля. Её ключ ограничен `restrict,from=`.
   Каталог окружения имеет права 750, `.env` и доступ к registry — 600.
   Вход под root и по паролю запрещён.
3. **Нет необъяснимых shell-команд.** Используются модули Ansible. Немногие
   `command` (проверка `dpkg-query`, `docker compose version`, скрипт firewall,
   `deploy.sh`) прокомментированы и помечены `changed_when`. Модуль `shell` не используется.
4. **Повторяемость.** Повторный запуск даёт `changed=0`. Каждая задача либо
   идемпотентна сама, либо выполняется только при реальном расхождении.
5. **Секретов в открытом виде нет.** Секреты хранятся только в зашифрованных
   `vault.yml`. `make infra-lint` (и CI) падает, если `vault.yml` не зашифрован
   или `vault_*` задан в другом файле. Пароль vault в git не попадает
   (`.gitignore: .vault_pass*`), `.env` не показывается в `--diff`.
6. **Изменения согласуются.** Весь `ansible/` входит в `.gitlab/CODEOWNERS`:
   MR, меняющий роли, шаблоны или секреты, не пройдёт `guard:critical-files`
   без одобрения владельца.

Запрещено: хранить незашифрованные секреты, открывать порты «на всякий
случай», использовать `shell`/`command` без комментария и `changed_when`,
снижать профиль ansible-lint или добавлять исключения без обоснования в MR.

## 5. Команды

| Команда | Что делает |
|---|---|
| `make infra-setup` | ставит Ansible и ansible-lint точных версий в `.venv-ansible/` |
| `make infra-lint` | открытые секреты, синтаксис обоих окружений, `ansible-lint` (профиль production) |
| `make infra-check ENV=...` | пробный запуск `--check --diff`: что изменилось бы |
| `make infra-apply ENV=...` | применить конфигурацию и проверить приложение |
| `make infra-vault ENV=...` | создать или изменить зашифрованные секреты окружения |

Необязательные параметры: `HOST`, `SSH_USER`, `REGISTRY_IMAGE` (см. §1) и
`VAULT_PASS_FILE` (по умолчанию `~/.vault_pass_city_duma`).

## 6. Настройка (один раз)

Всё на машине управления (например, на ВМ с GitLab, под `vboxuser`), из
клона репозитория.

**1. Ansible:**
```
sudo apt install -y python3-venv
make infra-setup
```

**2. Пароль vault** (один на команду, хранится вне git):
```
printf '%s' 'придумайте-длинный-пароль' > ~/.vault_pass_city_duma
chmod 600 ~/.vault_pass_city_duma
```

**3. Секреты окружений.** Откроется редактор, вставьте содержимое
`vault.yml.example` и замените значения:
```
make infra-vault ENV=staging
make infra-vault ENV=production
```
Для staging пароль БД должен совпасть с уже записанным в
`/srv/city-duma-staging/.env`, иначе приложение не подключится к старой БД:
`sudo grep POSTGRES_PASSWORD /srv/city-duma-staging/.env`.

**4. Ключ Ansible на серверах.** Вход должен работать по ключу без пароля:
```
ssh-keygen -t ed25519 -N "" -C ansible -f ~/.ssh/id_ed25519   # если ключа ещё нет
ssh-copy-id vboxuser@192.168.58.139
ssh-copy-id admin@192.168.58.140
```
⚠️ После первого запуска вход по паролю на серверах будет **запрещён**.
Сначала проверьте, что `ssh admin@192.168.58.140` пускает без пароля.

**5. Чистая машина production.** Установите Debian 12 netinst без графического
окружения, с компонентами «SSH server» и «стандартные системные утилиты».
Пароль root при установке оставьте пустым: тогда созданный пользователь
(`admin`) получит sudo. Задайте статический адрес 192.168.58.140. Больше на
ней ничего не делается: всё остальное настраивает Ansible. Если адрес или
пользователь другие, поправьте `host_vars/prod-1.yml` и `PRODUCTION_HOST` в
`.gitlab-ci.yml` (или передавайте `HOST=`, `SSH_USER=`).

**6. Тег образа.** В production запускается `app_version: v0.6.0`. Тег должен
быть в registry: создайте его в GitLab от `main` (*Code → Tags*).

**7. Переменные GitLab для CI** (*Settings → CI/CD → Variables*), тип **File**,
флаг **Protect variable**:

| Key | Значение |
|---|---|
| `ANSIBLE_SSH_KEY` | закрытый ключ, которым Ansible входит на серверы (`cat ~/.ssh/id_ed25519`) |
| `ANSIBLE_KNOWN_HOSTS` | `ssh-keyscan -t ed25519 192.168.58.139 192.168.58.140` |
| `ANSIBLE_VAULT_PASSWORD` | содержимое `~/.vault_pass_city_duma` |

Адреса и пользователи серверов — не секрет. Они записаны в `variables` в
`.gitlab-ci.yml`: `DEPLOY_HOST` (ЛР6), `STAGING_SSH_USER`, `PRODUCTION_HOST`,
`PRODUCTION_SSH_USER`.

## 7. CI/CD

| Job | Когда | Что |
|---|---|---|
| `infra:lint` | каждый pipeline (MR, `main`, теги), стадия `quality` | `make infra-lint`. Если упал, образ не публикуется |
| `infra:apply:staging` | `main`: автоматически, если изменился `ansible/`, иначе вручную | `make infra-apply ENV=staging` и проверка приложения |
| `infra:apply:production` | `main` и теги `vX.Y.Z`, только вручную (▶) | `make infra-apply ENV=production` |

Jobs применения используют `resource_group`, поэтому не пересекаются с
развёртыванием приложения в том же окружении.

## 8. Если что-то не так

| Симптом | Причина |
|---|---|
| `Укажите сервер и registry` | пустой `HOST`, `SSH_USER` или неверный `REGISTRY_IMAGE` |
| `Нет секретов окружения ...` | не создан `vault.yml`: `make infra-vault ENV=...` |
| `Attempting to decrypt but no vault secrets found` | нет файла `~/.vault_pass_city_duma` или `VAULT_PASS_FILE` |
| `Permission denied (publickey)` | ключ не скопирован на сервер (`ssh-copy-id`) |
| `Missing sudo password` / `Incorrect sudo password` | `vault_become_password` не совпадает с паролем пользователя |
| `[WARNING] ... world writable directory, ignoring it as an ansible.cfg` | клон лежит в общей папке. Запускайте из домашнего каталога Linux (в CI путь задан `ANSIBLE_CONFIG`) |
| падает «Развернуть образ из registry» с кодом 12/13 | нет тега в registry или неверный deploy token (коды — `docs/CD.md`) |
