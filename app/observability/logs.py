"""Структурированные журналы (ЛР8, п.1–3).

Каждая запись — одна строка JSON на stdout (её собирает Docker: `docker compose
logs app`). Обязательные поля: время (UTC), уровень, тип события, идентификатор
запроса; у HTTP-запросов ещё метод, адрес операции, код и результат.

Секреты в журнал не попадают:
  * поля с «секретными» именами (password, token, secret, authorization, ...)
    заменяются на "***" целиком;
  * в тексте маскируются пароль в строке подключения
    (postgresql://user:***@host), заголовок Authorization и пары вида
    password=..., token: ...;
  * тела запросов, строки запроса (?…) и заголовки не журналируются вообще.
Каждое маскирование увеличивает метрику app_log_redactions_total — по ней
срабатывает оповещение «в журнал пытались записать секрет».
"""

import json
import logging
import re
import sys
import traceback
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime

LOGGER_NAME = "city_duma"
MASK = "***"

# Идентификатор текущего запроса: его выставляет middleware, а форматтер
# добавляет в каждую запись, сделанную во время обработки этого запроса.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

SECRET_KEY = re.compile(
    r"pass(word|wd)?|secret|token|api[_-]?key|authorization|cookie|credential|"
    r"private[_-]?key|dsn|database_url",
    re.IGNORECASE,
)
_PATTERNS = [
    # postgresql+psycopg2://user:password@host -> user:***@host
    (
        re.compile(
            r"(?P<pre>[a-z][a-z0-9+.-]*://[^:/@\s]+:)[^@\s]+(?=@)", re.IGNORECASE
        ),
        r"\g<pre>",
    ),
    # Authorization: Bearer xxx / Basic xxx
    (
        re.compile(r"(?P<pre>\b(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
        r"\g<pre>",
    ),
    # password=xxx, token: xxx, "secret": "xxx"
    (
        re.compile(
            r"(?P<pre>\b[\w-]*(?:pass(?:word|wd)?|secret|token|api[_-]?key)[\w-]*"
            r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&]+",
            re.IGNORECASE,
        ),
        r"\g<pre>",
    ),
]


_factory_installed = False


def _install_record_factory() -> None:
    """Идентификатор запроса закрепляется за записью в момент её создания —
    даже если запись отформатируют позже (буфер, другой поток)."""
    global _factory_installed
    if _factory_installed:
        return
    base = logging.getLogRecordFactory()

    def factory(*args, **kwargs) -> logging.LogRecord:
        rec = base(*args, **kwargs)
        rec.request_id = request_id_var.get()
        return rec

    logging.setLogRecordFactory(factory)
    _factory_installed = True


_install_record_factory()

# Сюда middleware/метрики подставляют счётчик маскирований.
_on_redaction: list[Callable[[int], None]] = []


def on_redaction(callback: Callable[[int], None]) -> None:
    """Подписаться на маскирование (используется метрикой)."""
    _on_redaction.append(callback)


def _notify(count: int) -> None:
    if count:
        for callback in _on_redaction:
            callback(count)


def redact_text(text: str) -> tuple[str, int]:
    """Замаскировать секреты в строке. Возвращает (текст, сколько замен)."""
    total = 0
    for pattern, prefix in _PATTERNS:
        text, count = pattern.subn(prefix + MASK, text)
        total += count
    return text, total


def redact(value: object, key: str = "") -> tuple[object, int]:
    """Замаскировать секреты в значении поля (рекурсивно для dict/list)."""
    if key and SECRET_KEY.search(key) and value not in (None, "", MASK):
        return MASK, 1
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out, total = {}, 0
        for k, v in value.items():
            out[k], count = redact(v, str(k))
            total += count
        return out, total
    if isinstance(value, list | tuple):
        items, total = [], 0
        for v in value:
            item, count = redact(v)
            items.append(item)
            total += count
        return items, total
    return value, 0


# Стандартные атрибуты LogRecord — всё остальное пришло через extra={...}.
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Запись журнала -> одна строка JSON без секретов."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "event": getattr(record, "event", "log"),
            "request_id": getattr(record, "request_id", None) or "-",
            "logger": record.name,
        }
        message = record.getMessage()
        if message:
            entry["message"] = message
        for key, value in vars(record).items():
            if key not in _STANDARD and key not in entry:
                entry[key] = value
        if record.exc_info:
            entry["error"] = "".join(
                traceback.format_exception_only(*record.exc_info[:2])
            ).strip()
            entry["stack"] = "".join(traceback.format_exception(*record.exc_info))

        total = 0
        for key in list(entry):
            if key in ("time", "level", "event", "request_id", "logger"):
                continue
            entry[key], count = redact(entry[key], key)
            total += count
        _notify(total)
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Все журналы (приложения и uvicorn) — в JSON на stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn настраивает свои журналы до импорта приложения — переводим и их.
    # Журнал доступа uvicorn не нужен: каждый запрос пишет middleware.
    for name in ("uvicorn", "uvicorn.error"):
        uv = logging.getLogger(name)
        uv.handlers[:] = []
        uv.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers[:] = []
    access.propagate = False
    access.disabled = True


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
