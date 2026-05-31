from __future__ import annotations

import asyncio

from services.ai_service import local_ml
from services.ai_service import sentiment as sentiment_mod
from services.ai_service.facade import analyze_sentiment as facade_analyze_sentiment


def _fake_minilm(label: str = "neutral", score: float = 0.02, confidence: float = 0.72):
    def classify(_text: str) -> dict:
        return {
            "label": label,
            "score": score,
            "confidence": confidence,
            "emotion": "neutral",
            "breakdown": {"joy": max(score, 0.0), "anger": max(-score, 0.0)},
        }

    return classify


def test_sentiment_uses_local_minilm_without_provider_call(monkeypatch) -> None:
    async def fail_provider_call(*_args, **_kwargs):
        raise AssertionError("sentiment must not call provider APIs")

    monkeypatch.setattr(local_ml, "classify_sentiment", _fake_minilm("positive", 0.64, 0.81))
    monkeypatch.setattr(sentiment_mod, "_call_sentiment_api", fail_provider_call)

    result = asyncio.run(sentiment_mod.analyze_sentiment("Excellent service, this works perfectly.", company_id="co1"))

    assert -1.0 <= result["score"] <= 1.0
    assert result["provider"] == "local"
    assert result["source"] == "local_minilm"
    assert result["external_api_called"] is False
    assert "all-MiniLM-L6-v2" in result["model_name"]


def test_crm_keyword_heuristics_weight_the_local_score(monkeypatch) -> None:
    monkeypatch.setattr(local_ml, "classify_sentiment", _fake_minilm("neutral", 0.02, 0.75))

    result = sentiment_mod.analyze_local_sentiment("Refund now. This is a waste of money and the product is broken.")

    assert result["score"] < -0.2
    assert result["sentiment_label"] == "negative"
    assert result["local_negative_hits"] >= 2
    assert "refund now" in result["keywords"]


def test_sentiment_gate_logs_path_and_escalation(caplog) -> None:
    caplog.set_level("INFO", logger="services.ai_service.sentiment")

    negative_gate = sentiment_mod.build_sentiment_gate(
        "Refund now, this is terrible.",
        {"score": -0.72, "sentiment_label": "negative", "source": "local_minilm"},
    )
    positive_gate = sentiment_mod.build_sentiment_gate(
        "Thanks, this is great.",
        {"score": 0.55, "sentiment_label": "positive", "source": "local_minilm"},
    )

    assert negative_gate["path_taken"] == "human_escalation"
    assert negative_gate["escalation_required"] is True
    assert negative_gate["ai_response_allowed"] is False
    assert positive_gate["path_taken"] == "fast_path"
    assert positive_gate["escalation_required"] is False
    assert "sentiment_gate sentiment_score=" in caplog.text
    assert "path_taken=human_escalation" in caplog.text
    assert "escalation=true" in caplog.text


def test_facade_sentiment_is_local_only(monkeypatch) -> None:
    monkeypatch.setattr(local_ml, "classify_sentiment", _fake_minilm("negative", -0.5, 0.8))

    result = asyncio.run(facade_analyze_sentiment("This is broken and I want a refund.", company_id="co1"))

    assert result["provider"] == "local"
    assert result["external_api_called"] is False
    assert result["score"] < 0
