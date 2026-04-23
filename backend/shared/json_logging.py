"""Structured JSON log formatter for all Pulse Engine services.

Emits each log record as a single JSON object with trace context,
service name, and request metadata for machine-parseable observability.
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any

from shared.tracing import current_trace_context


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def __init__(self, service_name: str = "") -> None:
        super().__init__()
        self._service_name = service_name or os.environ.get("SERVICE_NAME", "unknown")

    def format(self, record: logging.LogRecord) -> str:
        trace = current_trace_context()
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service_name,
        }
        if trace:
            entry["trace_id"] = trace.trace_id
            entry["span_id"] = trace.span_id
            entry["request_id"] = trace.request_id
            if trace.parent_span_id:
                entry["parent_span_id"] = trace.parent_span_id
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Exception",
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }
        # Merge any extra fields
        for key in ("request_id", "company_id", "user_id", "endpoint", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                entry[key] = value
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_structured_logging(service_name: str = "", level: int = logging.INFO) -> None:
    """Replace the root logger's handlers with structured JSON output."""
    root = logging.getLogger()
    root.setLevel(level)
    # Remove existing handlers to avoid duplicate output
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter(service_name=service_name))
    root.addHandler(handler)
    # Suppress noisy libraries
    for noisy in ("httpx", "httpcore", "asyncio", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
