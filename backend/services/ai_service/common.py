from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SentimentResult(BaseModel):
    score: float = Field(..., ge=-1.0, le=1.0)
    emotion: Literal["angry", "frustrated", "confused", "neutral", "satisfied", "happy", "excited"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    sentiment_label: Literal["positive", "neutral", "negative"] = "neutral"
    keywords: List[str] = Field(default_factory=list)
    emotion_breakdown: Dict[str, float] = Field(
        default_factory=lambda: {
            "joy": 0.0,
            "anger": 0.0,
            "sadness": 0.0,
            "fear": 0.0,
            "surprise": 0.0,
        }
    )


class IntentResult(BaseModel):
    intent: str = "general_question"
    confidence: float = Field(default=0.3, ge=0.0, le=1.0)
    entities: Dict[str, Any] = Field(default_factory=dict)
    urgency: Literal["low", "medium", "high", "critical"] = "medium"


class LeadScoreResult(BaseModel):
    score: int = Field(default=50, ge=0, le=100)
    grade: Literal["hot", "warm", "cold"] = "warm"
    reasoning: str = "Default fallback"
    next_action: str = "Follow up"
    phase: Literal["awareness", "interest", "consideration", "intent", "evaluation", "purchase"] = "awareness"


class MemoryUpdateResult(BaseModel):
    summary: str = "No memory yet."
    key_facts: List[str] = Field(default_factory=list)
    overall_sentiment: Literal["negative", "neutral", "positive", "mixed"] = "neutral"
    sentiment_reasoning: str = ""


class InteractionSummaryResult(BaseModel):
    summary: str = ""
    topics: List[str] = Field(default_factory=list)
    sentiment_label: Literal["positive", "negative", "neutral", "mixed"] = "neutral"
    key_questions: List[str] = Field(default_factory=list)
    products_discussed: List[str] = Field(default_factory=list)
    resolution_status: Literal["resolved", "unresolved", "escalated", "in_progress"] = "in_progress"


class DailySummaryResult(BaseModel):
    total_interactions: int = 0
    top_topics: List[str] = Field(default_factory=list)
    overall_sentiment: Literal["positive", "negative", "neutral", "mixed"] = "neutral"
    highlight_issues: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    summary_text: str = ""


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _sanitize_schema(node: Any) -> Any:
    if isinstance(node, dict):
        return {key: _sanitize_schema(val) for key, val in node.items() if key != "additionalProperties"}
    if isinstance(node, list):
        return [_sanitize_schema(item) for item in node]
    return node


def _extract_data_url_payload(data_url: str) -> Optional[tuple[str, bytes, str]]:
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        return None
    try:
        header, b64 = data_url.split(",", 1)
        mime = header.split(";")[0].replace("data:", "").strip() or "image/jpeg"
        if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            return None
        raw = base64.b64decode(b64)
        if len(raw) < 32 or len(raw) > 8 * 1024 * 1024:
            return None
        return mime, raw, b64
    except Exception:
        return None


try:
    import tiktoken as _tiktoken

    _TIKTOKEN_ENC = _tiktoken.get_encoding("cl100k_base")
except Exception:
    _TIKTOKEN_ENC = None


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    if _TIKTOKEN_ENC is not None:
        try:
            return len(_TIKTOKEN_ENC.encode(text))
        except Exception:
            pass
    return int(len(text.split()) * 1.3)


def truncate_text_for_tokens(text: str, max_tokens: int) -> str:
    if not text:
        return ""
    if _TIKTOKEN_ENC is not None:
        try:
            tokens = _TIKTOKEN_ENC.encode(text)
            if len(tokens) <= max_tokens:
                return text
            truncated = _TIKTOKEN_ENC.decode(tokens[:max_tokens])
            return truncated.rstrip()
        except Exception:
            pass
    if estimate_tokens(text) <= max_tokens:
        return text
    chunks = re.split(r"(?<=[.!?])\s+", text.strip())
    kept: list[str] = []
    running = 0
    for chunk in chunks:
        chunk_tokens = estimate_tokens(chunk)
        if running + chunk_tokens > max_tokens:
            break
        kept.append(chunk)
        running += chunk_tokens
    if kept:
        return " ".join(kept).strip()
    return " ".join(text.split()[: max(1, int(max_tokens / 1.3))]).strip()


def latest_customer_message(conversation_context: list[dict[str, Any]]) -> str:
    for message in reversed(conversation_context or []):
        if str(message.get("sender_type") or "").lower() == "customer":
            return str(message.get("content") or "").strip()
    return ""


def latest_ai_message(conversation_context: list[dict[str, Any]]) -> str:
    for message in reversed(conversation_context or []):
        if str(message.get("sender_type") or "").lower() == "ai":
            return str(message.get("content") or "").strip()
    return ""


def normalize_similarity_text(text: str) -> str:
    lowered = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()))
    return lowered.strip()


def text_similarity(a: str, b: str) -> float:
    left = normalize_similarity_text(a)
    right = normalize_similarity_text(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()



