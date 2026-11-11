# Continuous Integration (ЛР5): GitLab CI/CD

CI — независимый и обязательный барьер перед созданием и публикацией образа.
Pipeline описан в [`.gitlab-ci.yml`](../.gitlab-ci.yml) и выполняет **те же
make-команды**, что разработчик запускает у себя. Образ попадает в registry
только если прошли **все** проверки.

## 1. Схема pipeline

```
quality → test → migrations → backup → security → build → container → guard → publish
                                                                        ▲
                         guard:critical-files стартует сразу (needs: []),
                         но publish ждёт и его тоже
```

| Стадия | Job | Команда (та же, что локально) | Что проверяет | Отчёт / артефакт |
|---|---|---|---|---|
| quality | `quality` | `make quality` | ruff (линтер) + black --check (стиль) | `reports/gl-code-quality.json` (виджет Code Quality в MR) |
| test | `test` | `make test` | unit + интеграционные тесты на PostgreSQL, покрытие ≥ 40 % | `reports/junit.xml` (вкладка Tests), `coverage.xml` (покрытие в MR) |
| migrations | `migrations` | `make migrate`, `make migrate-check` | миграции на чистой БД; одна голова; последняя миграция на БД с данными; `downgrade base` → `upgrade head` | лог job |
| backup | `backup` | `make backup`, `make backup-check` | копия → удаление и порча данных → восстановление → данные совпали | `reports/backup_ci.sql` |
| security | `sast` | `make sast` | bandit — SAST кода `app/` | `reports/bandit.json` |
| security | `deps-audit` | `make deps-audit` | pip-audit — известные уязвимости зависимостей | `reports/pip-audit.json` |
| security | `secrets` | gitleaks (как `make secrets`) | секреты в файлах и в новых коммитах | `reports/gitleaks-*.json` |
| build | `build:image` | `docker build` | сборка образа по `Dockerfile` | `image.tar` (1 день) |
| container | `container-check` | `make container-check` | правила ЛР4: не latest, не root, нет секретов в образе, healthcheck, порты, лимиты, сохранность данных, SIGTERM, восстановление в новый volume | лог job |
| container | `image-scan` | trivy (как `make image-scan`) | уязвимости HIGH/CRITICAL с исправлением и секреты в собранном образе | `reports/trivy.json` |
| guard | `guard:critical-files` | `make guard GUARD_ARGS=--ci` | изменения критических файлов согласованы; нет ослабления проверок | лог job |
| publish | `publish:image` | `docker push` | — | образ `<registry>/<проект>:<short SHA>` |

Все отчёты сохраняются с `artifacts: when: always` — и при успехе, и при
падении (как раз тогда они нужнее всего).

## 2. Когда запускается pipeline (п.3)

Правила `workflow` в `.gitlab-ci.yml`:

| Событие | Pipeline |
|---|---|
| push в ветку без открытого MR | branch pipeline |
| создание MR и каждый push в его ветку | merge request pipeline (branch pipeline для этой ветки не дублируется) |
| изменение `main` (слияние MR) | branch pipeline на `main` |
| тег `v*` | tag pipeline, образ публикуется ещё и с тегом версии |

Новый коммит в ту же ветку отменяет устаревший pipeline
(`auto_cancel: on_new_commit`), кроме уже начатой публикации.

## 3. Почему локальные и серверные проверки должны совпадать (вопрос защиты)

- **Одно определение «готово».** Если CI проверяет иначе, чем `make verify`,
  у команды два разных критерия приёмки: локально зелёное падает в CI
  (потеря времени) или, хуже, CI пропускает то, что ловится локально.
- **Воспроизводимость.** Любое падение CI разработчик повторяет у себя той же
  командой (`make test`, `make migrate-check`, ...) и чинит, а не гадает,
  что «на сервере по-другому».
- **CI нельзя обойти «тонкой настройкой».** Команды живут в Makefile — одном
  критическом файле; CI лишь вызывает их. Ослабить проверку в одном месте
  незаметно для другого нельзя, а изменение Makefile отслеживает guard.
- **Сервер всё равно нужен.** Локальная проверка зависит от добросовестности
  и окружения разработчика; CI выполняет её в чистом окружении на каждом
  изменении и не даёт слить или опубликовать то, что её не прошло.

Отличия CI от локального запуска сведены к окружению и не меняют сути
проверок: к pytest добавляется только опция отчёта JUnit (`PYTEST_ADDOPTS`),
БД — одноразовый сервис, Docker — сервис `docker:dind` (поэтому
`CONTAINER_CHECK_HOST=docker`).

## 4. Отдельная тестовая БД (п.6)

В `test`, `migrations`, `backup` поднимается собственный одноразовый сервис
`postgres:16.10-alpine` (та же версия, что в `compose.yaml`). Он существует
только на время job и уничтожается вместе с ним. Внутри:

- тесты работают в базе `city_duma_ci_test` (суффикс `_test` обязателен —
  `tests/conftest.py`);
- `migrate-check` и `backup-check` создают ещё и свои временные базы
  `*_migcheck_*_test` / `*_bkpcheck_*_test` и удаляют их после проверки —
  поэтому их безопасно запускать и локально, рабочая БД не затрагивается.

## 5. Кэширование (п.8)

| Что | Ключ | Где |
|---|---|---|
| venv и кеш pip | хеш `requirements.txt` | `.venv/`, `.cache/pip/` |
| то же для job `backup` (другой образ Python) | хеш `requirements.txt`, префикс `pg16-py311` | те же пути |
| база уязвимостей trivy | `trivy-db` | `.cache/trivy/` |

Изменился `requirements.txt` — меняется ключ, зависимости ставятся заново.

## 6. Тег образа и запрет публикации (п.9–10)

- Образ собирается один раз (`build:image`) с меткой
  `org.opencontainers.image.revision=<полный SHA>` и передаётся дальше файлом
  `image.tar`. Анализируется и публикуется **тот же самый** образ.
- `publish:image` стоит в последней стадии и не имеет `needs`, поэтому
  запускается, только если завершились успешно **все** jobs всех предыдущих
  стадий. Иначе GitLab помечает его `skipped`, и в registry ничего не
  попадает. До publish образ не уходит никуда — только артефакт pipeline.
- Перед push сверяется метка revision с `CI_COMMIT_SHA`.
- Тег: `<registry>/<проект>:<CI_COMMIT_SHORT_SHA>`; для git-тега ещё
  `:<тег>` (например `:v0.5.0`). Тег `latest` не используется (запрет ЛР4).
- `guard` проверяет инварианты: publish в последней стадии, без `needs`,
  ни у одной обязательной проверки нет `allow_failure`, `when: manual`,
  `|| true`.

## 7. Пароли и токены (п.11)

В репозитории нет ни одного пароля. Всё — в **Settings → CI/CD → Variables**:

| Переменная | Назначение | Флаги |
|---|---|---|
| `CI_TEST_DB_PASSWORD` | пароль одноразовой тестовой БД | Masked (или Masked and hidden), **не** Protected — нужен и в MR из feature-веток |
| `GUARD_API_TOKEN` | Project access token, роль Reporter, scope **только `read_api`** — guard читает одобрения MR | Masked and hidden, не Protected |
| `REGISTRY`, `REGISTRY_IMAGE`, `REGISTRY_USER`, `REGISTRY_PASSWORD` | только если используется свой `registry:2` вместо встроенного | Masked; Protected — если публикуете только из `main` и тегов |

Для встроенного Container Registry ничего задавать не нужно: GitLab сам
выдаёт каждому job одноразовый `CI_REGISTRY_PASSWORD`. Masked-значения
GitLab заменяет на `[MASKED]` в логах (в т.ч. внутри `DATABASE_URL`).

## 8. Безопасность (п.12)

| Что | Инструмент | Порог |
|---|---|---|
| код приложения | bandit (`make sast`) | любая находка; исключения — только `# nosec` с обоснованием |
| зависимости | pip-audit (`make deps-audit`) | любая известная уязвимость в `requirements.txt` и их зависимостях |
| секреты | gitleaks + `.gitleaks.toml` | любая находка в рабочем дереве и в новых коммитах ветки (в т.ч. удалённая позже в той же ветке) |
| образ | trivy (`make image-scan`) | HIGH/CRITICAL с доступным исправлением, секреты в слоях образа |

Если trivy или pip-audit нашли уязвимость — обновляется версия (базовый образ
в `Dockerfile` или пакет в `requirements.txt`), а не снижается порог.
Обоснованное исключение — `.trivyignore` / `--ignore-vuln` с комментарием,
и это изменение критического файла (см. ниже).

## 9. Защита критических файлов (п.13–15)

**Критические файлы** перечислены в [`.gitlab/CODEOWNERS`](../.gitlab/CODEOWNERS):
тесты, `Makefile`, `pyproject.toml`, `requirements.txt`, `.gitlab-ci.yml`,
`.gitlab/`, настройки gitleaks/trivy, скрипты-проверки, `CONTRIBUTING.md`.

Барьеры (все работают в GitLab Free):

1. **Защищённая ветка `main`**: push — No one, merge — Maintainers,
   force push запрещён. Изменение попадает в `main` только через MR.
2. **Pipelines must succeed** в настройках MR: красный pipeline блокирует merge.
3. **Job `guard:critical-files`** (`scripts/ci_guard.py`):
   - находит в MR изменённые критические файлы по CODEOWNERS **целевой**
     ветки (правка самого CODEOWNERS проверяется по старому списку);
   - отдельно и явно называет **ослабления**: удалённый тест, тест с меньшим
     числом `assert`/`pytest.raises`, новый `skip`/`xfail`/отбор тестов,
     снижение `--cov-fail-under`, новые исключения bandit/ruff/coverage,
     новые `# noqa`/`# nosec`, убранный шаг `make verify`, удалённая команда
     job, пропавший обязательный job, `allow_failure`, `when: manual`, `|| true`;
   - падает, пока MR не одобрит **владелец из CODEOWNERS, не являющийся
     автором**, причём **после последнего push** (одобрение старой версии не
     считается). Одобрения читаются через API (`GUARD_API_TOKEN`); нет токена
     или API недоступно — job падает (fail closed);
   - выполняет **свою версию из целевой ветки**: правка `ci_guard.py` в MR
     не может отключить проверку этого же MR.
4. **Конфигурация pipeline из `main`** (настраивается после слияния ЛР5,
   см. §10, шаг 8). Тогда MR не может удалить job guard или любую проверку из
   своего же pipeline: изменения `.gitlab-ci.yml` начинают действовать
   только после одобрения и слияния.
5. **GitLab Premium/Ultimate** (если есть лицензия или trial): дополнительно
   включить *Code owner approval* для `main` — GitLab будет требовать
   одобрение владельцев штатно, по тому же файлу CODEOWNERS.

Локально перед push: `make guard` (сравнение с `origin/main`) — покажет то же,
что увидит reviewer.

## 10. Настройка GitLab (один раз)

> GitLab требователен к памяти. Runner можно держать на той же машине, но
> выделите ему 2+ CPU и 4+ ГБ: jobs `container-check` и `build:image`
> запускают собственный Docker-демон.

**1. Проект и репозиторий.** Создайте пустой проект (без README) и
отправьте в него все ветки и теги из GitHub-клона:

```
git remote add gitlab http://<адрес-gitlab>/<группа>/city-duma.git
git push gitlab --all
git push gitlab --tags
```

Чтобы GitHub оставался актуальным, в GitLab: *Settings → Repository →
Mirroring repositories* → push-зеркало на GitHub (Free, нужен GitHub PAT).
Участников добавьте в проект (*Manage → Members*): кто будет одобрять
критические изменения — роль Developer или выше; кто сливает в main —
Maintainer.

**2. Runner (Docker executor, privileged).** *Settings → CI/CD → Runners →
New project runner* → получить токен `glrt-...`, затем на машине с Docker:

```
docker run -d --name gitlab-runner --restart always \
  -v /srv/gitlab-runner/config:/etc/gitlab-runner \
  -v /var/run/docker.sock:/var/run/docker.sock \
  gitlab/gitlab-runner:<версия, совпадающая с GitLab>

docker exec -it gitlab-runner gitlab-runner register --non-interactive \
  --url http://<адрес-gitlab> --token glrt-XXXXXXXX \
  --executor docker --docker-image alpine:3.22 \
  --docker-privileged --docker-volumes /certs/client
```

`privileged` и том `/certs/client` нужны для docker:dind с TLS
(jobs `build:image`, `container-check`, `publish:image`).
Если GitLab доступен job-контейнерам не по тому адресу, что в браузере
(например, `localhost`), в `/srv/gitlab-runner/config/config.toml` в секции
`[[runners]]` укажите `clone_url = "http://<IP-машины-с-GitLab>"`.

**3. Container Registry («локальный docker hub»).** Вариант А — встроенный:
в `gitlab.rb` (`/etc/gitlab/gitlab.rb` или `GITLAB_OMNIBUS_CONFIG`)

```
registry_external_url 'http://<IP>:5050'
```

пробросьте порт 5050 контейнера GitLab, `gitlab-ctl reconfigure`. Так как
registry по HTTP, в `.gitlab-ci.yml` в шаблоне `.docker` раскомментируйте
`command: ["--insecure-registry=<IP>:5050"]`. Проверка: *Deploy → Container
Registry* в проекте.

Вариант Б — отдельный `registry:2` (с htpasswd) на `<IP>:5000`: задайте
переменные `REGISTRY=<IP>:5000`, `REGISTRY_IMAGE=<IP>:5000/city-duma`,
`REGISTRY_USER`, `REGISTRY_PASSWORD` (§7) и тот же `--insecure-registry`.
Адрес `localhost` не подходит: внутри dind это сам dind.

**4. Размер артефактов.** `image.tar` ~ 70–90 МБ в архиве. Если upload
падает с `413`: *Admin → Settings → CI/CD → Continuous Integration and
Deployment → Maximum artifacts size* = 300 МБ.

**5. Переменные.** *Settings → CI/CD → Variables*: `CI_TEST_DB_PASSWORD`
(любой случайный пароль от 12 символов, только буквы и цифры) и
`GUARD_API_TOKEN` — *Settings → Access tokens → Add new token*: роль
Reporter, scope `read_api`, срок до конца семестра.

**6. Защищённая ветка и теги.** *Settings → Repository → Protected branches*:
`main` — Allowed to merge: Maintainers; Allowed to push and merge: No one;
Allow force push: выкл. *Protected tags*: `v*` — Maintainers.

**7. Правила MR.** *Settings → Merge requests*: Merge checks → **Pipelines
must succeed**, **All threads must be resolved**; шаблон описания MR
подхватится из `.gitlab/merge_request_templates/Default.md`.

**8. Pipeline только из `main` (после слияния ветки ЛР5).** *Settings →
CI/CD → General pipelines → CI/CD configuration file*:

```
.gitlab-ci.yml@<группа>/<проект>:main
```

С этого момента каждый pipeline (в т.ч. в MR) берёт описание из `main`.
Правка `.gitlab-ci.yml` в MR вступает в силу после одобрения и слияния.

## 11. Частые проблемы

| Симптом | Причина и решение |
|---|---|
| `Cannot connect to the Docker daemon at tcp://docker:2376` | runner без `--docker-privileged` или без тома `/certs/client` (§10, шаг 2) |
| `http: server gave HTTP response to HTTPS client` при push | registry по HTTP — нужен `--insecure-registry` в сервисе dind (§10, шаг 3) |
| `Не задана переменная CI_TEST_DB_PASSWORD` | переменная не создана или помечена Protected, а pipeline в незащищённой ветке |
| guard: `нет одобрения владельца` | так и задумано: владелец (не автор) нажимает **Approve**, затем **Retry** job guard |
| guard: логины не совпадают | в `.gitlab/CODEOWNERS` логины вашего GitLab, а не GitHub; или роль `@@maintainer` |
| image-scan падает на уязвимостях базового образа | обновить тег `FROM` в `Dockerfile` (и `PYTHON_IMAGE` в CI) на свежий патч-релиз — барьер сработал как надо |
| `413 Request Entity Too Large` при upload артефакта | увеличить Maximum artifacts size (§10, шаг 4) |
