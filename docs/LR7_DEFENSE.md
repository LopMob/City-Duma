# ЛР7: соответствие заданию и сценарий защиты

Устройство и настройка — [`docs/ANSIBLE.md`](ANSIBLE.md).

## Соответствие пунктам задания

| № | Требование | Где реализовано |
|---|---|---|
| 1 | Средство управления конфигурацией | Ansible (`ansible-core`, точная версия в `ansible/requirements.txt`) |
| 2 | Узлы; общие, окруженческие, узловые и секретные параметры | `inventories/*/hosts.yml`; `group_vars/all.yml`, `group_vars/<окр>/main.yml`, `host_vars/<узел>.yml`, `group_vars/<окр>/vault.yml` |
| 3 | Подготовка чистой Linux-машины | `make infra-apply ENV=production` на новой ВМ с Debian |
| 4 | Пользователи, SSH, пакеты, Docker, каталоги, права, firewall, конфигурация, запуск | роли `base`, `docker`, `firewall`, `app` |
| 5 | Шаблоны конфигурационных файлов | `sshd.conf.j2`, `daemon.json.j2`, `city-duma-firewall.sh.j2`, `city-duma-firewall.service.j2`, `app.env.j2`, `docker-config.json.j2` |
| 6 | Не менее двух окружений | `staging` и `production`: свои порт, каталог, лимит памяти, правила firewall |
| 7 | Повторный запуск без изменений | второй `make infra-apply` → `changed=0` |
| 8 | Восстановление вручную изменённого параметра | сценарий 3 ниже |
| 9 | Нет незашифрованных секретов | `vault.yml` зашифрован; `scripts/vault_check.py` в `make infra-lint` и CI |
| 10 | Проверка доступности после настройки | задачи роли `app`: `/health`, `/version`, `/ui/` с машины управления |
| 11 | Отдельный pipeline / процесс CD | `infra:lint`, `infra:apply:staging`, `infra:apply:production` в `.gitlab-ci.yml` |
| 12 | Требования к инфраструктурному коду | `docs/ANSIBLE.md`, §4 |
| 13 | Защита ролей, шаблонов и секретов согласованием | `/ansible/` в `.gitlab/CODEOWNERS` + `guard:critical-files` |

## Подготовка

1. Выполнена настройка из `docs/ANSIBLE.md`, §6: Ansible установлен,
   есть `~/.vault_pass_city_duma`, оба `vault.yml` созданы, ключ скопирован.
2. Для сценария 1 — новая ВМ с Debian 12 (192.168.58.140), на которой
   после установки ничего не настраивали.
3. Открыты: терминал машины управления в каталоге клона, второй терминал с
   SSH на сервер, *Build → Pipelines* в GitLab.

## 1. Подготовка чистой машины

```
make infra-apply ENV=production
```
В выводе видно, как ставятся пакеты и Docker, настраиваются SSH и iptables,
создаётся `deploy`, скачивается образ и проверяется приложение.
Ожидается: `failed=0`, открывается http://192.168.58.140:8000.

## 2. Повторный запуск без изменений

```
make infra-apply ENV=production
```
Ожидается: в `PLAY RECAP` — `changed=0`.

## 3. Восстановление вручную изменённой конфигурации

На сервере измените управляемый параметр, затем снова выполните
`make infra-apply ENV=production`:

| Что сломать на сервере | Что покажет повторный запуск |
|---|---|
| `sudo sed -i 's/^PermitRootLogin no/PermitRootLogin yes/' /etc/ssh/sshd_config.d/10-city-duma.conf` | `changed` у «Настроить SSH», SSH перезагружен |
| `sudo iptables -D CITY-DUMA-INPUT 4` | `check` видит расхождение, «Применить правила iptables» |
| `sudo sed -i 's/APP_MEM_LIMIT=.*/APP_MEM_LIMIT=1g/' /srv/city-duma-production/.env` | `.env` восстановлен, контейнеры пересозданы |
| `sudo chmod 777 /srv/city-duma-production` | права 750 возвращены |
| `sudo -iu deploy docker compose -p city-duma-production down` | приложение поднято снова из последнего проверенного релиза |

Посмотреть заранее, что будет исправлено: `make infra-check ENV=production`.
Пробный запуск имеет смысл на уже настроенной машине. На чистой он
остановится, потому что Docker и пользователя `deploy` ещё неоткуда проверить.

## 4. Проверка доступности приложения после настройки

Выполняется в конце каждого запуска: задачи «Дождаться ответа /health»,
«Проверить окружение и версию», «Проверить, что открывается веб-интерфейс».
Если остановить приложение во время запуска, Ansible завершится с ошибкой.
Вручную:
```
make release-check STAGING_URL=http://192.168.58.140:8000
```

Порты — с другой машины:
```
nc -zv 192.168.58.140 8000     # открыт
nc -zv 192.168.58.140 5432     # отказ: БД снаружи недоступна
```
На сервере: `sudo city-duma-firewall show`, `sudo ss -tlnp`.

## 5. Проверка отсутствия открытых секретов

```
make infra-lint                                        # [OK] открытых секретов в ansible нет
head -1 ansible/inventories/*/group_vars/*/vault.yml   # $ANSIBLE_VAULT;1.1;AES256
```
Демонстрация отказа: в отдельной ветке расшифровать `vault.yml`
(`.venv-ansible/bin/ansible-vault decrypt --vault-password-file ~/.vault_pass_city_duma <файл>`)
и отправить MR — `infra:lint` упадёт с сообщением «файл секретов не зашифрован».

## 6. Защита критических файлов

MR, меняющий что-либо в `ansible/` (роль, шаблон, `vault.yml`), без одобрения
владельца не проходит `guard:critical-files` — так же, как в ЛР5.
