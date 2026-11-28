"""make snapshot — снимок состояния системы до и после выпуска (ЛР8, п.12).

    python scripts/release_snapshot.py --url http://192.168.58.139:8800 \\
        --prometheus http://192.168.58.139:9090 --label before-v0.8.0

Сохраняет в docs/releases/<время>-<метка>.md:
  * какая версия запущена (/version), жив ли процесс (/health), доступна ли
    БД (/ready);
  * метрики приложения (/metrics): запросы, ошибки 5xx, заседания;
  * если указан Prometheus: доля ошибок и p95 времени ответа за 15 минут,
    перезапуски за час, сработавшие оповещения.

Два снимка (до и после) кладутся в MR выпуска — по ним видно, что изменилось.
Использует только стандартную библиотеку.
"""

import argparse
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

Fetch = Callable[[str], tuple[int, str]]

PROM_QUERIES = {
    "Доля ответов 5xx за 15 мин": 'sum(rate(http_requests_total{status=~"5.."}[15m])) '
    "/ sum(rate(http_requests_total[15m]))",
    "p95 времени ответа за 15 мин, с": "histogram_quantile(0.95, sum by (le) "
    "(rate(http_request_duration_seconds_bucket[15m])))",
    "Перезапуски приложения за 1 ч": 'changes(process_start_time_seconds{job="app"}[1h])',
    "Сработавшие оповещения": 'ALERTS{alertstate="firing"}',
}
APP_METRICS = ("app_db_up", "duma_meetings", "duma_quorum_rejections_total")


def http_fetch(url: str, timeout: float = 5) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


def endpoint(fetch: Fetch, url: str) -> str:
    status, body = fetch(url)
    if status == 0:
        return f"недоступен ({body})"
    return f"HTTP {status} `{body.strip()[:200]}`"


def app_metric_lines(text: str) -> list[str]:
    lines, totals = [], {"all": 0.0, "5xx": 0.0}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name = line.split("{", 1)[0].split(" ", 1)[0]
        if name == "http_requests_total":
            value = float(line.rsplit(" ", 1)[1])
            totals["all"] += value
            if 'status="5' in line:
                totals["5xx"] += value
        elif name in APP_METRICS:
            lines.append(f"- `{line}`")
    lines.insert(
        0,
        f"- HTTP-запросов с запуска: {totals['all']:.0f}, из них 5xx: {totals['5xx']:.0f}",
    )
    return lines


def prom_value(fetch: Fetch, base: str, query: str) -> str:
    status, body = fetch(
        f"{base}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    )
    if status != 200:
        return f"нет данных (HTTP {status})"
    result = json.loads(body).get("data", {}).get("result", [])
    if not result:
        return "нет (0)"
    if "alertname" in result[0].get("metric", {}):
        return ", ".join(sorted(r["metric"]["alertname"] for r in result))
    value = float(result[0]["value"][1])
    # NaN — запросов не было, делить не на что
    return "нет данных" if math.isnan(value) else f"{value:.4g}"


def snapshot(
    fetch: Fetch, url: str, prometheus: str | None, label: str, now: datetime
) -> str:
    url = url.rstrip("/")
    out = [
        f"# Снимок состояния: {label}",
        "",
        f"- Время (UTC): {now:%Y-%m-%d %H:%M:%S}",
        f"- Адрес: {url}",
        "",
        "## Приложение",
        "",
        f"- /version: {endpoint(fetch, url + '/version')}",
        f"- /health: {endpoint(fetch, url + '/health')}",
        f"- /ready: {endpoint(fetch, url + '/ready')}",
        "",
        "## Метрики приложения (/metrics)",
        "",
    ]
    status, text = fetch(url + "/metrics")
    out += (
        app_metric_lines(text) if status == 200 else [f"- недоступны (HTTP {status})"]
    )
    if prometheus:
        base = prometheus.rstrip("/")
        out += ["", f"## Prometheus ({base})", ""]
        out += [
            f"- {title}: {prom_value(fetch, base, q)}"
            for title, q in PROM_QUERIES.items()
        ]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None, fetch: Fetch = http_fetch) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="адрес приложения")
    parser.add_argument("--prometheus", help="адрес Prometheus (необязательно)")
    parser.add_argument(
        "--label", default="snapshot", help="метка: before-v0.8.0, after-v0.8.0"
    )
    parser.add_argument("--out-dir", default="docs/releases")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    text = snapshot(fetch, args.url, args.prometheus, args.label, now)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in args.label)
    path = out_dir / f"{now:%Y%m%d-%H%M%S}-{safe}.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"Сохранено: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
