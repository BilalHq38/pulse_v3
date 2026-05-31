"""Helpers for passing existing message rows into the conversation engine."""

from __future__ import annotations

from services.conversation_engine.prompt_builder import sanitise_history_content


def format_messages_as_dialogue(msgs: list[dict], max_pairs: int = 12) -> list[str]:
    """Convert message rows into the engine's dialogue history format."""
    lines: list[str] = []
    for msg in msgs or []:
        sender = str(msg.get("sender_type") or "")
        text = sanitise_history_content(str(msg.get("content") or ""))
        if not text:
            continue
        if sender == "customer":
            lines.append(f"User: {text}")
        elif sender == "ai":
            lines.append(f"Assistant: {text}")
        elif sender == "agent":
            lines.append(f"Agent: {text}")
    return lines[-(max_pairs * 2):]
