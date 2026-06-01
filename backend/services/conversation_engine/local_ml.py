"""
local_ml.py — all-MiniLM-L6-v2 inference engine for Pulse Engine.

Provides four zero-shot classifiers backed by cosine similarity against
pre-computed anchor embeddings:
  - classify_sentiment   → positive / neutral / negative
  - classify_intent      → 8 CRM intent categories
  - classify_buying_signal → high_intent / interested / price_sensitive / cold
  - classify_lead_quality  → hot / warm / cold

Model: sentence-transformers/all-MiniLM-L6-v2  (ONNX via fastembed)
  Size   : ~80 MB (downloaded once, baked into image at build time)
  Latency: 5–15 ms on CPU per embedding
  GPU    : not required
"""
from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Anchor phrase sets
# Each label is represented by several semantically diverse example phrases.
# The model embeds them all; we store their mean (unit-normalised) vector.
# ---------------------------------------------------------------------------

_SENTIMENT_ANCHORS: dict[str, list[str]] = {
    "positive": [
        "I love this product, it works perfectly",
        "excellent service, very happy with my purchase",
        "amazing quality, great value for money",
        "thank you so much, highly satisfied with everything",
        "this is fantastic, it exceeded my expectations",
        "super helpful team, everything arrived perfectly on time",
        "great experience overall, will definitely buy again",
        "works exactly as described, very impressed",
    ],
    "negative": [
        "terrible experience, completely disappointed with this",
        "this product is broken and does not work at all",
        "I want a refund immediately, total waste of money",
        "worst service ever, I am very angry about this",
        "disgusting quality, I will never buy from here again",
        "my order never arrived and nobody is helping me",
        "I am very unhappy, this is completely unacceptable",
        "absolute failure, demanding a full refund right now",
    ],
    "neutral": [
        "I have a question about my order",
        "when will my package arrive at my address",
        "I need more information about this product",
        "can you tell me the price of this item please",
        "okay I received it thank you",
        "please send me the tracking number for my order",
        "I am browsing your products right now",
    ],
}

_INTENT_ANCHORS: dict[str, list[str]] = {
    "buying_intent": [
        "I want to place an order right now",
        "how can I buy this product today",
        "I am ready to purchase, what are the payment options",
        "add this to my cart, I want to checkout",
        "I would like to order two of these immediately",
        "let me buy this item, how do I proceed",
    ],
    "product_inquiry": [
        "tell me more about this product please",
        "what are the features and specifications of this item",
        "do you have this in different colors or sizes",
        "what is included in the package when I buy this",
        "can you show me similar products or alternatives",
        "I am looking for a product recommendation",
    ],
    "complaint": [
        "I am very unhappy with my recent purchase",
        "this product is defective and completely broken",
        "your service is terrible and I want to file a complaint",
        "this is not what I ordered at all, wrong item",
        "I have been waiting too long and nobody is helping me",
    ],
    "refund_request": [
        "I want a full refund for my order please",
        "please return my money back to me immediately",
        "I need to return this product and receive a refund",
        "process my refund payment right now",
        "cancel my order and give me my money back",
    ],
    "order_tracking": [
        "where is my order right now, please track it",
        "what is the current status of my delivery",
        "can you track my shipment and give me an update",
        "when will my package finally arrive at my door",
        "I have not received my order yet, it is late",
    ],
    "pricing_question": [
        "how much does this product cost please",
        "what is the exact price of this item",
        "do you have any discounts or special promotions available",
        "is there a cheaper alternative or option available",
        "what is your best price for a bulk order",
    ],
    "human_handoff": [
        "let me speak to a real human agent please",
        "I want to talk to a real person not a chatbot",
        "connect me to a manager or senior representative",
        "I need human assistance urgently right now",
        "transfer me to your customer support team immediately",
    ],
    "general_question": [
        "I have a general question for you",
        "can you help me with something please",
        "I need some information from you",
        "hello I am looking for help with something",
        "what are your business hours and location",
    ],
}

_BUYING_SIGNAL_ANCHORS: dict[str, list[str]] = {
    "high_intent": [
        "I want to buy this right now, how do I pay",
        "I am ready to place an order immediately",
        "we have the budget approved and want to proceed today",
        "let us close this deal today, send me the invoice",
        "we are ready to sign the contract and get started",
        "I will take two units please, process my payment now",
    ],
    "interested": [
        "this looks very interesting and relevant for our needs",
        "I would like to learn more details before making a decision",
        "can you send me a detailed proposal or quote please",
        "we are currently evaluating this solution for our company",
        "this might be a good fit for us, please tell me more",
    ],
    "price_sensitive": [
        "can you give me a discount on this product",
        "what is the minimum order quantity for a better price",
        "is there a cheaper alternative that does the same thing",
        "we have a very limited budget for this project",
        "do you offer any flexible payment plans or installments",
    ],
    "cold": [
        "just browsing around, not really interested right now",
        "maybe later, I need to think about it more",
        "we are not ready to make any purchasing decisions yet",
        "I was just looking, thank you for your time",
        "not sure if we even need this product at all",
    ],
}

_LEAD_QUALITY_ANCHORS: dict[str, list[str]] = {
    "hot": [
        "we have the budget approved and need this solution urgently",
        "I am the decision maker and I want to buy today",
        "our team has evaluated and we are ready to start immediately",
        "let us schedule a demo and then close the deal this week",
        "we need this for our company urgently, how do we proceed",
        "send me the contract now, I am ready to sign it today",
    ],
    "warm": [
        "we are seriously evaluating this as a potential solution",
        "I need to present this option to my management team first",
        "this looks promising, can we schedule a discovery call",
        "I would like a custom proposal tailored to our company",
        "we have an upcoming project where this could be a good fit",
    ],
    "cold": [
        "just gathering general information for future reference",
        "we are definitely not looking to purchase anything right now",
        "I signed up out of curiosity, we have no specific need",
        "browsing available options but no budget has been allocated",
        "this might be useful someday but certainly not right now",
    ],
}

# ---------------------------------------------------------------------------
# Singleton model + pre-computed anchor embeddings
# ---------------------------------------------------------------------------

_model: Any = None
_model_lock = threading.Lock()
_anchor_embeddings: dict[str, dict[str, np.ndarray]] = {}
_anchors_ready = False
_init_lock = threading.Lock()


def _get_model() -> Any:
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from fastembed import TextEmbedding  # noqa: PLC0415
            _model = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
            logger.info("local_ml model_loaded model=all-MiniLM-L6-v2")
    return _model


def _embed_batch(texts: list[str]) -> list[np.ndarray]:
    return list(_get_model().embed(texts))


def _embed(text: str) -> np.ndarray:
    return _embed_batch([text])[0]


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a)) * float(np.linalg.norm(b))
    if denom < 1e-10:
        return 0.0
    return float(np.dot(a, b) / denom)


def _build_anchor_embeddings() -> None:
    """Pre-compute unit-normalised mean embedding for every anchor label."""
    global _anchor_embeddings, _anchors_ready
    all_anchors: dict[str, dict[str, list[str]]] = {
        "sentiment": _SENTIMENT_ANCHORS,
        "intent": _INTENT_ANCHORS,
        "buying_signal": _BUYING_SIGNAL_ANCHORS,
        "lead_quality": _LEAD_QUALITY_ANCHORS,
    }
    for classifier, anchor_map in all_anchors.items():
        _anchor_embeddings[classifier] = {}
        for label, phrases in anchor_map.items():
            embs = _embed_batch(phrases)
            mean_emb = np.mean(np.stack(embs), axis=0)
            norm = float(np.linalg.norm(mean_emb))
            _anchor_embeddings[classifier][label] = mean_emb / (norm + 1e-10)
    _anchors_ready = True
    logger.info("local_ml anchors_ready classifiers=%s", list(all_anchors.keys()))


def _ensure_ready() -> bool:
    """Load model + build anchors on first call. Thread-safe. Returns True on success."""
    global _anchors_ready
    if _anchors_ready:
        return True
    with _init_lock:
        if _anchors_ready:
            return True
        try:
            _get_model()
            _build_anchor_embeddings()
            return True
        except Exception as exc:
            logger.warning("local_ml init_failed error=%s", exc)
            return False


def _classify(text: str, classifier: str) -> dict:
    """Embed text; return best label by cosine similarity to anchor embeddings."""
    if not _ensure_ready():
        return {"label": None, "confidence": 0.0, "scores": {}}
    emb = _embed(text)
    norm = float(np.linalg.norm(emb))
    emb_unit = emb / (norm + 1e-10)
    scores: dict[str, float] = {
        label: _cosine_sim(emb_unit, anchor)
        for label, anchor in _anchor_embeddings[classifier].items()
    }
    best = max(scores, key=scores.__getitem__)
    vals = list(scores.values())
    spread = max(vals) - min(vals)
    confidence = round(min(spread / 0.25, 1.0), 3) if spread > 0 else 0.5
    return {"label": best, "confidence": confidence, "scores": scores}


# ---------------------------------------------------------------------------
# Public classifier APIs — every function falls back gracefully on error
# ---------------------------------------------------------------------------

def classify_sentiment(text: str) -> dict:
    """
    Classify sentiment with all-MiniLM-L6-v2.

    Returns:
        label      : "positive" | "neutral" | "negative"
        score      : float [-1, 1]
        confidence : float [0, 1]
        emotion    : angry | frustrated | confused | neutral | satisfied | excited
        breakdown  : {joy, anger, sadness, fear, surprise}
    """
    try:
        result = _classify(text, "sentiment")
        label: str = result["label"] or "neutral"
        scores = result["scores"]
        confidence = result["confidence"]

        pos = scores.get("positive", 0.0)
        neg = scores.get("negative", 0.0)
        neu = scores.get("neutral", 0.0)
        raw = pos - neg
        if neu > pos and neu > neg:
            raw *= 0.45
        score = float(max(-1.0, min(1.0, raw)))
        if abs(score) < 0.015:
            score = 0.02

        if label == "negative":
            emotion = "angry" if score <= -0.55 else "frustrated"
        elif label == "positive":
            emotion = "excited" if score >= 0.55 else "satisfied"
        else:
            emotion = "neutral"

        breakdown = {
            "joy": round(max(0.0, pos), 3),
            "anger": round(max(0.0, neg * 0.65), 3),
            "sadness": round(max(0.0, neg * 0.35), 3),
            "fear": round(max(0.0, min(neg * 0.2, 0.3)), 3),
            "surprise": 0.0,
        }
        return {
            "label": label,
            "score": round(score, 3),
            "confidence": confidence,
            "emotion": emotion,
            "breakdown": breakdown,
        }
    except Exception as exc:
        logger.debug("classify_sentiment failed: %s", exc)
        return {"label": "neutral", "score": 0.02, "confidence": 0.0, "emotion": "neutral", "breakdown": {}}


def classify_intent(text: str) -> dict:
    """
    Classify CRM intent with all-MiniLM-L6-v2.

    Returns:
        intent    : one of 8 CRM intent labels
        confidence: float [0, 1]
        urgency   : low | medium | high
        source    : "local_minilm"
    """
    try:
        result = _classify(text, "intent")
        label: str = result["label"] or "general_question"
        confidence = result["confidence"]
        urgency_map = {
            "complaint": "high",
            "refund_request": "high",
            "human_handoff": "high",
            "buying_intent": "medium",
            "order_tracking": "medium",
            "pricing_question": "medium",
            "product_inquiry": "low",
            "general_question": "low",
        }
        return {
            "intent": label,
            "confidence": confidence,
            "urgency": urgency_map.get(label, "low"),
            "source": "local_minilm",
        }
    except Exception as exc:
        logger.debug("classify_intent failed: %s", exc)
        return {"intent": "general_question", "confidence": 0.0, "urgency": "low", "source": "local_minilm"}


def classify_buying_signal(text: str) -> dict:
    """
    Detect buying signals in text with all-MiniLM-L6-v2.

    Returns:
        signal      : high_intent | interested | price_sensitive | cold
        confidence  : float [0, 1]
        buying_score: float [0, 1]  (0 = cold, 1 = ready to buy)
    """
    try:
        result = _classify(text, "buying_signal")
        label: str = result["label"] or "cold"
        scores = result["scores"]
        confidence = result["confidence"]
        high = scores.get("high_intent", 0.0)
        interested = scores.get("interested", 0.0)
        cold = scores.get("cold", 0.0)
        raw = (high * 1.0 + interested * 0.6) - (cold * 0.8)
        buying_score = round(max(0.0, min(1.0, (raw + 1.0) / 2.0)), 3)
        return {"signal": label, "confidence": confidence, "buying_score": buying_score}
    except Exception as exc:
        logger.debug("classify_buying_signal failed: %s", exc)
        return {"signal": "cold", "confidence": 0.0, "buying_score": 0.3}


def classify_lead_quality(text: str) -> dict:
    """
    Score lead quality from conversation text with all-MiniLM-L6-v2.

    Returns:
        quality      : hot | warm | cold
        confidence   : float [0, 1]
        quality_score: float [0, 1]  (0 = cold, 1 = hot)
    """
    try:
        result = _classify(text, "lead_quality")
        label: str = result["label"] or "cold"
        confidence = result["confidence"]
        base = {"hot": 0.9, "warm": 0.55, "cold": 0.1}.get(label, 0.3)
        quality_score = round(base * confidence + base * (1 - confidence) * 0.5, 3)
        return {"quality": label, "confidence": confidence, "quality_score": quality_score}
    except Exception as exc:
        logger.debug("classify_lead_quality failed: %s", exc)
        return {"quality": "cold", "confidence": 0.0, "quality_score": 0.1}
