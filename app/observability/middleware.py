"""Журнал и метрики каждого HTTP-запроса (ЛР8, п.1–2, 4).

Обычный ASGI-middleware (без зависимостей от фреймворка):
  * идентификатор запроса: берётся из заголовка X-Request-ID (если он
    корректный) или создаётся новый; возвращается в ответе тем же заголовком
    и попадает во все записи журнала, сделанные во время запроса;
  * одна запись журнала на запрос: метод, адрес операции, шаблон маршрута,
    код, результат, длительность;
  * метрики: число запросов, время ответа, запросы в обработке, исключения.

В метках метрик — только шаблон маршрута (/meetings/{meeting_id}), а не
настоящий адрес: иначе каждый id создавал бы новый временной ряд.
"""

import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from time import perf_counter
from typing import Any

from app.observability import instruments as m
from app.observability import logs

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

REQUEST_ID_HEADER = b"x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Служебные адреса опрашиваются каждые 10–15 секунд (healthcheck, Prometheus):
# успешные ответы пишем на уровне DEBUG, чтобы они не забивали журнал.
QUIET_ROUTES = {"/health", "/ready", "/metrics"}
# Сами опросы /metrics в метрики запросов не включаем.
NOT_MEASURED = {"/metrics"}


def incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers") or []:
        if name.lower() == REQUEST_ID_HEADER:
            candidate = value.decode("latin-1").strip()
            if _VALID_REQUEST_ID.match(candidate):
                return candidate
    return None


def route_template(scope: Scope, mounts: tuple[str, ...]) -> str:
    """Шаблон маршрута, который обработал запрос (FastAPI кладёт его в scope)."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    if template:
        return template
    path = scope.get("path", "")
    for prefix in mounts:
        if path == prefix or path.startswith(prefix + "/"):
            return prefix
    return "unmatched"


def result_of(status: int) -> str:
    if status >= 500:
        return "server_error"
    if status >= 400:
        return "client_error"
    return "success"


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp, mounts: tuple[str, ...] = ("/ui",)) -> None:
        self.app = app
        self.mounts = mounts
        self.log = logs.get_logger()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = incoming_request_id(scope) or uuid.uuid4().hex
        token = logs.request_id_var.set(request_id)
        started = perf_counter()
        state = {"status": 500, "sent": False}

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["sent"] = True
                headers = list(message.get("headers") or [])
                headers.append((REQUEST_ID_HEADER, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        m.HTTP_IN_PROGRESS.inc()
        failure: BaseException | None = None
        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:  # журналируем и отвечаем 500
            failure = exc
            if state["sent"]:
                raise
            await self._internal_error(send_with_id, request_id)
        finally:
            m.HTTP_IN_PROGRESS.dec()
            self._record(scope, int(state["status"]), perf_counter() - started, failure)
            logs.request_id_var.reset(token)

    async def _internal_error(self, send: Send, request_id: str) -> None:
        body = json.dumps(
            {"detail": "Внутренняя ошибка сервера", "request_id": request_id},
            ensure_ascii=False,
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _record(
        self, scope: Scope, status: int, duration: float, failure: BaseException | None
    ) -> None:
        method = scope.get("method", "GET")
        route = route_template(scope, self.mounts)
        if route not in NOT_MEASURED:
            m.HTTP_REQUESTS.inc(method=method, route=route, status=str(status))
            m.HTTP_DURATION.observe(duration, method=method, route=route)
        if failure is not None:
            m.UNHANDLED.inc(route=route)

        if status >= 500:
            level = logging.ERROR
        elif status >= 400:
            level = logging.WARNING
        elif route in QUIET_ROUTES:
            level = logging.DEBUG
        else:
            level = logging.INFO
        client = scope.get("client") or ("-", 0)
        self.log.log(
            level,
            "",
            exc_info=(
                (type(failure), failure, failure.__traceback__) if failure else None
            ),
            extra={
                "event": "http_request",
                "method": method,
                # Адрес операции — без строки запроса (?…): в ней могут быть токены.
                "path": scope.get("path", ""),
                "route": route,
                "status": status,
                "result": "exception" if failure else result_of(status),
                "duration_ms": round(duration * 1000, 2),
                "client": client[0],
            },
        )
