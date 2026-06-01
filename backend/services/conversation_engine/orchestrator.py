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
from services.conversation_engine.deterministic_answers import build_grounded_answer
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
from services.conversation_engine.sentiment import (
    analyze_local_sentiment,
    analyze_conversation_sentiment,
    build_sentiment_gate,
    should_auto_escalate,
)


logger = logging.getLogger(__name__)


_PER_SOURCE_TIMEOUTS: dict[SourceType, float] = {
    "company_data": 0.5,
    "product": 1.0,   # raised from 0.35 — vector search + image fetch routinely exceeded 350 ms
    "template": 0.2,
    "faq": 0.2,
    "knowledge_base": 0.8,  # raised from 0.4 — embedding search can exceed 400 ms under load
}
_VALIDATION_RETRY_DIRECTIVE_TEMPLATE = (
    "\n\nYour previous draft mentioned: {offences}. "
    "Do not mention any product or price that is not in the provided context."
)
_STATIC_FALLBACK = "I don't have that information right now. Would you like me to connect you with our team?"
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
                "whether their recent order arrived in good condition, how "
                "their overall experience was, and whether they are satisfied "
                "with the product. If the retrieved product context includes a "
                "related or complementary product from the customer's purchase "
                "history, suggest exactly one of those products gently. Do not "
                "pressure the customer. One short paragraph."
            )
        elif workflow_kind == "upsell":
            directive = (
                "Compose a brief, helpful suggestion of one complementary "
                "product or add-on that pairs with what the customer recently "
                "purchased. Only mention products that appear in the retrieved "
                "context. Do not pressure the customer. One short paragraph."
            )
        elif workflow_kind == "order_confirmed":
            directive = (
                "The customer's order has just been confirmed. Compose a brief, "
                "warm message acknowledging this and letting them know what to "
                "expect next. If the retrieved context contains a highly relevant "
                "complementary product, mention it gently — but only if it adds "
                "clear value. Be non-aggressive. One short paragraph."
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

        # Sentiment - pure local CPU, no DB, no LLM, sub-millisecond.
        # Runs before routing so escalation_required is available on every path.
        message_sentiment = analyze_local_sentiment(request.user_message)
        sentiment_gate = build_sentiment_gate(request.user_message, message_sentiment)
        _ = sentiment_gate
        escalation_required = should_auto_escalate(request.user_message, message_sentiment)
        # conversation_sentiment is set later (needs history). Default to message-level.
        conversation_sentiment: dict[str, Any] = dict(message_sentiment)

        logger.info(
            "guard_classification company_id=%s session_id=%s message_classified=%s path_selected=%s retrieval_triggered=%s sources=%s",
            request.company_id,
            request.session_id,
            decision.direct_intent or ("full_pipeline" if not decision.low_value else "low_value"),
            "direct_response" if decision.low_value else "full_pipeline",
            str(not decision.low_value).lower(),
            list(decision.sources or []),
        )
        if decision.low_value and decision.direct_response:
            return await self._direct_low_value_turn(
                db,
                request,
                decision,
                started_at=started_at,
                message_sentiment=message_sentiment,
                conversation_sentiment=conversation_sentiment,
                escalation_required=escalation_required,
            )

        retrieval_results, history_dialogue = await asyncio.gather(
            self._retrieve(db, request, decision.sources, low_value=decision.low_value),
            self._history_dialogue(db, request),
        )

        # Conversation sentiment - needs history, runs here after gather.
        # Pure local, no DB, no LLM.
        if history_dialogue:
            history_as_context = [
                {
                    "sender_type": "customer" if line.startswith("User:") else "ai",
                    "content": line.split(": ", 1)[1] if ": " in line else line,
                }
                for line in history_dialogue
            ]
            conversation_sentiment_result = analyze_conversation_sentiment(
                history_as_context, latest_message=request.user_message
            )
            if asyncio.iscoroutine(conversation_sentiment_result):
                conversation_sentiment_result = await conversation_sentiment_result
            conversation_sentiment = dict(conversation_sentiment_result or {})
            logger.debug(
                "conversation_sentiment_type company_id=%s session_id=%s type=%s keys=%s",
                request.company_id,
                request.session_id,
                type(conversation_sentiment).__name__,
                sorted(conversation_sentiment.keys()),
            )

        chunks: list[ContextChunk] = []
        for result in retrieval_results:
            chunks.extend(result.chunks)

        # Inject previously shown/discussed products so the validator never rejects
        # responses that reference products from earlier turns in the conversation.
        # Without this, the AI sees products in history but the validator fails because
        # those products aren't in the CURRENT turn's retrieval results.
        if request.customer_id and request.session_id and not decision.low_value:
            shown_chunks = await self._fetch_shown_product_chunks(db, request, existing_chunks=chunks)
            chunks.extend(shown_chunks)

        compressed = compression_module.compress(chunks)
        confidence = validator.compute_confidence(compressed)
        bucket = validator.confidence_bucket(confidence)

        deterministic = None
        if request.mode != "proactive":
            deterministic = build_grounded_answer(request.user_message, compressed)
        if deterministic is not None:
            answer = deterministic.answer
            product_links = deterministic.product_links
            sources_used = deterministic.sources_used
            confidence = max(confidence, deterministic.confidence)
            tokens = TokenUsage(
                prompt=0,
                completion=budget_module.count_tokens(answer),
                total=budget_module.count_tokens(answer),
            )
            turn_id, _ = await memory_module.persist_turn_atomic(
                db,
                company_id=request.company_id,
                session_id=request.session_id,
                customer_id=request.customer_id,
                user_message=request.user_message,
                ai_response=answer,
                sources_used=sources_used,
                product_links=product_links,
                confidence=confidence,
                active_template="",
                token_usage=tokens,
                mode=request.mode,
            )
            if product_links and request.customer_id:
                _shown = [pl.product_id for pl in product_links if pl.product_id]
                if _shown:
                    asyncio.create_task(
                        self._safe_store_shown_products(db, request, _shown, product_chunks=compressed),
                        name=f"shown-products-{request.session_id}",
                    )
            logger.info(
                "ai_turn_deterministic company_id=%s session_id=%s turn_id=%s sources=%s confidence=%.3f latency_ms=%d",
                request.company_id,
                request.session_id,
                turn_id,
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
                active_template="",
                confidence=confidence,
                sentiment=message_sentiment,
                conversation_sentiment=conversation_sentiment,
                escalation_required=escalation_required,
            )

        tier = budget_module.select_tier(request.user_message, compressed)
        target = budget_module.target_tokens(tier)
        style_prompt = _extract_style_prompt(compressed)
        active_template = _extract_template_name(compressed)

        system_tokens = (
            prompt_builder.STATIC_SYSTEM_TOKEN_BASELINE
            + budget_module.count_tokens(style_prompt)
        )
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
                message_sentiment=message_sentiment,
                conversation_sentiment=conversation_sentiment,
                escalation_required=escalation_required,
            )

        result = await self._gateway.generate(prompt)
        if not result.text:
            logger.warning(
                "llm_gateway_all_retries_failed company_id=%s last_error=%s",
                request.company_id,
                result.last_error,
            )
            return await self._fallback_turn(
                db, request, sources_used=[r.source_type for r in retrieval_results if r.chunks],
                active_template=active_template, confidence=confidence,
                reason="llm_gateway_failed",
                message_sentiment=message_sentiment,
                conversation_sentiment=conversation_sentiment,
                escalation_required=escalation_required,
            )
        answer = result.text.strip()
        product_links = _extract_product_links(answer, trimmed_chunks)

        safe_result = validator.safe_validate_response(
            answer=answer,
            product_links=product_links,
            chunks=trimmed_chunks,
            style_prompt=style_prompt,
            user_message=request.user_message,
        )
        answer = safe_result.answer
        product_links = safe_result.product_links
        report = safe_result.report
        logger.info(
            "safe_validation checks_passed=%s stripped_content=%s offences=%s",
            str(report.ok).lower(),
            safe_result.stripped_content,
            report.offences[:5],
        )
        if not report.ok:
            retry_prompt = prompt + _VALIDATION_RETRY_DIRECTIVE_TEMPLATE.format(
                offences=", ".join(report.offences[:5])
            )
            retry_result = await self._gateway.generate(retry_prompt)
            retry_answer = (retry_result.text or "").strip()
            retry_links = _extract_product_links(retry_answer, trimmed_chunks)
            retry_safe_result = validator.safe_validate_response(
                answer=retry_answer,
                product_links=retry_links,
                chunks=trimmed_chunks,
                style_prompt=style_prompt,
                user_message=request.user_message,
            )
            retry_answer = retry_safe_result.answer
            retry_links = retry_safe_result.product_links
            retry_report = retry_safe_result.report
            logger.info(
                "safe_validation checks_passed=%s stripped_content=%s offences=%s retry=true",
                str(retry_report.ok).lower(),
                retry_safe_result.stripped_content,
                retry_report.offences[:5],
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
                return await self._fallback_turn(
                    db, request, sources_used=[r.source_type for r in retrieval_results if r.chunks],
                    active_template=active_template, confidence=confidence,
                    reason="validation_failed",
                    message_sentiment=message_sentiment,
                    conversation_sentiment=conversation_sentiment,
                    escalation_required=escalation_required,
                )

        sources_used: list[SourceType] = [r.source_type for r in retrieval_results if r.chunks]
        tokens = TokenUsage(
            prompt=budget_module.count_tokens(prompt),
            completion=budget_module.count_tokens(answer),
            total=budget_module.count_tokens(prompt) + budget_module.count_tokens(answer),
        )
        turn_id, turn_index = await memory_module.persist_turn_atomic(
            db,
            company_id=request.company_id,
            session_id=request.session_id,
            customer_id=request.customer_id,
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
            asyncio.create_task(
                self._safe_refresh_rolling_summary(db, request, turn_index, chunks=trimmed_chunks),
                name=f"rolling-summary-{request.session_id}",
            )

        # Persist shown product IDs so future turns can avoid recommending the
        # same items and cross-sell logic can exclude already-seen products.
        if product_links and request.customer_id:
            _shown = [pl.product_id for pl in product_links if pl.product_id]
            if _shown:
                asyncio.create_task(
                    self._safe_store_shown_products(db, request, _shown, product_chunks=trimmed_chunks),
                    name=f"shown-products-{request.session_id}",
                )

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
            sentiment=message_sentiment,
            conversation_sentiment=conversation_sentiment,
            escalation_required=escalation_required,
        )

    async def _direct_low_value_turn(
        self,
        db,
        request: TurnRequest,
        decision: context_router.RoutingDecision,
        *,
        started_at: float,
        message_sentiment: dict[str, Any],
        conversation_sentiment: dict[str, Any],
        escalation_required: bool,
    ) -> TurnResult:
        answer = decision.direct_response.strip()
        confidence = 0.96
        tokens = TokenUsage(
            prompt=0,
            completion=budget_module.count_tokens(answer),
            total=budget_module.count_tokens(answer),
        )
        turn_id, _ = await memory_module.persist_turn_atomic(
            db,
            company_id=request.company_id,
            session_id=request.session_id,
            customer_id=request.customer_id,
            user_message=request.user_message,
            ai_response=answer,
            sources_used=[],
            product_links=[],
            confidence=confidence,
            active_template="",
            token_usage=tokens,
            mode=request.mode,
        )
        logger.info(
            "ai_turn_direct_response company_id=%s session_id=%s turn_id=%s intent=%s rag_called=false llm_called=false latency_ms=%d",
            request.company_id,
            request.session_id,
            turn_id,
            decision.direct_intent or "low_value",
            int((time.monotonic() - started_at) * 1000),
        )
        return TurnResult(
            answer=answer,
            session_id=request.session_id,
            turn_id=turn_id,
            sources_used=[],
            product_links=[],
            tokens_used=tokens,
            active_template="",
            confidence=confidence,
            sentiment=message_sentiment,
            conversation_sentiment=conversation_sentiment,
            escalation_required=escalation_required,
        )

    async def _fetch_shown_product_chunks(
        self,
        db,
        request: TurnRequest,
        *,
        existing_chunks: list[ContextChunk],
    ) -> list[ContextChunk]:
        """Return product chunks for products shown in previous turns but not retrieved this turn.

        This prevents the validator from rejecting responses that reference products
        discussed in earlier turns of the conversation. Without this, when a user says
        'I want to buy the one you showed me', the AI looks up conversation history and
        tries to respond about the previously-mentioned product, but the validator sees
        that product isn't in the current turn's retrieval and flags it as ungrounded.
        """
        existing_ids = {c.source_id for c in existing_chunks if c.source_type == "product"}
        try:
            from memory_engine.long_term import LongTermMemory
            shown_ids = await LongTermMemory()._fetch_shown_products(
                db, request.company_id, request.customer_id, request.session_id
            )
        except Exception:
            return []
        missing_ids = [pid for pid in (shown_ids or []) if pid not in existing_ids]
        if not missing_ids:
            return []
        # Limit to most recent 4 products to avoid context bloat
        missing_ids = missing_ids[-4:]
        from services.conversation_engine.retrieval.products import (
            _fetch_first_images,
            _product_row_to_chunk,
            _resolve_company_slug,
            _rls_fetch,
        )
        try:
            rows = await _rls_fetch(
                db,
                request.company_id,
                "SELECT id, name, product_title, category, product_type, price, "
                "price_currency, description, stock_quantity, slug, links "
                "FROM company_products "
                "WHERE company_id = $1 AND id = ANY($2::text[]) "
                "AND COALESCE(status, 'active') != 'archived'",
                request.company_id,
                missing_ids,
            )
        except Exception:
            return []
        if not rows:
            return []
        products = [dict(r) for r in rows]
        product_ids = [str(p.get("id") or "") for p in products if p.get("id")]
        company_slug, images = await asyncio.gather(
            _resolve_company_slug(db, request.company_id),
            _fetch_first_images(db, product_ids, company_id=request.company_id),
        )
        result_chunks: list[ContextChunk] = []
        for product in products:
            pid = str(product.get("id") or "")
            chunk = _product_row_to_chunk(
                product,
                score=0.5,  # lower than freshly retrieved; trimmer drops these last
                company_slug=company_slug,
                company_id=request.company_id,
                image_url=images.get(pid, ""),
                customer_id=request.customer_id,
                session_id=request.session_id,
            )
            if chunk is not None:
                result_chunks.append(chunk)
        return result_chunks

    def _retriever_for(self, source: SourceType, request: TurnRequest) -> SourceRetriever | None:
        # Product retrievers are created per-request so they carry the correct
        # customer_id and session_id for signed ref-token URLs. This ensures
        # order tracking links back to the customer without exposing internal IDs.
        if source == "product":
            if (
                request.mode == "proactive"
                and request.workflow_kind in {"post_delivery_feedback", "upsell", "order_confirmed"}
                and request.order_id
            ):
                return OrderRelatedProductRetriever(order_id=request.order_id)
            configured = self._retrievers.get("product")
            if configured is not None and not isinstance(configured, ProductRetriever):
                return configured
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
        *,
        low_value: bool = False,
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
        effective_sources: list[SourceType] = [] if low_value else list(sources)
        if not low_value and "company_data" not in effective_sources:
            effective_sources.append("company_data")
        # Faq retriever also returns the active template; include it so the
        # style_prompt makes it through even when faq scored below threshold.
        if not low_value and "faq" not in effective_sources:
            effective_sources.append("faq")
        # Proactive post-conversation turns must consult the product source even
        # if the directive's keywords didn't trip the rule scorer's product bucket.
        if (
            request.mode == "proactive"
            and request.workflow_kind in {"post_delivery_feedback", "upsell", "order_confirmed"}
            and request.order_id
            and "product" not in effective_sources
        ):
            effective_sources.append("product")

        results = await asyncio.gather(*[_run(s) for s in effective_sources], return_exceptions=False)
        return list(results)

    async def _history_dialogue(self, db, request: TurnRequest) -> list[str]:
        turns, rolling = await asyncio.gather(
            memory_module.fetch_recent_turns(
                db, company_id=request.company_id, session_id=request.session_id
            ),
            memory_module.fetch_rolling_summary(
                db, company_id=request.company_id, session_id=request.session_id
            ),
        )
        lines: list[str] = []
        if rolling:
            lines.append(f"[summary of earlier conversation] {rolling}")
        engine_lines = memory_module.history_as_dialogue(turns)
        lines.extend(engine_lines)
        extra_lines = [
            str(line).strip()
            for line in (request.extra_history or [])
            if str(line).strip()
        ]
        if extra_lines and not engine_lines:
            lines.extend(extra_lines[-24:])
        return lines

    async def _refresh_rolling_summary(
        self,
        db,
        request: TurnRequest,
        turn_index: int,
        *,
        chunks: list | None = None,
    ) -> None:
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
        summary_text = (result.text or "").strip()
        if not summary_text:
            logger.warning(
                "rolling_summary_skipped reason=empty_llm_response company_id=%s session_id=%s",
                request.company_id,
                request.session_id,
            )
            return
        # Validate the summary against retrieved product chunks to prevent
        # hallucinated product names from entering persistent memory.
        if chunks:
            report = validator.validate(
                answer=summary_text,
                product_links=[],
                chunks=chunks,
            )
            if not report.ok:
                logger.warning(
                    "rolling_summary_skipped reason=validation_failed company_id=%s session_id=%s offences=%s",
                    request.company_id,
                    request.session_id,
                    report.offences[:5],
                )
                return
        await memory_module.upsert_rolling_summary(
            db,
            company_id=request.company_id,
            session_id=request.session_id,
            summary=summary_text,
            covers_through_turn=turn_index,
        )

    async def _safe_refresh_rolling_summary(
        self,
        db,
        request: TurnRequest,
        turn_index: int,
        *,
        chunks: list | None = None,
    ) -> None:
        """Fire-and-forget wrapper around _refresh_rolling_summary.

        Swallows all exceptions so the background task never crashes
        the event loop. Failures are logged and the next turn falls
        back to reading individual turn rows from ai_conversation_turns.
        """
        try:
            await self._refresh_rolling_summary(db, request, turn_index, chunks=chunks)
        except Exception as exc:
            logger.warning(
                "rolling_summary_background_failed company_id=%s session_id=%s error=%s",
                request.company_id,
                request.session_id,
                exc,
            )

    async def _safe_store_shown_products(
        self,
        db,
        request: TurnRequest,
        shown_ids: list[str],
        *,
        product_chunks: list[ContextChunk] | None = None,
    ) -> None:
        """Fire-and-forget wrapper for storing shown product IDs.

        Swallows all exceptions. If this fails, the next turn's
        _fetch_shown_product_chunks simply re-fetches the product
        (which compression deduplicates). No correctness impact.
        """
        try:
            from memory_engine.long_term import LongTermMemory  # noqa: PLC0415
            await LongTermMemory().store_shown_products(
                db,
                request.company_id,
                request.customer_id,
                shown_ids,
                conversation_id=request.session_id,
                last_product_category=_last_product_category(shown_ids, product_chunks or []),
            )
        except Exception as exc:
            logger.debug(
                "shown_products_background_failed company_id=%s error=%s",
                request.company_id,
                exc,
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
        message_sentiment: dict[str, Any],
        conversation_sentiment: dict[str, Any],
        escalation_required: bool,
    ) -> TurnResult:
        tokens = TokenUsage(prompt=0, completion=budget_module.count_tokens(_STATIC_FALLBACK))
        tokens.total = tokens.prompt + tokens.completion
        turn_id, _ = await memory_module.persist_turn_atomic(
            db,
            company_id=request.company_id,
            session_id=request.session_id,
            customer_id=request.customer_id,
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
            sentiment=message_sentiment,
            conversation_sentiment=conversation_sentiment,
            escalation_required=escalation_required,
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


def _last_product_category(product_ids: list[str], chunks: list[ContextChunk]) -> str:
    if not product_ids or not chunks:
        return ""
    wanted = [str(item).strip() for item in product_ids if str(item).strip()]
    categories: dict[str, str] = {}
    for chunk in chunks:
        if chunk.source_type != "product":
            continue
        product_id = str(chunk.source_id or "").strip()
        if not product_id:
            continue
        category = str((chunk.metadata or {}).get("category") or "").strip()
        if category:
            categories[product_id] = category
    for product_id in reversed(wanted):
        category = categories.get(product_id, "")
        if category:
            return category
    return ""


def _extract_product_links(answer: str, chunks: list[ContextChunk]) -> list[ProductLink]:
    """Find URLs in the answer and match them to retrieved products. If the
    answer didn't include any URLs but a product was retrieved this turn, we
    still surface the top product as a card so the frontend gets a visible
    "View product" affordance — the validator separately enforces that the
    target URL resolves to a retrieved product, so this can't surface a
    fabricated link.

    Priority order:
      1. Products whose URLs appear explicitly in the AI answer.
      2. Products whose names are mentioned by the AI (image_url preserved).
      3. Highest-relevance product with a URL (legacy fallback).
    """
    product_chunks = [c for c in chunks if c.source_type == "product"]
    if not product_chunks:
        return []

    links: list[ProductLink] = []
    if answer:
        # Step 1: match on explicit URLs in the answer
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
                    links.append(ProductLink(
                        product_id=chunk.source_id,
                        url=cleaned,
                        name=str(meta.get("name") or chunk.title or ""),
                        image_url=str(meta.get("image_url") or ""),
                    ))
                    break

    if links:
        return links

    # Step 2: match products by name mentioned in the AI answer.
    # Prefer these over the highest-scored chunk because the AI's text
    # represents its actual intent — the AI may have been given context for
    # 6 products but only explicitly named 1-2 in its response.
    if answer:
        answer_lower = answer.lower()
        name_matched: list[ProductLink] = []
        for chunk in sorted(product_chunks, key=lambda c: c.relevance_score, reverse=True):
            meta = chunk.metadata or {}
            name = str(meta.get("name") or chunk.title or "").strip()
            url = str(meta.get("public_url") or meta.get("links") or "")
            if name and name.lower() in answer_lower and url:
                name_matched.append(ProductLink(
                    product_id=chunk.source_id,
                    url=url,
                    name=name,
                    image_url=str(meta.get("image_url") or ""),
                ))
        if name_matched:
            return name_matched[:3]

    # Step 3: fallback — top-relevance product with a URL
    for chunk in sorted(product_chunks, key=lambda c: c.relevance_score, reverse=True):
        meta = chunk.metadata or {}
        url = str(meta.get("public_url") or meta.get("links") or "")
        if url:
            links.append(ProductLink(
                product_id=chunk.source_id,
                url=url,
                name=str(meta.get("name") or chunk.title or ""),
                image_url=str(meta.get("image_url") or ""),
            ))
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
