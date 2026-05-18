from services.email_campaign_service import service as campaign_service


def test_campaign_ai_engine_applies_output_token_floor_without_mutating_source():
    engine = {"provider": "gemini", "model_name": "gemini-test", "max_tokens": 24}

    tuned = campaign_service._campaign_ai_engine(engine, min_output_tokens=1200)

    assert tuned["max_tokens"] == 1200
    assert engine["max_tokens"] == 24


def test_campaign_ai_engine_caps_unusually_large_output_limit():
    tuned = campaign_service._campaign_ai_engine(
        {"provider": "gemini", "model_name": "gemini-test", "max_tokens": 9999},
        min_output_tokens=1200,
    )

    assert tuned["max_tokens"] == campaign_service.CAMPAIGN_AI_OUTPUT_TOKEN_CEILING
