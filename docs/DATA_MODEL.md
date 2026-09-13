# Схема данных (целевая, все ЛР1)

Ниже — полная целевая модель проекта. На старте в `main` реализована
только часть (см. `docs/TASKS.md`), остальное добавляется участниками
команды в своих ветках.

```
erDiagram
    DEPUTY ||--o{ COMMISSION_MEMBERSHIP : "состоит в"
    COMMISSION ||--o{ COMMISSION_MEMBERSHIP : "включает"
    COMMISSION ||--o{ MEETING : "проводит"
    MEETING ||--o{ ATTENDANCE : "фиксирует"
    DEPUTY ||--o{ ATTENDANCE : "отмечается на"

    DEPUTY {
        int id PK
        string full_name
        string party
        string election_district
        bool is_active
        datetime created_at
    }

    COMMISSION {
        int id PK
        string name UK
        string description
        datetime created_at
    }

    COMMISSION_MEMBERSHIP {
        int id PK
        int commission_id FK
        int deputy_id FK
        bool is_chair
        date joined_at
    }

    MEETING {
        int id PK
        int commission_id FK "nullable — пленарное заседание"
        string title
        datetime scheduled_at
        string status "scheduled|held|cancelled"
        text agenda
    }

    ATTENDANCE {
        int id PK
        int meeting_id FK
        int deputy_id FK
        string status "present|absent|excused"
        string note
    }
```

## Реализовано в `main`

- `Deputy`, `Commission` — CRUD, без бизнес-правил (задача #1).

## В работе / предстоит

- `CommissionMembership` — задача #2 (см. `docs/tasks/ISSUE-2-membership.md`).
- `Meeting`, `Attendance` — задача #3 (см. `docs/tasks/ISSUE-3-meetings-attendance.md`).

## Ключевые ограничения (целевые, для справки)

- `COMMISSION.name` — уникально (уже реализовано).
- `COMMISSION_MEMBERSHIP` — уникальная пара `(commission_id, deputy_id)`.
- `ATTENDANCE` — уникальная пара `(meeting_id, deputy_id)`.
- Председатель (`is_chair = true`) — не более одного на комиссию.
- Внешние ключи — `ON DELETE CASCADE`.
