from __future__ import annotations

import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Any

_COUNTER_LOCK = threading.Lock()
_COUNTERS: dict[str, float] = defaultdict(float)
_HISTOGRAMS: dict[str, dict[str, float]] = defaultdict(
    lambda: {
        "count": 0.0,
        "sum": 0.0,
        "min": float("inf"),
        "max": 0.0,
    }
)


def _metric_key(name: str, labels: dict[str, Any] | None = None) -> str:
    if not labels:
        return name
    normalized = ",".join(
        f"{str(key)}={str(value)}" for key, value in sorted(labels.items(), key=lambda item: str(item[0]))
    )
    return f"{name}|{normalized}"


def increment_counter(name: str, value: float = 1.0, *, labels: dict[str, Any] | None = None) -> None:
    key = _metric_key(name, labels)
    with _COUNTER_LOCK:
        _COUNTERS[key] += float(value)


def observe_histogram(name: str, value: float, *, labels: dict[str, Any] | None = None) -> None:
    key = _metric_key(name, labels)
    sample = float(value)
    with _COUNTER_LOCK:
        hist = _HISTOGRAMS[key]
        hist["count"] += 1.0
        hist["sum"] += sample
        hist["min"] = min(hist["min"], sample)
        hist["max"] = max(hist["max"], sample)


def snapshot_metrics() -> dict[str, Any]:
    with _COUNTER_LOCK:
        counters = dict(_COUNTERS)
        histograms: dict[str, dict[str, float]] = {}
        for key, values in _HISTOGRAMS.items():
            count = float(values["count"])
            total = float(values["sum"])
            histograms[key] = {
                "count": count,
                "sum": total,
                "avg": (total / count) if count else 0.0,
                "min": 0.0 if values["min"] == float("inf") else float(values["min"]),
                "max": float(values["max"]),
            }
    return {
        "generated_at": time.time(),
        "counters": counters,
        "histograms": histograms,
    }


@contextmanager
def timed_metric(name: str, *, labels: dict[str, Any] | None = None):
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        observe_histogram(name, elapsed_ms, labels=labels)
