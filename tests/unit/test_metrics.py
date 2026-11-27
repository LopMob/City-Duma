"""ЛР8: формат метрик Prometheus (app/observability/metrics.py)."""

import pytest

from app.observability import metrics


def test_counter_renders_help_type_and_labels():
    reg = metrics.Registry()
    c = reg.counter("demo_total", "Пример.", ("route",))
    c.inc(route="/a")
    c.inc(2, route="/a")
    text = reg.render()
    assert "# HELP demo_total Пример." in text
    assert "# TYPE demo_total counter" in text
    assert 'demo_total{route="/a"} 3' in text


def test_counter_without_labels_starts_at_zero_and_cannot_decrease():
    c = metrics.Counter("x_total", "x")
    assert "x_total 0" in c.render()
    with pytest.raises(ValueError):
        c.inc(-1)


def test_wrong_labels_are_rejected():
    c = metrics.Counter("y_total", "y", ("a",))
    with pytest.raises(ValueError):
        c.inc(b="1")


def test_label_values_are_escaped():
    g = metrics.Gauge("g", "g", ("v",))
    g.set(1, v='a"b\\c\nd')
    assert 'g{v="a\\"b\\\\c\\nd"} 1' in g.render()


def test_gauge_set_inc_dec_and_clear():
    g = metrics.Gauge("g2", "g")
    g.set(5)
    g.inc()
    g.dec(2)
    assert g.value() == 4
    g.clear()
    assert g.value() is None


def test_histogram_buckets_are_cumulative():
    h = metrics.Histogram("lat_seconds", "t", ("route",), buckets=(0.1, 1))
    for v in (0.05, 0.5, 3):
        h.observe(v, route="/r")
    text = "\n".join(h.render())
    assert 'lat_seconds_bucket{route="/r",le="0.1"} 1' in text
    assert 'lat_seconds_bucket{route="/r",le="1"} 2' in text
    assert 'lat_seconds_bucket{route="/r",le="+Inf"} 3' in text
    assert 'lat_seconds_count{route="/r"} 3' in text
    assert 'lat_seconds_sum{route="/r"} 3.55' in text
    assert h.count(route="/r") == 3


def test_collectors_run_before_render_and_names_are_unique():
    reg = metrics.Registry()
    g = reg.gauge("live", "l")
    reg.collector(lambda: g.set(7))
    assert "live 7" in reg.render()
    with pytest.raises(ValueError):
        reg.gauge("live", "again")


def test_cgroup_and_rss_readers(tmp_path):
    limit = tmp_path / "memory.max"
    limit.write_text("268435456\n")
    assert metrics.read_first_int([str(tmp_path / "missing"), str(limit)]) == 268435456
    limit.write_text("max\n")
    assert metrics.read_first_int([str(limit)]) is None
    limit.write_text(str(1 << 62))
    assert metrics.read_first_int([str(limit)]) is None
    status = tmp_path / "status"
    status.write_text("Name:\tpython\nVmRSS:\t  2048 kB\n")
    assert metrics.read_rss_bytes(str(status)) == 2048 * 1024
    assert metrics.read_rss_bytes(str(tmp_path / "none")) is None


def test_application_metrics_cover_the_assignment():
    from app.observability import instruments

    names = set(instruments.REGISTRY.names())
    assert {
        "http_requests_total",
        "http_request_duration_seconds",
        "app_db_up",
        "app_info",
        "duma_meetings",
        "duma_quorum_rejections_total",
    } <= names
