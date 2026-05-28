"""run_turn() — single entry point used by both reactive and proactive paths.

Pipeline:
    1. Route       — pick which sources to query (rule scorer).
    2. Retrieve    — async-gather selected retrievers with per-source timeouts.
    3. Compress    — dedup / merge / cross-source elim / KB summary swap-in.
    4. Budget      — pick a tier, trim history+chunks to fit.
    5. Prompt      — assemble system+context+history+user with injection guard.
    6. Generate    — Gemini Flash Lite via the gateway (retry on transient).
    7. Validate    — schema / secret / grounding / link / tone.
    8. Persist     — write the turn row, refresh rolling summary if needed.

Per-source timeouts and the validation retry are configured via constants
near the top so a junior dev can adjust them in one place.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from services.conversation_engine import budget as budget_module
from services.conversation_engine import compression as compression_module
from services.conversation_engine import context_router
from services.conversation_engine import memory as memory_module
from services.conversation_engine import prompt_builder
from services.conversation_engine import validator
from services.conversation_engine.llm_gateway import LlmGateway
from services.conversation_engine.retrieval import (
    CompanyDataRetriever,
    KnowledgeBaseRetriever,
    OrderRelatedProductRetriever,
    ProductRetriever,
    SourceRetriever,
    TemplatesAndFaqsRetriever,
)
from services.conversation_engine.schemas import (
    ContextChunk,
    ProductLink,
    RetrievalResult,
    SourceType,
    TokenUsage,
    TurnRequest,
    TurnResult,
)


logger = logging.getLogger(__name__)

_PURCHASE_INTENT_RE = re.compile(
    r"\b(buy|purchase|order|price|cost|how much|available|in stock|want|need|looking for|"
    r"recommend|suggest|show me|do you have|do you sell|catalog|what products|affordable|budget|cheap|best|"
    r"can i see|view|see this|see the|details|more about|tell me more|this ring|that ring|"
    r"this product|that product|the ring|the product|proceed|confirm|place an order|checkout)\b",
    re.IGNORECASE,
)

# Strip source labels the LLM occasionally leaks despite prompt instructions.
_SOURCE_LABEL_RE = re.compile(
    r"\s*\(Source:\s*(?:Company Data|Product Database|FAQs?|Knowledge Base|Response Style)\)",
    re.IGNORECASE,
)
_SECTION_HEADER_RE = re.compile(
    r"#{1,3}\s*(?:Source\s*:\s*)?(?:Company Data|Product Database|FAQs?|Knowledge Base)[^\n]*\n?",
    re.IGNORECASE,
)


def _strip_source_labels(text: str) -> str:
    """Remove any internal source labels the LLM leaked into the customer response."""
    text = _SOURCE_LABEL_RE.sub("", text)
    text = _SECTION_HEADER_RE.sub("", text)
    return re.sub(r" {2,}", " ", text).strip()


# Positional image-location phrases the LLM sometimes writes despite prompt
# instructions (e.g. "see the image below", "as shown above"). They are wrong
# because product images are sent as a *separate earlier* message, so the AI
# text response must not reference their screen position.
_POSITIONAL_IMAGE_RE = re.compile(
    r"[\(\[]?"
    r"(?:you can |please )?"
    r"(?:see|view|find|check out|have a look at|look at|refer to)?\s*"
    r"(?:the\s+)?(?:product\s+)?(?:image|photo|picture|pic)\s*"
    r"(?:is\s+)?(?:shown\s+)?(?:displayed\s+)?"
    r"(?:right\s+)?(?:just\s+)?"
    r"(?:above|below|down below|underneath|up above|right above|right below|attached above|attached below)"
    r"[\)\]]?"
    r"[.,!]?",
    re.IGNORECASE,
)
# Also strip standalone positional references like "(see below)", "as shown above"
_POSITIONAL_REF_RE = re.compile(
    r"\(?(?:see|as shown|as seen|shown|displayed|found|available|pictured|enclosed)\s+"
    r"(?:above|below|down below|right below|right above|here above|here below)\)?"
    r"[.,!]?",
    re.IGNORECASE,
)


def _strip_positional_image_refs(text: str) -> str:
    """Remove spatial image-location phrases that are wrong when images are sent first."""
    text = _POSITIONAL_IMAGE_RE.sub("", text)
    text = _POSITIONAL_REF_RE.sub("", text)
    return re.sub(r"  +", " ", text).strip()


_PER_SOURCE_TIMEOUTS: dict[SourceType, float] = {
    "company_data": 1.0,
    "product": 1.5,
    "template": 0.4,
    "faq": 0.4,
    "knowledge_base": 1.0,
}
_VALIDATION_RETRY_DIRECTIVE_TEMPLATE = (
    "\n\nYour previous draft mentioned: {offences}. "
    "Do not mention any product or price that is not in the provided context."
)
_STATIC_FALLBACK = (
    "I'm not sure I have that information at hand. Would you like me to connect "
    "you with our team for a complete answer?"
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class Orchestrator:
    def __init__(
        self,
        *,
        retrievers: dict[SourceType, SourceRetriever] | None = None,
        gateway: LlmGateway | None = None,
    ) -> None:
        self._retrievers: dict[SourceType, SourceRetriever] = retrievers or {
            "company_data": CompanyDataRetriever(),
            "product": ProductRetriever(),
            "faq": TemplatesAndFaqsRetriever(),
            "knowledge_base": KnowledgeBaseRetriever(),
        }
        self._gateway = gateway or LlmGateway()

    async def run_proactive_turn(
        self,
        db,
        *,
        company_id: str,
        session_id: str,
        customer_id: str = "",
        order_id: str = "",
        workflow_kind: str = "post_delivery_feedback",
    ) -> TurnResult:
        """Generate a system-initiated outbound message.

        The engine has no real "user message" to route on here — proactive
        turns are triggered by lifecycle events, not user input. We synthesise
        a short directive based on workflow_kind, pass it as the user message,
        and let the rest of the pipeline (routing, retrieval, compression,
        validation) do its job. The validator's grounding check still applies,
        so a hallucinated product name in an upsell turn falls back to the
        static safe message rather than going out unfiltered.
        """
        if workflow_kind == "post_delivery_feedback":
            directive = (
                "Compose a brief, warm follow-up message asking the customer "
                "whether their recent order arrived in good condition. Invite "
                "them to reply if anything is wrong. One short paragraph; no "
                "marketing copy."
            )
        elif workflow_kind == "upsell":
            directive = (
                "Compose a brief, helpful suggestion of one complementary "
                "product or add-on that pairs with what the customer recently "
                "purchased. Only mention products that appear in the retrieved "
                "context. Do not pressure the customer. One short paragraph."
            )
        else:
            directive = "Compose a brief follow-up message."
        request = TurnRequest(
            session_id=session_id,
            company_id=company_id,
            customer_id=customer_id,
            user_message=directive,
            mode="proactive",
            workflow_kind=workflow_kind,  # type: ignore[arg-type]
            order_id=order_id,
        )
        return await self.run_turn(db, request)

    async def run_turn(self, db, request: TurnRequest) -> TurnResult:
        started_at = time.monotonic()
        decision = context_router.score(request.user_message)

        retrieval_results = await self._retrieve(db, request, decision.sources)
        chunks: list[ContextChunk] = []
        for result in retrieval_results:
            chunks.extend(result.chunks)

        compressed = compression_module.compress(chunks)
        confidence = validator.compute_confidence(compressed)
        bucket = validator.confidence_bucket(confidence)

        history_dialogue = await self._history_dialogue(db, request)
        tier = budget_module.select_tier(request.user_message, compressed)
        target = budget_module.target_tokens(tier)
        style_prompt = _extract_style_prompt(compressed)
        active_template = _extract_template_name(compressed)

        # Estimate the system+formatting overhead so the trimmer has a real
        # budget to work against.
        skeleton_prompt = prompt_builder.build_prompt(
            user_message=request.user_message,
            chunks=[],
            history_turns=[],
            style_prompt=style_prompt,
            confidence_bucket=bucket,
        )
        system_tokens = budget_module.count_tokens(skeleton_prompt)
        trimmed_chunks, trimmed_history = budget_module.trim_to_budget(
            compressed,
            history_dialogue,
            system_tokens=system_tokens,
            target_total_tokens=target,
        )

        try:
            prompt = prompt_builder.build_prompt(
                user_message=request.user_message,
                chunks=trimmed_chunks,
                history_turns=trimmed_history,
                style_prompt=style_prompt,
                confidence_bucket=bucket,
            )
        except prompt_builder.InjectionDetected as exc:
            logger.warning("injection_detected company_id=%s reason=%s", request.company_id, exc)
            return await self._fallback_turn(
                db, request, sources_used=[r.source_type for r in retrieval_results if r.chunks],
                active_template=active_template, confidence=confidence,
                reason="injection_detected",
            )

        result = await self._gateway.generate(prompt)
        answer = _strip_positional_image_refs(_strip_source_labels((result.text or "").strip()))
        product_links = _extract_product_links(answer, trimmed_chunks)

        report = validator.validate(
            answer=answer,
            product_links=product_links,
            chunks=trimmed_chunks,
            style_prompt=style_prompt,
        )
        if not report.ok:
            retry_prompt = prompt + _VALIDATION_RETRY_DIRECTIVE_TEMPLATE.format(
                offences=", ".join(report.offences[:5])
            )
            retry_result = await self._gateway.generate(retry_prompt)
            retry_answer = _strip_positional_image_refs(_strip_source_labels((retry_result.text or "").strip()))
            retry_links = _extract_product_links(retry_answer, trimmed_chunks)
            retry_report = validator.validate(
                answer=retry_answer,
                product_links=retry_links,
                chunks=trimmed_chunks,
                style_prompt=style_prompt,
            )
            if retry_report.ok:
                answer = retry_answer
                product_links = retry_links
            else:
                logger.warning(
                    "validation_failed_twice company_id=%s offences=%s",
                    request.company_id,
                    report.offences[:5] + retry_report.offences[:5],
                )
                # Graceful degrade: if the first answer is non-empty and all
                # offences are benign (grounding / price — not secrets,
                # profanity, or empty), return it rather than the useless
                # static fallback. Strip product links so we don't surface
                # cards for products that weren't retrieved this turn.
                _fatal_prefixes = ("secret_leak:", "profanity:", "shouting", "empty_answer")
                first_is_fatal = any(o.startswith(_fatal_prefixes) for o in report.offences)
                if not answer or first_is_fatal:
                    return await self._fallback_turn(
                        db, request, sources_used=[r.source_type for r in retrieval_results if r.chunks],
                        active_template=active_template, confidence=confidence,
                        reason="validation_failed",
                    )
                logger.info(
                    "validation_graceful_degrade company_id=%s using_first_attempt offences=%s",
                    request.company_id, report.offences[:5],
                )
                # Only strip product links if the offence is specifically about the
                # link itself being malformed or pointing to an unretrieved product.
                # Grounding failures in the answer text don't invalidate a valid link.
                _link_offence_prefixes = ("link_to_unretrieved_product:", "link_malformed:")
                if any(o.startswith(_link_offence_prefixes) for o in report.offences):
                    product_links = []

        sources_used: list[SourceType] = [r.source_type for r in retrieval_results if r.chunks]
        tokens = TokenUsage(
            prompt=budget_module.count_tokens(prompt),
            completion=budget_module.count_tokens(answer),
            total=budget_module.count_tokens(prompt) + budget_module.count_tokens(answer),
        )
        turn_index = await memory_module.next_turn_index(
            db, company_id=request.company_id, session_id=request.session_id
        )
        turn_id = await memory_module.persist_turn(
            db,
            company_id=request.company_id,
            session_id=request.session_id,
            customer_id=request.customer_id,
            turn_index=turn_index,
            user_message=request.user_message,
            ai_response=answer,
            sources_used=sources_used,
            product_links=product_links,
            confidence=confidence,
            active_template=active_template,
            token_usage=tokens,
            mode=request.mode,
        )

        if memory_module.should_summarise(turn_index):
            await self._refresh_rolling_summary(db, request, turn_index)

        logger.info(
            "ai_turn company_id=%s session_id=%s turn_id=%s tier=%s sources=%s confidence=%.3f latency_ms=%d",
            request.company_id,
            request.session_id,
            turn_id,
            tier,
            sources_used,
            confidence,
            int((time.monotonic() - started_at) * 1000),
        )

        return TurnResult(
            answer=answer,
            session_id=request.session_id,
            turn_id=turn_id,
            sources_used=sources_used,
            product_links=product_links,
            tokens_used=tokens,
            active_template=active_template,
            confidence=confidence,
        )

    def _retriever_for(self, source: SourceType, request: TurnRequest) -> SourceRetriever | None:
        if source == "product":
            # Proactive upsell: pull products related to the purchased item.
            if (
                request.mode == "proactive"
                and request.workflow_kind == "upsell"
                and request.order_id
            ):
                return OrderRelatedProductRetriever(order_id=request.order_id)
            # Reactive: create a fresh retriever per request so it carries the
            # customer_id and session_id used to build tracked product URLs.
            return ProductRetriever(
                customer_id=request.customer_id,
                session_id=request.session_id,
            )
        return self._retrievers.get(source)

    async def _retrieve(
        self,
        db,
        request: TurnRequest,
        sources: list[SourceType],
    ) -> list[RetrievalResult]:
        async def _run(source: SourceType) -> RetrievalResult:
            retriever = self._retriever_for(source, request)
            if retriever is None:
                return RetrievalResult(source_type=source, chunks=[], error="no_retriever")
            started = time.monotonic()
            try:
                chunks = await asyncio.wait_for(
                    retriever.fetch(db, company_id=request.company_id, query=request.user_message, top_k=6),
                    timeout=_PER_SOURCE_TIMEOUTS.get(source, 1.0),
                )
                latency_ms = int((time.monotonic() - started) * 1000)
                return RetrievalResult(source_type=source, chunks=chunks, latency_ms=latency_ms)
            except asyncio.TimeoutError:
                return RetrievalResult(source_type=source, chunks=[], error="timeout")
            except Exception as exc:  # noqa: BLE001
                return RetrievalResult(source_type=source, chunks=[], error=type(exc).__name__)

        # Always include company_data — it's tiny and almost always relevant.
        effective_sources: list[SourceType] = list(sources)
        if "company_data" not in effective_sources:
            effective_sources.append("company_data")
        # Faq retriever also returns the active template; include it so the
        # style_prompt makes it through even when faq scored below threshold.
        if "faq" not in effective_sources:
            effective_sources.append("faq")
        # Proactive upsell turns must consult the product source even if the
        # directive's keywords didn't trip the rule scorer's product bucket.
        if (
            request.mode == "proactive"
            and request.workflow_kind == "upsell"
            and "product" not in effective_sources
        ):
            effective_sources.append("product")
        # Purchase intent — always consult the product catalog so the LLM can
        # surface a link or availability without being asked to route there first.
        if (
            "product" not in effective_sources
            and _PURCHASE_INTENT_RE.search(request.user_message or "")
        ):
            effective_sources.append("product")

        results = await asyncio.gather(*[_run(s) for s in effective_sources], return_exceptions=False)
        return list(results)

    async def _history_dialogue(self, db, request: TurnRequest) -> list[str]:
        turns = await memory_module.fetch_recent_turns(
            db, company_id=request.company_id, session_id=request.session_id
        )
        rolling = await memory_module.fetch_rolling_summary(
            db, company_id=request.company_id, session_id=request.session_id
        )
        lines: list[str] = []
        if rolling:
            lines.append(f"[summary of earlier conversation] {rolling}")
        if turns:
            lines.extend(memory_module.history_as_dialogue(turns))
        elif request.extra_history:
            # ai_conversation_turns returned nothing (RLS not yet configured or
            # first turn). Fall back to the formatted messages the webhook pulled
            # directly from the messages table — these are always authoritative.
            lines.extend(request.extra_history)
        return lines

    async def _refresh_rolling_summary(self, db, request: TurnRequest, turn_index: int) -> None:
        # Pull the latest 20 turns, condense into a single short paragraph via
        # the gateway. This is the engine's only request-time LLM call beyond
        # the main generation, and it fires at most once per turn after the
        # 10-turn threshold.
        turns = await memory_module.fetch_recent_turns(
            db, company_id=request.company_id, session_id=request.session_id
        )
        if not turns:
            return
        dialogue = "\n".join(memory_module.history_as_dialogue(turns))
        prompt = (
            "Summarise this conversation so the assistant can continue with the "
            "key facts. Keep it under 200 words. Conversation:\n\n" + dialogue
        )
        result = await self._gateway.generate(prompt)
        await memory_module.upsert_rolling_summary(
            db,
            company_id=request.company_id,
            session_id=request.session_id,
            summary=result.text,
            covers_through_turn=turn_index,
        )

    async def _fallback_turn(
        self,
        db,
        request: TurnRequest,
        *,
        sources_used: list[SourceType],
        active_template: str,
        confidence: float,
        reason: str,
    ) -> TurnResult:
        tokens = TokenUsage(prompt=0, completion=budget_module.count_tokens(_STATIC_FALLBACK))
        tokens.total = tokens.prompt + tokens.completion
        turn_index = await memory_module.next_turn_index(
            db, company_id=request.company_id, session_id=request.session_id
        )
        turn_id = await memory_module.persist_turn(
            db,
            company_id=request.company_id,
            session_id=request.session_id,
            customer_id=request.customer_id,
            turn_index=turn_index,
            user_message=request.user_message,
            ai_response=_STATIC_FALLBACK,
            sources_used=sources_used,
            product_links=[],
            confidence=confidence,
            active_template=active_template,
            token_usage=tokens,
            mode=request.mode,
        )
        return TurnResult(
            answer=_STATIC_FALLBACK,
            session_id=request.session_id,
            turn_id=turn_id,
            sources_used=sources_used,
            product_links=[],
            tokens_used=tokens,
            active_template=active_template,
            confidence=confidence,
            error=reason,
        )


def _extract_style_prompt(chunks: list[ContextChunk]) -> str:
    for chunk in chunks:
        if chunk.source_type == "template" and chunk.content.strip():
            return chunk.content.strip()
    return ""


def _extract_template_name(chunks: list[ContextChunk]) -> str:
    for chunk in chunks:
        if chunk.source_type == "template":
            return chunk.title or (chunk.metadata or {}).get("name") or ""
    return ""


def _extract_product_links(answer: str, chunks: list[ContextChunk]) -> list[ProductLink]:
    """Find URLs in the answer and match them to retrieved products. If the
    answer didn't include any URLs but a product was retrieved this turn, we
    still surface the top product as a card so the frontend gets a visible
    "View product" affordance — the validator separately enforces that the
    target URL resolves to a retrieved product, so this can't surface a
    fabricated link.
    """
    product_chunks = [c for c in chunks if c.source_type == "product"]
    if not product_chunks:
        return []
    links: list[ProductLink] = []
    if answer:
        urls = _URL_RE.findall(answer)
        for url in urls:
            cleaned = url.rstrip(".,);:!?\"'")
            for chunk in product_chunks:
                meta = chunk.metadata or {}
                public_url = str(meta.get("public_url") or "")
                product_url = str(meta.get("links") or "")
                slug = str(meta.get("slug") or "")
                hit = (
                    (public_url and public_url in cleaned)
                    or (product_url and product_url in cleaned)
                    or (slug and f"/{slug}" in cleaned)
                )
                if hit:
                    name = str((chunk.metadata or {}).get("name") or chunk.title or "")
                    image_url = str((chunk.metadata or {}).get("image_url") or "")
                    links.append(ProductLink(product_id=chunk.source_id, url=cleaned, name=name, image_url=image_url))
                    break
    if links:
        return links
    # Fallback: pick the top-relevance product chunk that has a resolvable
    # public URL. This is what powers the product card in the chat widget when
    # the LLM declined to write the URL inline.
    for chunk in sorted(product_chunks, key=lambda c: c.relevance_score, reverse=True):
        meta = chunk.metadata or {}
        url = str(meta.get("public_url") or meta.get("links") or "")
        if url:
            name = str(meta.get("name") or chunk.title or "")
            image_url = str(meta.get("image_url") or "")
            links.append(ProductLink(product_id=chunk.source_id, url=url, name=name, image_url=image_url))
            break
    return links


# Convenience singleton — most callers want the default wiring.
default_orchestrator: Any = Orchestrator()


async def run_turn(db, request: TurnRequest) -> TurnResult:
    return await default_orchestrator.run_turn(db, request)


async def run_proactive_turn(
    db,
    *,
    company_id: str,
    session_id: str,
    customer_id: str = "",
    order_id: str = "",
    workflow_kind: str = "post_delivery_feedback",
) -> TurnResult:
    return await default_orchestrator.run_proactive_turn(
        db,
        company_id=company_id,
        session_id=session_id,
        customer_id=customer_id,
        order_id=order_id,
        workflow_kind=workflow_kind,
    )
