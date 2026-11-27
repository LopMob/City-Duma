"""Метрики приложения «Городская Дума» (ЛР8, п.4). Имена — в docs/OBSERVABILITY.md.

| Что нужно по заданию     | Метрика                                              |
|--------------------------|------------------------------------------------------|
| запросы                  | http_requests_total{method,route,status}             |
| ошибки                   | http_requests_total{status=~"5.."}, app_unhandled_exceptions_total |
| время ответа             | http_request_duration_seconds (гистограмма)          |
| состояние приложения     | app_info, app_db_up, process_start_time_seconds, память |
| предметная метрика       | duma_meetings{status}, duma_quorum_rejections_total   |
"""

import time

from app.observability import logs
from app.observability.metrics import (
    CGROUP_MEMORY_LIMIT,
    CGROUP_MEMORY_USAGE,
    Registry,
    read_first_int,
    read_rss_bytes,
)

REGISTRY = Registry()

HTTP_REQUESTS = REGISTRY.counter(
    "http_requests_total",
    "HTTP-запросы: метод, шаблон адреса, код ответа.",
    ("method", "route", "status"),
)
HTTP_DURATION = REGISTRY.histogram(
    "http_request_duration_seconds",
    "Время обработки HTTP-запроса, секунды.",
    ("method", "route"),
)
HTTP_IN_PROGRESS = REGISTRY.gauge(
    "http_requests_in_progress", "Запросы, которые обрабатываются сейчас."
)
UNHANDLED = REGISTRY.counter(
    "app_unhandled_exceptions_total",
    "Необработанные исключения (ответ 500).",
    ("route",),
)
APP_INFO = REGISTRY.gauge(
    "app_info", "Запущенная версия: всегда 1.", ("version", "commit", "environment")
)
DB_UP = REGISTRY.gauge(
    "app_db_up", "Доступна ли база данных приложению (1 — да, 0 — нет)."
)
START_TIME = REGISTRY.gauge(
    "process_start_time_seconds", "Время запуска процесса (unixtime)."
)
MEMORY_USAGE = REGISTRY.gauge(
    "app_memory_usage_bytes", "Память контейнера приложения (cgroup), байты."
)
MEMORY_LIMIT = REGISTRY.gauge(
    "app_memory_limit_bytes", "Ограничение памяти контейнера (mem_limit), байты."
)
LOG_REDACTIONS = REGISTRY.counter(
    "app_log_redactions_total",
    "Сколько раз в журнал пытались записать секрет (значение замаскировано).",
)
MEETINGS = REGISTRY.gauge(
    "duma_meetings", "Заседания по статусам (scheduled, held, cancelled).", ("status",)
)
QUORUM_REJECTIONS = REGISTRY.counter(
    "duma_quorum_rejections_total",
    "Попытки провести заседание без кворума (отклонены).",
)

START_TIME.set(time.time())
logs.on_redaction(lambda count: LOG_REDACTIONS.inc(count))


@REGISTRY.collector
def _process_memory() -> None:
    usage = read_first_int(CGROUP_MEMORY_USAGE) or read_rss_bytes()
    if usage is not None:
        MEMORY_USAGE.set(usage)
    limit = read_first_int(CGROUP_MEMORY_LIMIT)
    if limit is not None:
        MEMORY_LIMIT.set(limit)


def set_app_info(version: str, commit: str, environment: str) -> None:
    APP_INFO.clear()
    APP_INFO.set(1, version=version, commit=commit[:12], environment=environment)
