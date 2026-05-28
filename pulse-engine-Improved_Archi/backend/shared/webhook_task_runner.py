from __future__ import annotations

import json
import logging
import traceback
from typing import Any

from core.utils import make_id
from shared.background_queue import in_background_worker_execution
from shared.database import create_detached_task

logger = logging.getLogger(__name__)

_DLQ_SCHEMA_READY = False
_DLQ_SCHEMA_LOCK = None
_SAFE_TASK_FUNCTION = "_run_safe_task"
DEPLOYMENT_SCHEMA_MIGRATION = "backend/sql_migrations/011_deployment_runtime_schema_hardening.sql"


async def _ensure_dead_letter_queue_table(db) -> None:
    global _DLQ_SCHEMA_READY, _DLQ_SCHEMA_LOCK
    if _DLQ_SCHEMA_READY:
        return
    if _DLQ_SCHEMA_LOCK is None:
        import asyncio

        _DLQ_SCHEMA_LOCK = asyncio.Lock()
    async with _DLQ_SCHEMA_LOCK:
        if _DLQ_SCHEMA_READY:
            return
        required_columns = ("task_name", "event_id", "trace_id", "company_id", "channel", "source_queue", "error")
        missing = []
        relation_exists = bool(await db.fetchval("SELECT to_regclass($1) IS NOT NULL", "dead_letter_queue"))
        if not relation_exists:
            missing.append("relation:dead_letter_queue")
        else:
            for column in required_columns:
                exists = bool(
                    await db.fetchval(
                        "SELECT EXISTS("
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='dead_letter_queue' AND column_name=$1"
                        ")",
                        column,
                    )
                )
                if not exists:
                    missing.append(f"column:dead_letter_queue.{column}")
        if missing:
            logger.error(
                "runtime_schema_migration_required area=dead_letter_queue migration=%s missing=%s",
                DEPLOYMENT_SCHEMA_MIGRATION,
                ",".join(missing),
            )
            raise RuntimeError(f"Dead letter queue schema is missing required objects. Run {DEPLOYMENT_SCHEMA_MIGRATION}.")
        _DLQ_SCHEMA_READY = True


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)
    except Exception:
        return json.dumps({"value": str(value)}, ensure_ascii=True, separators=(",", ":"))


def _single_line_traceback(traceback_text: str) -> str:
    return str(traceback_text or "").strip().replace("\r", "\\r").replace("\n", "\\n")


def _describe_coroutine(task_coro) -> dict[str, Any]:
    frame = getattr(task_coro, "cr_frame", None)
    code = getattr(task_coro, "cr_code", None)
    module_name = str(getattr(frame, "f_globals", {}).get("__name__", "") or "").strip() if frame else ""
    function_name = str(getattr(code, "co_name", "") or "").strip() if code else ""
    return {
        "module": module_name,
        "function": function_name,
    }


def _extract_nested_task_spec(serialized_task_coro: Any) -> dict[str, Any]:
    if not isinstance(serialized_task_coro, dict):
        return {}
    nested = serialized_task_coro.get("__pulse_background_queue_coroutine__")
    if not isinstance(nested, dict):
        return {}
    return nested


def _build_payload(
    *,
    task_name: str,
    task_target: dict[str, Any],
    event_id: str,
    trace_id: str,
    company_id: str,
    channel: str,
    metadata: dict[str, Any] | None,
    payload: Any,
) -> str:
    return _json_text(
        {
            "task_name": task_name,
            "task_target": task_target,
            "event_id": event_id,
            "trace_id": trace_id,
            "company_id": company_id,
            "channel": channel,
            "metadata": metadata or {},
            "payload": payload,
        }
    )


async def _write_dead_letter_record(
    db,
    *,
    task_name: str,
    task_target: dict[str, Any],
    event_id: str,
    trace_id: str,
    company_id: str,
    channel: str,
    source_queue: str,
    metadata: dict[str, Any] | None,
    payload: Any,
    error_message: str,
    max_retries: int = 3,
) -> None:
    try:
        await _ensure_dead_letter_queue_table(db)
        await db.execute(
            "INSERT INTO dead_letter_queue("
            "id,task_name,event_id,trace_id,company_id,channel,source_queue,event_type,payload,error,error_message,retry_count,max_retries,status,created_at,updated_at"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,0,$12,'failed',NOW(),NOW())",
            make_id(),
            str(task_name or "detached_task").strip() or "detached_task",
            str(event_id or "").strip(),
            str(trace_id or "").strip(),
            str(company_id or "").strip(),
            str(channel or "").strip(),
            str(source_queue or "detached_async_tasks").strip() or "detached_async_tasks",
            str(task_name or "detached_task").strip() or "detached_task",
            _build_payload(
                task_name=task_name,
                task_target=task_target,
                event_id=event_id,
                trace_id=trace_id,
                company_id=company_id,
                channel=channel,
                metadata=metadata,
                payload=payload,
            ),
            str(error_message or "")[:4000],
            str(error_message or "")[:4000],
            max(1, int(max_retries or 3)),
        )
    except Exception:
        logger.exception(
            "safe_task_dead_letter_write_failed task=%s event_id=%s trace_id=%s company_id=%s channel=%s",
            task_name,
            event_id,
            trace_id,
            company_id,
            channel,
        )


async def _run_safe_task(
    db,
    task_coro,
    *,
    task_name: str = "",
    event_id: str = "",
    trace_id: str = "",
    company_id: str = "",
    channel: str = "",
    source_queue: str = "detached_async_tasks",
    metadata: dict[str, Any] | None = None,
    payload: Any = None,
) -> None:
    safe_task_name = str(task_name or "unnamed-task").strip() or "unnamed-task"
    safe_company_id = str(company_id or "").strip()
    safe_channel = str(channel or "").strip()
    safe_event_id = str(event_id or "").strip()
    safe_trace_id = str(trace_id or "").strip()
    task_target = _describe_coroutine(task_coro)
    try:
        await task_coro
    except Exception as exc:
        traceback_text = traceback.format_exc()
        logger.error(
            "safe_task_failed task=%s event_id=%s trace_id=%s company_id=%s channel=%s error=%s traceback=%s",
            safe_task_name,
            safe_event_id,
            safe_trace_id,
            safe_company_id,
            safe_channel,
            exc,
            _single_line_traceback(traceback_text),
        )
        if in_background_worker_execution():
            raise
        await _write_dead_letter_record(
            db,
            task_name=safe_task_name,
            task_target=task_target,
            event_id=safe_event_id,
            trace_id=safe_trace_id,
            company_id=safe_company_id,
            channel=safe_channel,
            source_queue=source_queue,
            metadata=metadata,
            payload=payload,
            error_message=f"{exc}\n{traceback_text}",
        )


def create_safe_detached_task(
    db,
    task_coro,
    *,
    name: str | None = None,
    event_id: str = "",
    trace_id: str = "",
    company_id: str = "",
    channel: str = "",
    source_queue: str = "detached_async_tasks",
    metadata: dict[str, Any] | None = None,
    payload: Any = None,
    job_id: str | None = None,
    idempotency_key: str | None = None,
    timeout_seconds: float | None = None,
    service_label: str | None = None,
):
    safe_name = str(name or "unnamed-task").strip() or "unnamed-task"
    return create_detached_task(
        _run_safe_task(
            db,
            task_coro,
            task_name=safe_name,
            event_id=str(event_id or "").strip(),
            trace_id=str(trace_id or "").strip(),
            company_id=str(company_id or "").strip(),
            channel=str(channel or "").strip(),
            source_queue=str(source_queue or "detached_async_tasks").strip() or "detached_async_tasks",
            metadata=dict(metadata or {}),
            payload=payload,
        ),
        name=safe_name,
        job_id=job_id,
        idempotency_key=idempotency_key,
        timeout_seconds=timeout_seconds,
        service_label=service_label,
    )


async def record_final_background_task_failure(
    db,
    spec: dict[str, Any],
    *,
    error_message: str,
    traceback_text: str = "",
) -> None:
    if str(spec.get("module", "")).strip() != __name__:
        return
    if str(spec.get("function", "")).strip() != _SAFE_TASK_FUNCTION:
        return
    kwargs = dict(spec.get("kwargs", {}) or {})
    nested_task_spec = _extract_nested_task_spec(kwargs.get("task_coro"))
    task_name = str(kwargs.get("task_name", "") or spec.get("name", "") or "unnamed-task").strip() or "unnamed-task"
    event_id = str(kwargs.get("event_id", "") or "").strip()
    trace_id = str(kwargs.get("trace_id", "") or "").strip()
    company_id = str(kwargs.get("company_id", "") or "").strip()
    channel = str(kwargs.get("channel", "") or "").strip()
    source_queue = str(kwargs.get("source_queue", "") or "detached_async_tasks").strip() or "detached_async_tasks"
    metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
    payload = kwargs.get("payload")
    logger.error(
        "safe_task_permanent_failure task=%s event_id=%s trace_id=%s company_id=%s channel=%s error=%s traceback=%s",
        task_name,
        event_id,
        trace_id,
        company_id,
        channel,
        error_message,
        _single_line_traceback(traceback_text),
    )
    await _write_dead_letter_record(
        db,
        task_name=task_name,
        task_target={
            "module": str(nested_task_spec.get("module", "")).strip(),
            "function": str(nested_task_spec.get("function", "")).strip(),
        },
        event_id=event_id,
        trace_id=trace_id,
        company_id=company_id,
        channel=channel,
        source_queue=source_queue,
        metadata=metadata,
        payload=payload,
        error_message=f"{error_message}\n{traceback_text}".strip(),
        max_retries=int(spec.get("max_retries", 3) or 3),
    )


__all__ = [
    "create_safe_detached_task",
    "record_final_background_task_failure",
]
