import asyncio

import routers.webhooks as webhooks
import services.messaging_service as messaging_service
from channel_layer.adapters.whatsapp import WhatsAppAdapter
from channel_layer.schemas import ChannelType, SendResult, UnifiedMessage


def test_ai_response_idempotency_key_is_stable_for_same_inbound_message():
    first = webhooks._build_ai_response_idempotency_key(
        company_id="company-1",
        conversation_id="conversation-1",
        inbound_message_id="msg-1",
        inbound_external_message_id="provider-1",
        channel="whatsapp",
    )
    second = webhooks._build_ai_response_idempotency_key(
        company_id="company-1",
        conversation_id="conversation-1",
        inbound_message_id="msg-1",
        inbound_external_message_id="provider-1",
        channel="whatsapp",
    )
    other = webhooks._build_ai_response_idempotency_key(
        company_id="company-1",
        conversation_id="conversation-1",
        inbound_message_id="msg-2",
        inbound_external_message_id="provider-2",
        channel="whatsapp",
    )

    assert first == second
    assert first != other
    assert first.startswith("ai:auto_response:company-1:conversation-1:whatsapp:")


def test_provider_avatar_url_strips_private_token_parameters():
    safe = webhooks._safe_provider_avatar_url(
        "https://cdn.example.test/avatar.jpg?access_token=secret&oe=123&appsecret_proof=proof"
    )

    assert safe == "https://cdn.example.test/avatar.jpg?oe=123"


def test_whatsapp_session_not_ready_is_not_retried_and_preserves_scope(monkeypatch):
    asyncio.run(_run_whatsapp_session_not_ready_case(monkeypatch))


def test_whatsapp_ai_auto_response_retryable_failure_is_not_retried(monkeypatch):
    asyncio.run(_run_whatsapp_ai_auto_response_retryable_failure_case(monkeypatch))


def test_whatsapp_adapter_marks_ai_auto_response_single_dispatch(monkeypatch):
    asyncio.run(_run_whatsapp_adapter_single_dispatch_case(monkeypatch))


def test_send_whatsapp_single_dispatch_suppresses_bridge_fallback(monkeypatch):
    asyncio.run(_run_send_whatsapp_single_dispatch_fallback_case(monkeypatch))


async def _run_whatsapp_session_not_ready_case(monkeypatch):
    calls = []
    failures = []

    class _Router:
        async def send_to_channel(self, **kwargs):
            calls.append(kwargs)
            return SendResult(
                success=False,
                error="WHATSAPP_SESSION_NOT_READY: WhatsApp session is still starting.",
                channel_type=ChannelType.WHATSAPP,
            )

    async def _noop_region(*_args, **_kwargs):
        return ""

    async def _persist_state(*_args, **_kwargs):
        return None

    async def _record_failure(*_args, **kwargs):
        failures.append(kwargs)

    monkeypatch.setattr(webhooks, "get_outbound_router", lambda: _Router())
    monkeypatch.setattr(webhooks, "company_default_phone_region", _noop_region)
    monkeypatch.setattr(webhooks, "_persist_outbound_message_state", _persist_state)
    monkeypatch.setattr(webhooks, "_emit_outbound_failure_notice", _record_failure)
    monkeypatch.setattr(webhooks, "outbound_retry_base_delay_seconds", lambda: 0)

    sent, error = await webhooks._send_outbound_response_via_channel_layer(
        db=object(),
        company_id="company-1",
        channel="whatsapp",
        recipient_id="+923001234567",
        content="Hello",
        conversation_id="conversation-1",
        db_message_id="ai-1",
        metadata={
            "actor_user_id": "user-1",
            "customer_id": "customer-1",
            "idempotency_key": "ai:auto_response:company-1:conversation-1:whatsapp:abc",
        },
    )

    assert sent is False
    assert "WHATSAPP_SESSION_NOT_READY" in error
    assert len(calls) == 1
    assert calls[0]["metadata"]["selected_whatsapp_scope"] == "user-company-1-user-1"
    assert calls[0]["metadata"]["actor_user_id"] == "user-1"
    assert calls[0]["metadata"]["conversation_id"] == "conversation-1"
    assert len(failures) == 1


async def _run_whatsapp_ai_auto_response_retryable_failure_case(monkeypatch):
    calls = []
    failures = []

    class _Router:
        async def send_to_channel(self, **kwargs):
            calls.append(kwargs)
            return SendResult(
                success=False,
                error="temporary upstream timeout after send request",
                channel_type=ChannelType.WHATSAPP,
            )

    async def _noop_region(*_args, **_kwargs):
        return ""

    async def _persist_state(*_args, **_kwargs):
        return None

    async def _record_failure(*_args, **kwargs):
        failures.append(kwargs)

    monkeypatch.setattr(webhooks, "get_outbound_router", lambda: _Router())
    monkeypatch.setattr(webhooks, "company_default_phone_region", _noop_region)
    monkeypatch.setattr(webhooks, "_persist_outbound_message_state", _persist_state)
    monkeypatch.setattr(webhooks, "_emit_outbound_failure_notice", _record_failure)
    monkeypatch.setattr(webhooks, "outbound_retry_base_delay_seconds", lambda: 0)

    sent, error = await webhooks._send_outbound_response_via_channel_layer(
        db=object(),
        company_id="company-1",
        channel="whatsapp",
        recipient_id="+923001234567",
        content="Hello",
        conversation_id="conversation-1",
        db_message_id="ai-1",
        metadata={
            "customer_id": "customer-1",
            "idempotency_key": "ai:auto_response:company-1:conversation-1:whatsapp:abc",
        },
    )

    assert sent is False
    assert "temporary upstream timeout" in error
    assert len(calls) == 1
    assert calls[0]["metadata"]["idempotency_key"] == "ai:auto_response:company-1:conversation-1:whatsapp:abc"
    assert len(failures) == 1


async def _run_whatsapp_adapter_single_dispatch_case(monkeypatch):
    calls = []

    async def _send_whatsapp_message(*_args, **kwargs):
        calls.append(kwargs)
        return True, ""

    monkeypatch.setattr(messaging_service, "send_whatsapp_message", _send_whatsapp_message)

    result = await WhatsAppAdapter().send_message(
        UnifiedMessage(
            message_id="out-1",
            tenant_id="company-1",
            external_user_id="+923001234567",
            channel_type=ChannelType.WHATSAPP,
            content="Hello",
            metadata={
                "db_message_id": "ai-1",
                "conversation_id": "conversation-1",
                "customer_id": "customer-1",
                "idempotency_key": "ai:auto_response:company-1:conversation-1:whatsapp:abc",
            },
        ),
        db=object(),
    )

    assert result.success is True
    assert len(calls) == 1
    assert calls[0]["single_dispatch"] is True


async def _run_send_whatsapp_single_dispatch_fallback_case(monkeypatch):
    bridge_calls = []
    persisted = []

    async def _tenant_meta(*_args, **_kwargs):
        return False, "temporary Meta timeout after send request", ""

    async def _bridge(*_args, **_kwargs):
        bridge_calls.append(_kwargs)
        return True, "", "bridge-message-1"

    async def _snapshot(*_args, **_kwargs):
        return {"state": "ready", "status": "ready", "scope": "company-company-1"}

    async def _persist(*_args, **kwargs):
        persisted.append(kwargs)

    monkeypatch.setattr(messaging_service, "_send_via_tenant_meta", _tenant_meta)
    monkeypatch.setattr(messaging_service, "_send_via_bridge", _bridge)
    monkeypatch.setattr(messaging_service, "_bridge_session_snapshot", _snapshot)
    monkeypatch.setattr(messaging_service, "_persist_outbound_message_state", _persist)

    sent, error = await messaging_service.send_whatsapp_message(
        "+923001234567",
        "Hello",
        db=object(),
        company_id="company-1",
        db_message_id="ai-1",
        conversation_id="conversation-1",
        customer_id="customer-1",
        idempotency_key="ai:auto_response:company-1:conversation-1:whatsapp:abc",
        single_dispatch=True,
    )

    assert sent is False
    assert "Meta timeout" in error
    assert bridge_calls == []
    assert persisted[0]["delivery_status"] == "failed"


class _UniqueViolation(Exception):
    sqlstate = "23505"


class _DedupRaceDb:
    async def fetchrow(self, *_args, **_kwargs):
        return None

    async def execute(self, query, *_args):
        if query.startswith("INSERT INTO whatsapp_event_dedup"):
            raise _UniqueViolation("duplicate key value violates unique constraint")
        return None


def test_whatsapp_event_dedup_insert_race_is_treated_as_duplicate(monkeypatch):
    asyncio.run(_run_whatsapp_event_dedup_insert_race_case(monkeypatch))


async def _run_whatsapp_event_dedup_insert_race_case(monkeypatch):
    monkeypatch.setattr(webhooks, "_WHATSAPP_IDENTITY_SCHEMA_READY", True)

    result = await webhooks._record_whatsapp_event_dedup(
        _DedupRaceDb(),
        "company-1",
        {"business_account_id": "waba-1"},
        event_type="message",
        provider_event_id="wamid.1",
        idempotency_key="whatsapp:company-1:message:wamid.1",
        payload={"id": "wamid.1"},
    )

    assert result["duplicate"] is True


class _Transaction:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class _EscalationOnceDb:
    def __init__(self):
        self.messages = {}
        self.conversation = {"id": "conversation-1", "company_id": "company-1", "escalation_notice": "Conversation escalated"}

    def transaction(self):
        return _Transaction(self)

    async def execute(self, query, *args):
        if query.startswith("SELECT pg_advisory_xact_lock"):
            return None
        if query.startswith("UPDATE conversations SET"):
            self.conversation["ai_handled"] = False
            self.conversation["ai_auto_paused"] = True
            self.conversation["ai_paused_reason"] = args[0]
            return None
        if query.startswith("INSERT INTO messages"):
            msg_id, company_id, conversation_id, content = args[:4]
            self.messages[msg_id] = {
                "id": msg_id,
                "company_id": company_id,
                "conversation_id": conversation_id,
                "content": content,
                "sender_type": "system",
                "idempotency_key": "",
            }
            return None
        if query.startswith("UPDATE messages SET idempotency_key"):
            key, msg_id, company_id = args[:3]
            message = self.messages[msg_id]
            assert message["company_id"] == company_id
            message["idempotency_key"] = key
            return None
        return None

    async def fetchrow(self, query, *args):
        if "FROM messages WHERE company_id=$1 AND idempotency_key=$2" in query:
            company_id, key = args[:2]
            for message in self.messages.values():
                if message["company_id"] == company_id and message["idempotency_key"] == key:
                    return {"id": message["id"]}
            return None
        if "SELECT * FROM messages WHERE id=$1" in query:
            return self.messages.get(args[0])
        if "SELECT * FROM conversations WHERE id=$1" in query:
            return self.conversation
        return None

    async def fetch(self, *_args, **_kwargs):
        return []


def test_ai_shutdown_escalation_uses_idempotency_key_once(monkeypatch):
    asyncio.run(_run_ai_shutdown_escalation_once_case(monkeypatch))


async def _run_ai_shutdown_escalation_once_case(monkeypatch):
    monkeypatch.setattr(webhooks, "_MESSAGES_IDEMPOTENCY_SCHEMA_READY", True)
    db = _EscalationOnceDb()
    key = "ai:shutdown:company-1:conversation-1:whatsapp:abc"

    first = await webhooks._escalate_conversation_to_human_once(
        db,
        company_id="company-1",
        conversation_id="conversation-1",
        customer_name="Customer",
        channel="whatsapp",
        reason="AI unavailable",
        automatic=True,
        idempotency_key=key,
    )
    second = await webhooks._escalate_conversation_to_human_once(
        db,
        company_id="company-1",
        conversation_id="conversation-1",
        customer_name="Customer",
        channel="whatsapp",
        reason="AI unavailable",
        automatic=True,
        idempotency_key=key,
    )

    assert first.get("duplicate") is not True
    assert second["duplicate"] is True
    assert len(db.messages) == 1
    assert next(iter(db.messages.values()))["idempotency_key"] == key
