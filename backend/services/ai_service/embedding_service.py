"""Backward-compatibility shim. Logic lives in services.conversation_engine.embedding_service."""
from services.conversation_engine.embedding_service import *  # noqa: F401, F403
from services.conversation_engine.embedding_service import (
    generate_embedding,
    store_embedding,
    search_similar_embeddings,
    batch_store_embeddings,
    index_knowledge_base,
)
