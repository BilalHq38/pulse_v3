from __future__ import annotations

import contextvars
import re
import secrets
from dataclasses import dataclass
from typing import Mapping

_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_TRACEPARENT_RE = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})(?:-.+)?$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class TraceContext:
    request_id: str
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    sampled: bool = True
    tracestate: str = ""


_TRACE_CONTEXT: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar(
    "trace_context",
    default=None,
)


def _normalize_request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    return candidate or secrets.token_hex(16)


def _normalize_trace_id(value: str | None = "") -> str:
    candidate = (value or "").strip().lower()
    if _TRACE_ID_RE.fullmatch(candidate) and candidate != "0" * 32:
        return candidate
    return secrets.token_hex(16)


def _normalize_span_id(value: str | None = "") -> str:
    candidate = (value or "").strip().lower()
    if _SPAN_ID_RE.fullmatch(candidate) and candidate != "0" * 16:
        return candidate
    return secrets.token_hex(8)


def _parse_traceparent(value: str | None) -> tuple[str, str, bool] | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    match = _TRACEPARENT_RE.fullmatch(candidate)
    if not match:
        return None
    version, trace_id, span_id, flags = match.groups()
    if version.lower() != "00":
        return None
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return trace_id.lower(), span_id.lower(), bool(int(flags, 16) & 1)


def seed_trace_context(
    headers: Mapping[str, str] | None = None,
    *,
    request_id: str | None = None,
) -> TraceContext:
    normalized_headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    existing_request_id = (
        request_id or normalized_headers.get("x-request-id") or normalized_headers.get("x-correlation-id")
    )
    resolved_request_id = _normalize_request_id(existing_request_id)
    parsed_traceparent = _parse_traceparent(normalized_headers.get("traceparent"))
    if parsed_traceparent:
        trace_id, parent_span_id, sampled = parsed_traceparent
        tracestate = normalized_headers.get("tracestate", "").strip()
    else:
        trace_id = _normalize_trace_id(normalized_headers.get("x-trace-id"))
        parent_span_id = (
            _normalize_span_id(normalized_headers.get("x-span-id")) if normalized_headers.get("x-span-id") else ""
        )
        sampled = True
        tracestate = normalized_headers.get("tracestate", "").strip()
    context = TraceContext(
        request_id=resolved_request_id,
        trace_id=trace_id,
        span_id=secrets.token_hex(8),
        parent_span_id=parent_span_id,
        sampled=sampled,
        tracestate=tracestate,
    )
    _TRACE_CONTEXT.set(context)
    return context


def ensure_trace_context(
    headers: Mapping[str, str] | None = None,
    *,
    request_id: str | None = None,
) -> TraceContext:
    context = _TRACE_CONTEXT.get()
    if context is not None and headers is None and request_id is None:
        return context
    return seed_trace_context(headers, request_id=request_id)


def current_trace_context() -> TraceContext | None:
    return _TRACE_CONTEXT.get()


def current_trace_headers() -> dict[str, str]:
    context = current_trace_context()
    if context is None:
        return {}
    headers = {
        "X-Request-ID": context.request_id,
        "X-Correlation-ID": context.request_id,
        "X-Trace-ID": context.trace_id,
        "X-Span-ID": context.span_id,
        "traceparent": f"00-{context.trace_id}-{context.span_id}-{'01' if context.sampled else '00'}",
    }
    if context.parent_span_id:
        headers["X-Parent-Span-ID"] = context.parent_span_id
    if context.tracestate:
        headers["tracestate"] = context.tracestate
    return headers


def trace_log_fields() -> dict[str, str]:
    context = current_trace_context()
    if context is None:
        return {}
    return {
        "request_id": context.request_id,
        "trace_id": context.trace_id,
        "span_id": context.span_id,
        "parent_span_id": context.parent_span_id,
    }
