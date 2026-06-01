"""Local-only sentiment analysis for the conversation engine.

No LLM calls. No external API. All analysis is done in-process using
all-MiniLM-L6-v2 (via local_ml) with a keyword-heuristic fallback.

Public API used by the orchestrator:
  analyze_local_sentiment(text)           -> dict
  analyze_conversation_sentiment(ctx, ...) -> dict
  build_sentiment_gate(text, sentiment)   -> dict
  should_auto_escalate(text, sentiment)   -> bool
  sentiment_to_percentage(score)          -> int
  get_sentiment_label(pct)               -> str
  normalize_sentiment_score(score)       -> float
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any

logger = logging.getLogger(__name__)

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

    # 1. Domain keyword supplement â€” always runs (fast, catches CRM-specific phrases)
    kw_score, keywords, pos_hits, neg_hits = _local_sentiment_components(source_text)

    # 2. MiniLM primary â€” semantic understanding beyond keyword matching
    ml_score: float = kw_score
    ml_confidence: float = 0.0
    ml_breakdown: dict | None = None
    ml_available = False
    try:
        from services.conversation_engine.local_ml import classify_sentiment as _ml_classify  # noqa: PLC0415
        ml_result = _ml_classify(source_text)
        if ml_result.get("model_available", True) and ml_result.get("label"):
            ml_score = float(ml_result["score"])
            ml_confidence = float(ml_result["confidence"])
            ml_breakdown = ml_result.get("breakdown")
            ml_available = True
    except Exception as exc:
        logger.debug("analyze_local_sentiment minilm unavailable: %s", exc)

    # 3. Blend: MiniLM primary (78%), keyword supplement (22%)
    #    Increase keyword weight when it has strong domain signal (â‰¥2 hits)
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

    # 6. Emotion breakdown â€” from MiniLM if available, else derive from score
    breakdown = ml_breakdown if (ml_available and ml_breakdown) else _normalize_breakdown(None, blended)

    model_name = "sentence-transformers/all-MiniLM-L6-v2+crm-keyword" if ml_available else "crm-keyword-heuristic-v1"
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
        "external_api_called": False,
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
    path_taken = "human_escalation" if is_negative else "fast_path"
    escalation_required = bool(is_negative)
    logger.info(
        "sentiment_gate sentiment_score=%s path_taken=%s escalation=%s classification=%s source=%s",
        round(raw_score, 3),
        path_taken,
        str(escalation_required).lower(),
        label,
        str(sentiment.get("source") or sentiment.get("provider") or "").strip(),
    )
    return {
        "message": message_text,
        "sentiment_score": normalized_score,
        "raw_sentiment_score": round(raw_score, 3),
        "raw_sentiment_label": sentiment_label,
        "classification": label,
        "ai_response_allowed": not is_negative,
        "path_taken": path_taken,
        "escalation_required": escalation_required,
        "recommended_action": "escalate_to_human" if escalation_required else "continue_pipeline",
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

def analyze_conversation_sentiment(
    conversation_context: list[dict] | None,
    *,
    latest_message: str = "",
    db=None,
    company_id: str = "",
    **kwargs,
) -> dict:
    """Analyze multi-turn conversation sentiment with MiniLM â€” zero API calls.

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

