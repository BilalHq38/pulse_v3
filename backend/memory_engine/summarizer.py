"""
memory_engine/summarizer.py — Token-efficient summarization utilities.

Compresses conversation histories and customer context to fit within
LLM token budgets while retaining critical information.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Approximate token-to-character ratio (conservative for English text)
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def truncate_to_token_budget(text: str, max_tokens: int) -> str:
    """Truncate text to fit within a token budget."""
    max_chars = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def summarize_messages(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 2000,
    max_messages: int = 20,
) -> str:
    """
    Compress conversation messages into a token-budgeted string.

    Strategy:
        1. Take the most recent max_messages
        2. Format as "role: content" lines
        3. Truncate to token budget
    """
    if not messages:
        return ""

    recent = messages[-max_messages:]
    lines: list[str] = []

    for msg in recent:
        sender = str(msg.get("sender_type") or msg.get("role") or "?").strip()
        content = str(msg.get("content") or msg.get("text") or "").strip()
        if not content:
            continue

        # Truncate individual long messages
        if len(content) > 500:
            content = content[:497].rstrip() + "..."

        lines.append(f"{sender}: {content}")

    result = "\n".join(lines)
    return truncate_to_token_budget(result, max_tokens)


def summarize_customer_profile(
    customer: dict[str, Any],
    *,
    max_tokens: int = 500,
) -> str:
    """
    Build a concise customer profile summary.

    Extracts the most relevant fields and formats them
    into a compact text block.
    """
    if not customer:
        return ""

    parts: list[str] = []

    name = str(customer.get("name") or "").strip()
    if name:
        parts.append(f"Name: {name}")

    segment = str(customer.get("segment") or "").strip()
    if segment and segment != "general":
        parts.append(f"Segment: {segment}")

    lifecycle = str(customer.get("lifecycle_stage") or "").strip()
    if lifecycle:
        parts.append(f"Stage: {lifecycle}")

    ltv = float(customer.get("lifetime_value") or 0)
    if ltv > 0:
        parts.append(f"LTV: ${ltv:.2f}")

    total_convos = int(customer.get("total_conversations") or 0)
    if total_convos > 0:
        parts.append(f"Conversations: {total_convos}")

    sentiment = str(customer.get("historical_sentiment") or "").strip()
    if sentiment:
        parts.append(f"Sentiment: {sentiment}")

    summary = str(customer.get("long_term_summary") or "").strip()
    if summary:
        parts.append(f"Summary: {summary}")

    result = " | ".join(parts)
    return truncate_to_token_budget(result, max_tokens)


def summarize_knowledge_context(
    knowledge_text: str,
    *,
    max_tokens: int = 500,
) -> str:
    """Truncate knowledge context to token budget."""
    return truncate_to_token_budget(knowledge_text, max_tokens)


def build_rolling_summary(
    existing_summary: str,
    new_messages: list[dict[str, Any]],
    *,
    max_tokens: int = 400,
) -> str:
    """
    Build a rolling summary by appending new message key points
    to the existing summary, staying within token budget.

    This is a lightweight rule-based approach. For LLM-based
    summarization, use memory_service.update_customer_memory().
    """
    if not new_messages:
        return truncate_to_token_budget(existing_summary, max_tokens)

    new_content: list[str] = []
    for msg in new_messages[-5:]:  # Only latest 5 messages
        sender = str(msg.get("sender_type") or "?").strip()
        content = str(msg.get("content") or "").strip()
        if content and sender == "customer":
            # Extract key phrases (first sentence)
            first_sentence = content.split(".")[0].strip()
            if len(first_sentence) > 100:
                first_sentence = first_sentence[:97] + "..."
            new_content.append(first_sentence)

    if not new_content:
        return truncate_to_token_budget(existing_summary, max_tokens)

    addition = ". ".join(new_content)
    combined = f"{existing_summary} | Recent: {addition}".strip()
    return truncate_to_token_budget(combined, max_tokens)
