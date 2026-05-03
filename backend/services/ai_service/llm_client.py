from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

from pydantic import BaseModel

from shared.config import (
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

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_types = None

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

_gemini_client = None
if genai and GEMINI_API_KEY:
    try:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as exc:
        logger.warning("Gemini client init failed: %s", exc)

_openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if AsyncOpenAI and OPENAI_API_KEY else None
_anthropic_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY) if AsyncAnthropic and ANTHROPIC_API_KEY else None


def _default_engine(use_pro: bool = False) -> dict:
    provider = ai_provider_name("openai")
    provider_defaults = {
        "openai": OPENAI_DEFAULT_MODEL,
        "anthropic": ANTHROPIC_DEFAULT_MODEL,
        "gemini": PRO_MODEL if use_pro else FLASH_MODEL,
    }
    if provider not in provider_defaults:
        provider = "openai"
    return {
        "provider": provider,
        "model_name": ai_model_name() or provider_defaults[provider],
        "temperature": DEFAULT_TEMPERATURE,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "supports_vision": provider in {"gemini", "openai"},
    }


def _provider_default_model(provider: str, use_pro: bool = False) -> str:
    provider = (provider or "").strip().lower()
    if provider == "openai":
        return OPENAI_DEFAULT_MODEL
    if provider == "anthropic":
        return ANTHROPIC_DEFAULT_MODEL
    if provider == "gemini":
        return PRO_MODEL if use_pro else FLASH_MODEL
    return ""


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


def validate_live_engine(engine: dict, require_vision: bool = True) -> None:
    ready, message = get_provider_runtime_info(engine.get("provider", ""))
    if not ready:
        raise RuntimeError(message)
    if require_vision and not engine_supports_vision(engine):
        raise RuntimeError("Selected model does not support image inputs")


def _generation_opts(engine: Optional[dict], generation_config: Any = None) -> tuple[Optional[float], Optional[int]]:
    temperature = float(engine["temperature"]) if engine and engine.get("temperature") is not None else None
    max_tokens = int(engine["max_tokens"]) if engine and engine.get("max_tokens") else None
    if isinstance(generation_config, dict):
        if generation_config.get("temperature") is not None:
            temperature = float(generation_config["temperature"])
        if generation_config.get("max_output_tokens") is not None:
            max_tokens = int(generation_config["max_output_tokens"])
        if generation_config.get("max_tokens") is not None:
            max_tokens = int(generation_config["max_tokens"])
    return temperature, max_tokens


def _iter_gemini_model_candidates(preferred_model: str | None) -> list[str]:
    ordered: list[str] = []
    for candidate in [preferred_model, FLASH_MODEL, PRO_MODEL, *GEMINI_FALLBACK_MODELS]:
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
    message = (
        "llm_call outcome=%s message_id=%s conversation_id=%s workflow_id=%s company_id=%s "
        "agent=%s function=%s provider=%s model=%s purpose=%s call_type=%s attempt=%s "
        "fallback_used=%s fallback_from=%s call_number=%s budget_count=%s token_estimate=%s latency_ms=%.2f "
        "prompt_tokens=%s completion_tokens=%s total_tokens=%s"
    )
    if error is None:
        logger.info(
            message,
            outcome,
            (context.message_id if context else "") or "-",
            (context.conversation_id if context else "") or "-",
            (context.workflow_id if context else "") or "-",
            (context.company_id if context else "") or "-",
            agent_name or (context.agent_name if context else "") or "-",
            function_name or "-",
            provider,
            model,
            call_purpose or "-",
            call_type or "-",
            attempt_number,
            bool(fallback_used),
            fallback_from or "-",
            call_number or (context.call_count if context else 0),
            call_number or (context.call_count if context else 0),
            token_estimate or estimate_tokens(prompt),
            latency_ms,
            usage_payload.get("prompt_tokens", 0),
            usage_payload.get("completion_tokens", 0),
            usage_payload.get("total_tokens", 0),
        )
        return
    logger.warning(
        message + " error_type=%s error=%s",
        outcome,
        (context.message_id if context else "") or "-",
        (context.conversation_id if context else "") or "-",
        (context.workflow_id if context else "") or "-",
        (context.company_id if context else "") or "-",
        agent_name or (context.agent_name if context else "") or "-",
        function_name or "-",
        provider,
        model,
        call_purpose or "-",
        call_type or "-",
        attempt_number,
        bool(fallback_used),
        fallback_from or "-",
        call_number or (context.call_count if context else 0),
        call_number or (context.call_count if context else 0),
        token_estimate or estimate_tokens(prompt),
        latency_ms,
        usage_payload.get("prompt_tokens", 0),
        usage_payload.get("completion_tokens", 0),
        usage_payload.get("total_tokens", 0),
        error.__class__.__name__,
        str(error).splitlines()[0][:240],
    )


async def _call_gemini(
    prompt: str,
    engine: dict,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> tuple[str, str, dict[str, int]]:
    if not _gemini_client:
        raise RuntimeError(get_provider_runtime_info("gemini")[1])
    config = generation_config
    temperature, max_tokens = _generation_opts(engine, generation_config)
    if config is None and genai_types:
        kwargs: dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens
        config = genai_types.GenerateContentConfig(**kwargs) if kwargs else None
    parts = [part for part in [_image_data_url_to_part(url) for url in (image_urls or [])] if part]
    contents: Any = [prompt] + parts if parts else prompt
    errors: list[str] = []
    for model_name in _iter_gemini_model_candidates(engine.get("model_name")):
        try:
            response = await _gemini_client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
            text = (getattr(response, "text", "") or "").strip()
            usage_meta = getattr(response, "usage_metadata", None)
            usage = _normalize_usage_dict(
                getattr(usage_meta, "prompt_token_count", None),
                getattr(usage_meta, "candidates_token_count", None),
                prompt=prompt,
                response_text=text,
            )
            return text, model_name, usage
        except Exception as exc:
            error_str = str(exc)
            errors.append(f"{model_name}:{exc.__class__.__name__}:{error_str}")
            # 429 RESOURCE_EXHAUSTED is a quota/billing limit that applies to
            # the entire project - retrying with another model candidate will
            # produce the same error. Break so the provider-level fallback
            # (OpenAI/Anthropic) can take over immediately.
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str.upper():
                break
            continue
    raise RuntimeError(
        "Gemini generation failed across models: " + "; ".join(errors)
        if errors
        else "Gemini generation failed with no eligible models"
    )


async def _call_openai(
    prompt: str,
    engine: dict,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> tuple[str, str, dict[str, int]]:
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
    response = await _openai_client.chat.completions.create(**kwargs)
    text = (response.choices[0].message.content or "").strip()
    usage_meta = getattr(response, "usage", None)
    usage = _normalize_usage_dict(
        getattr(usage_meta, "prompt_tokens", None),
        getattr(usage_meta, "completion_tokens", None),
        prompt=prompt,
        response_text=text,
    )
    return text, str(kwargs["model"]), usage


async def _call_anthropic(
    prompt: str,
    engine: dict,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> tuple[str, str, dict[str, int]]:
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
    response = await _anthropic_client.messages.create(**kwargs)
    text = "\n".join(
        [getattr(block, "text", "") for block in getattr(response, "content", []) or [] if getattr(block, "text", "")]
    ).strip()
    usage_meta = getattr(response, "usage", None)
    usage = _normalize_usage_dict(
        getattr(usage_meta, "input_tokens", None),
        getattr(usage_meta, "output_tokens", None),
        prompt=prompt,
        response_text=text,
    )
    return text, str(kwargs["model"]), usage


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


async def _call_provider_once(
    provider: str,
    prompt: str,
    engine: dict,
    *,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> tuple[str, str, dict[str, int]]:
    if provider == "gemini":
        return await _call_gemini(
            prompt,
            engine,
            generation_config=generation_config,
            image_urls=image_urls,
        )
    if provider == "openai":
        return await _call_openai(
            prompt,
            engine,
            generation_config=generation_config,
            image_urls=image_urls,
        )
    if provider == "anthropic":
        return await _call_anthropic(
            prompt,
            engine,
            generation_config=generation_config,
            image_urls=image_urls,
        )
    raise RuntimeError(f"Unsupported provider: {provider or 'unknown'}")


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
    selected = dict(engine or _default_engine(use_pro=use_pro))
    primary_provider = (selected.get("provider") or "openai").strip().lower()
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
            # 429/quota errors are project-level for this provider — skip
            # remaining models for this provider but CONTINUE to the next
            # provider in the fallback chain (OpenAI, Anthropic, etc.).
            # Previously `break` caused the entire loop to stop, meaning a
            # Gemini quota error would prevent OpenAI/Anthropic from being tried.
            continue

    raise RuntimeError(f"All AI providers exhausted. Last error: {last_exc}") from last_exc


def _extract_json_object(raw: str) -> dict[str, Any]:
    candidate = (raw or "").strip()
    if not candidate:
        return {}
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = re.sub(r"^json\s*", "", candidate, flags=re.IGNORECASE)
    try:
        return json.loads(candidate)
    except Exception:
        match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _classify_llm_error(exc: Exception) -> str:
    message = str(exc or "").lower()
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
    selected = dict(engine or _default_engine(use_pro=use_pro))
    provider = (selected.get("provider") or "openai").strip().lower()
    if provider == "gemini" and genai_types:
        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_sanitize_schema(schema.model_json_schema()),
        )
        raw = await call_model_text(
            prompt,
            engine=selected,
            generation_config=config,
            image_urls=image_urls,
            call_type="json",
            call_purpose=call_purpose,
            function_name=function_name or "call_model_json",
            agent_name=agent_name,
            max_provider_attempts=max_provider_attempts,
            allow_provider_fallback=allow_provider_fallback,
            count_against_budget=count_against_budget,
        )
    else:
        schema_text = json.dumps(_sanitize_schema(schema.model_json_schema()), ensure_ascii=True)
        raw = await call_model_text(
            f"Return ONLY valid JSON matching this schema.\nSchema: {schema_text}\n\n{prompt}",
            engine=selected,
            generation_config={"response_format": "json_object"},
            image_urls=image_urls,
            call_type="json",
            call_purpose=call_purpose,
            function_name=function_name or "call_model_json",
            agent_name=agent_name,
            max_provider_attempts=max_provider_attempts,
            allow_provider_fallback=allow_provider_fallback,
            count_against_budget=count_against_budget,
        )
    return schema.model_validate(_extract_json_object(raw)).model_dump()


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
        return engines[0] if engines else None


async def _resolve_engine_for_request(db=None, company_id: str = "", use_pro: bool = False) -> dict:
    if db:
        try:
            from services.db_helpers import resolve_active_llm_engine

            engine = await resolve_active_llm_engine(db, company_id or "")
            logger.debug(
                "llm_engine_resolved company_id=%s provider=%s model=%s selected_id=%s",
                company_id or "<global>",
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
        candidate = dict(engine or {})
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

    selected = dict(engine or _default_engine(use_pro=use_pro))
    provider = (selected.get("provider") or "openai").strip().lower()
    started = time.perf_counter()
    try:
        if provider == "gemini" and genai_types:
            # Build a combined response schema for Gemini structured output
            combined_schema: dict[str, Any] = {
                "type": "object",
                "properties": {
                    key: _sanitize_schema(spec["schema"].model_json_schema())
                    for key, spec in tasks.items()
                },
                "required": list(tasks.keys()),
            }
            config = genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=combined_schema,
            )
            raw = await call_model_text(
                wrapper_prompt,
                engine=selected,
                generation_config=config,
                call_type="json_batch",
                call_purpose=call_purpose or ",".join(tasks.keys()),
                function_name=function_name or "call_model_json_batch",
                agent_name=agent_name,
                max_provider_attempts=max_provider_attempts,
                allow_provider_fallback=allow_provider_fallback,
            )
        else:
            raw = await call_model_text(
                f"Return ONLY valid JSON.\n\n{wrapper_prompt}",
                engine=selected,
                generation_config={"response_format": "json_object"},
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
    "engine_supports_vision",
    "get_active_llm_engine",
    "get_active_llm_engines",
    "get_provider_runtime_info",
    "validate_live_engine",
]
