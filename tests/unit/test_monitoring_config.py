"""ЛР8: конфигурация наблюдаемости в репозитории согласована (п.10, п.14).

Барьер от «тихой» поломки мониторинга: переименовали метрику в приложении и
забыли про панель или правило; добавили правило без теста и без инструкции;
подняли контейнер мониторинга без лимитов или с плавающим тегом.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

from app.observability.instruments import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
MON = ROOT / "monitoring"
PROM = MON / "prometheus"
APP_METRIC = re.compile(r"\b((?:http|app|duma)_[a-z_]+|process_start_time_seconds)\b")
HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def alert_rules():
    groups = load_yaml(PROM / "alerts.yml")["groups"]
    return [rule for group in groups for rule in group["rules"]]


def dashboard_exprs():
    dash = json.loads((MON / "grafana/dashboards/city-duma.json").read_text("utf-8"))
    return dash, [
        t["expr"] for p in dash["panels"] for t in p.get("targets", []) if "expr" in t
    ]


def app_metrics_used(exprs):
    used = set()
    for expr in exprs:
        for name in APP_METRIC.findall(expr):
            for suffix in HISTOGRAM_SUFFIXES:
                if name.endswith(suffix) and name[: -len(suffix)] in REGISTRY.names():
                    name = name[: -len(suffix)]
            used.add(name)
    return used


def test_alerts_and_dashboard_use_only_existing_app_metrics():
    _, exprs = dashboard_exprs()
    exprs += [rule["expr"] for rule in alert_rules()]
    unknown = app_metrics_used(exprs) - set(REGISTRY.names())
    assert not unknown, f"метрик нет в app/observability/instruments.py: {unknown}"


def test_every_alert_has_severity_description_and_runbook_section():
    runbook = (ROOT / "docs/RUNBOOK.md").read_text(encoding="utf-8")
    for rule in alert_rules():
        name = rule["alert"]
        assert rule["labels"]["severity"] in {"warning", "critical"}, name
        notes = rule["annotations"]
        assert notes["summary"] and notes["description"], name
        assert notes["runbook"] == f"docs/RUNBOOK.md#{name}", name
        assert f'<a id="{name}"></a>' in runbook, f"нет раздела {name} в RUNBOOK.md"


def test_every_alert_has_a_promtool_test_that_fires_it():
    tests = load_yaml(PROM / "alerts.test.yml")
    assert tests["rule_files"] == ["alerts.yml"]
    fired = {
        case["alertname"]
        for t in tests["tests"]
        for case in t["alert_rule_test"]
        if case["exp_alerts"]
    }
    assert {rule["alert"] for rule in alert_rules()} <= fired


def test_prometheus_scrapes_app_host_and_containers():
    config = load_yaml(PROM / "prometheus.yml")
    jobs = {job["job_name"]: job for job in config["scrape_configs"]}
    assert {"app", "node", "cadvisor"} <= set(jobs)
    assert jobs["app"]["static_configs"][0]["targets"] == ["app:8000"]
    assert config["rule_files"] == ["alerts.yml"]


@pytest.mark.parametrize(
    "title",
    [
        "Приложение доступно",
        "Запросов в секунду по маршрутам",
        "Доля ответов 5xx",
        "Время ответа: p50, p95, p99",
        "CPU хоста",
        "Память приложения и лимит",
        "Свободно на диске",
        "Заседания по статусам",
    ],
)
def test_dashboard_has_required_panels(title):
    dash, _ = dashboard_exprs()
    assert title in {p["title"] for p in dash["panels"]}


def test_dashboard_ids_are_unique_and_datasource_is_provisioned():
    dash, _ = dashboard_exprs()
    ids = [p["id"] for p in dash["panels"]]
    assert len(ids) == len(set(ids))
    ds = load_yaml(MON / "grafana/provisioning/datasources/prometheus.yml")
    uid = ds["datasources"][0]["uid"]
    for panel in dash["panels"]:
        if panel["type"] != "row":
            assert panel["datasource"]["uid"] == uid, panel["title"]


def test_monitoring_containers_follow_container_policy():
    compose = load_yaml(MON / "compose.monitoring.yaml")
    for name, svc in compose["services"].items():
        image = svc["image"]
        tag = image.rsplit(":", 1)[-1] if ":" in image.split("/")[-1] else ""
        assert tag and tag != "latest", f"{name}: образ без точной версии"
        for limit in ("mem_limit", "cpus", "pids_limit"):
            assert limit in svc, f"{name}: нет {limit}"
        assert svc.get("healthcheck", {}).get("test"), f"{name}: нет healthcheck"
        assert svc.get("restart") == "unless-stopped", name
        for port in svc.get("ports", []):
            assert port.startswith(
                "${MONITORING_BIND"
            ), f"{name}: порт {port} без адреса"
    # пароль Grafana — только из переменной окружения
    env = compose["services"]["grafana"]["environment"]
    assert env["GF_SECURITY_ADMIN_PASSWORD"].startswith("${GRAFANA_ADMIN_PASSWORD")
    assert compose["networks"]["app"]["external"] is True
