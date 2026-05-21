from types import SimpleNamespace

import pytest

from memory_engine.semantic import SemanticMemory
from memory_engine.short_term import ShortTermMemory
from services.ai_service import embedding_service, memory_service
from services.ai_service.rag import _should_skip_rag_query
from shared.config import normalize_gemini_embedding_model_name


class FakeCache:
    def __init__(self):
        self.values = {}
        self.set_keys = []

    async def get_json(self, key):
        return self.values.get(key)

    async def set_json(self, key, value, ttl_seconds=None):
        self.values[key] = value
        self.set_keys.append(key)

    async def delete(self, key):
        self.values.pop(key, None)


class FakeGeminiModels:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    async def embed_content(self, **_kwargs):
        self.calls += 1
        raise self.exc


class FakeOpenAIEmbeddings:
    def __init__(self):
        self.calls = 0

    async def create(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])])


def _install_embedding_fakes(monkeypatch, *, ready_providers):
    import services.ai_service.llm_client as llm_client

    fake_cache = FakeCache()
    monkeypatch.setattr(embedding_service, "_EMBEDDING_CACHE", fake_cache)
    monkeypatch.setattr(embedding_service, "reserve_embedding_call", lambda **_kwargs: None)
    monkeypatch.setattr(
        embedding_service,
        "get_provider_runtime_info",
        lambda provider: (provider in ready_providers, {}),
    )
    embedding_service._UNSUPPORTED_MODEL_COOLDOWNS.clear()
    embedding_service._UNSUPPORTED_MODEL_LOGGED_UNTIL.clear()
    return llm_client, fake_cache


def test_gemini_embedding_model_name_is_normalized_for_sdk():
    assert normalize_gemini_embedding_model_name("models/text-embedding-004") == "text-embedding-004"
    assert normalize_gemini_embedding_model_name("gemini-embedding-exp-03-07") == "gemini-embedding-exp-03-07"


def test_embedding_cache_key_is_company_scoped():
    assert embedding_service._embedding_cache_key("same text", company_id="co_a") != embedding_service._embedding_cache_key(
        "same text",
        company_id="co_b",
    )


def test_rag_skips_yes_no_acknowledgement_even_with_history():
    assert _should_skip_rag_query("yes", intent_name="follow_up_continue", has_history=True) == (
        True,
        "low_value_message",
    )
    assert _should_skip_rag_query("show me more", intent_name="follow_up_continue", has_history=True) == (False, "")


@pytest.mark.asyncio
async def test_unsupported_gemini_embedding_model_enters_cooldown(monkeypatch):
    llm_client, _ = _install_embedding_fakes(monkeypatch, ready_providers={"gemini"})
    fake_models = FakeGeminiModels(
        RuntimeError("404 NOT_FOUND: model not found for API version v1beta or not supported for embedContent")
    )
    monkeypatch.setattr(llm_client, "_gemini_client", SimpleNamespace(aio=SimpleNamespace(models=fake_models)))
    monkeypatch.setattr(llm_client, "_openai_client", None)
    monkeypatch.setattr(embedding_service, "_EMBEDDING_PROVIDER_FALLBACK", False)

    first = await embedding_service.generate_embedding("customer asked about a product catalog", {"provider": "gemini"})
    second = await embedding_service.generate_embedding("customer asked about a product catalog", {"provider": "gemini"})

    assert first is None
    assert second is None
    assert fake_models.calls == 1


@pytest.mark.asyncio
async def test_embedding_falls_back_to_openai_after_gemini_unsupported(monkeypatch):
    llm_client, _ = _install_embedding_fakes(monkeypatch, ready_providers={"gemini", "openai"})
    fake_models = FakeGeminiModels(
        RuntimeError("404 NOT_FOUND: model not found for API version v1beta or not supported for embedContent")
    )
    fake_openai_embeddings = FakeOpenAIEmbeddings()
    monkeypatch.setattr(llm_client, "_gemini_client", SimpleNamespace(aio=SimpleNamespace(models=fake_models)))
    monkeypatch.setattr(llm_client, "_openai_client", SimpleNamespace(embeddings=fake_openai_embeddings))
    monkeypatch.setattr(embedding_service, "_EMBEDDING_PROVIDER_FALLBACK", True)

    values = await embedding_service.generate_embedding("customer asked about a product catalog", {"provider": "gemini"})

    assert values == [0.1, 0.2, 0.3]
    assert fake_models.calls == 1
    assert fake_openai_embeddings.calls == 1


@pytest.mark.asyncio
async def test_short_term_memory_keys_include_customer_and_conversation_scope():
    memory = ShortTermMemory()
    memory._cache = FakeCache()

    await memory.store_last_ai_response(
        "company-1",
        "customer-a",
        "Customer A likes Product A.",
        conversation_id="conversation-a",
    )
    await memory.store_last_ai_response(
        "company-1",
        "customer-b",
        "Customer B likes Product B.",
        conversation_id="conversation-b",
    )

    customer_b = await memory.load("company-1", "customer-b", conversation_id="conversation-b")
    cross_scope = await memory.load("company-1", "customer-a", conversation_id="conversation-b")

    assert "Product B" in customer_b.last_ai_response
    assert "Product A" not in customer_b.last_ai_response
    assert cross_scope.last_ai_response == ""


@pytest.mark.asyncio
async def test_conversation_memory_requires_conversation_scope():
    class FailingDb:
        async def fetchrow(self, *_args, **_kwargs):
            raise AssertionError("DB should not be queried without conversation scope")

    result = await memory_service.get_last_ai_response_context(
        FailingDb(),
        "company-1",
        "customer-1",
        convo_id="",
    )

    assert result == {}


@pytest.mark.asyncio
async def test_semantic_interaction_search_is_scoped_to_current_customer(monkeypatch):
    captured = {}

    async def fake_search(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(embedding_service, "search_similar_embeddings", fake_search)

    semantic = SemanticMemory()
    await semantic._search_interactions(
        object(),
        "company-1",
        "show me a similar product",
        user_id="customer-1",
        conversation_id="conversation-1",
    )

    assert captured["source_type"] == "interaction"
    assert captured["source_ids"] == ["conversation-1", "customer-1"]


@pytest.mark.asyncio
async def test_search_similar_embeddings_query_filters_by_company(monkeypatch):
    captured_queries = []

    class FakeDb:
        async def fetch(self, query, *args):
            captured_queries.append((query, args))
            return []

    async def fake_embedding(*_args, **_kwargs):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(embedding_service, "_should_skip_embedding_search", lambda _text: False)
    monkeypatch.setattr(embedding_service, "generate_embedding", fake_embedding)

    await embedding_service.search_similar_embeddings(FakeDb(), "co_a", "find product", top_k=3)

    assert captured_queries
    assert "WHERE company_id=$2" in captured_queries[0][0]
    assert captured_queries[0][1][1] == "co_a"
