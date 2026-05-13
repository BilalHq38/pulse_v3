"""Compact AI model catalog and capability helpers."""

from __future__ import annotations

from typing import Any


CAPABILITY_LABELS = {
    "supports_text": "Text",
    "supports_vision": "Vision",
    "supports_audio": "Audio",
    "supports_tts": "TTS",
    "supports_embeddings": "Embedding",
    "supports_image_generation": "Image Generation",
    "supports_video_generation": "Video Generation",
    "supports_tools": "Tools",
}

MODEL_CATEGORIES = {
    "text_generation": "Text generation",
    "vision": "Vision / image understanding",
    "audio_understanding": "Audio understanding",
    "text_to_speech": "Text-to-speech",
    "embeddings": "Embeddings",
}

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_PRO_MODEL = "gemini-2.5-pro"

_GEMINI_TEXT_MODELS = [
    ("gemini-2.5-flash", "Gemini 2.5 Flash (Default)", True),
    ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", True),
    ("gemini-2.5-pro", "Gemini 2.5 Pro", True),
]

_GEMINI_VISION_MODEL_NAMES = {
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
}

_GEMINI_AUDIO_MODEL_NAMES = {
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
}

_GEMINI_EMBEDDING_MODEL_NAMES = {"gemini-embedding-1", "gemini-embedding-2"}


def _caps(**overrides: bool) -> dict[str, bool]:
    base = {key: False for key in CAPABILITY_LABELS}
    base.update(overrides)
    return base


def _catalog_item(
    provider: str,
    model_name: str,
    label: str,
    category: str,
    *,
    recommended: bool = False,
    availability: str = "available",
    **capabilities: bool,
) -> dict[str, Any]:
    caps = _caps(**capabilities)
    return {
        "provider": provider,
        "model_name": model_name,
        "label": label,
        "category": category,
        "category_label": MODEL_CATEGORIES.get(category, category.replace("_", " ").title()),
        "recommended": bool(recommended),
        "availability": availability,
        **caps,
        "capabilities": [label for key, label in CAPABILITY_LABELS.items() if caps.get(key)],
    }


def supported_model_catalog(provider: str = "") -> list[dict[str, Any]]:
    provider = str(provider or "").strip().lower()
    items: list[dict[str, Any]] = [
        _catalog_item("openai", "gpt-4o", "GPT-4o", "text_generation", recommended=True, supports_text=True, supports_vision=True),
        _catalog_item("openai", "gpt-4o-mini", "GPT-4o mini", "text_generation", recommended=True, supports_text=True, supports_vision=True),
        _catalog_item("openai", "gpt-4-turbo", "GPT-4 Turbo", "text_generation", supports_text=True, supports_vision=True),
        _catalog_item("anthropic", "claude-3-7-sonnet-20250219", "Claude 3.7 Sonnet", "text_generation", recommended=True, supports_text=True, supports_vision=True),
        _catalog_item("anthropic", "claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet", "text_generation", recommended=True, supports_text=True, supports_vision=True),
        _catalog_item("anthropic", "claude-3-5-haiku-20241022", "Claude 3.5 Haiku", "text_generation", supports_text=True, supports_vision=True),
    ]
    for model_name, label, recommended in _GEMINI_TEXT_MODELS:
        items.append(
            _catalog_item(
                "gemini",
                model_name,
                label,
                "text_generation",
                recommended=recommended,
                supports_text=True,
                supports_vision=model_name in _GEMINI_VISION_MODEL_NAMES,
                supports_audio=model_name in _GEMINI_AUDIO_MODEL_NAMES,
            )
        )
    if provider:
        return [item for item in items if item["provider"] == provider]
    return items


def supported_model_names(provider: str = "") -> set[str]:
    provider_key = str(provider or "").strip().lower()
    return {str(item["model_name"]) for item in supported_model_catalog(provider_key)}


def is_supported_model(provider: str, model_name: str) -> bool:
    provider_key = str(provider or "").strip().lower()
    model_key = str(model_name or "").strip()
    if not provider_key or not model_key:
        return False
    return model_key in supported_model_names(provider_key)


def validate_model_selection(provider: str, model_name: str) -> None:
    provider_key = str(provider or "").strip().lower()
    model_key = str(model_name or "").strip()
    if provider_key == "gemini" and not is_supported_model(provider_key, model_key):
        raise ValueError(f"Unsupported Gemini model: {model_key}. Please choose a supported Gemini model.")


def model_capabilities(provider: str, model_name: str) -> dict[str, bool]:
    provider_key = str(provider or "").strip().lower()
    model_key = str(model_name or "").strip().lower()
    matches = [
        item for item in supported_model_catalog(provider_key)
        if item["provider"] == provider_key and item["model_name"].lower() == model_key
    ]
    if matches:
        merged = _caps()
        for item in matches:
            for key in CAPABILITY_LABELS:
                merged[key] = bool(merged[key] or item.get(key))
        return merged
    if provider_key == "gemini":
        if model_key.startswith("gemma-"):
            return _caps(supports_text=True)
        if model_key in _GEMINI_EMBEDDING_MODEL_NAMES or "embedding" in model_key:
            return _caps(supports_embeddings=True)
        if "tts" in model_key:
            return _caps(supports_tts=True)
        if model_key.startswith("gemini-"):
            return _caps(
                supports_text=True,
                supports_vision=model_key in _GEMINI_VISION_MODEL_NAMES,
                supports_audio=model_key in _GEMINI_AUDIO_MODEL_NAMES,
            )
    if provider_key == "openai":
        return _caps(supports_text=True, supports_vision=model_key.startswith("gpt-4"))
    if provider_key == "anthropic":
        return _caps(supports_text=True, supports_vision=model_key.startswith("claude-3"))
    return _caps()


def model_supports(provider: str, model_name: str, capability: str) -> bool:
    key = capability if capability.startswith("supports_") else f"supports_{capability}"
    return bool(model_capabilities(provider, model_name).get(key, False))


def capability_labels(provider: str, model_name: str) -> list[str]:
    caps = model_capabilities(provider, model_name)
    return [label for key, label in CAPABILITY_LABELS.items() if caps.get(key)]
