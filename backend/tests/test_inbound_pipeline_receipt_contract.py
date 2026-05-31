from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


def test_meta_dm_handlers_deduplicate_before_adapter_normalization() -> None:
    source = _read("backend/routers/webhooks.py")

    facebook = _between(
        source,
        "async def _handle_facebook_webhook_payload",
        "async def _handle_instagram_webhook_payload",
    )
    instagram = _between(
        source,
        "async def _handle_instagram_webhook_payload",
        "async def _handle_lead_form_webhook_payload",
    )

    assert facebook.index("_check_inbound_provider_duplicate") < facebook.index("adapter.receive_message")
    assert instagram.index("_check_inbound_provider_duplicate") < instagram.index("adapter.receive_message")
    assert '"provider_event_id": provider_event_id' in facebook
    assert '"idempotency_key": idempotency_key' in facebook
    assert '"provider_event_id": provider_event_id' in instagram
    assert '"idempotency_key": idempotency_key' in instagram


def test_web_chat_persists_message_store_idempotency_before_pipeline() -> None:
    source = _read("backend/routers/webhooks.py")
    web_chat = _between(
        source,
        "async def web_chat_webhook",
        '@router.get("/webhook/meta/facebook")',
    )

    assert web_chat.index("_check_inbound_provider_duplicate") < web_chat.index("adapter.receive_message")
    assert "external_message_id,idempotency_key" in web_chat
    assert "webchat_usage_idempotency_key" in web_chat
    assert web_chat.index('"passed_to_pipeline"') > web_chat.index("reserve_conversation_usage")


def test_shared_incoming_message_logs_before_pipeline_and_checks_idempotency() -> None:
    source = _read("backend/routers/webhooks.py")
    incoming = _between(
        source,
        "async def _process_incoming_message",
        "# Webhook endpoints",
    )

    assert "_find_existing_inbound_message_by_keys" in incoming
    assert "idempotency_key=usage_idempotency_key" in incoming
    assert incoming.index('"passed_to_pipeline"') < incoming.index("orchestrate_message_workflow")
    assert "external_message_id,idempotency_key,read,created_at" in incoming


def test_generic_channel_webhook_covers_all_channels_before_pipeline() -> None:
    source = _read("backend/channel_layer/router.py")
    endpoint = _between(
        source,
        "async def unified_channel_webhook",
        "async def _resolve_tenant_from_payload",
    )

    assert "_extract_provider_event_id" in endpoint
    assert endpoint.index("_check_channel_layer_duplicate") < endpoint.index("adapter.receive_message")
    assert endpoint.index('"passed_to_pipeline"') < endpoint.index("create_safe_detached_task")
    assert "provider_event_id=provider_event_id" in endpoint
    assert "idempotency_key=workflow_metadata" in endpoint

    assert "ChannelType.WHATSAPP" in source
    assert "ChannelType.FACEBOOK" in source
    assert "ChannelType.INSTAGRAM" in source
    assert '"message_id_header"' in source
    assert '"client_message_id"' in source


def test_channel_identity_log_chain_is_emitted_for_customer_company_and_conversation() -> None:
    source = _read("backend/routers/webhooks.py")
    auto_capture = _between(
        source,
        "async def _auto_capture_lead",
        "async def _try_auto_unify",
    )
    incoming = _between(
        source,
        "async def _process_incoming_message",
        "# Webhook endpoints",
    )
    web_chat = _between(
        source,
        "async def web_chat_webhook",
        '@router.get("/webhook/meta/facebook")',
    )

    assert "_log_channel_identity_stage" in source
    assert '"customer_identified"' in auto_capture
    assert '"company_assigned"' in auto_capture
    assert 'f"conversation_{conversation_resolution}"' in incoming
    assert 'f"conversation_{webchat_conversation_resolution}"' in web_chat
