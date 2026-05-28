from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi.encoders import jsonable_encoder

from agent_orchestrator.repository import (
    fetch_conversation,
    fetch_customer,
    fetch_lead,
    fetch_recent_messages,
)
from agent_orchestrator.schemas import GlobalMemory, WorkflowKind
from core.utils import make_id
from shared.cache import get_cache_client

logger = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self, db) -> None:
        self.db = db
        self.cache = get_cache_client(namespace="agent_orchestrator_memory")
        self._saved_hashes: dict[str, str] = {}

    def _memory_key(
        self,
        *,
        workflow_kind: WorkflowKind,
        company_id: str,
        conversation_id: str = "",
        customer_id: str = "",
        lead_id: str = "",
    ) -> str:
        discriminator = conversation_id or lead_id or customer_id or workflow_kind.value
        return f"{company_id}:{workflow_kind.value}:{discriminator}"

    async def load_global_memory(
        self,
        *,
        workflow_kind: WorkflowKind,
        company_id: str,
        conversation_id: str = "",
        customer_id: str = "",
        lead_id: str = "",
        conversation_context: list[dict[str, Any]] | None = None,
        customer: dict[str, Any] | None = None,
        lead: dict[str, Any] | None = None,
    ) -> GlobalMemory:
        memory_key = self._memory_key(
            workflow_kind=workflow_kind,
            company_id=company_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            lead_id=lead_id,
        )
        cached = await self.cache.get_json(memory_key)
        if isinstance(cached, dict) and cached.get("memory_key") == memory_key:
            memory = GlobalMemory.model_validate(cached)
        else:
            row = await self.db.fetchrow(
                "SELECT * FROM global_memory WHERE memory_key=$1 LIMIT 1",
                memory_key,
            )
            memory = GlobalMemory.model_validate(dict(row)) if row else GlobalMemory()
        memory.memory_key = memory_key
        memory.company_id = company_id
        memory.conversation_id = conversation_id or memory.conversation_id
        memory.customer_id = customer_id or memory.customer_id
        memory.lead_id = lead_id or memory.lead_id

        conversation = await fetch_conversation(self.db, memory.conversation_id)
        resolved_customer = dict(customer or {}) or await fetch_customer(
            self.db,
            memory.customer_id or conversation.get("customer_id", ""),
        )
        resolved_lead = dict(lead or {}) or await fetch_lead(self.db, memory.lead_id)
        memory.identity_context = {
            **dict(memory.identity_context or {}),
            "conversation": conversation,
            "customer": resolved_customer,
            "lead": resolved_lead,
        }
        memory.conversation_history = list(conversation_context or []) or await fetch_recent_messages(
            self.db,
            memory.conversation_id,
            limit=20,
        )
        return memory

    async def save_global_memory(self, memory: GlobalMemory) -> None:
        payload = jsonable_encoder(memory.model_dump(mode="json"))
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        if self._saved_hashes.get(payload["memory_key"]) == payload_hash:
            logger.debug("global_memory_save_skipped reason=unchanged memory_key=%s", payload["memory_key"])
            return
        await self.db.execute(
            "INSERT INTO global_memory("
            "id,memory_key,company_id,conversation_id,customer_id,lead_id,identity_context,"
            "conversation_history,knowledge_context,summary,shared_context,created_at,updated_at"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW(),NOW()) "
            "ON CONFLICT(memory_key) DO UPDATE SET "
            "company_id=EXCLUDED.company_id,conversation_id=EXCLUDED.conversation_id,"
            "customer_id=EXCLUDED.customer_id,lead_id=EXCLUDED.lead_id,"
            "identity_context=EXCLUDED.identity_context,conversation_history=EXCLUDED.conversation_history,"
            "knowledge_context=EXCLUDED.knowledge_context,summary=EXCLUDED.summary,"
            "shared_context=EXCLUDED.shared_context,updated_at=NOW()",
            make_id(),
            payload["memory_key"],
            payload["company_id"],
            payload["conversation_id"],
            payload["customer_id"],
            payload["lead_id"],
            payload["identity_context"],
            payload["conversation_history"],
            payload["knowledge_context"],
            payload["summary"],
            payload["shared_context"],
        )
        await self.cache.set_json(payload["memory_key"], payload, ttl_seconds=3600)
        self._saved_hashes[payload["memory_key"]] = payload_hash

    async def save_agent_memory(
        self,
        *,
        workflow_id: str,
        company_id: str,
        trace_id: str,
        agent_name: str,
        memory_key: str,
        memory_value: dict[str, Any],
    ) -> None:
        encoded_memory_value = jsonable_encoder(memory_value or {})
        await self.db.execute(
            "INSERT INTO agent_memory("
            "id,workflow_id,company_id,trace_id,agent_name,memory_key,memory_value,created_at,updated_at"
            ") VALUES($1,$2,$3,$4,$5,$6,$7,NOW(),NOW()) "
            "ON CONFLICT(workflow_id,agent_name,memory_key) DO UPDATE SET "
            "memory_value=EXCLUDED.memory_value,trace_id=EXCLUDED.trace_id,updated_at=NOW()",
            make_id(),
            workflow_id,
            company_id,
            trace_id,
            agent_name,
            memory_key,
            encoded_memory_value,
        )
