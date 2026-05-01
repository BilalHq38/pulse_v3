import asyncio

import routers.webhooks as webhooks
from channel_layer.schemas import ChannelType, SendResult


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
