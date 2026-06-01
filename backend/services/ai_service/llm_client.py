"""Backward-compatibility shim. All logic lives in services.ai_runtime.llm_client."""
from services.ai_runtime.llm_client import *  # noqa: F401, F403
from services.ai_runtime.llm_client import (  # explicit re-exports used by other modules
    GEMINI_EMBEDDING_MODEL,
    OPENAI_EMBEDDING_MODEL,
    call_gemini,
    call_gemini_json,
    call_model_json,
    call_model_json_batch,
    call_model_text,
    call_with_engines,
    engine_supports_vision,
    get_active_llm_engine,
    get_active_llm_engines,
    get_provider_runtime_info,
    validate_live_engine,
    _default_engine,
    _engine_for_provider,
    _gemini_client,
    _gemini_client_for_model,
    _openai_client,
    _provider_default_model,
    _resolve_engine_for_request,
)
