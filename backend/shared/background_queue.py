from __future__ import annotations

import asyncio
import contextvars
import hashlib
import importlib
import inspect
import json
import logging
import os
import socket
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

try:
    from redis.asyncio import Redis
    from redis.exceptions import ResponseError
except Exception:  # pragma: no cover - optional dependency guard
    Redis = None
    ResponseError = Exception

from shared.config import (
    background_queue_consumer_prefix,
    background_queue_job_timeout_seconds,
    background_queue_job_ttl_seconds,
    background_queue_max_stream_length,
    background_queue_enabled,
    background_queue_max_retries,
    background_queue_stream_prefix,
    background_queue_url,
    background_queue_visibility_timeout_seconds,
    service_name,
)
from shared.metrics import increment_counter, observe_histogram, timed_metric

logger = logging.getLogger(__name__)

_DB_SENTINEL = {"__pulse_background_queue__": "db"}
_COROUTINE_SENTINEL_KEY = "__pulse_background_queue_coroutine__"
_QUEUE_CACHE: dict[str, "BackgroundQueue"] = {}
_BACKGROUND_WORKER_EXECUTION: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "background_worker_execution",
    default=False,
)


@dataclass(slots=True)
class BackgroundWorkerHandle:
    task: asyncio.Task
    stop_event: asyncio.Event
    queue: "BackgroundQueue | None" = None


def _looks_like_db_handle(value: Any) -> bool:
    return all(hasattr(value, attr) for attr in ("fetch", "fetchrow", "execute"))


def _serialize_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if inspect.iscoroutine(value):
        return {_COROUTINE_SENTINEL_KEY: serialize_coroutine(value)}
    if _looks_like_db_handle(value):
        return dict(_DB_SENTINEL)
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in value]
    if hasattr(value, "model_dump"):
        return _serialize_value(value.model_dump())
    raise TypeError(f"Unsupported background queue argument type: {type(value)!r}")


def _deserialize_value(value: Any, db) -> Any:
    if isinstance(value, dict):
        if value == _DB_SENTINEL:
            return db
        nested_coro = value.get(_COROUTINE_SENTINEL_KEY)
        if isinstance(nested_coro, dict):
            return _deserialize_coroutine_spec(nested_coro, db)
        return {key: _deserialize_value(item, db) for key, item in value.items()}
    if isinstance(value, list):
        return [_deserialize_value(item, db) for item in value]
    return value


def _deserialize_coroutine_spec(spec: dict[str, Any], db):
    module_name = str(spec.get("module", "")).strip()
    function_name = str(spec.get("function", "")).strip()
    if not module_name or not function_name:
        raise ValueError("Nested coroutine payload is missing module/function information")
    module = importlib.import_module(module_name)
    function = getattr(module, function_name, None)
    if function is None:
        raise AttributeError(f"Nested coroutine target {module_name}.{function_name} not found")
    kwargs = _deserialize_value(dict(spec.get("kwargs", {}) or {}), db)
    result = function(**kwargs)
    if inspect.isawaitable(result):
        return result
    raise TypeError(f"Nested coroutine target {module_name}.{function_name} did not return an awaitable")


def in_background_worker_execution() -> bool:
    return bool(_BACKGROUND_WORKER_EXECUTION.get())


def _stable_job_fingerprint(module_name: str, function_name: str, kwargs: dict[str, Any], name: str) -> str:
    payload = json.dumps(
        {
            "module": module_name,
            "function": function_name,
            "kwargs": kwargs,
            "name": name,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def serialize_coroutine(
    coro,
    *,
    name: str | None = None,
    job_id: str | None = None,
    idempotency_key: str | None = None,
    timeout_seconds: int | float | None = None,
) -> dict[str, Any]:
    if not inspect.iscoroutine(coro):
        raise TypeError("create_detached_task expects a coroutine object")
    frame = getattr(coro, "cr_frame", None)
    code = getattr(coro, "cr_code", None)
    if frame is None or code is None:
        raise ValueError("Coroutine is not serializable after it has started executing")
    module_name = str(frame.f_globals.get("__name__", "")).strip()
    function_name = str(code.co_name or "").strip()
    if not module_name or not function_name or function_name.startswith("<"):
        raise ValueError("Cannot serialize anonymous coroutine jobs")
    module = importlib.import_module(module_name)
    function = getattr(module, function_name, None)
    if function is None:
        raise ValueError(f"Job target {module_name}.{function_name} is not importable")
    signature = inspect.signature(function)
    locals_map = dict(frame.f_locals)
    kwargs: dict[str, Any] = {}
    for param_name, param in signature.parameters.items():
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            continue
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            extra_kwargs = locals_map.get(param_name, {})
            if isinstance(extra_kwargs, dict):
                for extra_key, extra_value in extra_kwargs.items():
                    kwargs[str(extra_key)] = _serialize_value(extra_value)
            continue
        if param_name in locals_map:
            kwargs[param_name] = _serialize_value(locals_map[param_name])
    job_key = str(
        job_id or idempotency_key or _stable_job_fingerprint(module_name, function_name, kwargs, name or function_name)
    ).strip()
    ttl_seconds = max(300, int(background_queue_job_ttl_seconds()))
    timeout_value = timeout_seconds if timeout_seconds is not None else background_queue_job_timeout_seconds()
    return {
        "module": module_name,
        "function": function_name,
        "kwargs": kwargs,
        "name": name or function_name,
        "job_id": job_key,
        "idempotency_key": str(idempotency_key or job_key),
        "timeout_seconds": timeout_value,
        "attempt": 0,
        "max_retries": background_queue_max_retries(),
        "queued_at": time.time(),
        "not_before": 0.0,
        "job_ttl_seconds": ttl_seconds,
    }


class BackgroundQueue:
    def __init__(self, *, service_label: str | None = None) -> None:
        self.service_label = (service_label or service_name("service")).strip() or "service"
        self.redis_url = background_queue_url()
        self.stream_name = f"{background_queue_stream_prefix()}:{self.service_label}"
        self.dead_letter_stream = f"{self.stream_name}:deadletter"
        self.group_name = f"{self.service_label}:workers"
        self.consumer_name = f"{background_queue_consumer_prefix()}-{socket.gethostname()}-{os.getpid()}"
        self.max_retries = max(1, background_queue_max_retries())
        self.visibility_timeout_seconds = max(30, background_queue_visibility_timeout_seconds())
        self.job_timeout_seconds = max(60, background_queue_job_timeout_seconds())
        self.max_stream_length = max(1000, int(background_queue_max_stream_length()))
        self.job_ttl_seconds = max(
            self.visibility_timeout_seconds * (self.max_retries + 2),
            int(background_queue_job_ttl_seconds()),
        )
        self._redis: Any | None = None
        self._group_ready = False
        self._group_lock = asyncio.Lock()
        if background_queue_enabled() and Redis and self.redis_url:
            self._redis = Redis.from_url(
                self.redis_url,
                decode_responses=True,
                health_check_interval=30,
                retry_on_timeout=True,
            )
            self._metric_labels = {"service": self.service_label}

    @property
    def enabled(self) -> bool:
        return self._redis is not None

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            self._group_ready = False

    async def ensure_ready(self) -> bool:
        if not self.enabled:
            return False
        if self._group_ready:
            return True
        async with self._group_lock:
            if self._group_ready:
                return True
            assert self._redis is not None
            try:
                await self._redis.xgroup_create(
                    name=self.stream_name,
                    groupname=self.group_name,
                    id="0-0",
                    mkstream=True,
                )
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    increment_counter(
                        "background_queue.group_create.errors",
                        labels=self._metric_labels,
                    )
                    raise
            self._group_ready = True
            increment_counter("background_queue.group_ready", labels=self._metric_labels)
            return True

    def _job_state_key(self, job_id: str) -> str:
        return f"{self.stream_name}:job:{job_id}"

    def _job_lock_key(self, job_id: str) -> str:
        return f"{self.stream_name}:lock:{job_id}"

    def _job_state_payload(
        self,
        *,
        job_id: str,
        spec: dict[str, Any],
        status: str,
        message_id: str = "",
        error: str = "",
    ) -> str:
        payload = {
            "job_id": job_id,
            "status": status,
            "name": spec.get("name", ""),
            "attempt": int(spec.get("attempt", 0) or 0),
            "message_id": message_id,
            "error": error[:1000],
            "queued_at": float(spec.get("queued_at", time.time()) or time.time()),
            "updated_at": time.time(),
            "timeout_seconds": float(spec.get("timeout_seconds", self.job_timeout_seconds) or self.job_timeout_seconds),
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))

    async def _load_job_state(self, job_id: str) -> dict[str, Any]:
        if not self.enabled:
            return {}
        assert self._redis is not None
        raw = await self._redis.get(self._job_state_key(job_id))
        if not raw:
            return {}
        try:
            state = json.loads(raw)
        except Exception:
            return {}
        return state if isinstance(state, dict) else {}

    async def enqueue_coroutine(
        self,
        coro,
        *,
        name: str | None = None,
        job_id: str | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: int | float | None = None,
    ) -> str | None:
        if not self.enabled:
            return None
        spec = serialize_coroutine(
            coro,
            name=name,
            job_id=job_id,
            idempotency_key=idempotency_key,
            timeout_seconds=timeout_seconds,
        )
        return await self.enqueue_spec(spec)

    async def enqueue_spec(self, spec: dict[str, Any]) -> str | None:
        if not self.enabled:
            return None
        await self.ensure_ready()
        assert self._redis is not None
        normalized = dict(spec)
        job_id = str(
            normalized.get("job_id")
            or normalized.get("idempotency_key")
            or _stable_job_fingerprint(
                str(normalized.get("module", "")),
                str(normalized.get("function", "")),
                dict(normalized.get("kwargs", {}) or {}),
                str(normalized.get("name", "")),
            )
        ).strip()
        normalized["job_id"] = job_id
        normalized["idempotency_key"] = str(normalized.get("idempotency_key") or job_id)
        normalized["timeout_seconds"] = float(
            normalized.get("timeout_seconds", self.job_timeout_seconds) or self.job_timeout_seconds
        )
        normalized["job_ttl_seconds"] = int(
            normalized.get("job_ttl_seconds", self.job_ttl_seconds) or self.job_ttl_seconds
        )
        state_key = self._job_state_key(job_id)
        existing_state = await self._load_job_state(job_id)
        if existing_state and existing_state.get("status") not in {"failed", "completed", "expired"}:
            increment_counter(
                "background_queue.jobs.deduped",
                labels={**self._metric_labels, "reason": "active_state"},
            )
            logger.info(
                "background_queue deduped service=%s job=%s job_id=%s status=%s",
                self.service_label,
                normalized.get("name", ""),
                job_id,
                existing_state.get("status", "unknown"),
            )
            return None
        payload = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
        state_created = await self._redis.set(
            state_key,
            self._job_state_payload(job_id=job_id, spec=normalized, status="queued"),
            ex=int(normalized["job_ttl_seconds"]),
            nx=True,
        )
        if not state_created:
            increment_counter(
                "background_queue.jobs.deduped",
                labels={**self._metric_labels, "reason": "state_exists"},
            )
            logger.info(
                "background_queue deduped service=%s job=%s job_id=%s reason=state_exists",
                self.service_label,
                normalized.get("name", ""),
                job_id,
            )
            return None
        try:
            message_id = await self._redis.xadd(
                self.stream_name,
                {"payload": payload},
                maxlen=self.max_stream_length,
                approximate=True,
            )
        except Exception:
            await self._redis.delete(state_key)
            increment_counter("background_queue.enqueue.errors", labels=self._metric_labels)
            raise
        await self._redis.set(
            state_key,
            self._job_state_payload(job_id=job_id, spec=normalized, status="queued", message_id=message_id),
            ex=int(normalized["job_ttl_seconds"]),
        )
        logger.info(
            "background_queue enqueued service=%s job=%s attempt=%s message_id=%s",
            self.service_label,
            normalized.get("name", ""),
            normalized.get("attempt", 0),
            message_id,
        )
        increment_counter("background_queue.jobs.enqueued", labels=self._metric_labels)
        return message_id

    async def start_worker(self, db) -> BackgroundWorkerHandle | None:
        if not self.enabled:
            return None
        await self.ensure_ready()
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            self._worker_loop(db, stop_event),
            name=f"{self.service_label}-background-worker",
        )
        return BackgroundWorkerHandle(task=task, stop_event=stop_event, queue=self)

    async def stop_worker(self, handle: BackgroundWorkerHandle | None) -> None:
        if handle is None:
            return
        handle.stop_event.set()
        handle.task.cancel()
        try:
            await handle.task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("background_queue worker shutdown failed")

    async def _worker_loop(self, db, stop_event: asyncio.Event) -> None:
        assert self._redis is not None
        while not stop_event.is_set():
            try:
                await self._claim_stale_messages(db)
                messages = await self._redis.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={self.stream_name: ">"},
                    count=10,
                    block=1000,
                )
                if not messages:
                    continue
                for _stream_name, batch in messages:
                    for message_id, payload in batch:
                        await self._process_message(db, message_id, payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                increment_counter("background_queue.worker.errors", labels=self._metric_labels)
                logger.exception(
                    "background_queue worker error service=%s: %s",
                    self.service_label,
                    exc,
                )
                await asyncio.sleep(1.0)

    async def _claim_stale_messages(self, db) -> None:
        assert self._redis is not None
        start_id = "0-0"
        idle_ms = self.visibility_timeout_seconds * 1000
        for _ in range(5):
            try:
                result = await self._redis.xautoclaim(
                    name=self.stream_name,
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    min_idle_time=idle_ms,
                    start_id=start_id,
                    count=10,
                )
            except Exception:
                return
            next_start_id = result[0] if isinstance(result, tuple) and result else "0-0"
            claimed = result[1] if isinstance(result, tuple) and len(result) > 1 else []
            if not claimed:
                return
            for message_id, payload in claimed:
                await self._process_message(db, message_id, payload, claimed=True)
            if next_start_id == "0-0":
                return
            start_id = next_start_id

    async def _process_message(
        self,
        db,
        message_id: str,
        payload: dict[str, Any],
        *,
        claimed: bool = False,
    ) -> None:
        assert self._redis is not None
        raw_payload = payload.get("payload", "") if isinstance(payload, dict) else ""
        try:
            spec = json.loads(raw_payload or "{}")
        except Exception:
            spec = {}
        job_id = str(
            spec.get("job_id")
            or spec.get("idempotency_key")
            or _stable_job_fingerprint(
                str(spec.get("module", "")),
                str(spec.get("function", "")),
                dict(spec.get("kwargs", {}) or {}),
                str(spec.get("name", "")),
            )
        ).strip()
        spec["job_id"] = job_id
        spec.setdefault("idempotency_key", job_id)
        attempt = int(spec.get("attempt", 0) or 0)
        not_before = float(spec.get("not_before", 0) or 0)
        if not_before and not_before > time.time():
            # Keep deferred jobs pending and return immediately so delayed jobs
            # do not block the single worker loop and starve ready jobs.
            increment_counter(
                "background_queue.jobs.deferred",
                labels=self._metric_labels,
            )
            return
        state_key = self._job_state_key(job_id)
        lock_key = self._job_lock_key(job_id)
        current_state = await self._load_job_state(job_id)
        state_message_id = str(current_state.get("message_id", "") or "").strip()
        if current_state.get("status") in {"completed", "failed"}:
            await self._redis.xack(self.stream_name, self.group_name, message_id)
            await self._redis.xdel(self.stream_name, message_id)
            return
        if state_message_id and state_message_id != message_id:
            await self._redis.xack(self.stream_name, self.group_name, message_id)
            await self._redis.xdel(self.stream_name, message_id)
            return
        lock_acquired = await self._redis.set(
            lock_key,
            message_id,
            ex=self.visibility_timeout_seconds,
            nx=True,
        )
        if not lock_acquired:
            increment_counter(
                "background_queue.jobs.lock_contention",
                labels=self._metric_labels,
            )
            return
        await self._redis.set(
            state_key,
            self._job_state_payload(job_id=job_id, spec=spec, status="executing", message_id=message_id),
            ex=int(spec.get("job_ttl_seconds", self.job_ttl_seconds) or self.job_ttl_seconds),
        )
        try:
            timeout_seconds = float(spec.get("timeout_seconds", self.job_timeout_seconds) or self.job_timeout_seconds)
            increment_counter(
                "background_queue.jobs.executing",
                labels=self._metric_labels,
            )
            with timed_metric(
                "background_queue.job_runtime_ms",
                labels=self._metric_labels,
            ):
                await asyncio.wait_for(self._execute_spec(db, spec), timeout=timeout_seconds)
            await self._redis.set(
                state_key,
                self._job_state_payload(job_id=job_id, spec=spec, status="completed", message_id=message_id),
                ex=int(spec.get("job_ttl_seconds", self.job_ttl_seconds) or self.job_ttl_seconds),
            )
            queued_at = float(spec.get("queued_at", time.time()) or time.time())
            observe_histogram(
                "background_queue.end_to_end_latency_ms",
                max((time.time() - queued_at) * 1000.0, 0.0),
                labels=self._metric_labels,
            )
            increment_counter("background_queue.jobs.completed", labels=self._metric_labels)
            await self._redis.delete(lock_key)
            await self._redis.xack(self.stream_name, self.group_name, message_id)
            await self._redis.xdel(self.stream_name, message_id)
            logger.info(
                "background_queue completed service=%s job=%s attempt=%s message_id=%s claimed=%s",
                self.service_label,
                spec.get("name", ""),
                attempt,
                message_id,
                claimed,
            )
        except Exception as exc:
            next_attempt = attempt + 1
            retryable = next_attempt <= int(spec.get("max_retries", self.max_retries))
            retry_delay = min(60.0, 2.0 * (2 ** max(attempt, 0)))
            if retryable:
                retry_spec = dict(spec)
                retry_spec["attempt"] = next_attempt
                retry_spec["not_before"] = time.time() + retry_delay
                retry_spec["job_id"] = job_id
                retry_spec["idempotency_key"] = spec.get("idempotency_key", job_id)
                retry_spec["job_ttl_seconds"] = spec.get("job_ttl_seconds", self.job_ttl_seconds)
                retry_message_id = await self._redis.xadd(
                    self.stream_name,
                    {"payload": json.dumps(retry_spec, ensure_ascii=True, separators=(",", ":"))},
                    maxlen=self.max_stream_length,
                    approximate=True,
                )
                state_updated = await self._redis.set(
                    state_key,
                    self._job_state_payload(
                        job_id=job_id, spec=retry_spec, status="retrying", message_id=retry_message_id, error=str(exc)
                    ),
                    ex=int(spec.get("job_ttl_seconds", self.job_ttl_seconds) or self.job_ttl_seconds),
                )
                if not state_updated:
                    await self._redis.xdel(self.stream_name, retry_message_id)
                    await self._redis.delete(lock_key)
                    increment_counter(
                        "background_queue.retry.state_update_errors",
                        labels=self._metric_labels,
                    )
                    logger.warning(
                        "background_queue retry state update failed service=%s job=%s attempt=%s retry_in=%.1fs error=%s",  # noqa: E501
                        self.service_label,
                        spec.get("name", ""),
                        next_attempt,
                        retry_delay,
                        exc,
                    )
                    raise
                await self._redis.delete(lock_key)
                await self._redis.xack(self.stream_name, self.group_name, message_id)
                await self._redis.xdel(self.stream_name, message_id)
                increment_counter("background_queue.jobs.retried", labels=self._metric_labels)
                observe_histogram(
                    "background_queue.retry_delay_ms",
                    retry_delay * 1000.0,
                    labels=self._metric_labels,
                )
                logger.warning(
                    "background_queue retry service=%s job=%s attempt=%s retry_in=%.1fs retry_message_id=%s error=%s",
                    self.service_label,
                    spec.get("name", ""),
                    next_attempt,
                    retry_delay,
                    retry_message_id,
                    exc,
                )
            else:
                await self._redis.set(
                    state_key,
                    self._job_state_payload(
                        job_id=job_id, spec=spec, status="failed", message_id=message_id, error=str(exc)
                    ),
                    ex=int(spec.get("job_ttl_seconds", self.job_ttl_seconds) or self.job_ttl_seconds),
                )
                await self._redis.delete(lock_key)
                await self._redis.xadd(
                    self.dead_letter_stream,
                    {
                        "payload": json.dumps(spec, ensure_ascii=True, separators=(",", ":")),
                        "error": str(exc)[:1000],
                    },
                    maxlen=self.max_stream_length,
                    approximate=True,
                )
                try:
                    from shared.webhook_task_runner import record_final_background_task_failure

                    await record_final_background_task_failure(
                        db,
                        spec,
                        error_message=str(exc),
                        traceback_text=traceback.format_exc(),
                    )
                except Exception:
                    logger.exception("background_queue final dead-letter persistence failed")
                increment_counter("background_queue.jobs.dead_lettered", labels=self._metric_labels)
                logger.error(
                    "background_queue dead_letter service=%s job=%s attempt=%s error=%s",
                    self.service_label,
                    spec.get("name", ""),
                    attempt,
                    exc,
                )
            increment_counter("background_queue.jobs.failed", labels=self._metric_labels)
            await self._redis.xack(self.stream_name, self.group_name, message_id)
            await self._redis.xdel(self.stream_name, message_id)

    async def _execute_spec(self, db, spec: dict[str, Any]) -> Any:
        module_name = str(spec.get("module", "")).strip()
        function_name = str(spec.get("function", "")).strip()
        if not module_name or not function_name:
            raise ValueError("Background job payload is missing module/function information")
        module = importlib.import_module(module_name)
        function = getattr(module, function_name, None)
        if function is None:
            raise AttributeError(f"Background job target {module_name}.{function_name} not found")
        kwargs = _deserialize_value(dict(spec.get("kwargs", {}) or {}), db)
        token = _BACKGROUND_WORKER_EXECUTION.set(True)
        try:
            result = function(**kwargs)
            if inspect.isawaitable(result):
                return await result
            return result
        finally:
            _BACKGROUND_WORKER_EXECUTION.reset(token)


def get_background_queue(*, service_label: str | None = None) -> BackgroundQueue | None:
    if not background_queue_enabled():
        return None
    label = (service_label or service_name("service")).strip() or "service"
    queue = _QUEUE_CACHE.get(label)
    if queue is None:
        queue = BackgroundQueue(service_label=label)
        _QUEUE_CACHE[label] = queue
    return queue


async def collect_background_queue_snapshot() -> dict[str, Any]:
    if not background_queue_enabled() or not background_queue_url() or Redis is None:
        return {"enabled": False, "streams": []}
    client = Redis.from_url(
        background_queue_url(),
        decode_responses=True,
        health_check_interval=30,
        retry_on_timeout=True,
    )
    prefix = f"{background_queue_stream_prefix()}:"
    streams: list[dict[str, Any]] = []
    try:
        async for key in client.scan_iter(match=f"{prefix}*", count=100):
            stream_name = str(key)
            suffix = stream_name[len(prefix) :]
            if not suffix or ":" in suffix:
                continue
            try:
                info = await client.xinfo_stream(stream_name)
                groups = await client.xinfo_groups(stream_name)
                dead_letter_stream = f"{stream_name}:deadletter"
                dead_letter_length = 0
                try:
                    dead_letter_length = int(await client.xlen(dead_letter_stream) or 0)
                except Exception:
                    dead_letter_length = 0
                streams.append(
                    {
                        "stream_name": stream_name,
                        "length": int(info.get("length", 0) or 0),
                        "groups": len(groups),
                        "pending": int(sum(int(group.get("pending", 0) or 0) for group in groups)),
                        "last_generated_id": info.get("last-generated-id", ""),
                        "dead_letter_stream": dead_letter_stream,
                        "dead_letter_length": dead_letter_length,
                    }
                )
            except Exception:
                continue
    finally:
        await client.aclose()
    streams.sort(key=lambda item: item["stream_name"])
    return {
        "enabled": True,
        "prefix": background_queue_stream_prefix(),
        "visibility_timeout_seconds": background_queue_visibility_timeout_seconds(),
        "job_timeout_seconds": background_queue_job_timeout_seconds(),
        "max_retries": background_queue_max_retries(),
        "max_stream_length": background_queue_max_stream_length(),
        "streams": streams,
    }


async def start_background_queue_worker(db, *, service_label: str | None = None) -> BackgroundWorkerHandle | None:
    queue = get_background_queue(service_label=service_label)
    if queue is None:
        return None
    await queue.ensure_ready()
    return await queue.start_worker(db)


async def stop_background_queue_worker(handle: BackgroundWorkerHandle | None) -> None:
    if handle is None:
        return
    handle.stop_event.set()
    handle.task.cancel()
    try:
        await handle.task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("background_queue worker stop failed")
    if handle.queue is not None:
        await handle.queue.close()


async def enqueue_detached_coroutine(coro, *, name: str | None = None) -> bool:
    queue = get_background_queue()
    if queue is None:
        return False
    await queue.enqueue_coroutine(coro, name=name)
    return True


__all__ = [
    "BackgroundQueue",
    "BackgroundWorkerHandle",
    "collect_background_queue_snapshot",
    "enqueue_detached_coroutine",
    "get_background_queue",
    "in_background_worker_execution",
    "serialize_coroutine",
    "start_background_queue_worker",
    "stop_background_queue_worker",
]
