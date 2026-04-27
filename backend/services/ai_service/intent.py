from __future__ import annotations

from services.ai_service.common import IntentResult
from services.ai_service.llm_client import _resolve_engine_for_request, call_model_json

_ALLOWED_URGENCY = {"low", "medium", "high", "critical"}


def _normalize_intent_payload(raw: dict) -> dict:
    payload = dict(raw or {})
    intent_name = str(payload.get("intent") or "general_question").strip().lower()
    if not intent_name:
        intent_name = "general_question"
    confidence = float(payload.get("confidence") or 0.0)
    confidence = max(0.0, min(1.0, confidence))
    urgency = str(payload.get("urgency") or "medium").strip().lower()
    if urgency not in _ALLOWED_URGENCY:
        urgency = "medium"
    entities = payload.get("entities") if isinstance(payload.get("entities"), dict) else {}
    return {
        "intent": intent_name,
        "confidence": confidence,
        "entities": entities,
        "urgency": urgency,
    }


def _render_history_context(conversation_context: list | None) -> str:
    lines: list[str] = []
    for item in (conversation_context or [])[-10:]:
        if not isinstance(item, dict):
            continue
        sender = str(item.get("sender_type") or "unknown").strip().lower()
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{sender}: {content}")
    return "\n".join(lines)


async def classify_intent(text: str, db=None, company_id: str = "", **kwargs) -> dict:
    history_context = _render_history_context(kwargs.get("conversation_context"))
    previous_intent = str(kwargs.get("previous_intent") or "").strip().lower()
    prompt = (
        "You are an intent-routing classifier for a CRM assistant.\n"
        "Task: infer the single best customer intent for the latest message so downstream automation can choose the next step.\n"
        "Input format:\n"
        "- previous_intent: most recent known intent or 'unknown'\n"
        "- recent_conversation: last customer/agent turns, if any\n"
        "- latest_message: the newest customer text\n"
        "Output format: return ONLY valid JSON with exactly these keys:\n"
        '{"intent":"snake_case_intent","confidence":0.0,"entities":{},"urgency":"low|medium|high|critical"}\n'
        "Rules:\n"
        "- Choose one primary intent only.\n"
        "- Keep confidence between 0 and 1.\n"
        "- Put only factual extracted fields in entities; do not invent IDs, prices, orders, or names.\n"
        "- Set urgency to critical only for explicit immediate risk, legal threat, severe churn risk, or urgent handoff.\n"
        "- If the message is ambiguous, prefer a general but still useful intent and lower confidence.\n"
        f"\nprevious_intent: {previous_intent or 'unknown'}"
        f"\nrecent_conversation:\n{history_context or '[none]'}"
        f"\nlatest_message:\n{text}"
    )
    last_exc: Exception | None = None
    for _ in range(2):
        try:
            remote = await call_model_json(
                prompt,
                IntentResult,
                engine=await _resolve_engine_for_request(db=db, company_id=company_id),
            )
            return _normalize_intent_payload(remote)
        except Exception as exc:
            last_exc = exc
            continue
    raise RuntimeError(
        f"Intent classification failed: {(last_exc.__class__.__name__ if last_exc else 'UnknownError')}"
    ) from last_exc


__all__ = ["classify_intent"]
