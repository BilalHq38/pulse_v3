# Fixes.md Task Tracker

Source of truth: `docs/Fixes.md`

This file is only a progress tracker. It mirrors the documented fixes and must not introduce alternative fixes or extra scope.

| Issue ID | Issue Description | Documented Fix | Status | Dependencies |
| --- | --- | --- | --- | --- |
| Migration 1 | Move `llm_client.py` to `services/ai_runtime/`. | Copy `services/ai_service/llm_client.py` to `services/ai_runtime/llm_client.py`, add `services/ai_runtime/__init__.py`, replace old file with documented re-export shim, and update `services/conversation_engine/llm_gateway.py` to import `call_model_text` from `services.ai_runtime.llm_client`. | Completed | None |
| Migration 2 | Move `embedding_service.py` to `services/conversation_engine/`. | Copy `services/ai_service/embedding_service.py`, change its `llm_client` import to `services.ai_runtime.llm_client`, replace old file with documented re-export shim, and update `retrieval/knowledge_base.py` to import from `services.conversation_engine.embedding_service`. | Completed | Migration 1 |
| Migration 3 | Create `services/conversation_engine/product_ranker.py`. | Create ranker file with documented imports and selected sections from `services/ai_service/rag.py`, add documented re-exports at top of legacy `rag.py`, delete duplicate moved ranking definitions there, and update `retrieval/products.py` to import ranker functions from conversation engine. | Completed | Migration 2, Migration 4 |
| Migration 4 | Copy `routing_guards.py` to `services/conversation_engine/`. | Copy file unchanged, replace old file with documented re-export shim, and update `context_router.py` to import guards from conversation engine. | Completed | None |
| Migration 5 | Create conversation-engine local sentiment support and add sentiment fields to `TurnResult`. | Copy `local_ml.py`, create `services/conversation_engine/sentiment.py` from documented local-only blocks, update `TurnResult`, add sentiment imports/computation/returns in `orchestrator.py`, and update follow-up scheduler local ML import. | Completed | Migration 1, L1 |
| L1 | Parallelize retrieval and history fetch in `orchestrator.py`. | Replace sequential retrieval/history fetch with documented `asyncio.gather` block and remove later duplicate history fetch. | Completed | None |
| L2 | Background `_refresh_rolling_summary` in `orchestrator.py`. | Add documented `_safe_refresh_rolling_summary` wrapper and replace awaited refresh with named `asyncio.create_task`. | Completed | None |
| L3 | Fix O(n^2) token counting in `budget.py`. | Replace `trim_to_budget` function body with documented O(n) implementation. | Completed | None |
| L4 | Eliminate skeleton prompt double-build. | Add documented static prompt token baseline helpers/constant to `prompt_builder.py` and replace orchestrator skeleton prompt token counting with baseline plus style token count. | Completed | None |
| L5 | Parallelize template and FAQ queries in `templates_faqs.py`. | Add `asyncio` import if needed and replace sequential template/FAQ awaits with documented `asyncio.gather`. | Completed | None |
| L6 | Parallelize the two queries inside `_history_dialogue` in `orchestrator.py`. | Replace entire `_history_dialogue` method body with documented `asyncio.gather` implementation. | Completed | None |
| L7 | Parallelize slug and image fetches in `products.py` and shown-product chunks. | Add `asyncio` import if needed and replace all three documented sequential slug/image fetch blocks with `asyncio.gather`. | Completed | None |
| L8 | Collapse `next_turn_index` and `persist_turn` into one DB round-trip. | Add documented `persist_turn_atomic` after `persist_turn` and replace all four orchestrator `next_turn_index` plus `persist_turn` sequences with `persist_turn_atomic`. | Completed | None |
| L9 | Background `store_shown_products` in `orchestrator.py`. | Add documented `_safe_store_shown_products` wrapper and replace both awaited `LongTermMemory().store_shown_products` blocks with named background tasks. | Completed | None |
| F1 | Orphaned rows on timeout in follow-up scheduler loop. | Replace `except asyncio.TimeoutError` block in `_loop_body` with documented cleanup that marks follow-up outcome as expired/dispatch_timeout. | Completed | None |
| F2 | `followup_count` never incremented in scheduler. | Add documented `customer_engagement` upsert increment after `evaluate_order_event` insert and before `schedule_upsell_followup` success return. | Completed | None |
| F3 | Keyword false positive where `"issue"` matches `"no issues"`. | Replace `NEGATIVE_SIGNALS` tuple and `_any_keyword` implementation in `followup_scheduler/sentiment.py` with documented versions. | Completed | None |
| F4 | Dead code in follow-up scheduler health endpoint. | Replace entire `health()` function with documented implementation and add explicit top-level `asyncio` import if needed. | Completed | None |
| F5 | `"not interested"` is too broad in disengagement detection. | Replace `DISENGAGEMENT_KEYWORDS` tuple with documented narrower phrases. | Completed | None |
| F6 | Non-atomic outbound AI message persist in follow-up loop. | Replace entire `_persist_outbound_ai_message` function with documented transaction-based implementation and fallback. | Completed | None |
| F7 | Legacy `ai_service.local_ml` import in follow-up scheduler sentiment. | Change the single documented import to `services.conversation_engine.local_ml`; same code path as Migration 5 Step 8. | Completed | Migration 5 |
