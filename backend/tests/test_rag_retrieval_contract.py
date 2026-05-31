from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.ai_service import embedding_service, rag
from services.conversation_engine import prompt_builder
from services.conversation_engine.llm_gateway import GenerationResult
from services.conversation_engine.orchestrator import Orchestrator
from services.conversation_engine.schemas import ContextChunk, TurnRequest
from shared.config import gemini_embedding_model_name


REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeCache:
    def __init__(self):
        self.values = {}
        self.get_keys = []
        self.set_keys = []

    async def get_json(self, key):
        self.get_keys.append(key)
        return self.values.get(key)

    async def set_json(self, key, value, ttl_seconds=None):
        self.values[key] = value
        self.set_keys.append(key)


@pytest.mark.asyncio
async def test_keyword_retriever_queries_company_products_with_string_containment(monkeypatch) -> None:
    queries = []

    class FakeDb:
        async def fetch(self, query, *args):
            queries.append((query, args))
            if "information_schema.columns" in query:
                return [
                    {"column_name": "id"},
                    {"column_name": "company_id"},
                    {"column_name": "name"},
                    {"column_name": "product_title"},
                    {"column_name": "description"},
                    {"column_name": "category"},
                    {"column_name": "product_type"},
                    {"column_name": "price"},
                    {"column_name": "price_currency"},
                    {"column_name": "status"},
                    {"column_name": "created_at"},
                    {"column_name": "updated_at"},
                ]
            assert "FROM company_products" in query
            assert "LIKE ANY($3::text[])" in query
            return [
                {
                    "id": "prod-1",
                    "company_id": "co-1",
                    "name": "Black Shirt",
                    "product_title": "",
                    "description": "Cotton shirt",
                    "category": "shirts",
                    "product_type": "standard",
                    "price": "25",
                    "price_currency": "USD",
                    "status": "active",
                    "created_at": "",
                    "updated_at": "",
                }
            ]

    monkeypatch.setattr(rag, "_COMPANY_PRODUCTS_COLUMNS", None)

    products = await rag._keyword_retrieve_products(FakeDb(), "co-1", "black shirt", limit=3)

    assert products and products[0]["id"] == "prod-1"
    product_query, args = queries[-1]
    assert "LOWER(COALESCE(name,'')) LIKE ANY($3::text[])" in product_query
    assert args[0] == "co-1"
    assert "%black%" in args[2]
    assert "%shirt%" in args[2]


def test_fuzzy_category_match_uses_synonyms_and_close_spelling() -> None:
    assert rag.fuzzy_category_match("show me tees", "shirts")
    assert rag.fuzzy_category_match("jewelery rings", "jewelry")
    assert rag.fuzzy_category_match("sneekers", "sneakers")


def test_fuse_combines_keyword_fuzzy_vector_and_lexical_scores() -> None:
    fused = rag._fuse_product_scores(
        keyword_scores={"p-key": 1.0},
        fuzzy_scores={"p-fuzzy": 0.9},
        vector_scores={"p-vector": 0.8},
        lexical_scores={"p-lex": 0.5},
    )

    assert set(fused) == {"p-key", "p-fuzzy", "p-vector", "p-lex"}
    assert fused["p-vector"] > fused["p-key"] > fused["p-fuzzy"] > fused["p-lex"]


@pytest.mark.asyncio
async def test_gemini_embedding_uses_text_embedding_004_cache_and_pgvector(monkeypatch) -> None:
    import services.ai_service.llm_client as llm_client

    monkeypatch.delenv("GEMINI_EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr(embedding_service, "get_provider_runtime_info", lambda provider: (provider == "gemini", {}))
    monkeypatch.setattr(embedding_service, "reserve_embedding_call", lambda **_kwargs: None)
    monkeypatch.setattr(embedding_service, "_EMBEDDING_PROVIDER_FALLBACK", False)
    embedding_service._UNSUPPORTED_MODEL_COOLDOWNS.clear()
    embedding_service._UNSUPPORTED_MODEL_LOGGED_UNTIL.clear()
    fake_cache = FakeCache()
    monkeypatch.setattr(embedding_service, "_EMBEDDING_CACHE", fake_cache)

    captured = {}

    class FakeModels:
        async def embed_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2, 0.3])])

    monkeypatch.setattr(llm_client, "_gemini_client", SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))
    monkeypatch.setattr(llm_client, "_openai_client", None)

    values = await embedding_service.generate_embedding("customer asked about black shirts", {"provider": "gemini"}, company_id="co-1")

    assert gemini_embedding_model_name() == "text-embedding-004"
    assert embedding_service._embedding_model_for_provider("gemini") == "text-embedding-004"
    assert captured["model"] == "text-embedding-004"
    assert values == [0.1, 0.2, 0.3]
    assert fake_cache.get_keys
    assert fake_cache.set_keys

    source = (REPO_ROOT / "backend/services/ai_service/embedding_service.py").read_text(encoding="utf-8")
    schema = (REPO_ROOT / "backend/sql_schema.sql").read_text(encoding="utf-8")
    assert "ORDER BY embedding <=> $1::vector LIMIT" in source
    assert "USING ivfflat (embedding vector_cosine_ops)" in schema


def test_prompt_format_outputs_clean_rag_context_blocks() -> None:
    chunks = [
        ContextChunk("company_data", "co", "Company", "Company name: Pulse"),
        ContextChunk("product", "prod", "Product", "Product: Black Shirt"),
        ContextChunk("faq", "faq", "Returns", "Q: Returns?\nA: 30 days"),
        ContextChunk("knowledge_base", "kb", "Install", "Installation guide"),
    ]

    prompt = prompt_builder.build_prompt(user_message="What do you sell?", chunks=chunks, history_turns=[])

    assert "<company_info>" in prompt and "</company_info>" in prompt
    assert "<product_catalog>" in prompt and "</product_catalog>" in prompt
    assert "<faqs>" in prompt and "</faqs>" in prompt
    assert "<knowledge_base>" in prompt and "</knowledge_base>" in prompt
    retrieved = prompt.split("<retrieved_context>", 1)[1].split("</retrieved_context>", 1)[0]
    assert "### Source:" not in retrieved


def test_rag_context_is_prompt_only_not_outbound_delivery() -> None:
    class FakeRetriever:
        def __init__(self, source_type, chunks):
            self.source_type = source_type
            self._chunks = chunks

        async def fetch(self, db, *, company_id, query, top_k):
            return list(self._chunks)

    class FakeGateway:
        def __init__(self):
            self.prompt = ""

        async def generate(self, prompt, *, engine=None):
            self.prompt = prompt
            return GenerationResult(text="Here is the customer-facing answer.", attempts=1)

    class FakeDb:
        async def fetch(self, *_args):
            return []

        async def fetchrow(self, sql, *args):
            if "MAX(turn_index)" in sql:
                return {"max_idx": 0}
            return None

        async def execute(self, *_args):
            return None

    gateway = FakeGateway()
    orch = Orchestrator(
        retrievers={
            "company_data": FakeRetriever("company_data", [ContextChunk("company_data", "co", "Company", "SECRET INTERNAL CONTEXT")]),
            "product": FakeRetriever("product", [ContextChunk("product", "p1", "Product", "Product: Black Shirt", relevance_score=0.9)]),
            "faq": FakeRetriever("faq", []),
            "knowledge_base": FakeRetriever("knowledge_base", []),
        },
        gateway=gateway,
    )

    result = asyncio.run(
        orch.run_turn(
            FakeDb(),
            TurnRequest(session_id="s-1", company_id="co-1", user_message="Tell me about your company"),
        )
    )

    assert "SECRET INTERNAL CONTEXT" in gateway.prompt
    assert "SECRET INTERNAL CONTEXT" not in result.answer
