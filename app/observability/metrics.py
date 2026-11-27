"""Метрики в текстовом формате Prometheus (ЛР8, п.4).

Свой небольшой реестр вместо сторонней библиотеки: приложение работает в одном
процессе uvicorn, а формат простой — так в проект не добавляется зависимость,
а сам код полностью покрыт тестами (tests/unit/test_metrics.py).

Формат: https://prometheus.io/docs/instrumenting/exposition_formats/
"""

import math
import threading
from collections.abc import Callable, Iterable, Sequence

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

LabelValues = tuple[str, ...]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(names: Sequence[str], values: Sequence[str], extra: str = "") -> str:
    parts = [f'{n}="{_escape(str(v))}"' for n, v in zip(names, values, strict=True)]
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}" if parts else ""


def _number(value: float) -> str:
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


class _Metric:
    kind = ""

    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        self.name, self.help, self.label_names = name, help_text, tuple(labels)
        self._lock = threading.Lock()

    def _key(self, labels: dict[str, str]) -> LabelValues:
        if set(labels) != set(self.label_names):
            raise ValueError(
                f"{self.name}: метки {sorted(labels)} != {self.label_names}"
            )
        return tuple(str(labels[n]) for n in self.label_names)

    def header(self) -> list[str]:
        return [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} {self.kind}"]

    def samples(self) -> Iterable[str]:
        raise NotImplementedError

    def render(self) -> list[str]:
        return self.header() + list(self.samples())


class Counter(_Metric):
    """Только растёт: число запросов, ошибок, событий."""

    kind = "counter"

    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labels)
        self._values: dict[LabelValues, float] = {}
        if not self.label_names:
            self._values[()] = 0.0

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError("счётчик не может уменьшаться")
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        return self._values.get(self._key(labels), 0.0)

    def samples(self) -> Iterable[str]:
        for key, value in sorted(self._values.items()):
            yield f"{self.name}{_labels(self.label_names, key)} {_number(value)}"


class Gauge(_Metric):
    """Текущее значение: состояние, память, число объектов."""

    kind = "gauge"

    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labels)
        self._values: dict[LabelValues, float] = {}

    def set(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self.inc(-amount, **labels)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    def value(self, **labels: str) -> float | None:
        return self._values.get(self._key(labels))

    def samples(self) -> Iterable[str]:
        for key, value in sorted(self._values.items()):
            yield f"{self.name}{_labels(self.label_names, key)} {_number(value)}"


class Histogram(_Metric):
    """Распределение: время ответа. Корзины накопительные (le = «не больше»)."""

    kind = "histogram"
    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

    def __init__(
        self,
        name: str,
        help_text: str,
        labels: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, help_text, labels)
        self.buckets = tuple(sorted(buckets)) + (math.inf,)
        self._counts: dict[LabelValues, list[int]] = {}
        self._sums: dict[LabelValues, float] = {}

    def observe(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * len(self.buckets))
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    counts[i] += 1
            self._sums[key] = self._sums.get(key, 0.0) + value

    def count(self, **labels: str) -> int:
        counts = self._counts.get(self._key(labels))
        return counts[-1] if counts else 0

    def samples(self) -> Iterable[str]:
        for key in sorted(self._counts):
            counts = self._counts[key]
            for bound, count in zip(self.buckets, counts, strict=True):
                le = f'le="{_number(bound)}"'
                yield f"{self.name}_bucket{_labels(self.label_names, key, le)} {count}"
            yield f"{self.name}_sum{_labels(self.label_names, key)} {_number(self._sums[key])}"
            yield f"{self.name}_count{_labels(self.label_names, key)} {counts[-1]}"


class Registry:
    """Набор метрик + функции, обновляющие значения перед выдачей (/metrics)."""

    def __init__(self) -> None:
        self._metrics: dict[str, _Metric] = {}
        self._collectors: list[Callable[[], None]] = []

    def register(self, metric: _Metric) -> None:
        if metric.name in self._metrics:
            raise ValueError(f"метрика {metric.name} уже есть")
        self._metrics[metric.name] = metric

    def counter(self, name: str, help_text: str, labels: Sequence[str] = ()) -> Counter:
        metric = Counter(name, help_text, labels)
        self.register(metric)
        return metric

    def gauge(self, name: str, help_text: str, labels: Sequence[str] = ()) -> Gauge:
        metric = Gauge(name, help_text, labels)
        self.register(metric)
        return metric

    def histogram(
        self,
        name: str,
        help_text: str,
        labels: Sequence[str] = (),
        buckets: Sequence[float] = Histogram.DEFAULT_BUCKETS,
    ) -> Histogram:
        metric = Histogram(name, help_text, labels, buckets)
        self.register(metric)
        return metric

    def collector(self, func: Callable[[], None]) -> Callable[[], None]:
        """Функция, которая вызывается перед каждой выдачей метрик."""
        self._collectors.append(func)
        return func

    def names(self) -> list[str]:
        return sorted(self._metrics)

    def render(self) -> str:
        for collect in self._collectors:
            collect()
        lines: list[str] = []
        for name in sorted(self._metrics):
            lines.extend(self._metrics[name].render())
        return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ процесс --


def read_first_int(paths: Iterable[str]) -> float | None:
    """Первое число из первого существующего файла (cgroup v2 / v1)."""
    for path in paths:
        try:
            with open(path, encoding="ascii") as fh:
                raw = fh.read().strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < 1 << 60:
            return float(raw)
        return None  # «max» (или огромное число в cgroup v1) — ограничения нет
    return None


def read_rss_bytes(path: str = "/proc/self/status") -> float | None:
    """Резидентная память процесса (VmRSS) в байтах."""
    try:
        with open(path, encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) * 1024
    except OSError:
        return None
    return None


CGROUP_MEMORY_USAGE = (
    "/sys/fs/cgroup/memory.current",
    "/sys/fs/cgroup/memory/memory.usage_in_bytes",
)
CGROUP_MEMORY_LIMIT = (
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
)
