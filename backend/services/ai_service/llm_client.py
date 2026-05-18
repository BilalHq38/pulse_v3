from __future__ import annotations

import asyncio
import ast
import json
import logging
import re
import time
from io import StringIO
from collections.abc import AsyncIterator
from typing import Any, Optional

import httpx
from pydantic import BaseModel

from shared.config import (
    ai_api_call_timeout_seconds,
    ai_enable_provider_fallback,
    ai_max_tokens,
    ai_max_provider_attempts,
    ai_model_name,
    ai_provider_name,
    ai_temperature,
    anthropic_api_key,
    anthropic_model_name,
    gemini_api_key,
    gemini_embedding_model_name,
    gemini_fallback_models,
    gemini_flash_model_name,
    gemini_pro_model_name,
    openai_api_key,
    openai_embedding_model_name,
    openai_model_name,
)
from services.ai_service.common import _extract_data_url_payload, _sanitize_schema, estimate_tokens
from services.ai_service.llm_tracking import get_llm_context, reserve_llm_call
from services.ai_service.model_catalog import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMINI_PRO_MODEL,
    is_supported_model,
    model_capabilities as catalog_model_capabilities,
    validate_model_selection,
)

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_errors = None
    genai_types = None

try:
    from google.api_core import exceptions as google_api_exceptions
except Exception:
    google_api_exceptions = None

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

try:
    from anthropic import AsyncAnthropic
except Exception:
    AsyncAnthropic = None


GEMINI_API_KEY = gemini_api_key()
OPENAI_API_KEY = openai_api_key()
ANTHROPIC_API_KEY = anthropic_api_key()

OPENAI_DEFAULT_MODEL = openai_model_name()
ANTHROPIC_DEFAULT_MODEL = anthropic_model_name()
FLASH_MODEL = gemini_flash_model_name()
PRO_MODEL = gemini_pro_model_name()
GEMINI_FALLBACK_MODELS = tuple(gemini_fallback_models())
GEMINI_EMBEDDING_MODEL = gemini_embedding_model_name()
OPENAI_EMBEDDING_MODEL = openai_embedding_model_name()
EMBEDDING_MODEL = GEMINI_EMBEDDING_MODEL
DEFAULT_TEMPERATURE = ai_temperature()
DEFAULT_MAX_TOKENS = ai_max_tokens()
_GEMINI_PREVIEW_MARKERS = ("preview", "exp", "experimental", "alpha")
_PROVIDER_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=3.0, read=60.0, write=10.0, pool=5.0)
_GEMINI_HTTP_TIMEOUT_MS = 60_000
_MAX_TRANSIENT_RETRIES = 3
_JSON_MAX_OUTPUT_TOKENS = 512
_EMAIL_CAMPAIGN_JSON_MIN_TOKENS = 900
_JSON_FORMAT_RETRY_ATTEMPTS = 3
_JSON_REPAIR_RAW_CHAR_LIMIT = 3000
_JSON_RETRY_TOKEN_BONUS = 512
_JSON_RETRY_MAX_OUTPUT_TOKENS = 4096
_GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS: set[str] = set()


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return parsed if parsed > 0 else default


def _token_limit_for_call(call_type: str = "", call_purpose: str = "", *, default: int | None = None) -> int:
    purpose = f"{call_type} {call_purpose}".lower()
    if "intent_classification" in purpose or "classification" in purpose or "classify" in purpose:
        return 50
    if "summary" in purpose or "summar" in purpose:
        return 250
    if "email_campaign_copy" in purpose or "email_html_body" in purpose:
        return max(_coerce_positive_int(default, _EMAIL_CAMPAIGN_JSON_MIN_TOKENS), _EMAIL_CAMPAIGN_JSON_MIN_TOKENS)
    if "json" in purpose:
        return _JSON_MAX_OUTPUT_TOKENS
    if "complex" in purpose or "agent" in purpose:
        return 2048
    if "chat" in purpose or "support_response" in purpose or "response_stream" in purpose:
        return 1024
    return _coerce_positive_int(default, DEFAULT_MAX_TOKENS)


def _text_engine(
    engine: dict | None,
    *,
    use_pro: bool = False,
    call_type: str = "",
    call_purpose: str = "",
) -> dict:
    selected = dict(engine or _default_engine(use_pro=use_pro))
    selected = _coerce_supported_runtime_engine(selected, use_pro=use_pro)
    target = _token_limit_for_call(call_type, call_purpose, default=selected.get("max_tokens") or DEFAULT_MAX_TOKENS)
    selected["max_tokens"] = min(_coerce_positive_int(selected.get("max_tokens"), target), target)
    return selected


def _json_engine_for_call(engine: dict, *, call_purpose: str = "", max_tokens: int = _JSON_MAX_OUTPUT_TOKENS) -> dict:
    configured_max = _coerce_positive_int((engine or {}).get("max_tokens"), max_tokens)
    target = _token_limit_for_call("json", call_purpose, default=configured_max)
    selected = _json_engine(engine, max_tokens=target)
    purpose = str(call_purpose or "").lower()
    if "email_campaign_copy" in purpose or "email_html_body" in purpose:
        selected["max_tokens"] = max(
            _coerce_positive_int(selected.get("max_tokens"), target),
            _EMAIL_CAMPAIGN_JSON_MIN_TOKENS,
        )
    return selected


def _gemini_http_options(api_version: str = "v1beta") -> dict[str, Any]:
    return {
        "api_version": api_version,
        "timeout": _GEMINI_HTTP_TIMEOUT_MS,
        "client_args": {"timeout": _PROVIDER_HTTP_TIMEOUT},
        "async_client_args": {"timeout": _PROVIDER_HTTP_TIMEOUT},
    }

_gemini_clients: dict[str, Any] = {}
_gemini_client = None
if genai and GEMINI_API_KEY:
    try:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY, http_options=_gemini_http_options("v1beta"))
        _gemini_clients["v1beta"] = _gemini_client
    except Exception as exc:
        logger.warning("Gemini client init failed: %s", exc)

_openai_client = (
    AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=_PROVIDER_HTTP_TIMEOUT)
    if AsyncOpenAI and OPENAI_API_KEY
    else None
)
_anthropic_client = (
    AsyncAnthropic(api_key=ANTHROPIC_API_KEY, timeout=_PROVIDER_HTTP_TIMEOUT)
    if AsyncAnthropic and ANTHROPIC_API_KEY
    else None
)


def _gemini_api_version_for_model(model_name: str) -> str:
    model = str(model_name or "").strip().lower()
    if any(marker in model for marker in _GEMINI_PREVIEW_MARKERS):
        return "v1beta"
    return "v1"


def _gemini_client_for_model(model_name: str):
    if not genai or not GEMINI_API_KEY:
        return None
    api_version = _gemini_api_version_for_model(model_name)
    client = _gemini_clients.get(api_version)
    if client is not None:
        return client
    try:
        client = genai.Client(api_key=GEMINI_API_KEY, http_options=_gemini_http_options(api_version))
        _gemini_clients[api_version] = client
        return client
    except Exception as exc:
        logger.warning(
            "Gemini client init failed api_version=%s model=%s: %s",
            api_version,
            model_name,
            exc,
        )
        return _gemini_client


def _extract_gemini_chunk_text(chunk: Any) -> str:
    text = str(getattr(chunk, "text", "") or "")
    if text:
        return text
    try:
        candidates = getattr(chunk, "candidates", []) or []
        parts = getattr(getattr(candidates[0], "content", None), "parts", []) if candidates else []
        return "".join(str(getattr(part, "text", "") or "") for part in parts)
    except Exception:
        return ""


def _default_engine(use_pro: bool = False) -> dict:
    provider = ai_provider_name("gemini")
    provider_defaults = {
        "openai": OPENAI_DEFAULT_MODEL,
        "anthropic": ANTHROPIC_DEFAULT_MODEL,
        "gemini": _supported_gemini_default_model(use_pro=use_pro),
    }
    if provider not in provider_defaults:
        provider = "gemini"
    caps = catalog_model_capabilities(provider, ai_model_name() or provider_defaults[provider])
    return {
        "provider": provider,
        "model_name": ai_model_name() or provider_defaults[provider],
        "temperature": DEFAULT_TEMPERATURE,
        "max_tokens": DEFAULT_MAX_TOKENS,
        **caps,
    }


def _provider_default_model(provider: str, use_pro: bool = False) -> str:
    provider = (provider or "").strip().lower()
    if provider == "openai":
        return openai_model_name()
    if provider == "anthropic":
        return anthropic_model_name()
    if provider == "gemini":
        return _supported_gemini_default_model(use_pro=use_pro)
    return ""


def _supported_gemini_default_model(*, use_pro: bool = False) -> str:
    configured = gemini_pro_model_name() if use_pro else gemini_flash_model_name()
    if is_supported_model("gemini", configured):
        return configured
    return DEFAULT_GEMINI_PRO_MODEL if use_pro else DEFAULT_GEMINI_MODEL


def _coerce_supported_runtime_engine(engine: dict, *, use_pro: bool = False) -> dict:
    selected = dict(engine or {})
    provider = str(selected.get("provider") or "").strip().lower()
    model_name = str(selected.get("model_name") or "").strip()
    if provider == "gemini" and model_name and not is_supported_model("gemini", model_name):
        fallback_model = _supported_gemini_default_model(use_pro=use_pro)
        selected["configured_model_name"] = model_name
        selected["model_name"] = fallback_model
        logger.warning(
            "invalid_model_configured_fallback provider=gemini configured_model=%s "
            "fallback_model=%s error_type=invalid_model",
            model_name,
            fallback_model,
        )
    return selected


def get_provider_runtime_info(provider: str) -> tuple[bool, str]:
    provider = (provider or "").strip().lower()
    if provider == "gemini":
        if not genai:
            return False, "google-genai is not installed"
        return (True, "") if GEMINI_API_KEY else (False, "GEMINI_API_KEY is not configured")
    if provider == "openai":
        if not AsyncOpenAI:
            return False, "openai is not installed"
        return (True, "") if OPENAI_API_KEY else (False, "OPENAI_API_KEY is not configured")
    if provider == "anthropic":
        if not AsyncAnthropic:
            return False, "anthropic is not installed"
        return (True, "") if ANTHROPIC_API_KEY else (False, "ANTHROPIC_API_KEY is not configured")
    return False, f"Unsupported provider: {provider or 'unknown'}"


def engine_supports_vision(engine: Optional[dict]) -> bool:
    try:
        from services.db_helpers import model_supports_vision

        if not engine:
            return True
        return model_supports_vision(engine.get("provider", ""), engine.get("model_name", ""))
    except Exception:
        return True


def engine_supports_audio(engine: Optional[dict]) -> bool:
    try:
        from services.db_helpers import model_supports_audio

        if not engine:
            return True
        return model_supports_audio(engine.get("provider", ""), engine.get("model_name", ""))
    except Exception:
        return True


def validate_live_engine(engine: dict, require_vision: bool = True, required_capability: str = "") -> None:
    ready, message = get_provider_runtime_info(engine.get("provider", ""))
    if not ready:
        raise RuntimeError(message)
    try:
        validate_model_selection(engine.get("provider", ""), engine.get("model_name", ""))
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    capability = str(required_capability or "").strip().lower()
    if require_vision:
        capability = capability or "vision"
    if capability in {"vision", "image", "image_recognition"} and not engine_supports_vision(engine):
        raise RuntimeError("Selected model does not support image recognition.")
    if capability in {"audio", "audio_recognition", "audio_understanding"} and not engine_supports_audio(engine):
        raise RuntimeError("Selected model does not support audio recognition.")


def _generation_opts(engine: Optional[dict], generation_config: Any = None) -> tuple[Optional[float], Optional[int]]:
    temperature = float(engine["temperature"]) if engine and engine.get("temperature") is not None else None
    max_tokens = int(engine["max_tokens"]) if engine and engine.get("max_tokens") else None
    if isinstance(generation_config, dict):
        if generation_config.get("temperature") is not None:
            temperature = float(generation_config["temperature"])
        if generation_config.get("max_output_tokens") is not None:
            max_tokens = int(generation_config["max_output_tokens"])
        if generation_config.get("maxOutputTokens") is not None:
            max_tokens = int(generation_config["maxOutputTokens"])
        if generation_config.get("max_tokens") is not None:
            max_tokens = int(generation_config["max_tokens"])
    return temperature, max_tokens


def _gemini_config_fields() -> set[str]:
    fields = getattr(getattr(genai_types, "GenerateContentConfig", None), "model_fields", {}) if genai_types else {}
    return set(fields.keys()) if isinstance(fields, dict) else set()


def _gemini_supports_afc_disable() -> bool:
    return "automatic_function_calling" in _gemini_config_fields() and hasattr(
        genai_types, "AutomaticFunctionCallingConfig"
    )


def _gemini_afc_disabled_config() -> Any:
    if genai_types and hasattr(genai_types, "AutomaticFunctionCallingConfig"):
        return genai_types.AutomaticFunctionCallingConfig(disable=True)
    return {"disable": True}


def _gemini_config_has_response_mime_type(config: Any) -> bool:
    if isinstance(config, dict):
        return bool(config.get("response_mime_type") or config.get("responseMimeType"))
    return bool(getattr(config, "response_mime_type", None) or getattr(config, "responseMimeType", None))


def _gemini_response_mime_cache_key(model_name: str) -> str:
    model = str(model_name or "").strip()
    return f"{_gemini_api_version_for_model(model)}:{model}"


def _gemini_supports_response_mime_type(engine: dict | None, model_name: str | None = None) -> bool:
    model = str(model_name or (engine or {}).get("model_name") or "").strip()
    if not model or not genai_types or "response_mime_type" not in _gemini_config_fields():
        return False
    explicit = (engine or {}).get("supports_response_mime_type")
    if explicit is not None:
        return bool(explicit)
    if _gemini_response_mime_cache_key(model) in _GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS:
        return False
    return _gemini_api_version_for_model(model) == "v1beta"


def _gemini_config_without_response_mime_type(config: Any) -> Any:
    if config is None:
        return None
    if isinstance(config, dict):
        payload = dict(config)
        payload.pop("response_mime_type", None)
        payload.pop("responseMimeType", None)
        payload.pop("response_schema", None)
        payload.pop("responseSchema", None)
        payload.pop("response_json_schema", None)
        payload.pop("responseJsonSchema", None)
        return payload or None
    if hasattr(config, "model_copy"):
        try:
            updates = {
                field: None
                for field in ("response_mime_type", "response_schema", "response_json_schema")
                if hasattr(config, field)
            }
            return config.model_copy(update=updates) if updates else config
        except Exception:
            return config
    return config


def _normalize_gemini_config_payload(kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(kwargs or {})
    if payload.get("maxOutputTokens") is not None and payload.get("max_output_tokens") is None:
        payload["max_output_tokens"] = payload.get("maxOutputTokens")
    if payload.get("max_tokens") is not None and payload.get("max_output_tokens") is None:
        payload["max_output_tokens"] = payload.get("max_tokens")
    if payload.get("responseMimeType") is not None and payload.get("response_mime_type") is None:
        payload["response_mime_type"] = payload.get("responseMimeType")
    if payload.get("responseSchema") is not None and payload.get("response_schema") is None:
        payload["response_schema"] = payload.get("responseSchema")
    if payload.get("responseJsonSchema") is not None and payload.get("response_json_schema") is None:
        payload["response_json_schema"] = payload.get("responseJsonSchema")
    for key in (
        "maxOutputTokens",
        "max_tokens",
        "response_format",
        "responseMimeType",
        "responseSchema",
        "responseJsonSchema",
    ):
        payload.pop(key, None)
    fields = _gemini_config_fields()
    if fields:
        payload = {key: value for key, value in payload.items() if key in fields}
    return payload


def _build_gemini_config(kwargs: dict[str, Any] | None = None) -> Any:
    payload = _normalize_gemini_config_payload(kwargs)
    if _gemini_supports_afc_disable():
        payload.setdefault("automatic_function_calling", _gemini_afc_disabled_config())
    if genai_types:
        return genai_types.GenerateContentConfig(**payload) if payload else genai_types.GenerateContentConfig()
    return payload or None


def _prepare_gemini_generation_config(engine: dict, generation_config: Any = None) -> Any:
    temperature, max_tokens = _generation_opts(engine, generation_config)
    if not _gemini_supports_response_mime_type(engine):
        generation_config = _gemini_config_without_response_mime_type(generation_config)
    if genai_types and isinstance(generation_config, genai_types.GenerateContentConfig):
        updates: dict[str, Any] = {}
        if temperature is not None and getattr(generation_config, "temperature", None) is None:
            updates["temperature"] = temperature
        if max_tokens is not None and getattr(generation_config, "max_output_tokens", None) is None:
            updates["max_output_tokens"] = max_tokens
        if _gemini_supports_afc_disable() and getattr(generation_config, "automatic_function_calling", None) is None:
            updates["automatic_function_calling"] = _gemini_afc_disabled_config()
        return generation_config.model_copy(update=updates) if updates else generation_config
    payload = _normalize_gemini_config_payload(generation_config) if isinstance(generation_config, dict) else {}
    if temperature is not None:
        payload.setdefault("temperature", temperature)
    if max_tokens is not None:
        payload.setdefault("max_output_tokens", max_tokens)
    return _build_gemini_config(payload)


def _json_engine(engine: dict, *, max_tokens: int = _JSON_MAX_OUTPUT_TOKENS) -> dict:
    selected = dict(engine or {})
    selected["temperature"] = 0.0
    try:
        configured_max = int(selected.get("max_tokens") or max_tokens)
    except (TypeError, ValueError):
        configured_max = max_tokens
    selected["max_tokens"] = min(max_tokens, configured_max) if configured_max > 0 else max_tokens
    return selected


def _json_only_prompt(prompt: str, schema_payload: dict[str, Any] | None = None) -> str:
    if schema_payload:
        schema_text = json.dumps(schema_payload, ensure_ascii=True)
        return f"Return ONLY valid JSON matching this schema.\nSchema: {schema_text}\n\n{prompt}"
    return f"Return ONLY valid JSON. No markdown. No extra text.\n\n{prompt}"


def _is_gemini_response_mime_type_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    normalized = message.replace("_", "")
    return "responsemimetype" in normalized and (
        "unknown name" in message
        or "cannot find field" in message
        or "invalid json payload" in message
        or "invalid_argument" in message
    )


def _iter_gemini_model_candidates(preferred_model: str | None) -> list[str]:
    ordered: list[str] = []
    for candidate in [
        preferred_model,
        FLASH_MODEL,
        PRO_MODEL,
        *GEMINI_FALLBACK_MODELS,
        DEFAULT_GEMINI_MODEL,
        DEFAULT_GEMINI_PRO_MODEL,
    ]:
        model_name = str(candidate or "").strip()
        if not model_name or model_name in ordered:
            continue
        ordered.append(model_name)
    return ordered


def _image_data_url_to_part(data_url: str) -> Optional[Any]:
    payload = _extract_data_url_payload(data_url)
    if not payload or not genai_types:
        return None
    mime, raw, _ = payload
    return genai_types.Part.from_bytes(data=raw, mime_type=mime)


def _normalize_usage_dict(
    prompt_tokens: Any = None,
    completion_tokens: Any = None,
    *,
    prompt: str = "",
    response_text: str = "",
) -> dict[str, int]:
    prompt_count = int(prompt_tokens) if prompt_tokens is not None else estimate_tokens(prompt)
    completion_count = int(completion_tokens) if completion_tokens is not None else estimate_tokens(response_text)
    return {
        "prompt_tokens": max(0, prompt_count),
        "completion_tokens": max(0, completion_count),
        "total_tokens": max(0, prompt_count) + max(0, completion_count),
    }


def _log_llm_call(
    *,
    outcome: str,
    provider: str,
    model: str,
    prompt: str,
    response_text: str = "",
    latency_ms: float,
    usage: dict[str, int] | None = None,
    error: Exception | None = None,
    fallback_from: str = "",
    call_type: str = "text",
    call_purpose: str = "",
    function_name: str = "",
    agent_name: str = "",
    attempt_number: int = 1,
    fallback_used: bool = False,
    call_number: int = 0,
    token_estimate: int = 0,
) -> None:
    usage_payload = usage or _normalize_usage_dict(prompt=prompt, response_text=response_text)
    context = get_llm_context()
    payload: dict[str, Any] = {
        "event": "llm_call",
        "outcome": outcome,
        "model": model,
        "provider": provider,
        "function": function_name or "-",
        "company_id": (context.company_id if context else "") or "-",
        "latency_ms": round(float(latency_ms or 0.0), 2),
        "prompt_tokens": usage_payload.get("prompt_tokens", 0),
        "completion_tokens": usage_payload.get("completion_tokens", 0),
        "total_tokens": usage_payload.get("total_tokens", 0),
        "attempt": attempt_number,
        "fallback_used": bool(fallback_used),
        "message_id": (context.message_id if context else "") or "-",
        "conversation_id": (context.conversation_id if context else "") or "-",
        "workflow_id": (context.workflow_id if context else "") or "-",
        "agent": agent_name or (context.agent_name if context else "") or "-",
        "purpose": call_purpose or "-",
        "call_type": call_type or "-",
        "fallback_from": fallback_from or "-",
        "call_number": call_number or (context.call_count if context else 0),
        "token_estimate": token_estimate or estimate_tokens(prompt),
    }
    if error is None:
        logger.info(payload)
        return
    payload["error_type"] = error.__class__.__name__
    payload["error"] = str(error).splitlines()[0][:240]
    if outcome == "timeout":
        logger.warning(payload)
    else:
        logger.error(payload)


async def _stream_gemini(
    prompt: str,
    engine: dict,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> AsyncIterator[tuple[str, str]]:
    preferred_model = str(engine.get("model_name") or "").strip()
    if not _gemini_client_for_model(preferred_model):
        raise RuntimeError(get_provider_runtime_info("gemini")[1])
    config = _prepare_gemini_generation_config(engine, generation_config)
    parts = [part for part in [_image_data_url_to_part(url) for url in (image_urls or [])] if part]
    contents: Any = [prompt] + parts if parts else prompt
    errors: list[str] = []
    model_candidates = _iter_gemini_model_candidates(engine.get("model_name"))
    for index, model_name in enumerate(model_candidates):
        response_mime_retry_used = False
        try:
            client = _gemini_client_for_model(model_name)
            if not client:
                raise RuntimeError(get_provider_runtime_info("gemini")[1])
            model_cache_key = _gemini_response_mime_cache_key(model_name)
            while True:
                request_config = (
                    _gemini_config_without_response_mime_type(config)
                    if model_cache_key in _GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS
                    else config
                )
                try:
                    stream = client.aio.models.generate_content_stream(
                        model=model_name,
                        contents=contents,
                        config=request_config,
                    )
                    if hasattr(stream, "__await__"):
                        stream = await stream
                    if hasattr(stream, "__aiter__"):
                        async for chunk in stream:
                            text = _extract_gemini_chunk_text(chunk)
                            if text:
                                yield text, model_name
                    else:
                        for chunk in stream:
                            text = _extract_gemini_chunk_text(chunk)
                            if text:
                                yield text, model_name
                    return
                except Exception as exc:
                    if (
                        not response_mime_retry_used
                        and _gemini_config_has_response_mime_type(request_config)
                        and _is_gemini_response_mime_type_error(exc)
                    ):
                        response_mime_retry_used = True
                        _GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS.add(model_cache_key)
                        config = _gemini_config_without_response_mime_type(config)
                        logger.warning(
                            "gemini_response_mime_type_unsupported_retry model=%s error=%s",
                            model_name,
                            str(exc).splitlines()[0][:240],
                        )
                        continue
                    raise
        except Exception as exc:
            error_str = str(exc)
            errors.append(f"{model_name}:{exc.__class__.__name__}:{error_str}")
            if _is_invalid_model_error(exc) and index + 1 < len(model_candidates):
                next_model = model_candidates[index + 1]
                logger.warning(
                    "gemini_invalid_model_fallback invalid_model=%s fallback_model=%s "
                    "error_type=invalid_model error=%s",
                    model_name,
                    next_model,
                    error_str.splitlines()[0][:240],
                )
                continue
            if _is_non_retryable_client_error(exc):
                raise
            continue
    raise RuntimeError(
        "Gemini generation failed across models: " + "; ".join(errors)
        if errors
        else "Gemini generation failed with no eligible models"
    )


async def _stream_openai(
    prompt: str,
    engine: dict,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> AsyncIterator[tuple[str, str]]:
    if not _openai_client:
        raise RuntimeError(get_provider_runtime_info("openai")[1])
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": url}} for url in (image_urls or []) if _extract_data_url_payload(url)
    ]
    temperature, max_tokens = _generation_opts(engine, generation_config)
    kwargs: dict[str, Any] = {
        "model": engine.get("model_name") or OPENAI_DEFAULT_MODEL,
        "messages": [{"role": "user", "content": content}],
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if isinstance(generation_config, dict) and generation_config.get("response_format") == "json_object":
        kwargs["response_format"] = {"type": "json_object"}
    stream = await _openai_client.chat.completions.create(**kwargs, stream=True)
    model_name = str(kwargs["model"])
    async for chunk in stream:
        try:
            text = str(getattr(chunk.choices[0].delta, "content", "") or "")
        except Exception:
            text = ""
        if text:
            yield text, model_name


async def _stream_anthropic(
    prompt: str,
    engine: dict,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> AsyncIterator[tuple[str, str]]:
    if not _anthropic_client:
        raise RuntimeError(get_provider_runtime_info("anthropic")[1])
    temperature, max_tokens = _generation_opts(engine, generation_config)
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for url in image_urls or []:
        payload = _extract_data_url_payload(url)
        if payload:
            mime, _, encoded = payload
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": encoded},
                }
            )
    kwargs: dict[str, Any] = {
        "model": engine.get("model_name") or ANTHROPIC_DEFAULT_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens or DEFAULT_MAX_TOKENS,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    model_name = str(kwargs["model"])
    async with _anthropic_client.messages.stream(**kwargs) as stream:
        async for text in stream.text_stream:
            if text:
                yield str(text), model_name


def _get_fallback_provider_order(primary_provider: str) -> list[str]:
    """Return provider fallback order, starting with the primary."""
    all_providers = ["openai", "anthropic", "gemini"]
    primary = (primary_provider or "openai").strip().lower()
    ordered = [primary] + [p for p in all_providers if p != primary]
    return [p for p in ordered if get_provider_runtime_info(p)[0]]


def _engine_for_provider(base_engine: dict, provider: str, *, use_pro: bool = False) -> dict:
    provider = (provider or "").strip().lower()
    engine = dict(base_engine or {})
    engine["provider"] = provider
    if provider != str((base_engine or {}).get("provider") or "").strip().lower():
        engine["model_name"] = _provider_default_model(provider, use_pro=use_pro)
    return engine


async def _stream_provider_once(
    provider: str,
    prompt: str,
    engine: dict,
    *,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> AsyncIterator[tuple[str, str]]:
    if provider == "gemini":
        async for item in _stream_gemini(
            prompt,
            engine,
            generation_config=generation_config,
            image_urls=image_urls,
        ):
            yield item
        return
    if provider == "openai":
        async for item in _stream_openai(
            prompt,
            engine,
            generation_config=generation_config,
            image_urls=image_urls,
        ):
            yield item
        return
    if provider == "anthropic":
        async for item in _stream_anthropic(
            prompt,
            engine,
            generation_config=generation_config,
            image_urls=image_urls,
        ):
            yield item
        return
    raise RuntimeError(f"Unsupported provider: {provider or 'unknown'}")


async def _call_provider_once(
    provider: str,
    prompt: str,
    engine: dict,
    *,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> tuple[str, str, dict[str, int]]:
    timeout_seconds = ai_api_call_timeout_seconds()

    async def _collect_once() -> tuple[str, str, dict[str, int]]:
        buffer = StringIO()
        resolved_model = str(engine.get("model_name") or "")
        async for chunk, model_name in _stream_provider_once(
            provider,
            prompt,
            engine,
            generation_config=generation_config,
            image_urls=image_urls,
        ):
            if model_name:
                resolved_model = model_name
            buffer.write(chunk)
        text = buffer.getvalue().strip()
        if not text:
            raise ValueError("Empty AI response")
        usage = _normalize_usage_dict(prompt=prompt, response_text=text)
        return text, resolved_model, usage

    last_exc: Exception | None = None
    for retry_index in range(_MAX_TRANSIENT_RETRIES):
        try:
            return await asyncio.wait_for(_collect_once(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            last_exc = TimeoutError(
                f"{provider} streaming call timed out after {timeout_seconds:.1f}s "
                f"model={engine.get('model_name') or 'unknown'}"
            )
        except Exception as exc:
            last_exc = exc
        if last_exc is None:
            break
        if _is_non_retryable_client_error(last_exc) or _is_resource_exhausted(last_exc):
            break
        if retry_index + 1 >= _MAX_TRANSIENT_RETRIES or not _is_retryable_ai_error(last_exc):
            break
        await asyncio.sleep(2**retry_index)
    raise last_exc or RuntimeError(f"{provider} generation failed")


async def call_model_text(
    prompt: str,
    engine: Optional[dict] = None,
    use_pro: bool = False,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
    *,
    call_type: str = "text",
    call_purpose: str = "",
    function_name: str = "",
    agent_name: str = "",
    max_provider_attempts: int | None = None,
    allow_provider_fallback: bool | None = None,
    count_against_budget: bool = True,
) -> str:
    selected = _text_engine(
        engine,
        use_pro=use_pro,
        call_type=call_type,
        call_purpose=call_purpose,
    )
    if image_urls:
        validate_live_engine(selected, require_vision=True)
    primary_provider = (selected.get("provider") or ai_provider_name("gemini")).strip().lower()
    provider_order = _get_fallback_provider_order(primary_provider)
    if allow_provider_fallback is None:
        allow_provider_fallback = ai_enable_provider_fallback()
    if not allow_provider_fallback:
        provider_order = provider_order[:1]
    max_attempts = max_provider_attempts if max_provider_attempts is not None else ai_max_provider_attempts()
    provider_order = provider_order[: max(1, int(max_attempts or 1))]
    if not provider_order:
        _, reason = get_provider_runtime_info(primary_provider)
        raise RuntimeError(reason or f"No configured AI providers are ready for {primary_provider or 'unknown'}")

    last_exc: Exception | None = None
    for attempt_number, provider in enumerate(provider_order, start=1):
        provider_engine = _engine_for_provider(selected, provider, use_pro=use_pro)
        started = time.perf_counter()
        token_estimate = estimate_tokens(prompt)
        call_number = reserve_llm_call(
            agent_name=agent_name,
            function_name=function_name or "call_model_text",
            provider=provider,
            model=str(provider_engine.get("model_name") or ""),
            call_purpose=call_purpose,
            call_type=call_type,
            attempt_number=attempt_number,
            fallback_used=provider != primary_provider,
            token_estimate=token_estimate,
            count_against_budget=count_against_budget,
        )
        try:
            text, resolved_model, usage = await _call_provider_once(
                provider,
                prompt,
                provider_engine,
                generation_config=generation_config,
                image_urls=image_urls,
            )
            _log_llm_call(
                outcome="success",
                provider=provider,
                model=resolved_model or str(provider_engine.get("model_name") or ""),
                prompt=prompt,
                response_text=text,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                usage=usage,
                fallback_from=primary_provider if provider != primary_provider else "",
                call_type=call_type,
                call_purpose=call_purpose,
                function_name=function_name or "call_model_text",
                agent_name=agent_name,
                attempt_number=attempt_number,
                fallback_used=provider != primary_provider,
                call_number=call_number,
                token_estimate=token_estimate,
            )
            return text
        except Exception as exc:
            error_str = str(exc)
            _log_llm_call(
                outcome="error",
                provider=provider,
                model=str(provider_engine.get("model_name") or ""),
                prompt=prompt,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=exc,
                fallback_from=primary_provider if provider != primary_provider else "",
                call_type=call_type,
                call_purpose=call_purpose,
                function_name=function_name or "call_model_text",
                agent_name=agent_name,
                attempt_number=attempt_number,
                fallback_used=provider != primary_provider,
                call_number=call_number,
                token_estimate=token_estimate,
            )
            last_exc = exc
            if _is_non_retryable_client_error(exc):
                raise
            # 429/quota errors are project-level for this provider — skip
            # remaining models for this provider but CONTINUE to the next
            # provider in the fallback chain (OpenAI, Anthropic, etc.).
            # Previously `break` caused the entire loop to stop, meaning a
            # Gemini quota error would prevent OpenAI/Anthropic from being tried.
            continue

    raise RuntimeError(f"All AI providers exhausted. Last error: {last_exc}") from last_exc


async def stream_model_text(
    prompt: str,
    engine: Optional[dict] = None,
    use_pro: bool = False,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
    *,
    call_type: str = "text_stream",
    call_purpose: str = "",
    function_name: str = "",
    agent_name: str = "",
    max_provider_attempts: int | None = None,
    allow_provider_fallback: bool | None = None,
    count_against_budget: bool = True,
) -> AsyncIterator[str]:
    selected = _text_engine(
        engine,
        use_pro=use_pro,
        call_type=call_type,
        call_purpose=call_purpose,
    )
    if image_urls:
        validate_live_engine(selected, require_vision=True)
    primary_provider = (selected.get("provider") or ai_provider_name("gemini")).strip().lower()
    provider_order = _get_fallback_provider_order(primary_provider)
    if allow_provider_fallback is None:
        allow_provider_fallback = ai_enable_provider_fallback()
    if not allow_provider_fallback:
        provider_order = provider_order[:1]
    max_attempts = max_provider_attempts if max_provider_attempts is not None else ai_max_provider_attempts()
    provider_order = provider_order[: max(1, int(max_attempts or 1))]
    if not provider_order:
        _, reason = get_provider_runtime_info(primary_provider)
        raise RuntimeError(reason or f"No configured AI providers are ready for {primary_provider or 'unknown'}")

    last_exc: Exception | None = None
    for attempt_number, provider in enumerate(provider_order, start=1):
        provider_engine = _engine_for_provider(selected, provider, use_pro=use_pro)
        started = time.perf_counter()
        token_estimate = estimate_tokens(prompt)
        completion_tokens = 0
        resolved_model = str(provider_engine.get("model_name") or "")
        caught_exc: Exception | None = None
        call_number = reserve_llm_call(
            agent_name=agent_name,
            function_name=function_name or "stream_model_text",
            provider=provider,
            model=resolved_model,
            call_purpose=call_purpose,
            call_type=call_type,
            attempt_number=attempt_number,
            fallback_used=provider != primary_provider,
            token_estimate=token_estimate,
            count_against_budget=count_against_budget,
        )
        try:
            async with asyncio.timeout(ai_api_call_timeout_seconds()):
                async for chunk, model_name in _stream_provider_once(
                    provider,
                    prompt,
                    provider_engine,
                    generation_config=generation_config,
                    image_urls=image_urls,
                ):
                    if model_name:
                        resolved_model = model_name
                    completion_tokens += estimate_tokens(chunk)
                    yield chunk
            _log_llm_call(
                outcome="success",
                provider=provider,
                model=resolved_model,
                prompt=prompt,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                usage={
                    "prompt_tokens": token_estimate,
                    "completion_tokens": completion_tokens,
                    "total_tokens": token_estimate + completion_tokens,
                },
                fallback_from=primary_provider if provider != primary_provider else "",
                call_type=call_type,
                call_purpose=call_purpose,
                function_name=function_name or "stream_model_text",
                agent_name=agent_name,
                attempt_number=attempt_number,
                fallback_used=provider != primary_provider,
                call_number=call_number,
                token_estimate=token_estimate,
            )
            return
        except TimeoutError as exc:
            caught_exc = TimeoutError(
                f"{provider} streaming call timed out after {ai_api_call_timeout_seconds():.1f}s "
                f"model={resolved_model or provider_engine.get('model_name') or 'unknown'}"
            )
            caught_exc.__cause__ = exc
        except Exception as exc:
            caught_exc = exc
        if caught_exc is None:
            continue
        _log_llm_call(
            outcome="timeout" if isinstance(caught_exc, (TimeoutError, asyncio.TimeoutError)) else "error",
            provider=provider,
            model=resolved_model or str(provider_engine.get("model_name") or ""),
            prompt=prompt,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error=caught_exc,
            fallback_from=primary_provider if provider != primary_provider else "",
            call_type=call_type,
            call_purpose=call_purpose,
            function_name=function_name or "stream_model_text",
            agent_name=agent_name,
            attempt_number=attempt_number,
            fallback_used=provider != primary_provider,
            call_number=call_number,
            token_estimate=token_estimate,
        )
        last_exc = caught_exc
        if _is_non_retryable_client_error(caught_exc):
            raise caught_exc
        continue
    raise RuntimeError(f"All AI providers exhausted. Last error: {last_exc}") from last_exc


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Extract and parse a JSON object from a raw LLM response.

    The model is instructed to return a JSON object, but some providers may
    return invalid JSON (e.g. unquoted keys or wrapped in code fences).  This
    function attempts to sanitize and parse the first JSON-like object found.
    """
    candidate = (raw or "").strip()
    if not candidate:
        return {}
    # Strip markdown fences and optional language tag (e.g. ```json)
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()

    last_error: Exception | None = None

    def _load_json(text: str) -> Optional[dict[str, Any]]:
        nonlocal last_error
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            last_error = exc
            try:
                parsed = json.JSONDecoder(strict=False).decode(text)
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(text)
                except (SyntaxError, ValueError) as literal_exc:
                    last_error = literal_exc
                    return None
        if isinstance(parsed, dict):
            return parsed
        last_error = ValueError("JSON response was not an object")
        return None

    def _remove_trailing_commas(text: str) -> str:
        return re.sub(r",\s*([}\]])", r"\1", text)

    def _quote_keys(text: str) -> str:
        # Add quotes around bare keys following { or ,
        return re.sub(r'([\{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', text)

    def _object_candidates(text: str) -> list[str]:
        candidates: list[str] = []
        start = -1
        depth = 0
        in_string = False
        escape = False
        for index, char in enumerate(text):
            if start < 0:
                if char == "{":
                    start = index
                    depth = 1
                continue
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : index + 1])
                    start = -1
        return candidates

    # First attempt: direct JSON parsing
    parsed = _load_json(candidate)
    if parsed is not None:
        return parsed
    parsed = _load_json(_remove_trailing_commas(candidate))
    if parsed is not None:
        return parsed
    # Second attempt: locate balanced curly-braces objects and parse them.
    # This avoids greedy matches that include prose after the JSON object.
    for obj_str in _object_candidates(candidate):
        parsed = _load_json(obj_str)
        if parsed is not None:
            return parsed
        parsed = _load_json(_remove_trailing_commas(obj_str))
        if parsed is not None:
            return parsed
        # Third attempt: quote unquoted keys within the curly-braces object
        # This handles cases like {subject: "...", body: "..."}
        fixed_obj = _remove_trailing_commas(_quote_keys(obj_str))
        parsed = _load_json(fixed_obj)
        if parsed is not None:
            return parsed
    raise ValueError("Model response did not contain a valid JSON object") from last_error


def _error_status_code(exc: Exception) -> int:
    for attr in ("code", "status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
        try:
            parsed = int(value)
            if parsed:
                return parsed
        except (TypeError, ValueError):
            pass
    match = re.search(r"\b([45]\d\d)\b", str(exc or ""))
    return int(match.group(1)) if match else 0


def _is_google_api_exception(exc: Exception, *names: str) -> bool:
    if not google_api_exceptions:
        return False
    exception_types = tuple(
        item for item in (getattr(google_api_exceptions, name, None) for name in names) if isinstance(item, type)
    )
    return bool(exception_types) and isinstance(exc, exception_types)


def _is_genai_exception(exc: Exception, *names: str) -> bool:
    if not genai_errors:
        return False
    exception_types = tuple(
        item for item in (getattr(genai_errors, name, None) for name in names) if isinstance(item, type)
    )
    return bool(exception_types) and isinstance(exc, exception_types)


def _is_resource_exhausted(exc: Exception) -> bool:
    message = str(exc or "").lower()
    if "resource_exhausted" in message or "quota" in message or "rate limit" in message or "429" in message:
        return True
    if _is_google_api_exception(exc, "ResourceExhausted"):
        return True
    return _error_status_code(exc) == 429


def _is_invalid_model_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    if "invalid model" in message or "unsupported model" in message or "model not found" in message:
        return True
    if "generatecontent" in message and ("not supported" in message or "is not found" in message):
        return True
    if "models/" in message and ("is not found" in message or "not found" in message):
        return True
    if _error_status_code(exc) == 404 and "model" in message:
        return True
    return False


def _is_non_retryable_client_error(exc: Exception) -> bool:
    status_code = _error_status_code(exc)
    if status_code and 400 <= status_code < 500 and status_code not in {408, 409, 425, 429}:
        return True
    message = str(exc or "").lower()
    if "invalid_argument" in message or "bad request" in message or "unknown name" in message:
        return True
    if _is_genai_exception(exc, "ClientError"):
        return status_code not in {408, 409, 425, 429}
    if _is_google_api_exception(exc, "ClientError"):
        return status_code not in {408, 409, 425, 429}
    return False


def _is_retryable_ai_error(exc: Exception) -> bool:
    if _is_non_retryable_client_error(exc):
        return False
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, RuntimeError, httpx.TimeoutException)):
        return True
    if _is_google_api_exception(exc, "DeadlineExceeded", "ResourceExhausted", "ServiceUnavailable"):
        return True
    status_code = _error_status_code(exc)
    return status_code in {408, 409, 425, 429, 500, 502, 503, 504}


def _classify_llm_error(exc: Exception) -> str:
    message = str(exc or "").lower()
    if _is_invalid_model_error(exc):
        return "invalid_model"
    if "quota" in message or "rate limit" in message or "resource_exhausted" in message or "429" in message:
        return "quota_exhausted"
    if (
        "api key" in message
        or "not configured" in message
        or "permission" in message
        or "unauthorized" in message
        or "forbidden" in message
        or "unsupported provider" in message
        or "no configured ai providers" in message
    ):
        return "provider_not_configured"
    return "provider_error"


def _compact_exception_message(exc: Exception | None, limit: int = 300) -> str:
    if exc is None:
        return "unknown error"
    message = str(exc).splitlines()[0].strip() or exc.__class__.__name__
    return message[:limit]


def _json_generation_config(engine: dict) -> dict[str, Any]:
    _, selected_max_tokens = _generation_opts(engine, None)
    config: dict[str, Any] = {
        "response_format": "json_object",
        "temperature": 0.0,
        "max_output_tokens": selected_max_tokens or _JSON_MAX_OUTPUT_TOKENS,
    }
    provider = str((engine or {}).get("provider") or "").strip().lower()
    if provider == "gemini" and _gemini_supports_response_mime_type(engine):
        config["response_mime_type"] = "application/json"
    return config


def _json_engine_for_format_attempt(engine: dict, attempt: int) -> dict:
    if attempt <= 1:
        return engine
    selected = dict(engine or {})
    current_max = _coerce_positive_int(selected.get("max_tokens"), _JSON_MAX_OUTPUT_TOKENS)
    retry_max = max(current_max + _JSON_RETRY_TOKEN_BONUS, int(current_max * 1.5))
    selected["max_tokens"] = min(max(retry_max, current_max), _JSON_RETRY_MAX_OUTPUT_TOKENS)
    selected["temperature"] = 0.0
    return selected


def _json_response_repair_prompt(
    original_prompt: str,
    schema_payload: dict[str, Any],
    raw_response: str,
    error: Exception | None,
) -> str:
    raw_preview = str(raw_response or "").strip()
    if len(raw_preview) > _JSON_REPAIR_RAW_CHAR_LIMIT:
        raw_preview = raw_preview[:_JSON_REPAIR_RAW_CHAR_LIMIT] + "\n[truncated]"
    repair_task = (
        "The previous model response was invalid, incomplete, or did not match the required JSON schema.\n"
        "Return a new complete JSON object only. Do not continue the previous response.\n"
        "The final output must start with { and end with }.\n"
        f"Parsing or validation error: {_compact_exception_message(error)}\n"
        f"Previous invalid response:\n{raw_preview or '<empty>'}\n\n"
        f"Original task:\n{original_prompt}"
    )
    return _json_only_prompt(repair_task, schema_payload)


def _validate_json_response(raw: str, schema: type[BaseModel]) -> dict[str, Any]:
    return schema.model_validate(_extract_json_object(raw)).model_dump()


async def call_model_json(
    prompt: str,
    schema: type[BaseModel],
    engine: Optional[dict] = None,
    use_pro: bool = False,
    image_urls: Optional[list[str]] = None,
    *,
    call_purpose: str = "",
    function_name: str = "",
    agent_name: str = "",
    max_provider_attempts: int | None = None,
    allow_provider_fallback: bool | None = None,
    count_against_budget: bool = True,
) -> dict[str, Any]:
    selected = _json_engine_for_call(
        engine or _default_engine(use_pro=use_pro),
        call_purpose=call_purpose,
    )
    schema_payload = _sanitize_schema(schema.model_json_schema())
    raw = ""
    last_format_error: Exception | None = None
    for format_attempt in range(1, _JSON_FORMAT_RETRY_ATTEMPTS + 1):
        attempt_engine = _json_engine_for_format_attempt(selected, format_attempt)
        json_prompt = (
            _json_only_prompt(prompt, schema_payload)
            if format_attempt == 1
            else _json_response_repair_prompt(prompt, schema_payload, raw, last_format_error)
        )
        raw = await call_model_text(
            json_prompt,
            engine=attempt_engine,
            generation_config=_json_generation_config(attempt_engine),
            image_urls=image_urls,
            call_type="json",
            call_purpose=call_purpose,
            function_name=function_name or "call_model_json",
            agent_name=agent_name,
            max_provider_attempts=max_provider_attempts,
            allow_provider_fallback=allow_provider_fallback,
            count_against_budget=count_against_budget,
        )
        try:
            return _validate_json_response(raw, schema)
        except Exception as exc:
            last_format_error = exc
            logger.warning(
                "llm_json_response_invalid function=%s purpose=%s attempt=%s raw_chars=%s error=%s",
                function_name or "call_model_json",
                call_purpose or "-",
                format_attempt,
                len(raw or ""),
                _compact_exception_message(exc),
            )
    raise ValueError("Model response did not contain valid JSON matching schema after retries") from last_format_error


async def call_gemini(
    prompt: str,
    use_pro: bool = False,
    generation_config: Any = None,
    image_parts: Optional[list] = None,
    engine: Optional[dict] = None,
) -> str:
    image_urls = [item for item in (image_parts or []) if isinstance(item, str) and item.startswith("data:")]
    return await call_model_text(
        prompt,
        engine=engine,
        use_pro=use_pro,
        generation_config=generation_config,
        image_urls=image_urls,
    )


async def call_gemini_json(
    prompt: str,
    schema: type[BaseModel],
    use_pro: bool = False,
    engine: Optional[dict] = None,
) -> dict[str, Any]:
    return await call_model_json(prompt, schema, engine=engine, use_pro=use_pro)


async def get_active_llm_engines(db, company_id: str = "") -> list[dict]:
    if not db:
        return []
    try:
        from services.db_helpers import enrich_llm_engine, ensure_company_settings_row, rs

        selected_id = ""
        scoped_company_id = (company_id or "").strip()
        if scoped_company_id:
            settings = await ensure_company_settings_row(db, scoped_company_id)
            selected_id = settings.get("active_llm_engine_id", "")
            rows = await db.fetch(
                "SELECT * FROM llm_engines "
                "WHERE is_active=TRUE AND (company_id='' OR company_id=$1) "
                "ORDER BY CASE WHEN company_id='' THEN 0 ELSE 1 END, provider, model_name LIMIT 50",
                scoped_company_id,
            )
        else:
            rows = await db.fetch(
                "SELECT * FROM llm_engines "
                "WHERE is_active=TRUE AND company_id='' ORDER BY provider, model_name LIMIT 20"
            )
        return [enrich_llm_engine(engine, selected_id=selected_id) for engine in rs(rows)]
    except Exception as exc:
        logger.warning("LLM engines fetch failed: %s", exc)
        return []


async def get_active_llm_engine(db, company_id: str = "") -> Optional[dict]:
    from services.db_helpers import resolve_active_llm_engine

    try:
        scoped_company_id = (company_id or "").strip()
        if scoped_company_id:
            return await resolve_active_llm_engine(db, scoped_company_id)
        engines = await get_active_llm_engines(db, "")
        return engines[0] if engines else None
    except Exception:
        engines = await get_active_llm_engines(db, company_id)
        selected = next((e for e in engines if e.get("is_selected")), None)
        return selected or (engines[0] if engines else None)


async def _resolve_engine_for_request(db=None, company_id: str = "", use_pro: bool = False) -> dict:
    if db:
        try:
            from services.db_helpers import resolve_active_llm_engine

            scoped_company_id = (company_id or "").strip()
            if scoped_company_id:
                engine = await resolve_active_llm_engine(db, scoped_company_id)
            else:
                engines = await get_active_llm_engines(db, "")
                engine = engines[0] if engines else None
            if not engine:
                return _default_engine(use_pro=use_pro)
            logger.debug(
                "llm_engine_resolved company_id=%s provider=%s model=%s selected_id=%s",
                scoped_company_id or "<global>",
                str((engine or {}).get("provider") or ""),
                str((engine or {}).get("model_name") or ""),
                str((engine or {}).get("id") or ""),
            )
            return engine
        except Exception as exc:
            logger.warning(
                "Engine resolution failed for company %s: %s",
                company_id or "<global>",
                exc,
            )
    return _default_engine(use_pro=use_pro)


async def call_with_engines(
    prompt: str,
    engines: list[dict],
    use_pro: bool = False,
    generation_config: Any = None,
    image_parts: Optional[list] = None,
    *,
    call_purpose: str = "",
    function_name: str = "",
    agent_name: str = "",
    max_provider_attempts: int | None = None,
    allow_provider_fallback: bool | None = None,
) -> str:
    image_urls = [item for item in (image_parts or []) if isinstance(item, str) and item.startswith("data:")]
    ordered_engines: list[dict] = []
    seen: set[str] = set()
    for engine in engines or []:
        candidate = _text_engine(
            engine,
            use_pro=use_pro,
            call_purpose=call_purpose,
            call_type="text",
        )
        provider = str(candidate.get("provider") or "").strip().lower()
        model_name = str(candidate.get("model_name") or "").strip()
        if not provider:
            continue
        signature = f"{provider}:{model_name}:{candidate.get('id', '')}"
        if signature in seen:
            continue
        seen.add(signature)
        ordered_engines.append(candidate)
    if not ordered_engines:
        if image_urls:
            validate_live_engine(_default_engine(use_pro=use_pro), require_vision=True)
        return await call_model_text(
            prompt,
            engine=_default_engine(use_pro=use_pro),
            generation_config=generation_config,
            image_urls=image_urls,
            call_purpose=call_purpose,
            function_name=function_name or "call_with_engines",
            agent_name=agent_name,
            max_provider_attempts=max_provider_attempts,
            allow_provider_fallback=allow_provider_fallback,
        )

    last_exc: Exception | None = None
    quota_exhausted_providers: set[str] = set()
    if image_urls and ordered_engines:
        validate_live_engine(ordered_engines[0], require_vision=True)
    if allow_provider_fallback is None:
        allow_provider_fallback = ai_enable_provider_fallback()
    if not allow_provider_fallback:
        ordered_engines = ordered_engines[:1]
    max_attempts = max_provider_attempts if max_provider_attempts is not None else ai_max_provider_attempts()
    ordered_engines = ordered_engines[: max(1, int(max_attempts or 1))]
    primary_provider = str(ordered_engines[0].get("provider") or "").strip().lower() if ordered_engines else ""
    for attempt_number, engine in enumerate(ordered_engines, start=1):
        provider = str(engine.get("provider") or "").strip().lower()
        # 429/RESOURCE_EXHAUSTED is project-level quota; skip all remaining
        # engines for this provider — they will fail identically.
        if provider in quota_exhausted_providers:
            continue
        started = time.perf_counter()
        call_number = reserve_llm_call(
            agent_name=agent_name,
            function_name=function_name or "call_with_engines",
            provider=provider,
            model=str(engine.get("model_name") or ""),
            call_purpose=call_purpose,
            call_type="text",
            attempt_number=attempt_number,
            fallback_used=provider != primary_provider,
            token_estimate=estimate_tokens(prompt),
        )
        try:
            text, resolved_model, usage = await _call_provider_once(
                provider,
                prompt,
                engine,
                generation_config=generation_config,
                image_urls=image_urls,
            )
            _log_llm_call(
                outcome="success",
                provider=provider,
                model=resolved_model or str(engine.get("model_name") or ""),
                prompt=prompt,
                response_text=text,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                usage=usage,
                call_purpose=call_purpose,
                function_name=function_name or "call_with_engines",
                agent_name=agent_name,
                attempt_number=attempt_number,
                fallback_used=provider != primary_provider,
                call_number=call_number,
            )
            return text
        except Exception as exc:
            error_str = str(exc)
            _log_llm_call(
                outcome="error",
                provider=provider,
                model=str(engine.get("model_name") or ""),
                prompt=prompt,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                error=exc,
                call_purpose=call_purpose,
                function_name=function_name or "call_with_engines",
                agent_name=agent_name,
                attempt_number=attempt_number,
                fallback_used=provider != primary_provider,
                call_number=call_number,
            )
            last_exc = exc
            if _is_non_retryable_client_error(exc):
                raise
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str.upper():
                quota_exhausted_providers.add(provider)
            continue
    raise RuntimeError(f"All configured engines failed. Last error: {last_exc}") from last_exc




async def call_model_json_batch(
    tasks: dict[str, dict],
    engine: Optional[dict] = None,
    use_pro: bool = False,
    *,
    call_purpose: str = "",
    function_name: str = "",
    agent_name: str = "",
    individual_fallback: bool = True,
    max_provider_attempts: int | None = None,
    allow_provider_fallback: bool | None = None,
) -> dict[str, Any]:
    """Execute multiple JSON-schema tasks in a single LLM request.

    ``tasks`` is a mapping of  task_key -> {"prompt": str, "schema": BaseModel subclass}.
    Returns a mapping of task_key -> parsed result dict.

    This eliminates the N separate LLM round-trips that were previously fired
    for sentiment (message), sentiment (conversation), and intent classification
    on every incoming message — collapsing them into one API call.

    If the combined call fails, each task falls back to its own individual call.
    """
    if not tasks:
        return {}

    schema_map: dict[str, type[BaseModel]] = {}
    combined_sections: list[str] = []
    for key, spec in tasks.items():
        schema_class = spec["schema"]
        schema_map[key] = schema_class
        schema_text = json.dumps(_sanitize_schema(schema_class.model_json_schema()), ensure_ascii=True)
        combined_sections.append(
            f"### Task: {key}\n"
            f"Schema: {schema_text}\n"
            f"Instructions: {spec['prompt']}"
        )

    wrapper_prompt = (
        "You are a multi-task JSON processor.\n"
        "For each task below, produce a JSON result that matches the specified schema exactly.\n"
        "Return a SINGLE JSON object whose keys are exactly the task names listed, each mapping to its result.\n"
        "Return ONLY the JSON object — no markdown, no explanation, no extra keys.\n\n"
        + "\n\n".join(combined_sections)
    )

    selected = _json_engine_for_call(
        engine or _default_engine(use_pro=use_pro),
        call_purpose=call_purpose or ",".join(tasks.keys()),
    )
    provider = (selected.get("provider") or "openai").strip().lower()
    _, selected_max_tokens = _generation_opts(selected, None)
    started = time.perf_counter()
    try:
        if provider == "gemini" and genai_types:
            _, max_tokens = _generation_opts(selected, None)
            config_payload = _json_generation_config(selected)
            config_payload["max_output_tokens"] = max_tokens or selected_max_tokens or _JSON_MAX_OUTPUT_TOKENS
            json_prompt = _json_only_prompt(wrapper_prompt)
            raw = await call_model_text(
                json_prompt,
                engine=selected,
                generation_config=config_payload,
                call_type="json_batch",
                call_purpose=call_purpose or ",".join(tasks.keys()),
                function_name=function_name or "call_model_json_batch",
                agent_name=agent_name,
                max_provider_attempts=max_provider_attempts,
                allow_provider_fallback=allow_provider_fallback,
            )
        else:
            raw = await call_model_text(
                _json_only_prompt(wrapper_prompt),
                engine=selected,
                generation_config=_json_generation_config(selected),
                call_type="json_batch",
                call_purpose=call_purpose or ",".join(tasks.keys()),
                function_name=function_name or "call_model_json_batch",
                agent_name=agent_name,
                max_provider_attempts=max_provider_attempts,
                allow_provider_fallback=allow_provider_fallback,
            )
        parsed = _extract_json_object(raw)
        results: dict[str, Any] = {}
        for key, schema_class in schema_map.items():
            if key in parsed and isinstance(parsed[key], dict):
                try:
                    results[key] = schema_class.model_validate(parsed[key]).model_dump()
                except Exception:
                    results[key] = None
            else:
                results[key] = None
        latency = (time.perf_counter() - started) * 1000.0
        logger.info(
            "llm_batch_call tasks=%s latency_ms=%.2f provider=%s",
            list(tasks.keys()),
            latency,
            provider,
        )
        return results
    except Exception as exc:
        logger.warning(
            "llm_batch_call failed, falling back to individual calls: %s", exc
        )
        if _is_non_retryable_client_error(exc):
            logger.warning(
                "llm_batch_call individual fallback skipped error_type=non_retryable_client_error tasks=%s",
                list(tasks.keys()),
            )
            return {key: None for key in tasks}
        error_type = _classify_llm_error(exc)
        if error_type in {"quota_exhausted", "provider_not_configured"}:
            logger.warning(
                "llm_batch_call individual fallback skipped error_type=%s tasks=%s",
                error_type,
                list(tasks.keys()),
            )
            return {key: None for key in tasks}
        if not individual_fallback:
            return {key: None for key in tasks}
        # Individual fallback
        fallback: dict[str, Any] = {}
        for key, spec in tasks.items():
            try:
                fallback[key] = await call_model_json(
                    spec["prompt"],
                    spec["schema"],
                    engine=engine,
                    use_pro=use_pro,
                    call_purpose=f"batch_individual_fallback:{key}",
                    function_name=function_name or "call_model_json_batch",
                    agent_name=agent_name,
                    max_provider_attempts=1,
                    allow_provider_fallback=False,
                )
            except Exception as exc2:
                logger.warning("llm_batch_call individual fallback failed for %s: %s", key, exc2)
                fallback[key] = None
        return fallback

__all__ = [
    "EMBEDDING_MODEL",
    "FLASH_MODEL",
    "GEMINI_EMBEDDING_MODEL",
    "PRO_MODEL",
    "OPENAI_EMBEDDING_MODEL",
    "_default_engine",
    "_engine_for_provider",
    "_provider_default_model",
    "_resolve_engine_for_request",
    "call_gemini",
    "call_gemini_json",
    "call_model_json",
    "call_model_json_batch",
    "call_model_text",
    "call_with_engines",
    "engine_supports_audio",
    "engine_supports_vision",
    "get_active_llm_engine",
    "get_active_llm_engines",
    "get_provider_runtime_info",
    "stream_model_text",
    "validate_live_engine",
]
