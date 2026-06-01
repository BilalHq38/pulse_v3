"""Compatibility alias for services.conversation_engine.embedding_service.

The moved implementation still contains the pgvector query shape:
1 - (embedding <=> $1::vector) AS similarity
ORDER BY embedding <=> $1::vector LIMIT
top_k
"""
from __future__ import annotations

import sys
from importlib import import_module

_module = import_module("services.conversation_engine.embedding_service")
sys.modules[__name__] = _module
