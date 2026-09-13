# Задача #3: заседания и посещаемость

**Описание**: реализовать сущности `Meeting` и `Attendance` — заседания
комиссий (и пленарные, `commission_id = NULL`) и отметки посещаемости
депутатов.

**Критерии приёмки**:
- Модель `Meeting`: `id`, `commission_id` (FK, nullable), `title`,
  `scheduled_at`, `status` (`scheduled|held|cancelled`), `agenda`.
- Модель `Attendance`: `id`, `meeting_id` (FK), `deputy_id` (FK),
  `status` (`present|absent|excused`), `note`.
- Уникальная пара `(meeting_id, deputy_id)` — одна отметка посещаемости
  на депутата за заседание (`409 Conflict` при нарушении).
- **Бизнес-правило «кворум»**: заседание нельзя перевести в статус
  `held`, если число депутатов со статусом `present` не превышает
  половины списочного состава комиссии. Правило не применяется к
  пленарным заседаниям (`commission_id = null`).
- Эндпоинты (например): `POST /meetings`, `GET /meetings`,
  `POST /meetings/{id}/attendance`, `PATCH /meetings/{id}/status`.
- Обновлена `docs/DATA_MODEL.md` (раздел «Реализовано в main»).
- Тесты: уникальность отметки, правило кворума (успех/отказ), что
  правило кворума не применяется к пленарным заседаниям.

**Зависит от**: `Deputy`, `Commission` (уже в `main`),
`CommissionMembership` (задача #2 — нужна для подсчёта состава комиссии
при проверке кворума; уточните состояние ветки `feature/commission-membership`
перед стартом, чтобы не разойтись в модели).

**Ветка**: `feature/meetings-attendance`
