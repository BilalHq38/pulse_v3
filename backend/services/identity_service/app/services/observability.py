from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Any

_LOGGING_CONFIGURED = False
_METRIC_LOCK = threading.Lock()
_COUNTERS: dict[str, float] = defaultdict(float)
_HISTOGRAMS: dict[str, dict[str, float]] = defaultdict(
    lambda: {
        "count": 0.0,
        "sum": 0.0,
        "min": float("inf"),
        "max": 0.0,
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (
            "tenant_id",
            "event_type",
            "path",
            "method",
            "status_code",
            "duration_ms",
            "request_id",
            "trace_id",
            "operation",
        ):
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_structured_logging() -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    level_name = (os.environ.get("IDENTITY_LOG_LEVEL") or os.environ.get("LOG_LEVEL") or "INFO").upper()
    log_level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(log_level)

    _LOGGING_CONFIGURED = True


def _metric_key(name: str, labels: dict[str, Any] | None = None) -> str:
    if not labels:
        return name
    normalized = ",".join(
        f"{str(key)}={str(value)}" for key, value in sorted(labels.items(), key=lambda item: str(item[0]))
    )
    return f"{name}|{normalized}"


def increment_counter(name: str, value: float = 1.0, *, labels: dict[str, Any] | None = None) -> None:
    key = _metric_key(name, labels)
    with _METRIC_LOCK:
        _COUNTERS[key] += float(value)


def observe_histogram(name: str, value: float, *, labels: dict[str, Any] | None = None) -> None:
    key = _metric_key(name, labels)
    sample = float(value)
    with _METRIC_LOCK:
        hist = _HISTOGRAMS[key]
        hist["count"] += 1.0
        hist["sum"] += sample
        hist["min"] = min(hist["min"], sample)
        hist["max"] = max(hist["max"], sample)


@contextmanager
def timed_histogram(name: str, *, labels: dict[str, Any] | None = None):
    started = time.perf_counter()
    try:
        yield
    finally:
        observe_histogram(name, (time.perf_counter() - started) * 1000.0, labels=labels)


def snapshot_metrics() -> dict[str, Any]:
    with _METRIC_LOCK:
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
