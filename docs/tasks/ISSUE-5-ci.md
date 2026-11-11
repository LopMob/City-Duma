# Задача #5: Continuous Integration (ЛР5)

**Описание**: подключить репозиторий к GitLab CI/CD и сделать pipeline
независимым обязательным барьером перед публикацией образа.

**Критерии приёмки**:
- `.gitlab-ci.yml`: стадии quality, test, migrations, backup, security, build,
  container, guard, publish; запуск на push, MR, изменение `main`, теги.
- Jobs вызывают те же make-цели, что и локально; новые цели:
  `migrate-check`, `backup-check`, `deps-audit`, `secrets`, `image-scan`,
  `guard`, `ci-local`.
- Отдельная одноразовая тестовая БД в каждом job с БД.
- Отчёты: JUnit, покрытие (cobertura), Code Quality (ruff), bandit,
  pip-audit, gitleaks, trivy.
- Кеш venv/pip по хешу `requirements.txt`.
- Образ с тегом короткого SHA, публикуется только после всех проверок.
- Пароли и токены только в переменных GitLab (Masked).
- Поиск секретов (gitleaks), проверка зависимостей (pip-audit), анализ
  образа (trivy).
- Критические файлы в `.gitlab/CODEOWNERS`; job `guard:critical-files`
  требует одобрения владельца-не-автора и явно сообщает об ослаблении
  проверок; тесты самого guard.
- Документация: `docs/CI.md`, `docs/LR5_DEFENSE.md`, обновлены README и
  CONTRIBUTING.

**Ветка**: `feature/lab5-ci`
