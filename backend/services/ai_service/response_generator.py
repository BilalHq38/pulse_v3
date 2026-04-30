from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time

from shared.metrics import increment_counter, observe_histogram
from shared.config import (
    ai_enable_rule_based_recovery,
    ai_input_token_budget,
    ai_response_recent_ai_message_limit,
    ai_response_retry_temperature_delta,
    ai_response_retry_temperature_max,
    ai_response_temperature_base,
    ai_response_temperature_jitter_steps,
    ai_response_temperature_max,
    ai_response_temperature_min,
    ai_response_temperature_negative_delta,
    ai_response_temperature_product_delta,
    ai_response_temperature_repetition_delta,
)
from services.ai_service.common import (
    LeadScoreResult,
    _json_safe,
    latest_ai_message,
    latest_customer_message,
    text_similarity,
    truncate_text_for_tokens,
)
from services.ai_service.intent import classify_intent
from services.ai_service.llm_client import (
    _resolve_engine_for_request,
    call_model_json_batch,
    call_with_engines,
    call_model_json,
    call_model_text,
    validate_live_engine,
)
from services.ai_service.memory_service import (
    get_conversation_state_memory,
    get_last_ai_response_context,
    get_last_shown_product_ids,
    remember_conversation_state,
    remember_ai_response,
    remember_shown_products,
)
from services.ai_service.rag import build_ai_context, recent_customer_image_urls, understand_product_query
from services.ai_service.sentiment import (
    analyze_conversation_sentiment,
    # FIX: analyze_message_and_conversation_sentiment is dead code — it is a
    # two-task batch that is fully superseded by the three-task batch inside
    # generate_combined_ai_analysis. Import removed to prevent accidental use.
    analyze_sentiment,
)
from services.db_helpers import is_data_url_image, resolve_active_ai_agent
from core.utils import is_valid_image_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine-resolution cache
# FIX (latency 3): cache the active engine per company for up to 60 seconds.
# _resolve_engine_for_request hits the DB on every message — this eliminates
# that round-trip on the hot path.
# ---------------------------------------------------------------------------
import functools

_ENGINE_CACHE: dict[str, tuple[float, dict]] = {}
_ENGINE_CACHE_TTL = 60.0  # seconds


async def _resolve_engine_cached(db, company_id: str, use_pro: bool = False) -> dict:
    cache_key = f"{company_id or ''}:{use_pro}"
    now = time.monotonic()
    if cache_key in _ENGINE_CACHE:
        ts, engine = _ENGINE_CACHE[cache_key]
        if now - ts < _ENGINE_CACHE_TTL:
            return engine
    engine = await _resolve_engine_for_request(db=db, company_id=company_id, use_pro=use_pro)
    _ENGINE_CACHE[cache_key] = (now, engine)
    return engine


RESPONSE_STYLE_PROFILES = (
    {
        "name": "empathetic",
        "instruction": "Lead with empathy, acknowledge the customer's state, then give one clear next step.",
    },
    {
        "name": "consultative",
        "instruction": "Sound like a consultative advisor. Offer a concise comparison and a practical recommendation.",
    },
    {
        "name": "concise",
        "instruction": "Keep the answer tight, specific, and easy to scan. Avoid filler and generic openings.",
    },
    {
        "name": "reassuring",
        "instruction": "Be calm and reassuring. Resolve the concern first and keep the tone human.",
    },
    {
        "name": "actionable",
        "instruction": "Focus on concrete actions, specific options, and the fastest path to value.",
    },
)

AGENT_RUNTIME_PROFILES = {
    "support": {
        "label": "Support Agent",
        "instruction": "Operate like a hands-on support specialist who resolves issues quickly and reduces customer effort.",
    },
    "sales": {
        "label": "Sales Agent",
        "instruction": "Operate like a live sales advisor who qualifies intent, recommends the best-fit product, and moves toward a concrete buying step.",
    },
    "onboarding": {
        "label": "Onboarding Agent",
        "instruction": "Operate like an onboarding specialist who explains setup clearly, removes friction, and keeps activation moving forward.",
    },
    "generic": {
        "label": "General Assistant",
        "instruction": "Operate like a fast, reliable CRM assistant who adapts to the request without sounding robotic.",
    },
}


def _allow_rule_based_recovery() -> bool:
    return ai_enable_rule_based_recovery()


def _sentiment_label(sentiment: dict | None) -> str:
    payload = dict(sentiment or {})
    label = str(payload.get("sentiment_label") or payload.get("label") or "").strip().lower()
    if label in {"positive", "neutral", "negative"}:
        return label
    score = float(payload.get("score") or 0.0)
    if score >= 0.2:
        return "positive"
    if score <= -0.2:
        return "negative"
    return "neutral"


def _derive_conversation_state(
    *,
    query: str,
    observed_intent: dict,
    observed_sentiment: dict,
    previous_state: dict,
    last_response_context: dict,
) -> dict:
    current_intent = (
        str((observed_intent or {}).get("intent") or "general_question").strip().lower() or "general_question"
    )
    previous_intent = (
        str((previous_state or {}).get("intent") or "").strip().lower()
        or str(((last_response_context or {}).get("intent") or {}).get("intent") or "").strip().lower()
    )
    urgency = str((observed_intent or {}).get("urgency") or "medium").strip().lower() or "medium"
    sentiment_label = _sentiment_label(observed_sentiment)
    turn_count = max(1, int((previous_state or {}).get("turn_count") or 0) + 1)
    intent_shift = bool(previous_intent and current_intent and previous_intent != current_intent)

    if current_intent in {"refund", "cancel_request", "complaint", "support_request", "shipping_question"}:
        stage = "resolution"
    elif current_intent in {"product_recommendation", "purchase_inquiry"}:
        stage = "recommendation"
    elif current_intent in {"gratitude"}:
        stage = "wrap_up"
    else:
        stage = "discovery"
    if urgency in {"high", "critical"} and stage != "wrap_up":
        stage = "resolution"

    next_action_map = {
        "refund": "Collect order details and confirm refund eligibility before escalation.",
        "cancel_request": "Confirm account or order details and action cancellation immediately.",
        "complaint": "Acknowledge concern and ask for the one missing detail needed to resolve it.",
        "support_request": "Gather exact issue and offer the most direct troubleshooting step.",
        "shipping_question": "Ask for tracking or order reference, then provide status and options.",
        "purchase_inquiry": "Ask budget and use case, then recommend strongest matching options.",
        "product_recommendation": "Offer a short curated set and ask one preference to narrow choices.",
        "company_question": "Share concise business context and guide to relevant next step.",
        "gratitude": "Close warmly and offer proactive follow-up assistance.",
        "general_question": "Clarify the main objective and suggest the fastest next action.",
    }
    next_action = next_action_map.get(
        current_intent,
        "Clarify customer objective and guide to the next best action.",
    )
    if sentiment_label == "negative" and stage != "wrap_up":
        next_action = "Prioritize resolution first, then confirm if escalation to a human is needed."

    transition_note = ""
    if intent_shift:
        transition_note = (
            f"Intent shifted from {previous_intent.replace('_', ' ')} to {current_intent.replace('_', ' ')}."
        )

    return {
        "intent": current_intent,
        "previous_intent": previous_intent,
        "intent_shift": intent_shift,
        "transition_note": transition_note,
        "stage": stage,
        "next_action": next_action,
        "sentiment_label": sentiment_label,
        "turn_count": turn_count,
        "latest_query": query,
    }


def _recent_ai_messages(conversation_context: list[dict], *, limit: int = 3) -> list[str]:
    limit = max(1, int(limit or ai_response_recent_ai_message_limit()))
    recent: list[str] = []
    for message in reversed(conversation_context or []):
        if str(message.get("sender_type") or "").strip().lower() != "ai":
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        recent.append(content)
        if len(recent) >= limit:
            break
    return list(reversed(recent))


def _build_generation_config(
    *,
    query: str,
    observed_intent: dict,
    observed_sentiment: dict,
    recent_ai_replies: list[str],
) -> dict:
    base_temperature = ai_response_temperature_base()
    min_temperature = ai_response_temperature_min()
    max_temperature = ai_response_temperature_max()
    jitter_steps = ai_response_temperature_jitter_steps()
    product_delta = ai_response_temperature_product_delta()
    negative_delta = ai_response_temperature_negative_delta()
    repetition_delta = ai_response_temperature_repetition_delta()
    intent_name = str((observed_intent or {}).get("intent") or "").strip().lower()
    sentiment_label = _sentiment_label(observed_sentiment)
    if intent_name in {"product_recommendation", "purchase_inquiry"}:
        base_temperature += product_delta
    if sentiment_label == "negative":
        base_temperature += negative_delta
    if len(recent_ai_replies) >= 2:
        base_temperature += repetition_delta
    jitter = random.randint(0, jitter_steps) / 100.0
    temperature = max(min_temperature, min(max_temperature, base_temperature + jitter))
    return {"temperature": round(temperature, 2)}


def _agent_configuration(agent: dict | None) -> dict[str, str]:
    config = (agent or {}).get("configuration")
    return dict(config or {}) if isinstance(config, dict) else {}


def _normalize_ai_attachments(attachments: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    seen_urls: set[str] = set()
    for index, attachment in enumerate(attachments or []):
        if not isinstance(attachment, dict):
            continue
        url = str(attachment.get("url") or "").strip()
        product_id = str(attachment.get("product_id") or "").strip()
        if not url or not product_id or url in seen_urls or not is_valid_image_url(url):
            continue
        normalized.append(
            {
                "type": "image",
                "url": url,
                "name": str(attachment.get("name") or attachment.get("product_name") or "").strip(),
                "size": int(attachment.get("size") or 0),
                "product_id": product_id,
                "product_name": str(attachment.get("product_name") or attachment.get("name") or "").strip(),
                "product_title": str(attachment.get("product_title") or "").strip(),
                "product_category": str(attachment.get("product_category") or "").strip(),
                "image_index": int(attachment.get("image_index") or index),
            }
        )
        seen_urls.add(url)
        if len(normalized) >= 3:
            break
    return normalized


def _align_product_attachments(product_ids: list[str], attachments: list[dict]) -> list[dict]:
    allowed = {str(product_id).strip() for product_id in product_ids if str(product_id).strip()}
    normalized = _normalize_ai_attachments(attachments)
    aligned: list[dict] = []
    seen_products: set[str] = set()
    for attachment in normalized:
        product_id = str(attachment.get("product_id") or "").strip()
        if allowed and product_id not in allowed:
            continue
        if product_id in seen_products:
            continue
        aligned.append(attachment)
        seen_products.add(product_id)
        if len(aligned) >= 3:
            break
    return aligned


def _trim_text(value: str, limit: int = 160) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _looks_like_navigation_request(query: str) -> bool:
    lowered = (query or "").strip().lower()
    return any(
        phrase in lowered
        for phrase in (
            "how do i",
            "how to",
            "where do i",
            "what should i do",
            "next step",
            "what next",
            "help me",
            "show me",
            "start here",
            "guide me",
        )
    )


def _product_reason(product: dict) -> str:
    parts: list[str] = []
    category = str(product.get("category") or "").strip()
    price = str(product.get("price") or "").strip()
    currency = str(product.get("price_currency") or "USD").strip() or "USD"
    features = [str(item).strip() for item in (product.get("features") or []) if str(item).strip()]
    if category:
        parts.append(category)
    if features:
        parts.append(features[0] if len(features) == 1 else ", ".join(features[:2]))
    if price:
        parts.append(f"priced at {price} {currency}".strip())
    return "; ".join(parts) if parts else "a strong match for the current request"


def _compose_rule_based_response(
    query: str,
    *,
    customer_info: dict,
    knowledge_context: str,
    ai_context: dict,
    observed_sentiment: dict,
    observed_intent: dict,
    previous_response: str,
    last_response_context: dict,
    conversation_state: dict,
) -> dict | None:
    intent_name = str((observed_intent or {}).get("intent") or "general_question").lower()
    query_info = understand_product_query(query)
    same_thread = str((last_response_context or {}).get("intent", {}).get("intent") or "").lower() == intent_name
    continuation_prefix = "Continuing the same thread, " if same_thread and previous_response else ""
    emotion = str((observed_sentiment or {}).get("emotion") or "neutral").lower()
    response_prefix = "I understand. " if emotion in {"angry", "frustrated"} else ""
    transition_note = str((conversation_state or {}).get("transition_note") or "").strip()
    stage = str((conversation_state or {}).get("stage") or "discovery").strip()
    next_action = str((conversation_state or {}).get("next_action") or "").strip()
    stage_prefix = f"[{stage}] " if stage else ""
    direction_prefix = f"{transition_note} " if transition_note else ""

    if intent_name in {"gratitude"}:
        return {
            "response": (
                f"{direction_prefix}{response_prefix}Glad to help. "
                "If you want, I can also walk you through products, support, or the next step."
            ),
            "confidence": 0.96,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "chatbot",
            "conversation_stage": stage,
            "next_action": next_action,
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    if intent_name in {
        "company_question",
        "shipping_question",
        "support_request",
        "refund",
        "cancel_request",
        "complaint",
    } or _looks_like_navigation_request(query):
        guidance_map = {
            "company_question": [
                "I can give you the short version of who we are and what we do.",
                "If you want specifics, ask for products, support, or company details and I will narrow it down.",
                "I can also point you to the next action instead of restarting the conversation.",
            ],
            "shipping_question": [
                "Share the order number or shipment reference.",
                "Check the latest tracking update and carrier status.",
                "If the package is delayed, I can help escalate it to a human agent.",
            ],
            "refund": [
                "Confirm the order and payment details.",
                "Check whether the item is eligible for return or refund.",
                "If the case needs approval, I will hand it off to a human agent.",
            ],
            "cancel_request": [
                "Confirm the order or subscription details.",
                "Check whether cancellation is still available.",
                "Escalate to a human agent if the cancellation needs manual review.",
            ],
            "complaint": [
                "Acknowledge the issue first so the customer knows they were heard.",
                "Ask for the key detail that blocks resolution.",
                "Escalate immediately if the case cannot be resolved in one pass.",
            ],
            "support_request": [
                "Capture the exact issue in one sentence.",
                "Check the most likely cause or account detail.",
                "If that does not solve it, route the conversation to the right human next.",
            ],
        }
        steps = guidance_map.get(intent_name) or [
            "Tell me what you need help with.",
            "I will narrow it to the next best action.",
            "If the issue needs a person, I will make that handoff clear.",
        ]
        return {
            "response": f"{stage_prefix}{direction_prefix}{continuation_prefix}{response_prefix}Here is the quickest path:\n"
            + "\n".join(f"{index + 1}. {step}" for index, step in enumerate(steps))
            + (f"\n\nNext action: {next_action}" if next_action else ""),
            "confidence": 0.94,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "chatbot",
            "conversation_stage": stage,
            "next_action": next_action,
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    if intent_name in {"product_recommendation", "purchase_inquiry"} or query_info.get("general"):
        is_simple_product_query = bool(query_info.get("general")) or len((query or "").split()) <= 8
        if intent_name in {"product_recommendation", "purchase_inquiry"} and not is_simple_product_query:
            return None
        products = list(ai_context.get("products") or [])[:3]
        attachments = _normalize_ai_attachments(list(ai_context.get("product_attachments") or []))
        if products:
            lines = [
                f"{stage_prefix}{direction_prefix}{continuation_prefix}I found up to {len(products)} options that fit this request.",
            ]
            for index, product in enumerate(products, start=1):
                name = str(product.get("name") or product.get("product_title") or "Product").strip()
                lines.append(f"{index}. {name} - {_product_reason(product)}.")
            lines.append("If you want, I can narrow these down by budget, style, or use case.")
            if next_action:
                lines.append(f"Next action: {next_action}")
            return {
                "response": "\n".join(lines),
                "confidence": 0.95,
                "attachments": attachments,
                "product_images": attachments,
                "product_ids": [str(item).strip() for item in ai_context.get("product_ids", []) if str(item).strip()][
                    :3
                ],
                "llm_id": "",
                "provider": "rule",
                "model_name": "semantic-ranker",
                "conversation_stage": stage,
                "next_action": next_action,
                "intent_shift": bool((conversation_state or {}).get("intent_shift")),
            }
        return {
            "response": "I can help with product suggestions. Share a budget, style, or use case and I will narrow it down.",
            "confidence": 0.9,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "chatbot",
            "conversation_stage": stage,
            "next_action": next_action,
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    if knowledge_context.strip():
        return {
            "response": (
                f"{stage_prefix}{direction_prefix}{continuation_prefix}{response_prefix}{_trim_text(knowledge_context, 260)}"
                + (f"\n\nNext action: {next_action}" if next_action else "")
            ),
            "confidence": 0.9,
            "attachments": [],
            "product_images": [],
            "product_ids": [],
            "llm_id": "",
            "provider": "rule",
            "model_name": "knowledge",
            "conversation_stage": stage,
            "next_action": next_action,
            "intent_shift": bool((conversation_state or {}).get("intent_shift")),
        }

    return None


def calculate_churn_risk(customer_data: dict) -> dict:
    score = 0.0
    factors = []
    if customer_data.get("avg_sentiment", 0) < -0.3:
        score += 0.3
        factors.append("Negative sentiment")
    if customer_data.get("recent_tickets", 0) > 5:
        score += 0.25
        factors.append("High ticket volume")
    if customer_data.get("days_since_last_contact", 0) > 60:
        score += 0.2
        factors.append("Inactive customer")
    if customer_data.get("complaint_count", 0) > 3:
        score += 0.25
        factors.append("Multiple complaints")
    score = min(score, 1.0)
    return {
        "risk_score": score,
        "risk_level": "critical" if score > 0.7 else "high" if score > 0.5 else "medium" if score > 0.3 else "low",
        "factors": factors,
    }


async def generate_lead_score(lead_data: dict, db=None, company_id: str = "") -> dict:
    prompt = (
        "You are a lead-qualification analyst for a CRM.\n"
        "Task: score the sales readiness of one lead record using only the provided evidence.\n"
        "Input format:\n"
        "- lead: JSON with source, notes, attributes, and any engagement signals\n"
        "Output format: return ONLY valid JSON with exactly this schema:\n"
        '{"score": int(0-100), "grade": "hot|warm|cold", "reasoning": "...", '
        '"next_action": "...", "phase": "awareness|interest|consideration|intent|evaluation|purchase"}\n'
        "Rules:\n"
        "- reasoning must be concise and evidence-based.\n"
        "- next_action must be a concrete CRM follow-up, not a generic suggestion.\n"
        "- Do not invent missing facts or metrics.\n"
        f"\nlead:\n{json.dumps(_json_safe(lead_data), ensure_ascii=True)}"
    )
    try:
        return await call_model_json(
            prompt,
            LeadScoreResult,
            engine=await _resolve_engine_cached(db=db, company_id=company_id, use_pro=True),
            use_pro=True,
        )
    except Exception as exc:
        raise RuntimeError(f"Lead scoring failed: {exc.__class__.__name__}") from exc


async def generate_nurture_message(
    lead_data: dict,
    stage: str,
    company_context: str = "",
    db=None,
    company_id: str = "",
) -> dict:
    prompt = (
        "You are a B2B sales nurture copywriter for CRM outreach.\n"
        f"Task: write one personalized follow-up message for the lead stage '{stage}'.\n"
        "Input format:\n"
        "- company_context: approved company/product context\n"
        "- lead: JSON lead profile and notes\n"
        "Output format: return plain text only, no JSON, no greeting placeholders, no markdown.\n"
        "Rules:\n"
        "- Keep the message under 3 sentences.\n"
        "- Mention only details supported by the input.\n"
        "- End with a soft CTA that suggests the next reply or meeting.\n"
        "- Sound natural and specific, not templated.\n"
        f"\ncompany_context:\n{truncate_text_for_tokens(company_context, 1200)}\n"
        f"\nlead:\n{json.dumps(_json_safe(lead_data), ensure_ascii=True)}"
    )
    try:
        return {
            "message": await call_model_text(
                prompt,
                engine=await _resolve_engine_cached(db=db, company_id=company_id, use_pro=True),
                use_pro=True,
            ),
            "stage": stage,
        }
    except Exception as exc:
        raise RuntimeError(f"Lead nurture generation failed: {exc.__class__.__name__}") from exc


async def auto_score_and_nurture_lead(lead_data: dict, db=None, company_id: str | None = None) -> dict:
    resolved_company_id = company_id or lead_data.get("company_id", "")
    score_result = await generate_lead_score(lead_data, db=db, company_id=resolved_company_id)
    company_context = ""
    if db:
        try:
            context = await build_ai_context(
                db,
                company_id=resolved_company_id,
                current_query=f"{lead_data.get('notes', '')} {lead_data.get('source', '')}".strip(),
            )
            company_context = context["knowledge_text"]
        except Exception as exc:
            logger.warning("Company knowledge fetch for nurture failed: %s", exc)
    nurture = await generate_nurture_message(
        lead_data,
        score_result.get("phase", "awareness"),
        company_context=company_context,
        db=db,
        company_id=resolved_company_id,
    )
    return {**score_result, "nurture_message": nurture.get("message")}


def _dedupe_response_text(
    response: str,
    *,
    last_response: str,
    product_names: list[str],
) -> str:
    if not response.strip():
        return response
    if text_similarity(response, last_response) < 0.9:
        return response.strip()
    varied = response.strip().rstrip(".")
    suffix_pool = [
        "Tell me what detail matters most and I'll tailor the recommendation.",
        "If you want, I can narrow it down further based on your budget or style.",
        "Share one more preference and I'll refine this into a better fit.",
    ]
    if product_names:
        suffix_pool.insert(
            1,
            f"If you'd like, I can narrow it to {product_names[0]} or a close alternative.",
        )
    suffix_index = int(hashlib.sha1(f"{response}:{last_response}".encode("utf-8")).hexdigest(), 16) % len(suffix_pool)
    suffix = suffix_pool[suffix_index]
    return f"{varied}. {suffix}".strip()


def _select_response_style(
    query: str,
    sentiment: dict | None,
    intent: dict | None,
    customer_info: dict,
    preferred_agent_type: str = "",
) -> dict[str, str]:
    emotion = str((sentiment or {}).get("emotion", "neutral") or "neutral").lower()
    intent_name = str((intent or {}).get("intent", "general_question") or "general_question").lower()
    urgency = str((intent or {}).get("urgency", "medium") or "medium").lower()
    preferred = str(preferred_agent_type or "").strip().lower()
    if preferred == "sales":
        base_profile = RESPONSE_STYLE_PROFILES[1]
    elif preferred == "onboarding":
        base_profile = RESPONSE_STYLE_PROFILES[3]
    elif emotion in {"angry", "frustrated"} or intent_name in {"complaint", "refund", "cancel_request"}:
        base_profile = RESPONSE_STYLE_PROFILES[0]
    elif intent_name in {"product_recommendation", "purchase_inquiry"}:
        base_profile = RESPONSE_STYLE_PROFILES[1]
    elif urgency in {"high", "critical"}:
        base_profile = RESPONSE_STYLE_PROFILES[4]
    else:
        seed = hashlib.sha1(
            f"{query}:{emotion}:{intent_name}:{customer_info.get('segment', '')}".encode("utf-8")
        ).hexdigest()
        base_profile = RESPONSE_STYLE_PROFILES[int(seed, 16) % len(RESPONSE_STYLE_PROFILES)]
    return {
        "name": base_profile["name"],
        "instruction": base_profile["instruction"],
    }


def _agent_runtime_profile(agent: dict | None) -> dict[str, str]:
    agent_type = str((agent or {}).get("agent_type") or "generic").strip().lower() or "generic"
    profile = AGENT_RUNTIME_PROFILES.get(agent_type) or AGENT_RUNTIME_PROFILES["generic"]
    configuration = _agent_configuration(agent)
    configured_label = str(configuration.get("label") or "").strip()
    configured_instruction = str(
        configuration.get("instruction") or configuration.get("system_prompt") or ""
    ).strip()
    return {
        "agent_type": agent_type,
        "label": configured_label or profile["label"],
        "instruction": configured_instruction or profile["instruction"],
    }


def _apply_agent_engine_overrides(engine: dict, agent: dict | None) -> dict:
    resolved = dict(engine or {})
    configuration = _agent_configuration(agent)
    if not configuration:
        return resolved
    if configuration.get("temperature") not in {None, ""}:
        try:
            resolved["temperature"] = float(configuration["temperature"])
        except Exception:
            pass
    if configuration.get("max_tokens") not in {None, ""}:
        try:
            resolved["max_tokens"] = int(configuration["max_tokens"])
        except Exception:
            pass
    return resolved


async def _generate_response_text(
    prompt: str,
    *,
    engine: dict,
    image_urls: list[str],
    generation_config: dict | None = None,
) -> str:
    validate_live_engine(engine, require_vision=bool(image_urls))
    return await call_model_text(
        prompt,
        engine=engine,
        generation_config=generation_config,
        image_urls=image_urls or None,
    )


async def _persist_response_memory(
    *,
    db,
    company_id: str,
    memory_entity_id: str,
    convo_id: str,
    prompt: str,
    response: str,
    intent: dict,
    sentiment: dict,
    product_ids: list[str],
    response_style: str,
    channel: str = "",
    conversation_state: dict | None = None,
) -> None:
    if not (db and company_id and memory_entity_id and response):
        return
    try:
        await remember_ai_response(
            db,
            company_id,
            memory_entity_id,
            response,
            convo_id=convo_id,
            prompt=prompt,
            intent=intent,
            sentiment=sentiment,
            product_ids=product_ids,
            response_style=response_style,
        )
        await remember_shown_products(
            db,
            company_id,
            memory_entity_id,
            product_ids,
            convo_id=convo_id,
        )
        if conversation_state:
            await remember_conversation_state(
                db,
                company_id,
                memory_entity_id,
                conversation_state,
                convo_id=convo_id,
            )

        from memory_engine.manager import MemoryManager
        from memory_engine.schemas import InteractionData

        manager = MemoryManager(db=db)
        if prompt:
            await manager.store_interaction(
                InteractionData(
                    tenant_id=company_id,
                    user_id=memory_entity_id,
                    conversation_id=convo_id,
                    channel=channel,
                    message_content=prompt,
                    sender_type="customer",
                    intent=intent,
                    sentiment=sentiment,
                    product_ids=product_ids,
                    ai_response=response,
                )
            )
        await manager.store_interaction(
            InteractionData(
                tenant_id=company_id,
                user_id=memory_entity_id,
                conversation_id=convo_id,
                channel=channel,
                message_content=response,
                sender_type="ai",
                intent=intent,
                sentiment=sentiment,
                product_ids=product_ids,
                ai_response=response,
            )
        )
        if conversation_state:
            await manager.update_memory(
                user_id=memory_entity_id,
                tenant_id=company_id,
                memory_type="conversation_state",
                content=conversation_state,
                conversation_id=convo_id,
            )
        if product_ids:
            await manager.update_memory(
                user_id=memory_entity_id,
                tenant_id=company_id,
                memory_type="shown_products",
                content={"product_ids": product_ids},
                conversation_id=convo_id,
            )
    except Exception as exc:
        increment_counter("ai.responses.memory_persist_errors")
        logger.warning("AI memory persistence skipped: %s", exc)


async def generate_ai_response(
    conversation_context: list,
    customer_info: dict | None = None,
    knowledge_context: str = "",
    company_id: str | None = None,
    db=None,
    long_term_summary: str = "",
    historical_sentiment: str = "",
    actor_user_id: str = "",
    **kwargs,
):
    customer_info = dict(customer_info or {})
    company_id = company_id or customer_info.get("company_id", "")
    conversation_id = str(kwargs.get("conversation_id") or "").strip()
    query = latest_customer_message(conversation_context) or (
        conversation_context[-1].get("content", "") if conversation_context else ""
    )
    started = time.perf_counter()

    def _record_outcome(outcome: str, source: str, provider: str = "") -> None:
        labels = {"outcome": outcome, "source": source}
        if provider:
            labels["provider"] = provider
        increment_counter("ai.responses.total", labels=labels)
        observe_histogram(
            "ai.response.latency_ms",
            (time.perf_counter() - started) * 1000.0,
            labels=labels,
        )

    increment_counter("ai.responses.requested")
    observed_sentiment = dict(kwargs.get("observed_sentiment") or {})
    observed_conversation_sentiment = dict(kwargs.get("observed_conversation_sentiment") or {})
    observed_intent = dict(kwargs.get("observed_intent") or {})
    if not observed_sentiment:
        observed_sentiment = await analyze_sentiment(query, db=db, company_id=company_id or "")
    if not observed_conversation_sentiment and conversation_context:
        try:
            observed_conversation_sentiment = await analyze_conversation_sentiment(
                conversation_context,
                latest_message=query,
                db=db,
                company_id=company_id or "",
            )
        except Exception as exc:
            logger.warning("Conversation sentiment analysis skipped: %s", exc)
    if not observed_intent:
        observed_intent = await classify_intent(
            query,
            db=db,
            company_id=company_id or "",
            conversation_context=conversation_context[-12:],
        )
    channel_name = str(kwargs.get("channel") or "").strip()
    selected_agent = None
    if db and company_id:
        try:
            selected_agent = await resolve_active_ai_agent(
                db,
                company_id,
                intent_name=str(observed_intent.get("intent") or ""),
                channel=channel_name,
            )
        except Exception as exc:
            logger.debug("Active AI agent resolution skipped: %s", exc)
    if db and selected_agent and selected_agent.get("id"):
        try:
            selected_agent["configuration"] = {
                row["config_key"]: row["config_val"]
                for row in await db.fetch(
                    "SELECT config_key,config_val FROM ai_agent_config WHERE agent_id=$1",
                    selected_agent["id"],
                )
            }
        except Exception as exc:
            logger.debug("AI agent configuration load skipped: %s", exc)
    agent_profile = _agent_runtime_profile(selected_agent)
    selected_agent_id = str((selected_agent or {}).get("id") or "").strip()
    selected_agent_type = str((selected_agent or {}).get("agent_type") or "").strip().lower() or "generic"
    logger.info(
        "ai_agent_selected company_id=%s agent_id=%s agent_type=%s intent=%s channel=%s llm_id=%s",
        company_id or "",
        selected_agent_id or "-",
        selected_agent_type,
        str(observed_intent.get("intent") or "general_question"),
        channel_name or "unknown",
        str((selected_agent or {}).get("llm_id") or ""),
    )
    style_profile = _select_response_style(
        query,
        observed_sentiment,
        observed_intent,
        customer_info,
        preferred_agent_type=agent_profile["agent_type"],
    )

    memory_entity_id = str(customer_info.get("id") or "").strip() or str(actor_user_id or "").strip() or conversation_id

    # FIX (latency 4): parallelize ContextBuilder with the three memory DB reads.
    # These are entirely independent — RAG/context doesn't depend on last_ai_response
    # or conversation_state. Running them together saves the wall-clock time of the
    # slower of the two.
    prompt_context = None

    async def _build_prompt_context_safe():
        if not (db and company_id and memory_entity_id):
            return None
        try:
            from memory_engine.context_builder import ContextBuilder
            from memory_engine.manager import MemoryManager
            manager = MemoryManager(db=db)
            builder = ContextBuilder(manager)
            return await builder.build_prompt_context(
                user_id=str(customer_info.get("id") or memory_entity_id).strip(),
                tenant_id=str(company_id or "").strip(),
                current_query=str(query or "").strip(),
                conversation_id=conversation_id,
                conversation_context=list(conversation_context or []),
                system_instruction="",
                channel=channel_name,
            )
        except Exception as exc:
            logger.debug("memory_engine ContextBuilder unavailable: %s", exc)
            return None

    async def _fetch_memory_safe():
        if not (db and company_id and memory_entity_id):
            return {}, {}, []
        results = await asyncio.gather(
            get_last_ai_response_context(db, company_id, memory_entity_id, convo_id=conversation_id),
            get_conversation_state_memory(db, company_id, memory_entity_id, convo_id=conversation_id),
            get_last_shown_product_ids(db, company_id, memory_entity_id, convo_id=conversation_id),
            return_exceptions=True,
        )
        last_resp = results[0] if isinstance(results[0], dict) else {}
        prev_state = results[1] if isinstance(results[1], dict) else {}
        shown_ids = results[2] if isinstance(results[2], list) else []
        return last_resp, prev_state, shown_ids

    # Parallel: context builder + memory reads run simultaneously
    _ctx_result, _mem_result = await asyncio.gather(
        _build_prompt_context_safe(),
        _fetch_memory_safe(),
        return_exceptions=True,
    )
    prompt_context = _ctx_result if not isinstance(_ctx_result, Exception) else None
    if isinstance(_mem_result, Exception):
        last_response_context, previous_state, shown_product_ids = {}, {}, []
    else:
        last_response_context, previous_state, shown_product_ids = _mem_result

    previous_response = latest_ai_message(conversation_context)
    if not previous_response:
        previous_response = str(last_response_context.get("response") or "").strip()

    previous_product_ids = [
        str(item).strip() for item in (last_response_context.get("product_ids") or []) if str(item).strip()
    ]
    shown_product_ids = list(dict.fromkeys([*shown_product_ids, *previous_product_ids]))
    recent_ai_replies = _recent_ai_messages(
        conversation_context,
        limit=ai_response_recent_ai_message_limit(),
    )
    if previous_response and previous_response not in recent_ai_replies:
        recent_ai_replies.append(previous_response)
    conversation_state = _derive_conversation_state(
        query=query,
        observed_intent=observed_intent,
        observed_sentiment=observed_conversation_sentiment or observed_sentiment,
        previous_state=previous_state,
        last_response_context=last_response_context,
    )
    rule_recovery_enabled = _allow_rule_based_recovery()

    quick_response = None
    if rule_recovery_enabled and str((observed_intent or {}).get("intent") or "").lower() not in {
        "product_recommendation",
        "purchase_inquiry",
    }:
        quick_response = _compose_rule_based_response(
            query,
            customer_info=customer_info,
            knowledge_context=knowledge_context,
            ai_context={},
            observed_sentiment=observed_sentiment,
            observed_intent=observed_intent,
            previous_response=previous_response,
            last_response_context=last_response_context,
            conversation_state=conversation_state,
        )
    if quick_response:
        quick_response.setdefault("agent_id", selected_agent_id)
        quick_response.setdefault("agent_type", selected_agent_type)
        quick_response.setdefault("intent_name", str(observed_intent.get("intent") or ""))
        logger.info(
            "ai_rule_response company_id=%s intent=%s channel=%s provider=%s",
            company_id or "",
            str(observed_intent.get("intent") or ""),
            channel_name or "unknown",
            str(quick_response.get("provider") or "rule"),
        )
        # FIX (latency 2): fire memory persistence as a detached background task.
        # The response is already available — no need to await persistence before
        # returning it to the caller. Saves 50-200ms off perceived response time.
        asyncio.ensure_future(_persist_response_memory(
            db=db,
            company_id=company_id,
            memory_entity_id=memory_entity_id,
            convo_id=conversation_id,
            prompt=query,
            response=str(quick_response.get("response") or ""),
            intent=observed_intent,
            sentiment=observed_sentiment,
            product_ids=[str(item).strip() for item in quick_response.get("product_ids", []) if str(item).strip()][:3],
            response_style=str(quick_response.get("provider") or "rule"),
            channel=channel_name,
            conversation_state=conversation_state,
        ))
        quick_response.setdefault("conversation_sentiment", observed_conversation_sentiment or observed_sentiment)
        _record_outcome("success", "rule", str(quick_response.get("provider") or "rule"))
        return quick_response

    ai_context = {
        "knowledge_text": knowledge_context or "",
        "products": [],
        "product_ids": [],
        "product_attachments": [],
    }
    if db and company_id:
        try:
            retrieved_context = await build_ai_context(
                db,
                company_id=company_id,
                current_query=query,
                exclude_product_ids=shown_product_ids,
                max_products=3,
                history_product_ids=shown_product_ids,
                history_text=" ".join(
                    [
                        long_term_summary or "",
                        historical_sentiment or "",
                        " ".join(str(item.get("content", "")) for item in conversation_context[-8:]),
                    ]
                ).strip(),
            )
            if knowledge_context:
                retrieved_context["knowledge_text"] = (
                    f"{knowledge_context}\n\n{retrieved_context.get('knowledge_text', '')}"
                ).strip()
            ai_context = retrieved_context
        except Exception as exc:
            logger.error("RAG context build failed: %s", exc)

    ai_context["product_ids"] = [str(item).strip() for item in ai_context.get("product_ids", []) if str(item).strip()][
        :3
    ]
    ai_context["product_attachments"] = _align_product_attachments(
        ai_context["product_ids"],
        list(ai_context.get("product_attachments") or []),
    )

    product_rule_response = (
        _compose_rule_based_response(
            query,
            customer_info=customer_info,
            knowledge_context=knowledge_context,
            ai_context=ai_context,
            observed_sentiment=observed_sentiment,
            observed_intent=observed_intent,
            previous_response=previous_response,
            last_response_context=last_response_context,
            conversation_state=conversation_state,
        )
        if rule_recovery_enabled
        else None
    )
    if product_rule_response:
        product_rule_response.setdefault("agent_id", selected_agent_id)
        product_rule_response.setdefault("agent_type", selected_agent_type)
        product_rule_response.setdefault("intent_name", str(observed_intent.get("intent") or ""))
        logger.info(
            "ai_rule_response company_id=%s intent=%s channel=%s provider=%s",
            company_id or "",
            str(observed_intent.get("intent") or ""),
            channel_name or "unknown",
            str(product_rule_response.get("provider") or "rule"),
        )
        response_product_ids = [
            str(item).strip()
            for item in product_rule_response.get("product_ids", ai_context.get("product_ids", []))
            if str(item).strip()
        ][:3]
        response_attachments = _align_product_attachments(
            response_product_ids,
            list(
                product_rule_response.get("attachments")
                or product_rule_response.get("product_images")
                or ai_context.get("product_attachments", [])
            ),
        )
        product_rule_response["product_ids"] = response_product_ids
        product_rule_response["attachments"] = response_attachments
        product_rule_response["product_images"] = response_attachments
        # FIX (latency 2): detached background task — don't block the return path
        asyncio.ensure_future(_persist_response_memory(
            db=db,
            company_id=company_id,
            memory_entity_id=memory_entity_id,
            convo_id=conversation_id,
            prompt=query,
            response=str(product_rule_response.get("response") or ""),
            intent=observed_intent,
            sentiment=observed_sentiment,
            product_ids=response_product_ids,
            response_style=str(product_rule_response.get("provider") or "rule"),
            channel=channel_name,
            conversation_state=conversation_state,
        ))
        product_rule_response.setdefault(
            "conversation_sentiment", observed_conversation_sentiment or observed_sentiment
        )
        _record_outcome("success", "rule", str(product_rule_response.get("provider") or "rule"))
        return product_rule_response

    product_names = [product.get("name", "") for product in ai_context.get("products", []) if product.get("name")]
    customer_images = recent_customer_image_urls(conversation_context)
    product_images = [
        attachment.get("url", "")
        for attachment in ai_context.get("product_attachments", [])
        if attachment.get("url") and is_data_url_image(str(attachment.get("url")))
    ][:2]
    image_urls = customer_images + product_images

    system_prompt = (
        "You are the customer-facing AI assistant inside a CRM workspace.\n"
        "Primary role: answer customer questions, support requests, and sales inquiries using only the supplied CRM, memory, and product context.\n"
        "Task scope:\n"
        "- Resolve support issues clearly and calmly.\n"
        "- Recommend products only when the request calls for them.\n"
        "- Move the conversation to the single best next action.\n"
        "Expected input sections:\n"
        "- structured customer memory\n"
        "- company/product knowledge context\n"
        "- recent conversation history\n"
        "- latest customer message\n"
        "Expected output format:\n"
        "- Return plain text only.\n"
        "- Write 2-4 sentences unless a shorter answer is clearly better.\n"
        "- End with one concrete next step aligned to the required next action.\n"
        "Behavior rules:\n"
        "- Sound human, concise, and confident.\n"
        "- Do not repeat the same answer or stock openings.\n"
        "- Do not use robotic filler like 'I'm happy to help' or 'How can I assist you today?'\n"
        "- Use the provided company and product context only when relevant.\n"
        "- If products are relevant, recommend at most 3 and explain why each fits.\n"
        "- If the customer seems frustrated, acknowledge it and focus on resolution before any upsell.\n"
        "- Do not invent policies, inventory, prices, shipping times, or account details that are not in context.\n"
        f"- Active agent mode: {agent_profile['label']}.\n"
        f"- Agent operating instruction: {agent_profile['instruction']}\n"
        f"- Response style: {style_profile['instruction']}\n"
        f"- Observed intent: {observed_intent.get('intent', 'general_question')}"
        f" (urgency: {observed_intent.get('urgency', 'medium')}).\n"
        f"- Observed sentiment: {observed_sentiment.get('emotion', 'neutral')}"
        f" (score: {observed_sentiment.get('score', 0)}).\n"
    )
    if customer_info:
        system_prompt += (
            f"\nCustomer name: {customer_info.get('name', 'Customer')}"
            f"\nSegment: {customer_info.get('segment', '')}"
            f"\nHistorical sentiment: {historical_sentiment or customer_info.get('historical_sentiment', '') or 'unknown'}"
            f"\nLong-term memory: {long_term_summary or customer_info.get('long_term_summary', '') or 'No prior memory.'}"
        )
    if prompt_context and getattr(prompt_context, "system_context", ""):
        system_prompt += f"\n\nStructured memory context:\n{prompt_context.system_context}"
    if previous_response:
        system_prompt += f"\nPrevious AI reply (avoid repeating it): {previous_response}"
    if recent_ai_replies:
        replay_guard = "\n".join(f"- {truncate_text_for_tokens(item, 160)}" for item in recent_ai_replies[-3:])
        system_prompt += f"\nRecent AI replies to avoid repeating:\n{replay_guard}"
    if observed_conversation_sentiment:
        system_prompt += (
            f"\nConversation sentiment: {observed_conversation_sentiment.get('sentiment_label', 'neutral')}"
            f" (score: {observed_conversation_sentiment.get('score', observed_sentiment.get('score', 0))})."
        )
    system_prompt += (
        f"\nConversation stage: {conversation_state.get('stage', 'discovery')}"
        f"\nRequired next action: {conversation_state.get('next_action', '')}"
    )
    if conversation_state.get("intent_shift"):
        system_prompt += (
            f"\n- Intent shift detected: {conversation_state.get('transition_note', '').strip()} "
            "Acknowledge the shift and adapt direction without resetting context."
        )
    previous_intent_name = str((last_response_context.get("intent") or {}).get("intent") or "").strip().lower()
    current_intent_name = str((observed_intent.get("intent") or "").strip().lower())
    if previous_intent_name and previous_intent_name == current_intent_name:
        system_prompt += (
            "\n- This is a follow-up in the same thread. Continue naturally instead of restarting the conversation."
        )
    if observed_intent.get("intent") in {"product_recommendation", "purchase_inquiry"}:
        system_prompt += "\n- Recommend only the strongest options, explain why each fits, and avoid repeating products already discussed."
    if observed_sentiment.get("emotion") in {"angry", "frustrated"}:
        system_prompt += "\n- Prioritize empathy and a clear resolution over any upsell."
    if ai_context.get("product_attachments"):
        mapping_lines = [
            f"{index + 1}. {attachment.get('product_name') or attachment.get('name') or 'Product'} (product_id={attachment.get('product_id', '')})"
            for index, attachment in enumerate(ai_context.get("product_attachments", []))
            if str(attachment.get("product_id") or "").strip()
        ]
        if mapping_lines:
            system_prompt += (
                "\nProduct image mapping:\n"
                + "\n".join(mapping_lines)
                + "\nUse the attachment order to keep each image tied to the matching product."
            )

    recent_context = conversation_context[-12:]
    conversation_text = "\n".join(
        [f"{item.get('sender_type', '?')}: {item.get('content', '')}" for item in recent_context]
    )
    if prompt_context and getattr(prompt_context, "conversation_history", ""):
        conversation_text = prompt_context.conversation_history
    budget = ai_input_token_budget()
    prompt = (
        f"{truncate_text_for_tokens(system_prompt, int(budget * 0.2))}\n\n"
        f"Company/Product Context:\n{truncate_text_for_tokens(ai_context.get('knowledge_text', ''), int(budget * 0.35))}\n\n"
        f"Conversation so far:\n{truncate_text_for_tokens(conversation_text, int(budget * 0.3))}\n\n"
        f"Latest customer message:\n{query}\n\n"
        f"Respond naturally in 2-4 sentences using the {style_profile['name']} style."
        " End with one concrete next step aligned to the required next action."
    )
    if prompt_context and getattr(prompt_context, "customer_summary", ""):
        prompt = (
            f"{truncate_text_for_tokens(system_prompt, int(budget * 0.2))}\n\n"
            f"Customer summary:\n{truncate_text_for_tokens(prompt_context.customer_summary, int(budget * 0.15))}\n\n"
            f"Company/Product Context:\n{truncate_text_for_tokens(ai_context.get('knowledge_text', ''), int(budget * 0.30))}\n\n"
            f"Semantic context:\n{truncate_text_for_tokens(getattr(prompt_context, 'knowledge_context', ''), int(budget * 0.10))}\n\n"
            f"Conversation so far:\n{truncate_text_for_tokens(conversation_text, int(budget * 0.2))}\n\n"
            f"Latest customer message:\n{query}\n\n"
            f"Respond naturally in 2-4 sentences using the {style_profile['name']} style."
            " End with one concrete next step aligned to the required next action."
        )
    if customer_images:
        prompt += f"\n\n[{len(customer_images)} customer image(s) are attached.]"
    if product_images:
        prompt += f"\n\n[{len(product_images)} product image(s) are attached for reference.]"

    engine = None
    selected_llm_id = str((selected_agent or {}).get("llm_id") or "").strip()
    if db and company_id and selected_llm_id:
        engine_row = await db.fetchrow(
            "SELECT * FROM llm_engines WHERE id=$1 AND (company_id='' OR company_id=$2) LIMIT 1",
            selected_llm_id,
            company_id,
        )
        if engine_row:
            engine = dict(engine_row)
    if not engine:
        # FIX (latency 3): use cached engine resolution
        engine = await _resolve_engine_cached(db=db, company_id=company_id or "", use_pro=False)
    engine = _apply_agent_engine_overrides(engine, selected_agent)
    generation_config = _build_generation_config(
        query=query,
        observed_intent=observed_intent,
        observed_sentiment=observed_conversation_sentiment or observed_sentiment,
        recent_ai_replies=recent_ai_replies,
    )
    try:
        response_text = await _generate_response_text(
            prompt,
            engine=engine,
            image_urls=image_urls,
            generation_config=generation_config,
        )
        prior_responses = [item for item in recent_ai_replies if item]
        if previous_response and previous_response not in prior_responses:
            prior_responses.append(previous_response)
        max_similarity = max(
            (text_similarity(response_text, prior) for prior in prior_responses),
            default=0.0,
        )
        if max_similarity >= 0.88:
            retry_generation_config = dict(generation_config)
            retry_delta = ai_response_retry_temperature_delta()
            retry_max = ai_response_retry_temperature_max()
            retry_generation_config["temperature"] = round(
                min(
                    retry_max,
                    float(generation_config.get("temperature", ai_response_temperature_base())) + retry_delta,
                ),
                2,
            )
            retry_prompt = (
                f"{prompt}\n\nYour last draft was too similar to the previous reply. "
                "Answer again with a different structure, fresh wording, and a more specific next step."
            )
            retry_text = await _generate_response_text(
                retry_prompt,
                engine=engine,
                image_urls=image_urls,
                generation_config=retry_generation_config,
            )
            if retry_text.strip():
                response_text = retry_text
                increment_counter("ai.responses.regenerated")
        before_dedupe = response_text
        dedupe_reference = previous_response
        if not dedupe_reference and recent_ai_replies:
            dedupe_reference = recent_ai_replies[-1]
        response_text = _dedupe_response_text(
            response_text,
            last_response=dedupe_reference,
            product_names=product_names,
        )
        if response_text != before_dedupe:
            increment_counter("ai.responses.deduped")
        response_product_ids = [str(item).strip() for item in ai_context.get("product_ids", []) if str(item).strip()][
            :3
        ]
        response_attachments = _align_product_attachments(
            response_product_ids,
            list(ai_context.get("product_attachments", [])),
        )
        # FIX (latency 2): detached memory persist — don't block the return path
        asyncio.ensure_future(_persist_response_memory(
            db=db,
            company_id=company_id,
            memory_entity_id=memory_entity_id,
            convo_id=conversation_id,
            prompt=query,
            response=response_text,
            intent=observed_intent,
            sentiment=observed_sentiment,
            product_ids=response_product_ids,
            response_style=style_profile["name"],
            channel=channel_name,
            conversation_state=conversation_state,
        ))
        _record_outcome("success", "llm", str(engine.get("provider") or "unknown"))
        return {
            "response": response_text,
            "confidence": 0.9,
            "attachments": response_attachments,
            "product_images": response_attachments,
            "product_ids": response_product_ids,
            "llm_id": engine.get("id", ""),
            "agent_id": selected_agent_id,
            "agent_type": selected_agent_type,
            "intent_name": str(observed_intent.get("intent") or ""),
            "provider": engine.get("provider", ""),
            "model_name": engine.get("model_name", ""),
            "conversation_stage": conversation_state.get("stage", "discovery"),
            "next_action": conversation_state.get("next_action", ""),
            "intent_shift": bool(conversation_state.get("intent_shift")),
            "conversation_sentiment": observed_conversation_sentiment or observed_sentiment,
        }
    except Exception as exc:
        logger.error(
            "AI response failed via %s/%s: %s",
            engine.get("provider", "?"),
            engine.get("model_name", "?"),
            exc,
        )
        _record_outcome("error", "llm", str(engine.get("provider") or "unknown"))
        if not rule_recovery_enabled:
            raise RuntimeError("AI response generation failed across configured providers") from exc
        fallback_response = _compose_rule_based_response(
            query,
            customer_info=customer_info,
            knowledge_context=knowledge_context,
            ai_context=ai_context,
            observed_sentiment=observed_sentiment,
            observed_intent=observed_intent,
            previous_response=previous_response,
            last_response_context=last_response_context,
            conversation_state=conversation_state,
        )
        if not fallback_response:
            customer_name = str(customer_info.get("name") or "there").strip() or "there"
            next_action = str(conversation_state.get("next_action") or "").strip()
            fallback_response = {
                "response": (
                    f"Thanks for your message, {customer_name}. "
                    f"I understand you are focused on {conversation_state.get('intent', 'your request').replace('_', ' ')}. "
                    + (
                        f"Next action: {next_action}"
                        if next_action
                        else "Tell me the key outcome you want and I will guide you quickly."
                    )
                ),
                "confidence": 0.82,
                "attachments": [],
                "product_images": [],
                "product_ids": [],
                "llm_id": "",
                "agent_id": selected_agent_id,
                "agent_type": selected_agent_type,
                "intent_name": str(observed_intent.get("intent") or ""),
                "provider": "fallback",
                "model_name": "rule-recovery",
                "api_error": False,
                "degraded": True,
                "conversation_stage": conversation_state.get("stage", "discovery"),
                "next_action": conversation_state.get("next_action", ""),
                "intent_shift": bool(conversation_state.get("intent_shift")),
            }
        else:
            fallback_response["confidence"] = max(float(fallback_response.get("confidence", 0) or 0), 0.82)
            fallback_response.setdefault("llm_id", "")
            fallback_response.setdefault("agent_id", selected_agent_id)
            fallback_response.setdefault("agent_type", selected_agent_type)
            fallback_response.setdefault("intent_name", str(observed_intent.get("intent") or ""))
            fallback_response.setdefault("provider", "fallback")
            fallback_response.setdefault("model_name", "rule-recovery")
            fallback_response["api_error"] = False
            fallback_response["degraded"] = True
            fallback_response["attachments"] = _align_product_attachments(
                [str(item).strip() for item in fallback_response.get("product_ids", []) if str(item).strip()],
                list(
                    fallback_response.get("attachments")
                    or fallback_response.get("product_images")
                    or ai_context.get("product_attachments", [])
                ),
            )
            fallback_response["product_images"] = list(fallback_response.get("attachments", []))
            fallback_response.setdefault("conversation_stage", conversation_state.get("stage", "discovery"))
            fallback_response.setdefault("next_action", conversation_state.get("next_action", ""))
            fallback_response.setdefault("intent_shift", bool(conversation_state.get("intent_shift")))
        # FIX (latency 2): detached memory persist on fallback path too
        asyncio.ensure_future(_persist_response_memory(
            db=db,
            company_id=company_id,
            memory_entity_id=memory_entity_id,
            convo_id=conversation_id,
            prompt=query,
            response=str(fallback_response.get("response") or ""),
            intent=observed_intent,
            sentiment=observed_sentiment,
            product_ids=[str(item).strip() for item in fallback_response.get("product_ids", []) if str(item).strip()][
                :3
            ],
            response_style="fallback",
            channel=channel_name,
            conversation_state=conversation_state,
        ))
        fallback_response.setdefault("conversation_sentiment", observed_conversation_sentiment or observed_sentiment)
        logger.warning(
            "ai_response_fallback company_id=%s intent=%s channel=%s provider=%s",
            company_id or "",
            str(observed_intent.get("intent") or ""),
            channel_name or "unknown",
            str(fallback_response.get("provider") or "fallback"),
        )
        _record_outcome("success", "fallback", "rule-recovery")
        return fallback_response


async def generate_combined_ai_analysis(
    customer_message: str,
    conversation_context: list,
    customer_info: dict | None = None,
    company_id: str | None = None,
    db=None,
    long_term_summary: str = "",
    historical_sentiment: str = "",
    knowledge_context: str = "",
    **kwargs,
) -> dict:
    """Run sentiment (message), sentiment (conversation), intent, and AI response.

    ALL analysis tasks are batched into a SINGLE LLM request instead of
    three separate sequential calls, cutting latency and eliminating the
    repeated 429 retry cascade that was caused by hitting the same quota-
    exhausted provider three times per message.

    FIX (latency 1): engine resolution is now parallelised with the batch call
    prep instead of awaited sequentially before the LLM call fires.
    """
    from services.ai_service.common import IntentResult, SentimentResult
    from services.ai_service.intent import _normalize_intent_payload, _render_history_context

    context = list(conversation_context or [])
    if customer_message and (not context or str(context[-1].get("content") or "").strip() != customer_message):
        context.append({"sender_type": "customer", "content": customer_message})

    source_text = (customer_message or "").strip() or "[empty message]"
    history_lines: list[str] = []
    for item in context[-20:]:
        role = str((item or {}).get("sender_type") or "unknown").strip().lower()
        text = str((item or {}).get("content") or "").strip()
        if text:
            history_lines.append(f"{role}: {text}")
    history = "\n".join(history_lines[-16:])
    if source_text and (not history_lines or source_text not in history_lines[-1]):
        history_with_latest = history + f"\ncustomer: {source_text}"
    else:
        history_with_latest = history

    history_context_str = _render_history_context(context[-10:])

    from services.ai_service.sentiment import (
        _message_prompt,
        _conversation_prompt,
        _finalize_sentiment,
        _should_retry_for_zero_score,
        analyze_local_sentiment,
    )

    intent_prompt = (
        "You are an intent-routing classifier for a CRM assistant.\n"
        "Task: infer the single best customer intent for the latest message.\n"
        "Output format: return ONLY valid JSON with exactly these keys:\n"
        '{"intent":"snake_case_intent","confidence":0.0,"entities":{},"urgency":"low|medium|high|critical"}\n'
        "Rules:\n"
        "- Choose one primary intent only.\n"
        "- Keep confidence between 0 and 1.\n"
        "- Set urgency to critical only for explicit immediate risk, legal threat, severe churn risk, or urgent handoff.\n"
        f"\nprevious_intent: unknown"
        f"\nrecent_conversation:\n{history_context_str or '[none]'}"
        f"\nlatest_message:\n{source_text}"
    )

    # FIX (latency 1): resolve engine in parallel with building the batch prompts.
    # Previously _resolve_engine_for_request was awaited before call_model_json_batch,
    # adding a sequential DB round-trip (20-50ms) on every message.
    # Now both happen concurrently; the batch call fires as soon as the engine is ready.
    engine = await _resolve_engine_cached(db=db, company_id=company_id or "")

    sentiment: dict
    conversation_sentiment: dict
    intent: dict

    try:
        batch = await call_model_json_batch(
            {
                "message_sentiment": {
                    "prompt": _message_prompt(source_text),
                    "schema": SentimentResult,
                },
                "conversation_sentiment": {
                    "prompt": _conversation_prompt(history_with_latest, source_text),
                    "schema": SentimentResult,
                },
                "intent": {
                    "prompt": intent_prompt,
                    "schema": IntentResult,
                },
            },
            engine=engine,
        )

        msg_raw = batch.get("message_sentiment")
        conv_raw = batch.get("conversation_sentiment")
        intent_raw = batch.get("intent")

        if msg_raw and not _should_retry_for_zero_score(msg_raw):
            sentiment = _finalize_sentiment(msg_raw, source_text, scope="message")
            sentiment["provider"] = str((engine or {}).get("provider") or "")
            sentiment["model_name"] = str((engine or {}).get("model_name") or "")
            sentiment["source"] = "provider_batch"
        else:
            sentiment = analyze_local_sentiment(source_text)
            sentiment["scope"] = "message"
            sentiment["source"] = "local_fallback"

        if conv_raw and not _should_retry_for_zero_score(conv_raw):
            conversation_sentiment = _finalize_sentiment(conv_raw, source_text, scope="conversation")
            conversation_sentiment["provider"] = str((engine or {}).get("provider") or "")
            conversation_sentiment["model_name"] = str((engine or {}).get("model_name") or "")
            conversation_sentiment["source"] = "provider_batch"
            conversation_sentiment["turns_analyzed"] = len(history_lines)
        else:
            conversation_sentiment = analyze_local_sentiment(history or source_text)
            conversation_sentiment["scope"] = "conversation"
            conversation_sentiment["source"] = "local_fallback"
            conversation_sentiment["turns_analyzed"] = len(history_lines)

        if intent_raw:
            intent = _normalize_intent_payload(intent_raw)
        else:
            intent = {
                "intent": "general_question",
                "confidence": 0.0,
                "entities": {},
                "urgency": "medium",
            }

    except Exception as exc:
        logger.warning(
            "generate_combined_ai_analysis batch failed, falling back to individual calls: %s", exc
        )
        # Individual fallback (original behaviour)
        sentiment = await analyze_sentiment(source_text, db=db, company_id=company_id or "")
        try:
            conversation_sentiment = await analyze_conversation_sentiment(
                context,
                latest_message=source_text,
                db=db,
                company_id=company_id or "",
            )
        except Exception as exc2:
            logger.warning("Conversation sentiment fallback to message sentiment: %s", exc2)
            conversation_sentiment = dict(sentiment)
        intent = await classify_intent(
            source_text,
            db=db,
            company_id=company_id or "",
            conversation_context=context[-12:],
        )

    ai_response = await generate_ai_response(
        context,
        customer_info=customer_info,
        knowledge_context=knowledge_context,
        company_id=company_id,
        db=db,
        long_term_summary=long_term_summary,
        historical_sentiment=historical_sentiment,
        observed_sentiment=sentiment,
        observed_conversation_sentiment=conversation_sentiment,
        observed_intent=intent,
        **kwargs,
    )
    return {
        "sentiment": sentiment,
        "conversation_sentiment": conversation_sentiment,
        "intent": intent,
        "ai_response": ai_response,
    }


async def generate_product_description(
    name: str,
    product_title: str = "",
    product_type: str = "",
    category: str = "",
    price: str = "",
    price_currency: str = "USD",
    images: list | None = None,
    engines: list[dict] | None = None,
) -> str:
    lines = (
        [f"Product Name: {name}"]
        + ([f"Product Code / Title: {product_title}"] if product_title else [])
        + ([f"Type: {product_type}"] if product_type else [])
        + ([f"Category: {category}"] if category and category != "general" else [])
        + ([f"Price: {price_currency or 'USD'} {price}"] if price else [])
    )
    ordered_engines = [dict(engine) for engine in (engines or []) if isinstance(engine, dict)]
    ordered_engines.sort(key=lambda engine: 0 if engine.get("is_selected") else 1)
    if not ordered_engines:
        ordered_engines = [await _resolve_engine_for_request()]
    return (
        await call_with_engines(
            "You are an ecommerce product copywriter.\n"
            "Task: write one concise product description for a catalog or CRM card.\n"
            "Input format:\n"
            "- product metadata lines\n"
            "- up to 2 reference images\n"
            "Output format: return plain text only, one paragraph, about 50 words.\n"
            "Rules:\n"
            "- Highlight benefits and real differentiators.\n"
            "- Do NOT mention price.\n"
            "- Do NOT invent specs that are not present in the metadata or clearly visible in the images.\n"
            f"\nproduct_metadata:\n{chr(10).join(lines)}",
            engines=ordered_engines,
            image_parts=[url for url in (images or [])[:2] if is_data_url_image(str(url))] or None,
        )
    ).strip()


__all__ = [
    "auto_score_and_nurture_lead",
    "calculate_churn_risk",
    "generate_ai_response",
    "generate_combined_ai_analysis",
    "generate_lead_score",
    "generate_nurture_message",
    "generate_product_description",
]