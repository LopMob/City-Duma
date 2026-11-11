# ЛР5: соответствие заданию и сценарий защиты

Описание pipeline и настройки GitLab — [`docs/CI.md`](CI.md).

## Соответствие пунктам задания

| № | Требование | Где реализовано |
|---|---|---|
| 1 | Выбор CI-системы | GitLab CI/CD (self-managed), `.gitlab-ci.yml` |
| 2 | Подключение репозитория и registry | проект в GitLab + push-зеркало на GitHub; Container Registry GitLab или `registry:2` (CI.md §10) |
| 3 | Запуск на push, MR, изменение main | `workflow: rules` (CI.md §2) |
| 4 | Те же команды, что локально | jobs вызывают `make quality / test / migrate / migrate-check / backup-check / sast / deps-audit / container-check / guard` (CI.md §1, §3) |
| 5 | Стадии: стиль, тесты, миграции, backup, безопасность, контейнер, сборка, публикация | `stages:` — 9 стадий |
| 6 | Отдельная тестовая БД | сервис `postgres` на каждый job + базы `*_test` (CI.md §4) |
| 7 | Отчёты тестов и анализаторов | `artifacts:reports` junit, coverage, codequality + JSON bandit/pip-audit/gitleaks/trivy |
| 8 | Кэширование зависимостей | `cache:` venv + pip по хешу `requirements.txt`, БД trivy (CI.md §5) |
| 9 | Тег образа по commit SHA | `:$CI_COMMIT_SHORT_SHA` + метка revision (CI.md §6) |
| 10 | Нет публикации при падении проверки | `publish:image` — последняя стадия без `needs`; инвариант проверяет guard |
| 11 | Пароли и токены в защищённых переменных | `CI_TEST_DB_PASSWORD`, `GUARD_API_TOKEN`, `CI_REGISTRY_PASSWORD` (CI.md §7) |
| 12 | Поиск секретов, проверка зависимостей, анализ образа | gitleaks, pip-audit, trivy (+ bandit) |
| 13 | Защита критических файлов | `.gitlab/CODEOWNERS`, protected `main`, pipeline из `main` |
| 14 | Обязательная проверка изменений критических файлов | job `guard:critical-files`: одобрение владельца-не-автора после последнего push |
| 15 | Удаление теста / отключение проверки не проходит незаметно | guard называет ослабление явно и блокирует MR; тесты guard — `tests/unit/test_ci_guard.py` |
| 16 | Зелёный CI для корректного и красный для дефектного изменения | сценарии A и B ниже |

## Подготовка (до защиты)

1. Pipeline `main` зелёный, в *Deploy → Container Registry* есть образ с
   тегом короткого SHA последнего коммита `main`.
2. Открыты вкладки: MR, *Build → Pipelines*, *Deploy → Container Registry*,
   *Settings → Repository → Protected branches*, `.gitlab/CODEOWNERS`.
3. В команде есть второй участник с доступом, чтобы одобрить MR (или
   второй тестовый пользователь).

Каждый сценарий — отдельная ветка от `main` и MR. Ветки после показа
закрываются без слияния.

## A. Корректное изменение проходит CI (п.16)

```
git switch -c demo/ok main
# небольшое безопасное изменение, например текст в docs/API.md
git commit -am "docs: уточнение описания API"
git push gitlab demo/ok        # открыть MR в GitLab
```

Показать: все jobs зелёные, `guard:critical-files` — «критические файлы не
затронуты», `publish:image` выполнен, в registry появился тег `<short SHA>`
этого коммита. В MR видны вкладки Tests, Code Quality и покрытие.

## B. Сломанный тест / логика — pipeline останавливается (п.16, защита)

В `app/crud.py` в `_ensure_quorum` заменить
`required = total_members // 2 + 1` на `required = total_members // 2`.

```
git switch -c demo/broken-logic main
git commit -am "change quorum rule"
git push gitlab demo/broken-logic
```

Показать: job `test` красный (тесты кворума в `tests/unit/test_quorum_boundaries.py`
нашли изменение), последующие стадии и **`publish:image` — skipped**.

## C. Сломанная миграция

Создать `alembic/versions/0005_broken.py`:

```python
"""broken demo migration"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOT NULL без значения по умолчанию: на БД с данными миграция упадёт
    op.add_column("deputies", sa.Column("rank", sa.Integer(), nullable=False))


def downgrade() -> None:
    op.drop_column("deputies", "rank")
```

Показать: pipeline остановлен — интеграционные тесты миграций (`test`)
и `make migrate-check` (шаг «последняя миграция на БД с данными») не
принимают миграцию, ломающую существующие данные; `publish:image` — skipped.
Локально то же: `make migrate-check`.

## D. Тестовый секрет обнаруживается

Сгенерировать **фиктивный** токен формата GitHub и положить в код (в
репозитории его заранее нет, иначе guard и gitleaks сработали бы на сам
документ):

```
python -c "import secrets,string; a=string.ascii_letters+string.digits; print('GITHUB_TOKEN = \"ghp_' + ''.join(secrets.choice(a) for _ in range(36)) + '\"')" > app/demo_settings.py
git switch -c demo/secret main && git add app/demo_settings.py
git commit -m "add settings" && git push gitlab demo/secret
```

Показать: job `secrets` красный, в логе — правило `github-pat`, файл и
коммит (значение скрыто `--redact`). Затем удалить файл **следующим
коммитом** и запушить: `secrets` всё равно красный — секрет остался в
истории ветки. Вывод: секрет не «удаляют», а отзывают; `publish:image` — skipped.

## E. Образ не публикуется после ошибки

В пайплайнах B, C, D: `publish:image` в статусе *skipped*. В *Deploy →
Container Registry* нет тега с SHA этих коммитов; при желании:

```
docker pull <registry>/<группа>/city-duma:<short SHA из сценария B>
# manifest unknown
```

## F. Защита критических файлов

Варианты ослабления (каждый — отдельная ветка/MR, по одному хватит):

| Попытка | Что пишет guard |
|---|---|
| удалить функцию теста из `tests/unit/test_quorum_boundaries.py` | `удалён тест ...`, `число тестов уменьшилось` |
| убрать `assert` из теста | `в тесте ... меньше проверок: 2 -> 1` |
| `@pytest.mark.skip` над тестом | `добавлены пропуски тестов` |
| в `pyproject.toml` `--cov-fail-under=40` → `10` | `снижен порог покрытия --cov-fail-under: 40 -> 10` |
| в `pyproject.toml` `skips = ["B104"]` | `новые пропуски правил bandit` |
| в `Makefile` `verify: quality test` | `из make verify убраны шаги: sast` |
| в `.gitlab-ci.yml` у `test` добавить `allow_failure: true` | `job test: allow_failure — падение не остановит CI` |
| в `scripts/ci_guard.py` сделать `return 0` | выполняется доверенная версия из `main`, правка не действует |

Показать по шагам:

1. guard красный, перечислены критические файлы и ослабление, merge
   недоступен (*Pipelines must succeed*).
2. Автор нажимает **Approve** у себя и делает Retry guard — всё равно
   красный: «одобрение автора MR не учитывается».
3. Владелец из CODEOWNERS (не автор) одобряет, Retry — guard зелёный
   и пишет, **кто** согласовал ослабление. Незаметно принять нельзя:
   решение осознанное и записано в MR.
4. Новый push после одобрения → guard снова красный («одобрение дано до
   последнего push»).
5. *Protected branches*: в `main` нельзя push напрямую; *General pipelines*:
   конфигурация берётся из `main`, поэтому удаление job guard в MR не
   влияет на pipeline этого MR.

## Вопросы на защите — краткие ответы

- **Почему локальные и серверные проверки должны совпадать?** — CI.md §3.
- **Почему publish не может выполниться после ошибки?** Последняя стадия,
  без `needs`: GitLab запускает её, только если все предыдущие jobs успешны;
  guard следит, чтобы никто не добавил `needs`/`allow_failure`.
- **Зачем образ через артефакт, а не сразу в registry?** В registry
  попадает только проверенный образ; анализируется ровно тот, что публикуется.
- **Почему секреты в переменных, а не в `.env` в репозитории?** История git
  вечна и копируется вместе с репозиторием; переменная хранится в GitLab,
  маскируется в логах и меняется без коммита.
- **Что будет, если удалить job guard из `.gitlab-ci.yml`?** Pipeline
  берётся из `main` (CI.md §10, шаг 8), поэтому в MR удаление не работает;
  а сам MR с такой правкой требует одобрения владельца.
- **Почему не Code Owners GitLab?** Обязательное одобрение Code Owners —
  функция Premium. В Free то же правило реализует guard по тому же файлу
  CODEOWNERS; при наличии Premium включается штатная галочка.
