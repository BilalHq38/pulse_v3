# 27-Issue Tracker

Status legend: TODO, IN_PROGRESS, DONE, BLOCKED

- [x] 01. User Session Persistence - DONE
- [x] 02. Navigation & Route Transition Performance - DONE
- [x] 03. First-Click Loading Failures - DONE
- [x] 04. Loading State & Error Display Behaviour - DONE
- [x] 05. Mobile Responsiveness Testing & Fixes - DONE
- [x] 06. Mobile Scroll Performance - DONE
- [x] 07. First-Request Backend Reliability - DONE
- [x] 08. Signup Email Verification Reliability - DONE
- [x] 09. Duplicate Counting in Dashboard Metrics - DONE
- [x] 10. Super Admin Excluded from All User Counts - DONE
- [x] 11. Post-Payment Finalizing Signup Stuck Screen - DONE
- [x] 12. Login Fails After Payment - DONE
- [x] 13. RAG History Prompt Injection Sanitization - DONE
- [x] 14. Order Completion Flow Silence After Address Collection order page - DONE
- [x] 15. Greeting Messages should Calling LLM and remove the legacy ai - DONE
- [x] 16. AI Pipeline: Inbound Channel Receipt - DONE
- [x] 17. AI Pipeline: Channel Identity Resolution - DONE
- [x] 18. AI Pipeline: Sentiment Classification - DONE
- [x] 19. AI Pipeline: GUARD Classification - DONE
- [x] 20. AI Pipeline: Memory Hydration - DONE
- [x] 21. AI Pipeline: Intent Classification - DONE
- [x] 22. AI Pipeline: RAG Retrieval and conversation engine - DONE
- [x] 23. AI Pipeline: Prompt Construction - DONE
- [x] 24. AI Pipeline: LLM Inference - DONE
- [x] 25. AI Pipeline: SAFE Validation - DONE
- [x] 26. AI Pipeline: WRITE and Outbound Delivery - DONE
- [x] 27. Post-Conversation Flow: Trigger Verification - DONE

## Evidence

- Initial issue source: `C:\Users\Spider\.codex\attachments\bd947fd8-34e3-4b88-8063-405088024ff1\pasted-text.txt`
- 11-12: Fixed `/auth/register/status` request propagation into Stripe fallback finalization, added finalization failure reporting, gated signup-complete redirect on active user + workspace records, added frontend support fallback, and verified `backend/tests/test_super_admin_business_controls.py` passes.
- 13: Sanitized conversation history at formatting and prompt-build time, escaped false role labels, dropped chunk-style injection history, and verified `backend/tests/test_wave3_conversation_engine.py` passes.
- 14: Updated the conversation prompt so active order flows may collect quantity/address/contact details, preserved deterministic order-flow support responses from engine overwrite, added staged order flow regression coverage, and verified order/webchat/prompt tests pass.
- 15: Added greeting/social/low-value direct responses in the conversation router, made orchestrator return them before retrieval or gateway generation, added social short-circuiting in the combined legacy path, and verified targeted zero-LLM/RAG tests pass.
- 16: Added inbound receipt/dedup/pipeline-stage logging for WhatsApp, Facebook, Instagram, web chat, and the generic channel webhook path that covers email; added message-store idempotency checks before pipeline dispatch and verified `backend/tests/test_inbound_pipeline_receipt_contract.py` passes.
- 17: Scoped social identity resolution by company, added customer/company/conversation identity log stages for inbound and web-chat paths, and verified `backend/tests/test_channel_identity.py` plus the inbound contract test pass.
- 18: Made sentiment scoring local-only through all-MiniLM/CRM keyword blending, added sentiment gate path/escalation outputs and logs, kept scores clamped to [-1.0, 1.0], and verified `backend/tests/test_sentiment_pipeline_contract.py` passes.
- 19: Added GUARD classification logs, verified direct responses for greeting/social/low-value messages skip retrieval and LLM, and added coverage that business/product/order/support messages stay on the full pipeline.
- 20: Parallelized and verified Redis short-term memory hydration, set 30-minute/max-20 short-term context behavior, added memory hydration timing/token logs, and confirmed pgvector cosine top-k query coverage.
- 21: Added deterministic intent coverage for product inquiry, order intent, company question, FAQ, complaint, and unknown; logged raw message to classified intent with confidence; persisted classified capture intent to Redis `pending_intent`; and verified `backend/tests/test_intent_pipeline_contract.py`, existing intent fallback, and memory hydration tests pass.
- 22: Made product RAG stages explicit with keyword containment retrieval, synonym/fuzzy category matching, vector/embedding retrieval through cached Gemini `text-embedding-004` and pgvector cosine search, weighted fusion logging, clean XML context formatting checks, and proof that retrieved context is prompt-only; verified the new RAG contract tests plus conversation-engine and embedding regressions pass.
- 23: Scoped prompt factual context by user intent so product/order prompts use `<product_catalog>`, company prompts use `<company_info>`, and FAQ/policy prompts use `<faqs>`/`<knowledge_base>`; verified grounded purchase links/CTA, the exact escalation phrase, and no ungrounded product names/prices/URLs in prompt contract tests.
- 24: Set conversation inference defaults to Vertex Gemini 2.5 Flash, 25-second API timeout, temperature 0.7, and 2048 max tokens; added gateway model fallback on primary timeout and inference logs for model, response time, token count, and fallback status; verified new LLM inference contract tests plus Gemini config regressions pass.
- 25: Added SAFE sanitization for fabricated product names, prices, URLs, internal context tags/source labels, invalid product links, missing purchase links, and duplicate response blocks; logged validation pass/fail plus stripped content; preserved fallback behavior when stripping removes the required grounded answer; verified SAFE and conversation validation suites pass.
- 26: Added outbound delivery provider metadata for WhatsApp bridge/Meta API/SMTP/WebSocket paths, explicit logs for write success, channel used, delivery confirmed, and analytics event fired, and outbound AI response capture into the data pipeline after delivery; verified new outbound delivery contract tests plus WhatsApp idempotency, web chat, socket, email, inbound receipt, media URL, and WhatsApp bridge regressions pass.
- 27: Added order/delivery trigger aliases for order completed, delivery completed, and delivered; logged post-conversation trigger pass/fail and message content checks; made post-delivery proactive prompts ask about experience/product satisfaction and use purchase-history related product retrieval; verified trigger scheduling, dispatch, content, order status alias emitters, related-product prompt grounding, and Wave 4/5 follow-up suites pass.
- Final evaluation: DONE. Backend suite passes (`430 passed, 37 skipped, 4 warnings`). Frontend suite passes (`17 passed, 72 tests`). Frontend production build passes. Targeted issue 26/27 suites and production hardening tests pass.

## 2026-06-01 Deployment/Auth Recheck

- [x] 01. Google OAuth must stop at super admin verification before onboarding/dashboard - DONE
- [x] 02. Credential login after payment must not return a generic 401 loop - DONE
- [x] 03. Email verification deadlock must be removed - DONE
- [x] 04. Super admin approval must not be skipped - DONE
- [x] 05. `/settings/company`, `/settings/personal`, and `/notifications` must not fail with 500 during bootstrap/missing-record states - DONE
- [x] 06. Partial workspace provisioning must surface as pending approval/status, not signup failure - DONE
- [x] 07. Dashboard routing must wait for approval and onboarding completion - DONE
- [x] 08. Frontend retry loop must not repeatedly hammer guarded bootstrap endpoints - DONE

Evidence:

- Backend full suite: `430 passed, 37 skipped, 4 warnings`.
- Frontend full suite: `17 passed, 72 tests`.
- Frontend production build: passed.
- Local Docker cleanup: old `pulse-v3` and `pulse-engine` containers/volumes removed; accidental `database_complete.sql` directory removed.
- Local Docker run: fresh `pulse-v3` stack is running with all containers healthy.
- Database bootstrap: `backend/sql_schema.sql` now mounts into Postgres init; fresh local DB has 137 public tables including `users`, `companies`, `company_settings`, `notifications`, and `pending_signups`.
