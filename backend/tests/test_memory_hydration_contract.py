from __future__ import annotations

import asyncio
import time
from pathlib import Path

from memory_engine.manager import MemoryManager
from memory_engine.schemas import LongTermContext, SemanticContext, ShortTermContext
from memory_engine.short_term import ShortTermMemory


REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeCache:
    async def get_list(self, _key):
        return [{"content": f"msg-{i}"} for i in range(25)]

    async def get_json(self, key):
        if key.endswith(":session"):
            return {"stage": "active"}
        if key.endswith(":intent"):
            return {"intent": "order_intent"}
        if key.endswith(":last_ai"):
            return {"text": "last response"}
        if key.endswith(":conv_state"):
            return {"turn": 3}
        return {}


def test_short_term_memory_loads_expected_redis_fields() -> None:
    memory = ShortTermMemory.__new__(ShortTermMemory)
    memory._cache = _FakeCache()

    context = asyncio.run(memory.load("tenant-1", "user-1", conversation_id="conv-1"))

    assert len(context.recent_messages) == 20
    assert context.active_session == {"stage": "active"}
    assert context.pending_intent == {"intent": "order_intent"}
    assert context.last_ai_response == "last response"
    assert context.conversation_state == {"turn": 3}
    assert context.ttl_seconds == 1800


class _ParallelTier:
    def __init__(self, name, starts):
        self.name = name
        self.starts = starts

    async def load(self, *_args, **_kwargs):
        self.starts[self.name] = time.monotonic()
        await asyncio.sleep(0.02)
        if self.name == "short_term":
            return ShortTermContext(recent_messages=[{"content": "hello"}], pending_intent={"intent": "faq"})
        if self.name == "long_term":
            return LongTermContext(customer_profile={"id": "user-1"}, interaction_summaries=[{"summary": "recent"}])
        return SemanticContext(relevant_knowledge=[{"content": "kb", "similarity": 0.9}], knowledge_text="kb")


def test_memory_manager_loads_all_tiers_in_parallel_and_logs_tokens(caplog) -> None:
    starts = {}
    manager = MemoryManager(db=object())
    manager.short_term = _ParallelTier("short_term", starts)
    manager.long_term = _ParallelTier("long_term", starts)
    manager.semantic = _ParallelTier("semantic", starts)
    caplog.set_level("INFO", logger="memory_engine.manager")

    context = asyncio.run(
        manager.fetch_context(
            "user-1",
            "tenant-1",
            conversation_id="conv-1",
            current_query="what do you know?",
        )
    )

    assert context.short_term.pending_intent == {"intent": "faq"}
    assert context.long_term.customer_profile == {"id": "user-1"}
    assert context.semantic.knowledge_text == "kb"
    assert max(starts.values()) - min(starts.values()) < 0.015
    assert "memory_hydration load_time_ms=" in caplog.text
    assert "short_term_tokens=" in caplog.text
    assert "long_term_tokens=" in caplog.text
    assert "semantic_tokens=" in caplog.text
    assert "total_context_tokens=" in caplog.text


def test_pgvector_semantic_search_uses_cosine_top_k_query() -> None:
    source = (REPO_ROOT / "backend/services/ai_service/embedding_service.py").read_text(encoding="utf-8")

    assert "1 - (embedding <=> $1::vector) AS similarity" in source
    assert "ORDER BY embedding <=> $1::vector LIMIT" in source
    assert "top_k" in source
