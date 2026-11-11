"""make release-check — проверка работающего релиза после развёртывания (ЛР6).

Успешная сборка не равна успешному релизу: версия принимается, только если
в целевом окружении:

  1. /health отвечает 200 и status=ok;
  2. /version показывает ожидаемую версию (тег или SHA коммита) — запущено
     именно то, что выкатывали, а не старый контейнер;
  3. веб-интерфейс /ui/ открывается;
  4. проходит основной пользовательский сценарий: депутат -> комиссия ->
     член комиссии -> заседание -> отметка присутствия -> заседание проведено
     (правило кворума) -> посещаемость видна в списке.

Созданные проверкой записи удаляются в конце (и при ошибке тоже).
Любой непройденный шаг — код возврата 1 с понятной причиной.

    python scripts/release_check.py --url http://192.168.58.139:8800 --expect <тег|SHA>

Скрипт использует только стандартную библиотеку — запускается в CI в лёгком
образе без установки зависимостей проекта.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

Response = tuple[int, object]
Transport = Callable[[str, str, dict | None], Response]


class CheckFailed(Exception):
    pass


def http_transport(base_url: str, timeout: float = 10) -> Transport:
    def call(method: str, path: str, payload: dict | None = None) -> Response:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            base_url.rstrip("/") + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return resp.status, _decode(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise CheckFailed(f"{method} {path}: нет ответа ({exc})") from exc

    return call


def _decode(body: bytes) -> object:
    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def version_matches(expected: str, info: dict) -> bool:
    """Ожидаемое значение — тег (v0.6.0), короткий или полный SHA коммита."""
    if not expected:
        return True
    version = str(info.get("version", ""))
    commit = str(info.get("commit", ""))
    if expected == version:
        return True
    return len(expected) >= 7 and commit.startswith(expected)


def expect_status(response: Response, code: int, step: str) -> object:
    status, body = response
    if status != code:
        raise CheckFailed(f"{step}: ожидался HTTP {code}, получен {status}: {body}")
    return body


class ReleaseCheck:
    def __init__(self, call: Transport, expected: str, log=print) -> None:
        self.call = call
        self.expected = expected
        self.log = log
        self.cleanup: list[str] = []

    def step(self, title: str) -> None:
        self.log(f"  - {title}")

    def run(self) -> None:
        self.step("/health")
        body = expect_status(self.call("GET", "/health", None), 200, "/health")
        if not isinstance(body, dict) or body.get("status") != "ok":
            raise CheckFailed(f"/health: неожиданный ответ {body}")

        self.step("/version")
        info = expect_status(self.call("GET", "/version", None), 200, "/version")
        if not isinstance(info, dict):
            raise CheckFailed(f"/version: неожиданный ответ {info}")
        self.log(
            f"    запущена версия {info.get('version')} "
            f"(коммит {str(info.get('commit'))[:12]}, окружение {info.get('environment')})"
        )
        if not version_matches(self.expected, info):
            raise CheckFailed(
                f"/version: ожидалась версия {self.expected}, запущена "
                f"{info.get('version')} ({info.get('commit')})"
            )

        self.step("веб-интерфейс /ui/")
        expect_status(self.call("GET", "/ui/", None), 200, "/ui/")

        try:
            self.scenario()
        finally:
            self.clean()

    def scenario(self) -> None:
        mark = f"release-check {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {uuid.uuid4().hex[:6]}"
        self.step("сценарий: депутат -> комиссия -> заседание -> кворум")
        deputy = expect_status(
            self.call("POST", "/deputies", {"full_name": f"Депутат {mark}"}),
            201,
            "создание депутата",
        )
        self.cleanup.append(f"/deputies/{deputy['id']}")

        commission = expect_status(
            self.call("POST", "/commissions", {"name": f"Комиссия {mark}"}),
            201,
            "создание комиссии",
        )
        self.cleanup.insert(0, f"/commissions/{commission['id']}")

        expect_status(
            self.call(
                "POST",
                f"/commissions/{commission['id']}/members",
                {"deputy_id": deputy["id"], "is_chair": True},
            ),
            201,
            "включение депутата в комиссию",
        )
        meeting = expect_status(
            self.call(
                "POST",
                "/meetings",
                {
                    "commission_id": commission["id"],
                    "title": f"Заседание {mark}",
                    "scheduled_at": datetime.now(timezone.utc).isoformat(),
                },
            ),
            201,
            "создание заседания",
        )
        expect_status(
            self.call(
                "POST",
                f"/meetings/{meeting['id']}/attendance",
                {"deputy_id": deputy["id"], "status": "present"},
            ),
            201,
            "отметка присутствия",
        )
        held = expect_status(
            self.call("PATCH", f"/meetings/{meeting['id']}/status", {"status": "held"}),
            200,
            "проведение заседания (кворум)",
        )
        if held.get("status") != "held":
            raise CheckFailed(f"заседание не переведено в held: {held}")
        attendance = expect_status(
            self.call("GET", f"/meetings/{meeting['id']}/attendance", None),
            200,
            "список посещаемости",
        )
        if len(attendance) != 1 or attendance[0].get("status") != "present":
            raise CheckFailed(f"посещаемость не сохранилась: {attendance}")
        self.log("    сценарий выполнен: заседание проведено, посещаемость сохранена")

    def clean(self) -> None:
        for path in self.cleanup:
            try:
                self.call("DELETE", path, None)
            except CheckFailed as exc:
                self.log(f"    не удалось удалить тестовую запись {path}: {exc}")
        self.cleanup.clear()


def wait_for_app(call: Transport, attempts: int, delay: float) -> None:
    for attempt in range(1, attempts + 1):
        try:
            if call("GET", "/health", None)[0] == 200:
                return
        except CheckFailed:
            if attempt == attempts:
                raise
        time.sleep(delay)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="адрес окружения")
    parser.add_argument(
        "--expect", default="", help="ожидаемая версия: тег или SHA коммита"
    )
    parser.add_argument("--attempts", type=int, default=10)
    args = parser.parse_args()

    call = http_transport(args.url)
    print(f"Проверка релиза {args.url} (ожидается версия {args.expect or 'любая'}):")
    try:
        wait_for_app(call, args.attempts, delay=3)
        ReleaseCheck(call, args.expect).run()
    except CheckFailed as exc:
        print(f"\nПРОВЕРКА РЕЛИЗА НЕ ПРОЙДЕНА: {exc}", file=sys.stderr)
        print(
            "Выпуск не принят. Верните предыдущую версию (rollback).", file=sys.stderr
        )
        sys.exit(1)
    print("\nПроверка релиза пройдена: версия принята.")


if __name__ == "__main__":
    main()
