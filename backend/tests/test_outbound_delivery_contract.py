import asyncio
import inspect
import logging

import routers.webhooks as webhooks
import services.email_service as email_service
import services.messaging_service as messaging_service
from channel_layer.adapters.chat_widget import ChatWidgetAdapter
from channel_layer.adapters.email import EmailAdapter
from channel_layer.adapters.facebook import FacebookAdapter
from channel_layer.adapters.instagram import InstagramAdapter
from channel_layer.adapters.whatsapp import WhatsAppAdapter
from channel_layer.schemas import ChannelType, SendResult, UnifiedMessage
from data_pipeline.processors.message_processor import MessageProcessor


def test_ai_outbound_delivery_selects_channel_marks_delivered_and_logs(monkeypatch, caplog):
    asyncio.run(_run_channel_selection_case(monkeypatch, caplog))


def test_outbound_adapters_report_delivery_providers(monkeypatch):
    asyncio.run(_run_adapter_provider_case(monkeypatch))


def test_ai_outbound_write_delivery_and_analytics_contract_is_wired():
    source = inspect.getsource(webhooks)
    processor_source = inspect.getsource(MessageProcessor.process)

    assert "ai_outbound_write_success" in source
    assert "ai_outbound_delivery_channel_used" in source
    assert "ai_outbound_delivery_confirmed" in source
    assert "ai_outbound_delivery_analytics_event_fired" in source
    assert source.count("_capture_outbound_delivery_analytics_background(") >= 3
    assert '"action": "ai_response_delivered"' in source
    assert 'sender_type == "ai"' in processor_source
    assert 'event_kind = "ai_response_generated"' in processor_source


async def _run_channel_selection_case(monkeypatch, caplog):
    calls = []
    persisted = []

    provider_by_channel = {
        ChannelType.WHATSAPP: "whatsapp_bridge",
        ChannelType.FACEBOOK: "meta_api",
        ChannelType.INSTAGRAM: "meta_api",
        ChannelType.EMAIL: "smtp",
        ChannelType.WEB_CHAT: "websocket",
    }

    class _Router:
        async def send_to_channel(self, **kwargs):
            calls.append(kwargs)
            provider = provider_by_channel[kwargs["channel_type"]]
            return SendResult(
                success=True,
                external_message_id=f"{provider}-message-1",
                channel_type=kwargs["channel_type"],
                metadata={"delivery_provider": provider},
            )

    async def _persist_state(*_args, **kwargs):
        persisted.append(kwargs)

    async def _noop_region(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(webhooks, "get_outbound_router", lambda: _Router())
    monkeypatch.setattr(webhooks, "_persist_outbound_message_state", _persist_state)
    monkeypatch.setattr(webhooks, "company_default_phone_region", _noop_region)

    caplog.set_level(logging.INFO, logger="routers.webhooks")

    cases = [
        ("whatsapp", "+923001234567", ChannelType.WHATSAPP, "whatsapp_bridge"),
        ("facebook", "fb-user-1", ChannelType.FACEBOOK, "meta_api"),
        ("instagram", "ig-user-1", ChannelType.INSTAGRAM, "meta_api"),
        ("email", "customer@example.com", ChannelType.EMAIL, "smtp"),
        ("web_chat", "session-1", ChannelType.WEB_CHAT, "websocket"),
    ]
    for channel, recipient, expected_channel_type, expected_provider in cases:
        metadata = {"trace_id": f"trace-{channel}"}
        sent, error = await webhooks._send_outbound_response_via_channel_layer(
            db=object(),
            company_id="company-1",
            channel=channel,
            recipient_id=recipient,
            content="Hello from AI",
            conversation_id="conversation-1",
            db_message_id=f"ai-{channel}",
            metadata=metadata,
        )

        assert sent is True
        assert error == ""
        assert calls[-1]["channel_type"] == expected_channel_type
        assert calls[-1]["external_user_id"]
        assert metadata["delivery_provider"] == expected_provider
        assert persisted[-1]["delivery_status"] == "delivered"
        assert persisted[-1]["external_message_id"] == f"{expected_provider}-message-1"

    assert "ai_outbound_delivery_channel_used" in caplog.text
    assert "ai_outbound_delivery_confirmed" in caplog.text


async def _run_adapter_provider_case(monkeypatch):
    whatsapp_calls = []
    meta_calls = []
    email_calls = []
    socket_emits = []

    async def _send_whatsapp_message(*_args, **kwargs):
        whatsapp_calls.append(kwargs)
        return True, "", {
            "delivery_provider": "whatsapp_bridge",
            "external_message_id": "bridge-message-1",
            "bridge_state": "ready",
            "bridge_scope": "user-company-1-user-1",
        }

    async def _send_meta_channel_message(*args, **kwargs):
        meta_calls.append((args, kwargs))
        return True, ""

    async def _send_tenant_email_async(*args, **kwargs):
        email_calls.append((args, kwargs))
        return True

    async def _emit_new_message(conversation_id, message):
        socket_emits.append((conversation_id, message))

    monkeypatch.setattr(messaging_service, "send_whatsapp_message", _send_whatsapp_message)
    monkeypatch.setattr(messaging_service, "send_meta_channel_message", _send_meta_channel_message)
    monkeypatch.setattr(email_service, "send_tenant_email_async", _send_tenant_email_async)

    import core.socket as socket_module

    monkeypatch.setattr(socket_module, "emit_new_message", _emit_new_message)

    whatsapp_result = await WhatsAppAdapter().send_message(
        UnifiedMessage(
            message_id="out-1",
            tenant_id="company-1",
            external_user_id="+923001234567",
            channel_type=ChannelType.WHATSAPP,
            content="WhatsApp reply",
            metadata={
                "db_message_id": "ai-1",
                "conversation_id": "conversation-1",
                "actor_user_id": "user-1",
                "idempotency_key": "ai:auto_response:company-1:conversation-1:whatsapp:abc",
            },
        ),
        db=object(),
    )
    assert whatsapp_result.metadata["delivery_provider"] == "whatsapp_bridge"
    assert whatsapp_result.external_message_id == "bridge-message-1"
    assert whatsapp_calls[0]["single_dispatch"] is True
    assert whatsapp_calls[0]["return_details"] is True

    facebook_result = await FacebookAdapter().send_message(
        UnifiedMessage(
            message_id="out-2",
            tenant_id="company-1",
            external_user_id="fb-user-1",
            channel_type=ChannelType.FACEBOOK,
            content="Facebook reply",
            metadata={"db_message_id": "ai-2"},
        ),
        db=object(),
    )
    instagram_result = await InstagramAdapter().send_message(
        UnifiedMessage(
            message_id="out-3",
            tenant_id="company-1",
            external_user_id="ig-user-1",
            channel_type=ChannelType.INSTAGRAM,
            content="Instagram reply",
            metadata={"db_message_id": "ai-3"},
        ),
        db=object(),
    )
    assert facebook_result.metadata["delivery_provider"] == "meta_api"
    assert instagram_result.metadata["delivery_provider"] == "meta_api"
    assert len(meta_calls) == 2

    email_result = await EmailAdapter().send_message(
        UnifiedMessage(
            message_id="out-4",
            tenant_id="company-1",
            external_user_id="customer@example.com",
            channel_type=ChannelType.EMAIL,
            subject="Reply",
            content="Email reply",
            metadata={"db_message_id": "ai-4", "email_provider": "smtp"},
        ),
        db=object(),
    )
    assert email_result.metadata["delivery_provider"] == "smtp"
    assert email_calls[0][1]["to_email"] == "customer@example.com"

    webchat_result = await ChatWidgetAdapter().send_message(
        UnifiedMessage(
            message_id="out-5",
            tenant_id="company-1",
            external_user_id="session-1",
            channel_type=ChannelType.WEB_CHAT,
            content="Web chat reply",
            metadata={
                "conversation_id": "conversation-1",
                "message_payload": {"id": "ai-5", "content": "Web chat reply"},
            },
        ),
        db=object(),
    )
    assert webchat_result.metadata["delivery_provider"] == "websocket"
    assert webchat_result.metadata["delivery_method"] == "websocket"
    assert socket_emits == [("conversation-1", {"id": "ai-5", "content": "Web chat reply"})]
