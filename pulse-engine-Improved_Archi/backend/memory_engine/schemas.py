"""
memory_engine/schemas.py — Data models for the unified memory system.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    """Categories of memory stored per customer/conversation."""

    CUSTOMER_PROFILE = "customer_profile"
    LAST_AI_RESPONSE = "last_ai_response"
    CONVERSATION_STATE = "conversation_state"
    SHOWN_PRODUCTS = "shown_products"
    INTERACTION_SUMMARY = "interaction_summary"
    PREFERENCE = "preference"
    INTENT_HISTORY = "intent_history"
    SENTIMENT_TREND = "sentiment_trend"


class ShortTermContext(BaseModel):
    """Recent conversation context from Redis."""

    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    active_session: dict[str, Any] = Field(default_factory=dict)
    pending_intent: dict[str, Any] = Field(default_factory=dict)
    last_ai_response: str = ""
    conversation_state: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = 300  # 5 minutes


class LongTermContext(BaseModel):
    """Persistent customer memory from PostgreSQL."""

    customer_profile: dict[str, Any] = Field(default_factory=dict)
    long_term_summary: str = ""
    historical_sentiment: str = ""
    interaction_summaries: list[dict[str, Any]] = Field(default_factory=list)
    preferences: dict[str, str] = Field(default_factory=dict)
    intent_history: list[str] = Field(default_factory=list)
    sentiment_trend: list[dict[str, Any]] = Field(default_factory=list)
    shown_product_ids: list[str] = Field(default_factory=list)
    lifetime_value: float = 0.0
    total_conversations: int = 0


class SemanticContext(BaseModel):
    """Semantically relevant context from pgvector."""

    relevant_knowledge: list[dict[str, Any]] = Field(default_factory=list)
    similar_interactions: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_text: str = ""


class MemoryContext(BaseModel):
    """
    Complete memory context combining all three tiers.
    This is what MemoryManager.fetch_context() returns.
    """

    user_id: str = ""
    tenant_id: str = ""
    conversation_id: str = ""
    short_term: ShortTermContext = Field(default_factory=ShortTermContext)
    long_term: LongTermContext = Field(default_factory=LongTermContext)
    semantic: SemanticContext = Field(default_factory=SemanticContext)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PromptContext(BaseModel):
    """
    Token-budgeted context ready for AI prompt injection.
    This is what ContextBuilder.build_prompt_context() returns.
    """

    system_context: str = ""
    conversation_history: str = ""
    customer_summary: str = ""
    knowledge_context: str = ""
    recent_products: str = ""
    conversation_state_summary: str = ""
    total_estimated_tokens: int = 0

    def to_prompt_string(self) -> str:
        """Assemble all context sections into a single prompt string."""
        sections: list[str] = []
        if self.system_context:
            sections.append(f"[System Context]\n{self.system_context}")
        if self.customer_summary:
            sections.append(f"[Customer Profile]\n{self.customer_summary}")
        if self.conversation_state_summary:
            sections.append(f"[Conversation State]\n{self.conversation_state_summary}")
        if self.knowledge_context:
            sections.append(f"[Knowledge Base]\n{self.knowledge_context}")
        if self.recent_products:
            sections.append(f"[Recent Products]\n{self.recent_products}")
        if self.conversation_history:
            sections.append(f"[Conversation History]\n{self.conversation_history}")
        return "\n\n".join(sections)


class InteractionData(BaseModel):
    """Data for a single interaction to be stored in memory."""

    tenant_id: str
    user_id: str
    conversation_id: str = ""
    channel: str = ""
    message_content: str = ""
    sender_type: str = ""  # customer, ai, agent
    intent: dict[str, Any] = Field(default_factory=dict)
    sentiment: dict[str, Any] = Field(default_factory=dict)
    product_ids: list[str] = Field(default_factory=list)
    ai_response: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
