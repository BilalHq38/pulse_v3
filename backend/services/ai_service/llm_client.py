from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from pydantic import BaseModel

from services.ai_service.common import _extract_data_url_payload, _sanitize_schema

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


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
FLASH_MODEL = "gemini-2.5-flash"
PRO_MODEL = "gemini-2.5-pro"
GEMINI_FALLBACK_MODELS = tuple(
    model.strip()
    for model in os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-2.5-flash-lite,gemini-2.5-flash",
    ).split(",")
    if model.strip()
)
EMBEDDING_MODEL = "models/text-embedding-004"

_gemini_client = None
if genai and GEMINI_API_KEY:
    try:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as exc:
        logger.warning("Gemini client init failed: %s", exc)

_openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if AsyncOpenAI and OPENAI_API_KEY else None
_anthropic_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY) if AsyncAnthropic and ANTHROPIC_API_KEY else None


def _default_engine(use_pro: bool = False) -> dict:
    return {
        "provider": "gemini",
        "model_name": PRO_MODEL if use_pro else FLASH_MODEL,
        "temperature": 0.65,
        "max_tokens": 2048,
        "supports_vision": True,
    }


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


async def _call_gemini(
    prompt: str,
    engine: dict,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> str:
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
            return (getattr(response, "text", "") or "").strip()
        except Exception as exc:
            errors.append(f"{model_name}:{exc.__class__.__name__}:{exc}")
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
) -> str:
    if not _openai_client:
        raise RuntimeError(get_provider_runtime_info("openai")[1])
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": url}} for url in (image_urls or []) if _extract_data_url_payload(url)
    ]
    temperature, max_tokens = _generation_opts(engine, generation_config)
    kwargs: dict[str, Any] = {
        "model": engine.get("model_name") or "gpt-4o-mini",
        "messages": [{"role": "user", "content": content}],
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if isinstance(generation_config, dict) and generation_config.get("response_format") == "json_object":
        kwargs["response_format"] = {"type": "json_object"}
    response = await _openai_client.chat.completions.create(**kwargs)
    return (response.choices[0].message.content or "").strip()


async def _call_anthropic(
    prompt: str,
    engine: dict,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> str:
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
        "model": engine.get("model_name") or "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens or 2048,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    response = await _anthropic_client.messages.create(**kwargs)
    return "\n".join(
        [getattr(block, "text", "") for block in getattr(response, "content", []) or [] if getattr(block, "text", "")]
    ).strip()


def _get_fallback_provider_order(primary_provider: str) -> list[str]:
    """Return provider fallback order, starting with the primary."""
    all_providers = ["gemini", "openai", "anthropic"]
    primary = (primary_provider or "gemini").strip().lower()
    ordered = [primary] + [p for p in all_providers if p != primary]
    return [p for p in ordered if get_provider_runtime_info(p)[0]]


async def call_model_text(
    prompt: str,
    engine: Optional[dict] = None,
    use_pro: bool = False,
    generation_config: Any = None,
    image_urls: Optional[list[str]] = None,
) -> str:
    selected = dict(engine or _default_engine(use_pro=use_pro))
    primary_provider = (selected.get("provider") or "gemini").strip().lower()
    provider_order = _get_fallback_provider_order(primary_provider)

    last_exc: Exception | None = None
    for provider in provider_order:
        try:
            if provider == "gemini":
                return await _call_gemini(
                    prompt,
                    selected,
                    generation_config=generation_config,
                    image_urls=image_urls,
                )
            if provider == "openai":
                return await _call_openai(
                    prompt,
                    selected,
                    generation_config=generation_config,
                    image_urls=image_urls,
                )
            if provider == "anthropic":
                return await _call_anthropic(
                    prompt,
                    selected,
                    generation_config=generation_config,
                    image_urls=image_urls,
                )
        except Exception as exc:
            if provider != primary_provider:
                logger.warning(
                    "AI provider fallback: %s failed, tried %s: %s",
                    primary_provider,
                    provider,
                    exc,
                )
            last_exc = exc
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


async def call_model_json(
    prompt: str,
    schema: type[BaseModel],
    engine: Optional[dict] = None,
    use_pro: bool = False,
    image_urls: Optional[list[str]] = None,
) -> dict[str, Any]:
    selected = dict(engine or _default_engine(use_pro=use_pro))
    provider = (selected.get("provider") or "gemini").strip().lower()
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
        )
    else:
        schema_text = json.dumps(_sanitize_schema(schema.model_json_schema()), ensure_ascii=True)
        raw = await call_model_text(
            f"Return ONLY valid JSON matching this schema.\nSchema: {schema_text}\n\n{prompt}",
            engine=selected,
            generation_config={"response_format": "json_object"},
            image_urls=image_urls,
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

            return await resolve_active_llm_engine(db, company_id or "")
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
) -> str:
    image_urls = [item for item in (image_parts or []) if isinstance(item, str) and item.startswith("data:")]
    return await call_model_text(
        prompt,
        engine=dict((engines or [None])[0] or _default_engine(use_pro=use_pro)),
        generation_config=generation_config,
        image_urls=image_urls,
    )


__all__ = [
    "EMBEDDING_MODEL",
    "FLASH_MODEL",
    "PRO_MODEL",
    "_default_engine",
    "_resolve_engine_for_request",
    "call_gemini",
    "call_gemini_json",
    "call_model_json",
    "call_model_text",
    "call_with_engines",
    "engine_supports_vision",
    "get_active_llm_engine",
    "get_active_llm_engines",
    "get_provider_runtime_info",
    "validate_live_engine",
]
