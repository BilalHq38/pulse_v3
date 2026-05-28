from __future__ import annotations

import asyncio
import ast
import copy
import json
import logging
import os
import re
import time
from io import StringIO
from collections.abc import AsyncIterator
from typing import Any, Optional, get_args, get_origin

import httpx
from pydantic import BaseModel, TypeAdapter

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
    GEMINI_PROVIDER_KEYS,
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
_UNIFIED_MESSAGE_JSON_MIN_TOKENS = 2000
_UNIFIED_LEAD_JSON_MIN_TOKENS = 1500
_JSON_FORMAT_RETRY_ATTEMPTS = 3
_JSON_REPAIR_RAW_CHAR_LIMIT = 3000
_JSON_RECOVERY_RAW_CHAR_LIMIT = 50000
_JSON_RECOVERED_TEXT_LIMIT = 4000
_JSON_RETRY_TOKEN_BONUS = 512
_JSON_RETRY_MAX_OUTPUT_TOKENS = 4096
_GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS: set[str] = set()
_GEMINI_RESPONSE_FORMAT_UNSUPPORTED_MODELS: set[str] = set()
_JSON_REPLY_FIELD_NAMES = ("response", "reply", "message", "answer", "body")
_STRICT_JSON_OUTPUT_INSTRUCTIONS = (
    "Return ONLY valid JSON.\n"
    "Do not return markdown.\n"
    "Do not wrap JSON in ```json blocks.\n"
    "Do not include explanations before or after JSON.\n"
    "Do not use comments inside JSON.\n"
    "Do not use trailing commas.\n"
    "Do not use single quotes.\n"
    "All keys and string values must use double quotes.\n"
    "Output must match the provided schema exactly.\n"
    "If information is missing, use safe default values from the schema.\n"
    "The response must be parseable by Python json.loads().\n"
    "Never return partial JSON."
)


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _provider_key(provider: str = "") -> str:
    return str(provider or "").strip().lower()


def _is_gemini_provider(provider: str = "") -> bool:
    return _provider_key(provider) in GEMINI_PROVIDER_KEYS


def _gemini_provider_mode(provider: str = "gemini") -> str:
    key = _provider_key(provider)
    if key == "vertex_ai":
        return "vertex_ai"
    if key == "gemini_api":
        return "gemini_api"
    return "vertex_ai" if _env_flag("GOOGLE_GENAI_USE_VERTEXAI") or _env_flag("VERTEX_AI_ENABLED") else "gemini_api"


def _env_timeout_seconds(name: str, default: float, *, maximum: float = 30.0) -> float:
    try:
        value = float(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(1.0, min(float(maximum), value))


def _gemini_json_timeout_seconds() -> float:
    return _env_timeout_seconds("GEMINI_JSON_TIMEOUT_SECONDS", 8.0)


def _gemini_text_timeout_seconds() -> float:
    return _env_timeout_seconds("GEMINI_TEXT_TIMEOUT_SECONDS", 8.0)


def _gemini_stream_timeout_seconds() -> float:
    return _env_timeout_seconds("GEMINI_STREAM_TIMEOUT_SECONDS", 10.0)


def _provider_timeout_seconds(provider: str, call_type: str = "text") -> float:
    if _is_gemini_provider(provider):
        return _gemini_json_timeout_seconds() if call_type == "json" else _gemini_text_timeout_seconds()
    return ai_api_call_timeout_seconds()


def _provider_stream_timeout_seconds(provider: str) -> float:
    if _is_gemini_provider(provider):
        return _gemini_stream_timeout_seconds()
    return ai_api_call_timeout_seconds()


def _gemini_uses_vertex_ai(provider: str = "gemini") -> bool:
    return _gemini_provider_mode(provider) == "vertex_ai"


def _gemini_vertex_project() -> str:
    return (os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()


def _gemini_vertex_location() -> str:
    return (os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1").strip()


def _gemini_client_ready(provider: str = "gemini") -> tuple[bool, str]:
    if not genai:
        return False, "google-genai is not installed"
    if _gemini_uses_vertex_ai(provider):
        if not _gemini_vertex_project():
            return False, "GOOGLE_CLOUD_PROJECT is not configured for Vertex AI"
        if not _gemini_vertex_location():
            return False, "GOOGLE_CLOUD_LOCATION is not configured for Vertex AI"
        return True, ""
    return (True, "") if GEMINI_API_KEY else (False, "GEMINI_API_KEY is not configured")


def _gemini_client_kwargs(api_version: str, provider: str = "gemini") -> dict[str, Any]:
    kwargs: dict[str, Any] = {"http_options": _gemini_http_options(api_version)}
    if _gemini_uses_vertex_ai(provider):
        kwargs.update(
            {
                "vertexai": True,
                "project": _gemini_vertex_project(),
                "location": _gemini_vertex_location(),
            }
        )
    else:
        kwargs["api_key"] = GEMINI_API_KEY
    return kwargs


def _gemini_client_cache_key(api_version: str, provider: str = "gemini") -> str:
    return f"{api_version}:{_gemini_provider_mode(provider)}"


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
    if "unified_message_ai" in purpose:
        return max(_coerce_positive_int(default, _UNIFIED_MESSAGE_JSON_MIN_TOKENS), _UNIFIED_MESSAGE_JSON_MIN_TOKENS)
    if "unified_lead_ai" in purpose:
        return max(_coerce_positive_int(default, _UNIFIED_LEAD_JSON_MIN_TOKENS), _UNIFIED_LEAD_JSON_MIN_TOKENS)
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
    if "unified_message_ai" in purpose:
        selected["max_tokens"] = max(
            _coerce_positive_int(selected.get("max_tokens"), target),
            _UNIFIED_MESSAGE_JSON_MIN_TOKENS,
        )
    if "unified_lead_ai" in purpose:
        selected["max_tokens"] = max(
            _coerce_positive_int(selected.get("max_tokens"), target),
            _UNIFIED_LEAD_JSON_MIN_TOKENS,
        )
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
if _gemini_client_ready("gemini")[0]:
    try:
        _gemini_client = genai.Client(**_gemini_client_kwargs("v1beta", "gemini"))
        _gemini_clients[_gemini_client_cache_key("v1beta", "gemini")] = _gemini_client
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


def _gemini_client_for_model(model_name: str, provider: str = "gemini"):
    ready, _ = _gemini_client_ready(provider)
    if not ready:
        return None
    api_version = _gemini_api_version_for_model(model_name)
    cache_key = _gemini_client_cache_key(api_version, provider)
    client = _gemini_clients.get(cache_key)
    if client is not None:
        return client
    try:
        client = genai.Client(**_gemini_client_kwargs(api_version, provider))
        _gemini_clients[cache_key] = client
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


def _gemini_chunk_compact_reason(chunk: Any) -> str:
    try:
        candidates = getattr(chunk, "candidates", []) or []
        finish = str(getattr(candidates[0], "finish_reason", "") or "").upper() if candidates else ""
    except Exception:
        finish = ""
    if "PROHIBITED" in finish:
        return "gemini_blocked:PROHIBITED_CONTENT"
    if "SAFETY" in finish:
        return "gemini_blocked:SAFETY"
    if "MAX" in finish and "TOKEN" in finish:
        return "gemini_finish:MAX_TOKENS"
    if "MALFORMED" in finish:
        return "gemini_finish:MALFORMED_RESPONSE"
    return ""


def _gemini_exception_compact_reason(exc: Exception) -> str:
    message = str(exc or "").upper()
    if "RESOURCE_EXHAUSTED" in message or "QUOTA" in message or "429" in message:
        return "gemini_quota:RESOURCE_EXHAUSTED"
    if "PROHIBITED_CONTENT" in message:
        return "gemini_blocked:PROHIBITED_CONTENT"
    if "SAFETY" in message:
        return "gemini_blocked:SAFETY"
    if "MAX_TOKENS" in message or "MAX TOKENS" in message:
        return "gemini_finish:MAX_TOKENS"
    if "MALFORMED" in message:
        return "gemini_finish:MALFORMED_RESPONSE"
    return ""


def _default_engine(use_pro: bool = False) -> dict:
    provider = ai_provider_name("gemini")
    provider_defaults = {
        "openai": OPENAI_DEFAULT_MODEL,
        "anthropic": ANTHROPIC_DEFAULT_MODEL,
        "gemini": _supported_gemini_default_model(use_pro=use_pro),
        "gemini_api": _supported_gemini_default_model(use_pro=use_pro),
        "vertex_ai": os.environ.get("VERTEX_AI_MODEL", "").strip()
        or _supported_gemini_default_model(use_pro=use_pro),
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
    if provider == "vertex_ai":
        return os.environ.get("VERTEX_AI_MODEL", "").strip() or _supported_gemini_default_model(use_pro=use_pro)
    if _is_gemini_provider(provider):
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
    if _is_gemini_provider(provider) and model_name and not is_supported_model(provider, model_name):
        fallback_model = _supported_gemini_default_model(use_pro=use_pro)
        selected["configured_model_name"] = model_name
        selected["model_name"] = fallback_model
        logger.warning(
            "invalid_model_configured_fallback provider=%s configured_model=%s "
            "fallback_model=%s error_type=invalid_model",
            provider,
            model_name,
            fallback_model,
        )
    return selected


def get_provider_runtime_info(provider: str) -> tuple[bool, str]:
    provider = (provider or "").strip().lower()
    if _is_gemini_provider(provider):
        return _gemini_client_ready(provider)
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


def _gemini_config_has_response_format(config: Any) -> bool:
    if isinstance(config, dict):
        return bool(config.get("response_format") or config.get("responseFormat"))
    return bool(getattr(config, "response_format", None) or getattr(config, "responseFormat", None))


def _gemini_config_has_structured_json_fields(config: Any) -> bool:
    if isinstance(config, dict):
        return any(
            config.get(field)
            for field in (
                "response_format",
                "responseFormat",
                "response_mime_type",
                "responseMimeType",
                "response_schema",
                "responseSchema",
                "response_json_schema",
                "responseJsonSchema",
            )
        )
    return any(
        getattr(config, field, None)
        for field in ("response_format", "response_mime_type", "response_schema", "response_json_schema")
    )


def _gemini_response_mime_cache_key(model_name: str) -> str:
    model = str(model_name or "").strip()
    return f"{_gemini_api_version_for_model(model)}:{model}"


def _is_gemini_25_structured_output_model(model_name: str) -> bool:
    model = str(model_name or "").strip().lower()
    return bool(re.fullmatch(r"gemini-2\.5-(?:flash-lite|flash|pro)(?:-.+)?", model))


def _gemini_supports_response_mime_type(engine: dict | None, model_name: str | None = None) -> bool:
    model = str(model_name or (engine or {}).get("model_name") or "").strip()
    if not model or not genai_types or "response_mime_type" not in _gemini_config_fields():
        return False
    explicit = (engine or {}).get("supports_response_mime_type")
    if explicit is not None:
        return bool(explicit)
    if _gemini_response_mime_cache_key(model) in _GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS:
        return False
    return _is_gemini_25_structured_output_model(model) or _gemini_api_version_for_model(model) == "v1beta"


def _is_customer_json_call(call_purpose: str = "") -> bool:
    purpose = str(call_purpose or "").lower()
    return any(marker in purpose for marker in ("unified_message_ai", "customer_message", "inbox_message"))


def _gemini_structured_json_mode(
    engine: dict | None,
    *,
    model_name: str | None = None,
    call_purpose: str = "",
) -> str:
    model = str(model_name or (engine or {}).get("model_name") or "").strip()
    fields = _gemini_config_fields()
    if not model or not genai_types or not fields:
        return "prompt"
    cache_key = _gemini_response_mime_cache_key(model)
    if "response_format" in fields and cache_key not in _GEMINI_RESPONSE_FORMAT_UNSUPPORTED_MODELS:
        return "response_format"
    if _gemini_supports_response_mime_type(engine, model):
        return "legacy"
    if _is_customer_json_call(call_purpose):
        return "prompt"
    return "prompt"


def _gemini_mark_structured_mode_unsupported(model_name: str, config: Any) -> None:
    cache_key = _gemini_response_mime_cache_key(model_name)
    if _gemini_config_has_response_format(config):
        _GEMINI_RESPONSE_FORMAT_UNSUPPORTED_MODELS.add(cache_key)
    if _gemini_config_has_response_mime_type(config):
        _GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS.add(cache_key)


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


def _gemini_config_without_response_format(config: Any) -> Any:
    if config is None:
        return None
    if isinstance(config, dict):
        payload = dict(config)
        payload.pop("response_format", None)
        payload.pop("responseFormat", None)
        return payload or None
    if hasattr(config, "model_copy"):
        try:
            return config.model_copy(update={"response_format": None}) if hasattr(config, "response_format") else config
        except Exception:
            return config
    return config


def _gemini_config_without_structured_json(config: Any) -> Any:
    return _gemini_config_without_response_mime_type(_gemini_config_without_response_format(config))


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
    if payload.get("responseFormat") is not None and payload.get("response_format") is None:
        payload["response_format"] = payload.get("responseFormat")
    for key in (
        "maxOutputTokens",
        "max_tokens",
        "responseMimeType",
        "responseSchema",
        "responseJsonSchema",
        "responseFormat",
    ):
        payload.pop(key, None)
    fields = _gemini_config_fields()
    if fields and "response_format" not in fields:
        payload.pop("response_format", None)
    if fields:
        payload = {key: value for key, value in payload.items() if key in fields}
    return payload


def _build_gemini_config(kwargs: dict[str, Any] | None = None) -> Any:
    payload = _normalize_gemini_config_payload(kwargs)
    if _gemini_supports_afc_disable():
        payload.setdefault("automatic_function_calling", _gemini_afc_disabled_config())
    if genai_types:
        try:
            return genai_types.GenerateContentConfig(**payload) if payload else genai_types.GenerateContentConfig()
        except Exception:
            fallback_payload = _gemini_config_without_structured_json(payload) if payload else None
            if fallback_payload != payload:
                return (
                    genai_types.GenerateContentConfig(**fallback_payload)
                    if fallback_payload
                    else genai_types.GenerateContentConfig()
                )
            raise
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
        return f"{_STRICT_JSON_OUTPUT_INSTRUCTIONS}\nSchema: {schema_text}\n\n{prompt}"
    return f"{_STRICT_JSON_OUTPUT_INSTRUCTIONS}\n\n{prompt}"


def _is_gemini_response_mime_type_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    normalized = message.replace("_", "")
    structured_field = any(
        field in normalized
        for field in ("responseformat", "responsemimetype", "responseschema", "responsejsonschema", "generationconfig")
    )
    return structured_field and (
        "unknown name" in message
        or "unknown field" in message
        or "cannot find field" in message
        or "invalid json payload" in message
        or "invalid_argument" in message
        or "unsupported" in message
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
    provider = str(engine.get("provider") or "gemini")
    if not _gemini_client_for_model(preferred_model, provider):
        raise RuntimeError(get_provider_runtime_info(provider)[1])
    config = _prepare_gemini_generation_config(engine, generation_config)
    parts = [part for part in [_image_data_url_to_part(url) for url in (image_urls or [])] if part]
    contents: Any = [prompt] + parts if parts else prompt
    errors: list[str] = []
    model_candidates = _iter_gemini_model_candidates(engine.get("model_name"))
    for index, model_name in enumerate(model_candidates):
        response_mime_retry_used = False
        logged_empty_reasons: set[str] = set()
        try:
            client = _gemini_client_for_model(model_name, provider)
            if not client:
                raise RuntimeError(get_provider_runtime_info(provider)[1])
            model_cache_key = _gemini_response_mime_cache_key(model_name)
            while True:
                request_config = config
                if model_cache_key in _GEMINI_RESPONSE_FORMAT_UNSUPPORTED_MODELS and _gemini_config_has_response_format(
                    request_config
                ):
                    request_config = _gemini_config_without_response_format(request_config)
                    logger.warning("gemini_structured_mode_disabled model=%s mode=response_format", model_name)
                if model_cache_key in _GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS and _gemini_config_has_response_mime_type(
                    request_config
                ):
                    request_config = _gemini_config_without_response_mime_type(request_config)
                    logger.warning("gemini_structured_mode_disabled model=%s mode=response_mime_type", model_name)
                if _gemini_config_has_structured_json_fields(config) and not _gemini_config_has_structured_json_fields(
                    request_config
                ):
                    logger.info("gemini_prompt_json_mode_used model=%s reason=structured_mode_disabled", model_name)
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
                                reason = _gemini_chunk_compact_reason(chunk)
                                if reason and reason not in logged_empty_reasons:
                                    logged_empty_reasons.add(reason)
                                    logger.warning("%s model=%s", reason, model_name)
                    else:
                        for chunk in stream:
                            text = _extract_gemini_chunk_text(chunk)
                            if text:
                                yield text, model_name
                            else:
                                reason = _gemini_chunk_compact_reason(chunk)
                                if reason and reason not in logged_empty_reasons:
                                    logged_empty_reasons.add(reason)
                                    logger.warning("%s model=%s", reason, model_name)
                    return
                except Exception as exc:
                    if (
                        not response_mime_retry_used
                        and _gemini_config_has_structured_json_fields(request_config)
                        and _is_gemini_response_mime_type_error(exc)
                    ):
                        response_mime_retry_used = True
                        _gemini_mark_structured_mode_unsupported(model_name, request_config)
                        config = _gemini_config_without_structured_json(config)
                        logger.warning(
                            "gemini_config_rejected model=%s mode=%s error=%s",
                            model_name,
                            "response_format"
                            if _gemini_config_has_response_format(request_config)
                            else "response_mime_type",
                            str(exc).splitlines()[0][:240],
                        )
                        logger.warning(
                            "gemini_structured_mode_disabled model=%s mode=%s",
                            model_name,
                            "response_format"
                            if _gemini_config_has_response_format(request_config)
                            else "response_mime_type",
                        )
                        if engine.get("_disable_structured_config_retry"):
                            raise
                        continue
                    raise
        except Exception as exc:
            error_str = str(exc)
            compact_reason = _gemini_exception_compact_reason(exc)
            if compact_reason:
                logger.warning("%s model=%s", compact_reason, model_name)
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
            if _is_resource_exhausted(exc):
                raise
            if _is_non_retryable_client_error(exc):
                raise
            continue
    raise RuntimeError(
        "Gemini generation failed across models: " + "; ".join(errors)
        if errors
        else "Gemini generation failed with no eligible models"
    )


async def _generate_gemini_content(
    prompt: str,
    engine: dict,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> tuple[str, str, dict[str, int]]:
    preferred_model = str(engine.get("model_name") or "").strip()
    provider = str(engine.get("provider") or "gemini")
    if not _gemini_client_for_model(preferred_model, provider):
        raise RuntimeError(get_provider_runtime_info(provider)[1])
    config = _prepare_gemini_generation_config(engine, generation_config)
    parts = [part for part in [_image_data_url_to_part(url) for url in (image_urls or [])] if part]
    contents: Any = [prompt] + parts if parts else prompt
    errors: list[str] = []
    model_candidates = _iter_gemini_model_candidates(engine.get("model_name"))
    for index, model_name in enumerate(model_candidates):
        response_mime_retry_used = False
        try:
            client = _gemini_client_for_model(model_name, provider)
            if not client:
                raise RuntimeError(get_provider_runtime_info(provider)[1])
            model_cache_key = _gemini_response_mime_cache_key(model_name)
            while True:
                request_config = config
                if model_cache_key in _GEMINI_RESPONSE_FORMAT_UNSUPPORTED_MODELS and _gemini_config_has_response_format(
                    request_config
                ):
                    request_config = _gemini_config_without_response_format(request_config)
                    logger.warning("gemini_structured_mode_disabled model=%s mode=response_format", model_name)
                if model_cache_key in _GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS and _gemini_config_has_response_mime_type(
                    request_config
                ):
                    request_config = _gemini_config_without_response_mime_type(request_config)
                    logger.warning("gemini_structured_mode_disabled model=%s mode=response_mime_type", model_name)
                if _gemini_config_has_structured_json_fields(config) and not _gemini_config_has_structured_json_fields(
                    request_config
                ):
                    logger.info("gemini_prompt_json_mode_used model=%s reason=structured_mode_disabled", model_name)
                try:
                    response = client.aio.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=request_config,
                    )
                    if hasattr(response, "__await__"):
                        response = await response
                    text = _extract_gemini_chunk_text(response).strip()
                    if not text:
                        reason = _gemini_chunk_compact_reason(response)
                        logger.warning("%s model=%s", reason or "gemini_empty_response", model_name)
                        raise ValueError("Empty AI response")
                    return text, model_name, _normalize_usage_dict(prompt=prompt, response_text=text)
                except Exception as exc:
                    if (
                        not response_mime_retry_used
                        and _gemini_config_has_structured_json_fields(request_config)
                        and _is_gemini_response_mime_type_error(exc)
                    ):
                        response_mime_retry_used = True
                        _gemini_mark_structured_mode_unsupported(model_name, request_config)
                        config = _gemini_config_without_structured_json(config)
                        logger.warning(
                            "gemini_config_rejected model=%s mode=%s error=%s",
                            model_name,
                            "response_format"
                            if _gemini_config_has_response_format(request_config)
                            else "response_mime_type",
                            str(exc).splitlines()[0][:240],
                        )
                        logger.warning(
                            "gemini_structured_mode_disabled model=%s mode=%s",
                            model_name,
                            "response_format"
                            if _gemini_config_has_response_format(request_config)
                            else "response_mime_type",
                        )
                        if engine.get("_disable_structured_config_retry"):
                            raise
                        continue
                    raise
        except Exception as exc:
            error_str = str(exc)
            compact_reason = _gemini_exception_compact_reason(exc)
            if compact_reason:
                logger.warning("%s model=%s", compact_reason, model_name)
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
            if _is_resource_exhausted(exc):
                raise
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
    all_providers = ["openai", "anthropic", "gemini_api", "vertex_ai", "gemini"]
    primary = (primary_provider or "openai").strip().lower()
    ordered = [primary] + [p for p in all_providers if p != primary]
    ready: list[str] = []
    seen_runtime: set[str] = set()
    for provider in ordered:
        if not get_provider_runtime_info(provider)[0]:
            continue
        runtime_key = f"gemini:{_gemini_provider_mode(provider)}" if _is_gemini_provider(provider) else provider
        if runtime_key in seen_runtime:
            continue
        seen_runtime.add(runtime_key)
        ready.append(provider)
    return ready


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
    if _is_gemini_provider(provider):
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
    call_type: str = "text",
    provider_count: int = 1,
) -> tuple[str, str, dict[str, int]]:
    timeout_seconds = _provider_timeout_seconds(provider, call_type)

    if _is_gemini_provider(provider) and call_type == "json":
        last_exc: Exception | None = None
        max_attempts = 2
        for attempt_index in range(max_attempts):
            try:
                logger.debug(
                    "gemini_json_non_streaming_used model=%s timeout_seconds=%.1f",
                    engine.get("model_name") or "",
                    timeout_seconds,
                )
                return await asyncio.wait_for(
                    _generate_gemini_content(
                        prompt,
                        engine,
                        generation_config=generation_config,
                        image_urls=image_urls,
                    ),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                last_exc = TimeoutError(
                    f"{provider} JSON call timed out after {timeout_seconds:.1f}s "
                    f"model={engine.get('model_name') or 'unknown'}"
                )
                last_exc.__cause__ = exc
                logger.warning(
                    "gemini_json_timeout provider_count=%s active_provider=%s reason=timeout "
                    "call_type=json timeout_seconds=%.1f attempt=%s max_attempts=%s model=%s",
                    provider_count,
                    provider,
                    timeout_seconds,
                    attempt_index + 1,
                    max_attempts,
                    engine.get("model_name") or "",
                )
                if attempt_index + 1 >= max_attempts:
                    break
            except Exception as exc:
                last_exc = exc
                if _is_resource_exhausted(exc):
                    logger.warning(
                        "gemini_quota:RESOURCE_EXHAUSTED provider_count=%s active_provider=%s "
                        "call_type=json model=%s",
                        provider_count,
                        provider,
                        engine.get("model_name") or "",
                    )
                break
        raise last_exc or RuntimeError(f"{provider} JSON generation failed")

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
            if _is_gemini_provider(provider):
                logger.warning("gemini_empty_response model=%s", resolved_model or engine.get("model_name") or "")
            raise ValueError("Empty AI response")
        usage = _normalize_usage_dict(prompt=prompt, response_text=text)
        return text, resolved_model, usage

    last_exc: Exception | None = None
    max_retries = (
        1
        if (engine or {}).get("_disable_transient_retries") or (engine or {}).get("_disable_structured_config_retry")
        else _MAX_TRANSIENT_RETRIES
    )
    for retry_index in range(max_retries):
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
        if retry_index + 1 >= max_retries or not _is_retryable_ai_error(last_exc):
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
    # Wave 6+: track non-Gemini provider dispatch so we can confirm whether
    # any tenant config still routes to OpenAI / Anthropic / Vertex before
    # deleting those branches. A week of zero hits clears the path.
    if not _is_gemini_provider(primary_provider):
        logger.warning(
            "non_gemini_provider_invoked provider=%s call_type=%s call_purpose=%s",
            primary_provider, call_type, call_purpose or "",
        )
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
                call_type=call_type,
                provider_count=len(provider_order),
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
                outcome="timeout" if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else "error",
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

    logger.warning(
        "llm_providers_exhausted provider_count=%s active_provider=%s reason=%s "
        "call_type=%s timeout_seconds=%.1f",
        len(provider_order),
        primary_provider,
        _llm_exhaustion_reason(last_exc),
        call_type,
        _provider_timeout_seconds(primary_provider, call_type),
    )
    raise RuntimeError(_provider_exhausted_error_message(provider_order, last_exc)) from last_exc


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
    # Wave 6+: track non-Gemini provider dispatch so we can confirm whether
    # any tenant config still routes to OpenAI / Anthropic / Vertex before
    # deleting those branches. A week of zero hits clears the path.
    if not _is_gemini_provider(primary_provider):
        logger.warning(
            "non_gemini_provider_invoked provider=%s call_type=%s call_purpose=%s",
            primary_provider, call_type, call_purpose or "",
        )
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
        timeout_seconds = _provider_stream_timeout_seconds(provider)
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
            async with asyncio.timeout(timeout_seconds):
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
                f"{provider} streaming call timed out after {timeout_seconds:.1f}s "
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
    logger.warning(
        "llm_providers_exhausted provider_count=%s active_provider=%s reason=%s "
        "call_type=%s timeout_seconds=%.1f",
        len(provider_order),
        primary_provider,
        _llm_exhaustion_reason(last_exc),
        call_type,
        _provider_stream_timeout_seconds(primary_provider),
    )
    raise RuntimeError(_provider_exhausted_error_message(provider_order, last_exc)) from last_exc


def _strip_json_fence(text: str) -> str:
    candidate = (text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return candidate


def _load_json_object(text: str) -> tuple[dict[str, Any] | None, Exception | None]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        last_error: Exception = exc
        try:
            parsed = json.JSONDecoder(strict=False).decode(text)
        except json.JSONDecodeError as strict_exc:
            last_error = strict_exc
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError) as literal_exc:
                return None, literal_exc
        except Exception as strict_exc:
            return None, strict_exc
    except Exception as exc:
        return None, exc
    if isinstance(parsed, dict):
        return parsed, None
    return None, ValueError("JSON response was not an object")


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def _quote_keys(text: str) -> str:
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


def _complete_truncated_json_like(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    candidate = text[start:].strip()
    stack: list[str] = []
    in_string = False
    quote = ""
    escape = False
    for char in candidate:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                in_string = False
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
        elif char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in ("}", "]") and stack and stack[-1] == char:
            stack.pop()
    if in_string:
        candidate += quote or '"'
    candidate = re.sub(r',\s*["\']?[A-Za-z_][A-Za-z0-9_]*["\']?\s*:?\s*$', "", candidate.rstrip())
    candidate = re.sub(r"[:,]\s*$", "", candidate.rstrip())
    return candidate + "".join(reversed(stack))


def _try_parse_json_variants(text: str) -> tuple[dict[str, Any] | None, Exception | None]:
    last_error: Exception | None = None
    for variant in (
        text,
        _remove_trailing_commas(text),
        _remove_trailing_commas(_quote_keys(text)),
    ):
        parsed, error = _load_json_object(variant)
        if parsed is not None:
            return parsed, None
        last_error = error or last_error
    return None, last_error


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Extract and parse a JSON object from a raw LLM response.

    The model is instructed to return a JSON object, but some providers may
    return invalid JSON (e.g. unquoted keys or wrapped in code fences).  This
    function attempts to sanitize and parse the first JSON-like object found.
    """
    candidate = _strip_json_fence(raw or "")
    if not candidate:
        return {}

    parsed, last_error = _try_parse_json_variants(candidate)
    if parsed is not None:
        return parsed

    recovery_candidate = candidate[:_JSON_RECOVERY_RAW_CHAR_LIMIT]
    for obj_str in _object_candidates(recovery_candidate):
        parsed, error = _try_parse_json_variants(obj_str)
        if parsed is not None:
            return parsed
        last_error = error or last_error

    completed = _complete_truncated_json_like(recovery_candidate)
    if completed:
        parsed, error = _try_parse_json_variants(completed)
        if parsed is not None:
            return parsed
        last_error = error or last_error
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


def _llm_exhaustion_reason(exc: Exception | None) -> str:
    message = str(exc or "").lower()
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timed out" in message:
        return "timeout"
    if exc is not None and _is_resource_exhausted(exc):
        return "quota"
    if exc is not None and _is_invalid_model_error(exc):
        return "invalid_model"
    return "provider_error"


def _provider_exhausted_error_message(provider_order: list[str], last_exc: Exception | None) -> str:
    if len(provider_order or []) <= 1:
        return f"Selected AI provider failed with no usable provider fallback. Last error: {last_exc}"
    return f"All AI providers exhausted. Last error: {last_exc}"


def _compact_exception_message(exc: Exception | None, limit: int = 300) -> str:
    if exc is None:
        return "unknown error"
    message = str(exc).splitlines()[0].strip() or exc.__class__.__name__
    return message[:limit]


def _gemini_json_schema_config(schema: type[BaseModel] | None, schema_payload: dict[str, Any] | None) -> dict[str, Any]:
    if schema is None:
        return {}
    fields = _gemini_config_fields()
    if "response_schema" in fields:
        return {"response_schema": schema}
    if "response_json_schema" in fields:
        return {"response_json_schema": schema_payload or _sanitize_schema(schema.model_json_schema())}
    return {}


def _json_generation_config(
    engine: dict,
    schema: type[BaseModel] | None = None,
    schema_payload: dict[str, Any] | None = None,
    call_purpose: str = "",
) -> dict[str, Any]:
    _, selected_max_tokens = _generation_opts(engine, None)
    config: dict[str, Any] = {
        "response_format": "json_object",
        "temperature": 0.0,
        "max_output_tokens": selected_max_tokens or _JSON_MAX_OUTPUT_TOKENS,
    }
    provider = str((engine or {}).get("provider") or "").strip().lower()
    if not _is_gemini_provider(provider):
        return config
    config.pop("response_format", None)
    if schema is None and not schema_payload:
        logger.info(
            "gemini_prompt_json_mode_used model=%s reason=missing_schema",
            str((engine or {}).get("model_name") or ""),
        )
        return config
    mode = _gemini_structured_json_mode(engine, call_purpose=call_purpose)
    if mode == "response_format" and schema_payload:
        config["response_format"] = {
            "text": {
                "mime_type": "application/json",
                "schema": schema_payload,
            }
        }
        logger.info(
            "gemini_structured_json_mode_used model=%s mode=response_format",
            str((engine or {}).get("model_name") or ""),
        )
        return config
    if mode == "legacy":
        config["response_mime_type"] = "application/json"
        config.update(_gemini_json_schema_config(schema, schema_payload))
        logger.info(
            "gemini_structured_json_mode_used model=%s mode=response_mime_type",
            str((engine or {}).get("model_name") or ""),
        )
        return config
    cache_key = _gemini_response_mime_cache_key(str((engine or {}).get("model_name") or ""))
    if cache_key in _GEMINI_RESPONSE_FORMAT_UNSUPPORTED_MODELS:
        logger.warning(
            "gemini_structured_mode_disabled model=%s mode=response_format",
            str((engine or {}).get("model_name") or ""),
        )
    if cache_key in _GEMINI_RESPONSE_MIME_UNSUPPORTED_MODELS:
        logger.warning(
            "gemini_structured_mode_disabled model=%s mode=response_mime_type",
            str((engine or {}).get("model_name") or ""),
        )
    logger.info(
        "gemini_prompt_json_mode_used model=%s reason=structured_mode_unavailable",
        str((engine or {}).get("model_name") or ""),
    )
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


def _sanitize_recovered_text(value: Any) -> str:
    text = str(value or "").replace("\x00", "").strip()
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]+", " ", text)
    text = _strip_json_fence(text)
    if len(text) > _JSON_RECOVERED_TEXT_LIMIT:
        text = text[:_JSON_RECOVERED_TEXT_LIMIT].rstrip()
    return text


def _scan_jsonish_string_value(text: str, key: str) -> str:
    pattern = re.compile(rf'(?<![A-Za-z0-9_])["\']?{re.escape(key)}["\']?\s*:', flags=re.IGNORECASE)
    for match in pattern.finditer(text[:_JSON_RECOVERY_RAW_CHAR_LIMIT]):
        pos = match.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            continue
        quote = text[pos] if text[pos] in {'"', "'"} else ""
        if quote:
            pos += 1
            chars: list[str] = []
            escape = False
            while pos < len(text):
                char = text[pos]
                if escape:
                    chars.append(char)
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == quote:
                    break
                else:
                    chars.append(char)
                pos += 1
            recovered = _sanitize_recovered_text("".join(chars))
            if recovered:
                return recovered
        else:
            end = pos
            while end < len(text) and text[end] not in ",}]\n\r":
                end += 1
            recovered = _sanitize_recovered_text(text[pos:end])
            if recovered:
                return recovered
    return ""


def _extract_reply_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    ai_response = payload.get("ai_response")
    if isinstance(ai_response, dict):
        nested = _extract_reply_from_payload(ai_response)
        if nested:
            return nested
    for key in _JSON_REPLY_FIELD_NAMES:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _sanitize_recovered_text(value)
    for value in payload.values():
        if isinstance(value, dict):
            nested = _extract_reply_from_payload(value)
            if nested:
                return nested
    return ""


def _extract_reply_from_text(raw: str) -> str:
    text = (raw or "")[:_JSON_RECOVERY_RAW_CHAR_LIMIT]
    ai_response_match = re.search(r'["\']?ai_response["\']?\s*:\s*\{', text, flags=re.IGNORECASE)
    search_text = text[ai_response_match.start() :] if ai_response_match else text
    for key in _JSON_REPLY_FIELD_NAMES:
        recovered = _scan_jsonish_string_value(search_text, key)
        if recovered:
            return recovered
    return ""


def _model_schema_from_annotation(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in get_args(annotation):
        nested = _model_schema_from_annotation(arg)
        if nested is not None:
            return nested
    return None


def _default_for_annotation(annotation: Any, field_name: str, recovered_reply: str) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if field_name in _JSON_REPLY_FIELD_NAMES and recovered_reply:
        return recovered_reply
    if field_name == "confidence":
        return 0.2
    if field_name == "source":
        return "recovered"
    if field_name in {"status", "scoring_status"}:
        return "recovered"
    if field_name == "intent":
        return "unknown"
    if field_name in {"sentiment", "label", "emotion"}:
        return "neutral"
    if field_name == "summary":
        return ""
    if origin in (list, tuple, set):
        return []
    if origin is dict:
        return {}
    if args and "Literal" in str(origin):
        return args[0]
    if origin is not None and args:
        non_none = [arg for arg in args if arg is not type(None)]
        if non_none:
            return _default_for_annotation(non_none[0], field_name, recovered_reply)
    if annotation is bool:
        return False
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is dict:
        return {}
    if annotation is list:
        return []
    if annotation is str:
        return recovered_reply or "recovered"
    nested_schema = _model_schema_from_annotation(annotation)
    if nested_schema is not None:
        return _schema_recovery_payload(nested_schema, {}, recovered_reply)
    return None


def _field_default(field_name: str, field: Any, recovered_reply: str) -> Any:
    if field_name in _JSON_REPLY_FIELD_NAMES and recovered_reply:
        return recovered_reply
    if field_name == "confidence":
        return 0.2
    try:
        if not field.is_required():
            return copy.deepcopy(field.get_default(call_default_factory=True))
    except Exception:
        pass
    return _default_for_annotation(getattr(field, "annotation", Any), field_name, recovered_reply)


def _validated_field_value(field: Any, value: Any) -> Any:
    adapter = TypeAdapter(getattr(field, "annotation", Any))
    validated = adapter.validate_python(value)
    return validated.model_dump() if isinstance(validated, BaseModel) else validated


def _schema_recovery_payload(
    schema: type[BaseModel],
    partial: dict[str, Any] | None,
    recovered_reply: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    source = partial if isinstance(partial, dict) else {}
    for field_name, field in schema.model_fields.items():
        nested_schema = _model_schema_from_annotation(getattr(field, "annotation", Any))
        if nested_schema is not None:
            nested_source = source.get(field_name) if isinstance(source.get(field_name), dict) else {}
            payload[field_name] = _schema_recovery_payload(nested_schema, nested_source, recovered_reply)
            continue
        if field_name in source:
            try:
                payload[field_name] = _validated_field_value(field, source[field_name])
                continue
            except Exception:
                pass
        payload[field_name] = _field_default(field_name, field, recovered_reply)
    return payload


def _validate_json_response(raw: str, schema: type[BaseModel]) -> dict[str, Any]:
    partial: dict[str, Any] = {}
    try:
        partial = _extract_json_object(raw)
        return schema.model_validate(partial).model_dump()
    except Exception as exc:
        logger.warning(
            "llm_json_response_invalid schema=%s raw_chars=%s error=%s",
            getattr(schema, "__name__", "BaseModel"),
            len(raw or ""),
            _compact_exception_message(exc),
        )
        recovered_reply = _extract_reply_from_payload(partial) or _extract_reply_from_text(raw)
        fallback_payload = _schema_recovery_payload(schema, partial, recovered_reply)
        try:
            result = schema.model_validate(fallback_payload).model_dump()
            logger.warning(
                "llm_json_local_recovery_success schema=%s raw_chars=%s recovered_reply=%s",
                getattr(schema, "__name__", "BaseModel"),
                len(raw or ""),
                bool(recovered_reply),
            )
            return result
        except Exception:
            logger.warning(
                "llm_json_local_recovery_failed schema=%s raw_chars=%s error=%s",
                getattr(schema, "__name__", "BaseModel"),
                len(raw or ""),
                _compact_exception_message(exc),
            )
            raise


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
    if _is_customer_json_call(call_purpose):
        selected["_disable_transient_retries"] = True
        if max_provider_attempts is None:
            max_provider_attempts = 1
        if allow_provider_fallback is None:
            allow_provider_fallback = False
    schema_payload = _sanitize_schema(schema.model_json_schema())
    raw = await call_model_text(
        _json_only_prompt(prompt, schema_payload),
        engine=selected,
        generation_config=_json_generation_config(selected, schema, schema_payload, call_purpose),
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
        logger.warning(
            "llm_json_response_invalid function=%s purpose=%s raw_chars=%s error=%s",
            function_name or "call_model_json",
            call_purpose or "-",
            len(raw or ""),
            _compact_exception_message(exc),
        )
        raise


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
    if len(ordered_engines or []) <= 1:
        raise RuntimeError(f"Selected AI engine failed with no usable provider fallback. Last error: {last_exc}") from last_exc
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
