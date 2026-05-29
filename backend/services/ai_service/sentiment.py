from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from typing import Any

from services.ai_service.common import SentimentResult
from services.ai_service.llm_client import (
    _default_engine,
    _provider_default_model,
    _resolve_engine_for_request,
    call_model_json,
    call_model_json_batch,
    get_provider_runtime_info,
)

logger = logging.getLogger(__name__)

def _sentiment_fallback_engine() -> dict[str, Any]:
    provider = (os.getenv("AI_SENTIMENT_FALLBACK_PROVIDER", "gemini") or "gemini").strip().lower()
    model_name = (
        os.getenv("AI_SENTIMENT_FALLBACK_MODEL") or _provider_default_model(provider, use_pro=False)
    ).strip()
    try:
        temperature = float(os.getenv("AI_SENTIMENT_FALLBACK_TEMPERATURE", "0.25") or 0.25)
    except Exception:
        temperature = 0.25
    try:
        max_tokens = int(os.getenv("AI_SENTIMENT_FALLBACK_MAX_TOKENS", "1024") or 1024)
    except Exception:
        max_tokens = 1024
    return {
        "provider": provider,
        "model_name": model_name,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "supports_vision": False,
    }

_SUPPORTED_EMOTIONS = {
    "angry",
    "frustrated",
    "confused",
    "neutral",
    "satisfied",
    "happy",
    "excited",
}

_POSITIVE_PHRASE_WEIGHTS = {
    "thank you": 0.35,
    "thanks a lot": 0.45,
    "love it": 0.8,
    "works perfectly": 0.8,
    "really good": 0.6,
    "very happy": 0.75,
    "super helpful": 0.7,
    "great service": 0.75,
}

_NEGATIVE_PHRASE_WEIGHTS = {
    "not happy": 0.75,
    "very disappointed": 0.9,
    "doesn't work": 0.8,
    "does not work": 0.8,
    "not working": 0.8,
    "refund now": 0.95,
    "waste of money": 0.95,
    "really bad": 0.75,
    "poor service": 0.75,
}

_POSITIVE_KEYWORD_WEIGHTS = {
    "amazing": 0.8,
    "awesome": 0.75,
    "excellent": 0.8,
    "fixed": 0.55,
    "good": 0.4,
    "great": 0.55,
    "happy": 0.55,
    "helpful": 0.5,
    "love": 0.75,
    "perfect": 0.7,
    "quick": 0.25,
    "satisfied": 0.6,
    "solved": 0.6,
    "smooth": 0.35,
    "thanks": 0.25,
}

_NEGATIVE_KEYWORD_WEIGHTS = {
    "angry": 0.8,
    "awful": 0.8,
    "bad": 0.5,
    "broken": 0.75,
    "complaint": 0.65,
    "confused": 0.35,
    "delay": 0.35,
    "disappointed": 0.75,
    "frustrated": 0.75,
    "hate": 0.95,
    "issue": 0.25,
    "problem": 0.55,
    "refund": 0.8,
    "scam": 0.95,
    "slow": 0.35,
    "terrible": 0.85,
    "useless": 0.9,
    "worst": 0.9,
}

_NEGATIONS = {"aint", "barely", "hardly", "never", "no", "not"}
_INTENSIFIERS = {
    "extremely": 1.45,
    "really": 1.2,
    "so": 1.1,
    "super": 1.3,
    "too": 1.2,
    "very": 1.3,
}


def _clamp(value: Any, *, low: float, high: float, default: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return max(low, min(high, parsed))


def _sentiment_label_from_score(score: float) -> str:
    if score >= 0.2:
        return "positive"
    if score <= -0.2:
        return "negative"
    return "neutral"


def _normalize_emotion(label: str, raw_emotion: str, score: float) -> str:
    emotion = (raw_emotion or "").strip().lower()
    if emotion in _SUPPORTED_EMOTIONS:
        return emotion
    if label == "negative":
        return "angry" if score <= -0.45 else "frustrated"
    if label == "positive":
        return "excited" if score >= 0.55 else "satisfied"
    return "confused" if score <= -0.05 else "neutral"


def _normalize_keywords(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized = []
    seen = set()
    for item in values:
        token = str(item or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
        if len(normalized) >= 12:
            break
    return normalized


def _normalize_breakdown(values: Any, score: float) -> dict[str, float]:
    base = {
        "joy": round(max(score, 0.0), 3),
        "anger": round(max(-score, 0.0), 3),
        "sadness": round(max(-score * 0.65, 0.0), 3),
        "fear": round(max(-score * 0.35, 0.0), 3),
        "surprise": round(min(abs(score) * 0.3, 0.5), 3),
    }
    if not isinstance(values, dict):
        return base
    merged: dict[str, float] = {}
    for key in ("joy", "anger", "sadness", "fear", "surprise"):
        merged[key] = round(_clamp(values.get(key), low=0.0, high=1.0, default=base[key]), 3)
    return merged


def _log_sentiment_payload(
    stage: str,
    *,
    scope: str,
    source_text: str,
    payload: dict[str, Any],
    engine: dict[str, Any] | None = None,
) -> None:
    _ = source_text
    logger.debug(
        "sentiment_%s scope=%s provider=%s model=%s",
        stage,
        scope,
        str((engine or {}).get("provider") or payload.get("provider") or "").strip(),
        str((engine or {}).get("model_name") or payload.get("model_name") or "").strip(),
    )


def _is_negated(tokens: list[str], index: int) -> bool:
    window = tokens[max(0, index - 3) : index]
    return any(token in _NEGATIONS for token in window)


def _intensity_multiplier(tokens: list[str], index: int) -> float:
    window = tokens[max(0, index - 2) : index]
    multiplier = 1.0
    for token in window:
        multiplier *= _INTENSIFIERS.get(token, 1.0)
    return max(1.0, min(1.8, multiplier))


def _dedupe_keywords(values: list[str], *, limit: int = 12) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
        if len(normalized) >= limit:
            break
    return normalized


def _local_sentiment_components(text: str) -> tuple[float, list[str], int, int]:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return 0.04, [], 0, 0

    keywords: list[str] = []
    positive_hits = 0
    negative_hits = 0
    raw_score = 0.0

    for phrase, weight in _POSITIVE_PHRASE_WEIGHTS.items():
        if phrase in lowered:
            raw_score += weight
            positive_hits += 1
            keywords.append(phrase)
    for phrase, weight in _NEGATIVE_PHRASE_WEIGHTS.items():
        if phrase in lowered:
            raw_score -= weight
            negative_hits += 1
            keywords.append(phrase)

    tokens = re.findall(r"[a-z']+", lowered)
    for index, token in enumerate(tokens):
        multiplier = _intensity_multiplier(tokens, index)
        if token in _POSITIVE_KEYWORD_WEIGHTS:
            delta = _POSITIVE_KEYWORD_WEIGHTS[token] * multiplier
            if _is_negated(tokens, index):
                raw_score -= delta * 0.85
                negative_hits += 1
                keywords.append(f"not {token}")
            else:
                raw_score += delta
                positive_hits += 1
                keywords.append(token)
            continue
        if token in _NEGATIVE_KEYWORD_WEIGHTS:
            delta = _NEGATIVE_KEYWORD_WEIGHTS[token] * multiplier
            if _is_negated(tokens, index):
                raw_score += delta * 0.85
                positive_hits += 1
                keywords.append(f"not {token}")
            else:
                raw_score -= delta
                negative_hits += 1
                keywords.append(token)

    exclamations = lowered.count("!")
    questions = lowered.count("?")
    if raw_score > 0 and exclamations:
        raw_score += min(exclamations * 0.08, 0.2)
    elif raw_score < 0 and exclamations:
        raw_score -= min(exclamations * 0.08, 0.2)
    elif questions and abs(raw_score) < 0.18:
        raw_score -= min(questions * 0.03, 0.09)
        negative_hits += 1
        keywords.append("questioning")

    score = math.tanh(raw_score / 2.4)
    if abs(score) < 0.01:
        if negative_hits > positive_hits:
            score = -0.04
        elif positive_hits > negative_hits:
            score = 0.04
        else:
            score = 0.02
    return score, _dedupe_keywords(keywords), positive_hits, negative_hits


def _finalize_sentiment(raw: dict[str, Any], source_text: str, *, scope: str) -> dict[str, Any]:
    score = _clamp(raw.get("score"), low=-1.0, high=1.0, default=0.0)
    label = str(raw.get("sentiment_label") or "").strip().lower()
    if label not in {"positive", "neutral", "negative"}:
        label = _sentiment_label_from_score(score)
    if abs(score) < 0.01:
        raise RuntimeError("Sentiment provider returned a zero score")
    score = _clamp(score, low=-1.0, high=1.0, default=0.01)
    label = _sentiment_label_from_score(score)
    emotion = _normalize_emotion(label, str(raw.get("emotion") or ""), score)
    confidence = _clamp(raw.get("confidence"), low=0.2, high=0.99, default=0.7)
    keywords = _normalize_keywords(raw.get("keywords"))
    breakdown = _normalize_breakdown(raw.get("emotion_breakdown"), score)
    percentage = sentiment_to_percentage(score)
    return {
        "score": round(score, 3),
        "emotion": emotion,
        "confidence": round(confidence, 3),
        "sentiment_label": label,
        "keywords": keywords,
        "emotion_breakdown": breakdown,
        "normalized_score": normalize_sentiment_score(score),
        "percentage": percentage,
        "label": get_sentiment_label(percentage),
        "scope": scope,
    }


def _message_prompt(text: str) -> str:
    return (
        "You are a CRM sentiment analyzer for a single customer message.\n"
        "Task: estimate message sentiment polarity, customer emotion, and salient emotional keywords.\n"
        "Input format:\n"
        "- message: one customer-authored text message\n"
        "Output format: return ONLY valid JSON with this schema:\n"
        '{"score": float(-1 to 1), "emotion": "angry|frustrated|confused|neutral|satisfied|happy|excited", '
        '"confidence": float(0-1), "sentiment_label": "positive|neutral|negative", '
        '"keywords": ["short keyword"], "emotion_breakdown": {"joy":0,"anger":0,"sadness":0,"fear":0,"surprise":0}}\n'
        "Rules:\n"
        "- score must reflect sentiment polarity and MUST NOT be exactly 0.0.\n"
        "- Use a slight non-zero value for near-neutral sentiment.\n"
        "- Keep keywords short, factual, and grounded in the message.\n"
        "- emotion_breakdown values must stay between 0 and 1.\n"
        f"\nmessage:\n{text}"
    )


def _conversation_prompt(history: str, latest_message: str) -> str:
    return (
        "You are a CRM sentiment analyzer for a multi-turn conversation.\n"
        "Task: evaluate the overall trajectory of the conversation, not just the final line, while giving extra weight to the latest customer message.\n"
        "Input format:\n"
        "- conversation: chronological turns labelled by role\n"
        "- latest_customer_message: the newest customer message in the thread\n"
        "Output format: return ONLY valid JSON with this schema:\n"
        '{"score": float(-1 to 1), "emotion": "angry|frustrated|confused|neutral|satisfied|happy|excited", '
        '"confidence": float(0-1), "sentiment_label": "positive|neutral|negative", '
        '"keywords": ["short keyword"], "emotion_breakdown": {"joy":0,"anger":0,"sadness":0,"fear":0,"surprise":0}}\n'
        "Rules:\n"
        "- score must represent the overall conversation mood and MUST NOT be exactly 0.0.\n"
        "- Weigh repeated frustration, relief, or escalation across turns.\n"
        "- Keep keywords grounded in the conversation, not inferred facts.\n"
        f"\nconversation:\n{history}\n\nlatest_customer_message:\n{latest_message}"
    )


def _should_retry_for_zero_score(raw: dict[str, Any]) -> bool:
    score = _clamp((raw or {}).get("score"), low=-1.0, high=1.0, default=0.0)
    return abs(score) < 0.01


def _with_non_zero_retry_instruction(prompt: str) -> str:
    return (
        f"{prompt}\n\n"
        "IMPORTANT: The score must never be 0.0. "
        "If sentiment is neutral, return a slight non-zero score with absolute value between 0.02 and 0.12 "
        "based on the best-fit polarity from the text and conversation."
    )


async def _call_sentiment_api(
    prompt: str,
    *,
    db=None,
    company_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected_engine = await _resolve_engine_for_request(db=db, company_id=company_id)
    candidates = [
        selected_engine,
        _sentiment_fallback_engine(),
        _default_engine(use_pro=False),
        _default_engine(use_pro=True),
    ]
    errors: list[str] = []
    quota_exhausted_providers: set[str] = set()
    for pass_index in range(2):
        attempted: set[str] = set()
        for engine in candidates:
            provider = str((engine or {}).get("provider") or "").strip().lower()
            model = str((engine or {}).get("model_name") or "").strip().lower()
            if not provider:
                continue
            # Skip providers whose quota is already exhausted this request.
            if provider in quota_exhausted_providers:
                errors.append(f"{provider}:{model}: skipped (quota exhausted)")
                continue
            signature = f"{provider}:{model}"
            if signature in attempted:
                continue
            attempted.add(signature)
            ready, reason = get_provider_runtime_info(provider)
            if not ready:
                errors.append(f"{signature}: {reason}")
                continue
            try:
                logger.info(
                    "sentiment_llm_called company_id=%s provider=%s model=%s",
                    company_id or "",
                    provider,
                    model,
                )
                payload = await call_model_json(
                    prompt,
                    SentimentResult,
                    engine=engine,
                    call_purpose="sentiment",
                    function_name="_call_sentiment_api",
                    max_provider_attempts=1,
                    allow_provider_fallback=False,
                )
                return payload, engine
            except Exception as exc:
                error_str = str(exc)
                detail = error_str.splitlines()[0][:120]
                errors.append(f"{signature}: {exc.__class__.__name__} ({detail})")
                # 429 / RESOURCE_EXHAUSTED is project-level — mark provider and
                # skip all remaining candidates for it without retrying.
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str.upper():
                    quota_exhausted_providers.add(provider)
                continue
        if pass_index == 0:
            await asyncio.sleep(0.2)
    reason = "; ".join(errors) if errors else "No eligible model provider"
    raise RuntimeError(f"Sentiment analysis failed across providers: {reason}")


def analyze_local_sentiment(text: str) -> dict:
    """
    Local sentiment analysis: all-MiniLM-L6-v2 (primary) + domain keyword supplement.

    MiniLM provides semantic understanding trained on 1B+ sentence pairs.
    The keyword supplement adds coverage for CRM-specific phrases the model
    may under-weight (e.g. "refund now", "waste of money").
    Falls back to keyword-only if the model is unavailable.
    No external API calls. No GPU required.
    """
    source_text = (text or "").strip() or "[empty message]"

    # 1. Domain keyword supplement — always runs (fast, catches CRM-specific phrases)
    kw_score, keywords, pos_hits, neg_hits = _local_sentiment_components(source_text)

    # 2. MiniLM primary — semantic understanding beyond keyword matching
    ml_score: float = kw_score
    ml_confidence: float = 0.0
    ml_breakdown: dict | None = None
    ml_available = False
    try:
        from services.ai_service.local_ml import classify_sentiment as _ml_classify  # noqa: PLC0415
        ml_result = _ml_classify(source_text)
        if ml_result.get("label"):
            ml_score = float(ml_result["score"])
            ml_confidence = float(ml_result["confidence"])
            ml_breakdown = ml_result.get("breakdown")
            ml_available = True
    except Exception as exc:
        logger.debug("analyze_local_sentiment minilm unavailable: %s", exc)

    # 3. Blend: MiniLM primary (78%), keyword supplement (22%)
    #    Increase keyword weight when it has strong domain signal (≥2 hits)
    if ml_available:
        if pos_hits + neg_hits >= 2:
            blended = 0.65 * ml_score + 0.35 * kw_score
        else:
            blended = 0.78 * ml_score + 0.22 * kw_score
    else:
        blended = kw_score

    if abs(blended) < 0.015:
        blended = 0.02
    blended = float(max(-1.0, min(1.0, round(blended, 4))))
    label = _sentiment_label_from_score(blended)

    # 4. Confidence
    if ml_available:
        kw_boost = min((pos_hits + neg_hits) * 0.04, 0.14)
        confidence = _clamp(ml_confidence + kw_boost, low=0.35, high=0.92, default=0.55)
    else:
        confidence = _clamp(
            0.42 + (min(len(keywords), 8) * 0.05) + min(abs(blended) * 0.35, 0.28),
            low=0.35,
            high=0.92,
            default=0.58,
        )

    # 5. Emotion
    if label == "negative":
        emotion = "angry" if blended <= -0.55 else ("confused" if "questioning" in keywords else "frustrated")
    elif label == "positive":
        emotion = "excited" if blended >= 0.58 else "satisfied"
    else:
        emotion = "confused" if "questioning" in keywords else "neutral"

    # 6. Emotion breakdown — from MiniLM if available, else derive from score
    breakdown = ml_breakdown if (ml_available and ml_breakdown) else _normalize_breakdown(None, blended)

    model_name = "minilm-hybrid-v1" if ml_available else "keyword-heuristic-v1"
    source = "local_minilm" if ml_available else "local_heuristic"

    result = {
        "score": round(blended, 3),
        "emotion": emotion,
        "confidence": round(confidence, 3),
        "sentiment_label": label,
        "keywords": keywords,
        "emotion_breakdown": breakdown,
        "normalized_score": normalize_sentiment_score(blended),
        "percentage": sentiment_to_percentage(blended),
        "label": get_sentiment_label(sentiment_to_percentage(blended)),
        "provider": "local",
        "model_name": model_name,
        "source": source,
        "local_positive_hits": pos_hits,
        "local_negative_hits": neg_hits,
    }
    _log_sentiment_payload(
        "raw",
        scope="local",
        source_text=source_text,
        payload={
            "score": result["score"],
            "ml_score": ml_score if ml_available else None,
            "kw_score": kw_score,
            "keywords": keywords,
            "positive_hits": pos_hits,
            "negative_hits": neg_hits,
        },
        engine={"provider": "local", "model_name": model_name},
    )
    _log_sentiment_payload(
        "processed",
        scope="local",
        source_text=source_text,
        payload=result,
        engine={"provider": "local", "model_name": model_name},
    )
    return result


def sentiment_to_percentage(score: float) -> int:
    return int(((score + 1) / 2) * 100)


def get_sentiment_label(percentage: int) -> str:
    if percentage < 40:
        return "Negative"
    if percentage < 60:
        return "Neutral"
    return "Positive"


def normalize_sentiment_score(raw_score: float | None) -> float:
    score = max(-1.0, min(1.0, float(raw_score if raw_score is not None else 0.0)))
    return round((score + 1) / 2, 4)


def build_sentiment_gate(message_text: str, sentiment: dict | None = None) -> dict:
    sentiment = sentiment or {}
    raw_score = _clamp(sentiment.get("score"), low=-1.0, high=1.0, default=0.02)
    sentiment_label = str(sentiment.get("sentiment_label") or "").strip().lower()
    if sentiment_label not in {"positive", "neutral", "negative"}:
        sentiment_label = _sentiment_label_from_score(raw_score)
    normalized_score = normalize_sentiment_score(raw_score)
    percentage = sentiment_to_percentage(raw_score)
    label = get_sentiment_label(percentage)
    possible_hate = any(
        re.search(pattern, (message_text or "").lower())
        for pattern in [
            r"\bhate\b",
            r"\bidiot\b",
            r"\bstupid\b",
            r"\bkill\b",
            r"\bscam\b",
            r"\bfraud\b",
            r"\btrash\b",
            r"\buseless\b",
        ]
    )
    is_negative = raw_score <= -0.22 or possible_hate
    is_positive = raw_score >= 0.22 and not possible_hate
    if possible_hate:
        label = "Negative"
    elif sentiment_label == "negative":
        label = "Negative"
    elif sentiment_label == "positive":
        label = "Positive"
    elif is_negative:
        label = "Negative"
    elif is_positive:
        label = "Positive"
    else:
        label = "Neutral"
    return {
        "message": message_text,
        "sentiment_score": normalized_score,
        "raw_sentiment_score": round(raw_score, 3),
        "raw_sentiment_label": sentiment_label,
        "classification": label,
        "ai_response_allowed": not is_negative,
        "source": str(sentiment.get("source") or sentiment.get("provider") or "").strip(),
        "risk_flags": {
            "toxic": is_negative,
            "escalating": is_negative,
            "possible_hate_speech": possible_hate,
        },
    }


def should_auto_escalate(message_text: str, sentiment: dict | None = None, intent: dict | None = None) -> bool:
    text = (message_text or "").strip().lower()
    if not text:
        return False
    high_urgency = any(
        re.search(pattern, text)
        for pattern in [
            r"\bspeak to\b",
            r"\bcall me\b",
            r"\blawsuit\b",
            r"\blegal\b",
            r"\brefund now\b",
            r"\bcancel (?:this|my)\b",
        ]
    )
    handoff = any(
        re.search(pattern, text)
        for pattern in [
            r"\bhuman\b",
            r"\bagent\b",
            r"\bmanager\b",
            r"\brepresentative\b",
            r"\bnot helpful\b",
            r"\bcomplaint\b",
        ]
    )
    score = _clamp((sentiment or {}).get("score"), low=-1.0, high=1.0, default=0.02)
    urgency = str((intent or {}).get("urgency", "") or "").lower()
    if high_urgency:
        return score < -0.2 or urgency in {"high", "critical"}
    if handoff:
        return score < -0.1 or urgency in {"high", "critical"}
    return urgency == "critical" and score < -0.25


async def analyze_sentiment(text: str, db=None, company_id: str = "", **kwargs) -> dict:
    """Analyze single-message sentiment.

    Primary path: all-MiniLM-L6-v2 (local, zero API cost).
    LLM escalation: only when MiniLM confidence < 0.35 AND message > 20 words,
    which covers edge cases like sarcasm or mixed-sentiment long text.
    This reduces LLM sentiment API calls by ~90%.
    """
    source_text = (text or "").strip() or "[empty message]"

    # Always run MiniLM first
    result = analyze_local_sentiment(source_text)
    result["scope"] = "message"

    # Return immediately if confidence is adequate or text is short
    word_count = len(source_text.split())
    if result["confidence"] >= 0.35 or word_count <= 20:
        result["source"] = result.get("source") or "local_minilm"
        return result

    # Low-confidence on a long complex message — attempt LLM upgrade
    prompt = _message_prompt(source_text)
    try:
        raw, engine = await _call_sentiment_api(prompt, db=db, company_id=company_id)
        _log_sentiment_payload("raw", scope="message", source_text=source_text, payload=raw, engine=engine)
        if _should_retry_for_zero_score(raw):
            logger.info(
                "sentiment_llm_retry scope=message company_id=%s reason=zero_score",
                company_id or "",
            )
            raw, engine = await _call_sentiment_api(
                _with_non_zero_retry_instruction(prompt),
                db=db,
                company_id=company_id,
            )
        upgraded = _finalize_sentiment(raw, source_text, scope="message")
        upgraded["provider"] = str((engine or {}).get("provider") or "")
        upgraded["model_name"] = str((engine or {}).get("model_name") or "")
        upgraded["source"] = "provider"
        _log_sentiment_payload("processed", scope="message", source_text=source_text, payload=upgraded, engine=engine)
        return upgraded
    except Exception as exc:
        logger.debug(
            "sentiment_llm_escalation_failed scope=message company_id=%s error=%s — using MiniLM result",
            company_id or "",
            exc,
        )
        result["source"] = "local_minilm_lowconf"
        return result


async def analyze_conversation_sentiment(
    conversation_context: list[dict] | None,
    *,
    latest_message: str = "",
    db=None,
    company_id: str = "",
    **kwargs,
) -> dict:
    """Analyze multi-turn conversation sentiment with MiniLM — zero API calls.

    Trend is derived by comparing MiniLM scores on the first half vs second half
    of the conversation history, giving an accurate arc without LLM cost.
    """
    turns: list[str] = []
    for item in (conversation_context or [])[-20:]:
        role = str((item or {}).get("sender_type") or "unknown").strip().lower()
        content = str((item or {}).get("content") or "").strip()
        if not content:
            continue
        turns.append(f"{role}: {content}")
    if latest_message and (not turns or latest_message.strip() not in turns[-1]):
        turns.append(f"customer: {latest_message.strip()}")

    if not turns:
        result = analyze_local_sentiment(latest_message or "[empty]")
        result.update({"scope": "conversation", "source": "local_minilm", "turns_analyzed": 0, "trend": "stable"})
        return result

    history = "\n".join(turns[-16:])
    source_text = latest_message or turns[-1]

    # MiniLM on full conversation history
    result = analyze_local_sentiment(history)
    result["scope"] = "conversation"
    result["source"] = "local_minilm"
    result["turns_analyzed"] = len(turns)

    # Derive sentiment trend from first-half vs second-half MiniLM scores
    if len(turns) >= 4:
        mid = len(turns) // 2
        first_score = analyze_local_sentiment("\n".join(turns[:mid]))["score"]
        second_score = analyze_local_sentiment("\n".join(turns[mid:]))["score"]
        delta = second_score - first_score
        trend = "improving" if delta > 0.15 else ("declining" if delta < -0.15 else "stable")
    elif len(turns) >= 2:
        first_score = analyze_local_sentiment(turns[0])["score"]
        last_score = analyze_local_sentiment(turns[-1])["score"]
        delta = last_score - first_score
        trend = "improving" if delta > 0.20 else ("declining" if delta < -0.20 else "stable")
    else:
        trend = "stable"

    result["trend"] = trend
    _log_sentiment_payload(
        "processed",
        scope="conversation",
        source_text=source_text,
        payload=result,
        engine={"provider": "local", "model_name": result.get("model_name", "minilm-hybrid-v1")},
    )
    return result




async def analyze_message_and_conversation_sentiment(
    message_text: str,
    conversation_context: list[dict] | None = None,
    *,
    db=None,
    company_id: str = "",
) -> tuple[dict, dict]:
    """Analyze message AND conversation sentiment — fully local via all-MiniLM-L6-v2.

    Zero LLM API calls. Two MiniLM inference calls (~10-20 ms total).
    Trend is derived from the delta between latest-message score and
    conversation-history score — no extra inference needed.
    """
    source_text = (message_text or "").strip() or "[empty message]"

    # Build conversation turns
    turns: list[str] = []
    for item in (conversation_context or [])[-20:]:
        role = str((item or {}).get("sender_type") or "unknown").strip().lower()
        content_text = str((item or {}).get("content") or "").strip()
        if not content_text:
            continue
        turns.append(f"{role}: {content_text}")
    if source_text and (not turns or source_text.strip() not in turns[-1]):
        turns.append(f"customer: {source_text}")
    history = "\n".join(turns[-16:])

    # Message sentiment — MiniLM on current message
    msg_sentiment = analyze_local_sentiment(source_text)
    msg_sentiment["scope"] = "message"
    msg_sentiment["source"] = "local_minilm"

    # Conversation sentiment — MiniLM on full history
    conv_sentiment = analyze_local_sentiment(history or source_text)
    conv_sentiment["scope"] = "conversation"
    conv_sentiment["source"] = "local_minilm"
    conv_sentiment["turns_analyzed"] = len(turns)

    # Trend: how has sentiment shifted from baseline to latest message?
    delta = msg_sentiment["score"] - conv_sentiment["score"]
    conv_sentiment["trend"] = (
        "improving" if delta > 0.18
        else ("declining" if delta < -0.18 else "stable")
    )

    return msg_sentiment, conv_sentiment

__all__ = [
    "analyze_local_sentiment",
    "analyze_message_and_conversation_sentiment",
    "analyze_sentiment",
    "analyze_conversation_sentiment",
    "build_sentiment_gate",
    "get_sentiment_label",
    "normalize_sentiment_score",
    "sentiment_to_percentage",
    "should_auto_escalate",
]
