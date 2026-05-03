import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.auth.dependencies import get_current_user
from shared.schemas.contracts import ProductDescriptionRequest
from shared.usage_guard import check_ai_usage_limit
from services.ai_service import router as ai_router_module


def _app():
    app = FastAPI()
    app.state.db = object()
    app.dependency_overrides[get_current_user] = lambda: {
        "company_id": "co_test",
        "sub": "user_test",
        "role": "admin",
    }
    app.dependency_overrides[check_ai_usage_limit] = lambda: None
    app.include_router(ai_router_module.router, prefix="/api")
    return app


def test_classify_route_accepts_text_body(monkeypatch):
    async def fake_classify(text, **_kwargs):
        assert text == "hello"
        return {"intent": "greeting", "confidence": 0.9, "entities": {}, "urgency": "low"}

    monkeypatch.setattr(ai_router_module, "classify_intent", fake_classify)

    response = TestClient(_app()).post("/api/ai/classify", json={"text": "hello"})

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["intent"], str)
    assert isinstance(payload["confidence"], float)
    assert isinstance(payload["entities"], dict)
    assert isinstance(payload["urgency"], str)


def test_classify_route_empty_text_returns_200(monkeypatch):
    async def fake_classify(text, **_kwargs):
        assert text == ""
        return {"intent": "general_question", "confidence": 0.0, "entities": {}, "urgency": "low"}

    monkeypatch.setattr(ai_router_module, "classify_intent", fake_classify)

    response = TestClient(_app()).post("/api/ai/classify", json={"text": ""})

    assert response.status_code == 200


def test_product_description_request_accepts_company_id():
    payload = ProductDescriptionRequest(name="Product", company_id="co_test")

    assert payload.company_id == "co_test"


def test_product_description_route_passes_company_id():
    source = inspect.getsource(ai_router_module.product_description_route)

    assert "company_id=company_id" in source


def test_product_description_route_uses_company_scoped_engine(monkeypatch):
    async def fake_engines(_db, company_id=""):
        assert company_id == "co_test"
        return [{"id": "llm_company", "company_id": "co_test", "is_selected": True}]

    async def fake_description(name, **kwargs):
        assert kwargs["company_id"] == "co_test"
        assert kwargs["engines"][0]["id"] == "llm_company"
        return f"{name} description"

    monkeypatch.setattr(ai_router_module, "get_active_llm_engines", fake_engines)
    monkeypatch.setattr(ai_router_module, "generate_product_description", fake_description)

    response = TestClient(_app()).post("/api/ai/product-description", json={"name": "Product"})

    assert response.status_code == 200
    assert response.json()["description"] == "Product description"
