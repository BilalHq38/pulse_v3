import asyncio
from datetime import datetime, timezone

import routers.webhooks as webhooks
from routers.webhooks import _extract_sender_contact_fields, _is_whatsapp_web_bridge_payload
from channel_layer.normalizer import MessageNormalizer
from channel_layer.schemas import ChannelType, MessageDirection, UnifiedMessage


def test_whatsapp_web_bridge_payload_is_detected_from_metadata():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"source": "whatsapp_web_bridge"},
                            "messages": [{"from": "923445563662", "type": "text"}],
                        }
                    }
                ]
            }
        ]
    }

    assert _is_whatsapp_web_bridge_payload(payload)


def test_whatsapp_contact_fields_preserve_normalized_sender_over_provider_id():
    fields = _extract_sender_contact_fields(
        "whatsapp",
        "+923445563662",
        {
            "normalized_sender_id": "+923445563662",
            "raw_sender_id": "923445563662",
            "provider_sender_id": "222436977021015@lid",
            "raw_wa_id": "923445563662",
        },
    )

    assert fields["phone"] == "+923445563662"
    assert fields["channel_id"] == "+923445563662"


def test_whatsapp_contact_fields_reject_provider_id_without_valid_sender():
    fields = _extract_sender_contact_fields(
        "whatsapp",
        "",
        {
            "raw_sender_id": "222436977021015",
            "raw_wa_id": "222436977021015",
            "provider_sender_id": "222436977021015@lid",
        },
    )

    assert fields["phone"] == ""
    assert fields["channel_id"] == "222436977021015@lid"


def test_whatsapp_bridge_outbound_message_is_detected_from_web_bridge_metadata():
    assert webhooks._is_whatsapp_bridge_outbound_message(
        {
            "from": "923001234567",
            "id": "wamid.out-1",
            "web_bridge": {"direction": "outbound", "from_me": True},
        },
        {"source": "whatsapp_web_bridge"},
    )


def test_whatsapp_reaction_only_payload_is_ignored_before_resolution(monkeypatch):
    asyncio.run(_run_whatsapp_reaction_only_payload_is_ignored_before_resolution(monkeypatch))


async def _run_whatsapp_reaction_only_payload_is_ignored_before_resolution(monkeypatch):
    def fail_registry():
        raise AssertionError("registry should not be used for reaction-only payloads")

    monkeypatch.setattr(webhooks, "get_channel_registry", fail_registry)
    result = await webhooks._handle_whatsapp_webhook_payload(
        object(),
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"source": "whatsapp_web_bridge"},
                                "messages": [
                                    {
                                        "id": "reaction-1",
                                        "type": "reaction",
                                        "reaction": {"message_id": "wamid.1", "emoji": "👍"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        },
        event_id="evt-reaction",
        return_results=True,
    )

    assert result["processed"] is True
    assert result["status"] == "ignored_reaction"
    assert result["results"][0]["reason"] == "whatsapp_reactions_disabled"


class _QueuedReactionDb:
    def __init__(self):
        self.updated = []

    async def fetch(self, *_args):
        return [
            {
                "channel": "whatsapp",
                "event_id": "evt-reaction",
                "retry_count": 0,
                "raw_payload": (
                    '{"entry":[{"changes":[{"value":{"messages":[{"id":"reaction-1","type":"reaction"}]}}]}]}'
                ),
            }
        ]

    async def fetchval(self, *_args):
        return None

    async def execute(self, query, *args):
        self.updated.append((query, args))
        return None


def test_queued_whatsapp_reaction_retry_is_dropped(monkeypatch):
    asyncio.run(_run_queued_whatsapp_reaction_retry_is_dropped(monkeypatch))


async def _run_queued_whatsapp_reaction_retry_is_dropped(monkeypatch):
    db = _QueuedReactionDb()
    monkeypatch.setattr(webhooks, "_ensure_unprocessed_events_table", lambda *_args, **_kwargs: _async_none())

    await webhooks._retry_unprocessed_events(db, channel="whatsapp")

    assert db.updated
    assert "whatsapp_reactions_disabled" in db.updated[0][0]


class _LeadCaptureFakeDb:
    def __init__(self):
        self.customer = {
            "id": "cust-1",
            "company_id": "company-1",
            "name": "Bilal",
            "email": "",
            "phone": "923445563662",
            "avatar": "",
            "lifecycle_stage": "customer",
            "updated_at": datetime(2026, 5, 15, tzinfo=timezone.utc),
        }
        self.customer_inserts = 0
        self.executed = []

    async def fetchrow(self, query, *args):
        if "SELECT default_phone_region FROM company_settings" in query:
            return None
        if "SELECT * FROM customers WHERE company_id=$1" in query and "regexp_replace(phone" in query:
            digits = args[2]
            if digits == "923445563662":
                return self.customer
        if "SELECT * FROM customers WHERE id=$1 AND company_id=$2" in query:
            customer_id, company_id = args[0], args[1]
            if customer_id == self.customer["id"] and company_id == self.customer["company_id"]:
                return self.customer
        return None

    async def fetch(self, *_args):
        return []

    async def fetchval(self, *_args):
        return None

    async def execute(self, query, *args):
        self.executed.append((query, args))
        if query.startswith("INSERT INTO customers"):
            self.customer_inserts += 1
        return None


def test_whatsapp_auto_capture_matches_legacy_digits_phone_without_duplicate(monkeypatch):
    asyncio.run(_run_whatsapp_auto_capture_matches_legacy_digits_phone_without_duplicate(monkeypatch))


async def _run_whatsapp_auto_capture_matches_legacy_digits_phone_without_duplicate(monkeypatch):
    db = _LeadCaptureFakeDb()

    async def resolve_company(*_args, **_kwargs):
        return "company-1"

    monkeypatch.setattr(webhooks, "_resolve_inbound_company_id", resolve_company)

    result = await webhooks._auto_capture_lead(
        db,
        "whatsapp",
        "Bilal",
        "+923445563662",
        "hello",
        {"company_id": "company-1", "normalized_sender_id": "+923445563662"},
    )

    assert result["customer"]["id"] == "cust-1"
    assert db.customer_inserts == 0


def test_whatsapp_auto_capture_refuses_customer_without_stable_identity(monkeypatch):
    asyncio.run(_run_whatsapp_auto_capture_refuses_customer_without_stable_identity(monkeypatch))


async def _run_whatsapp_auto_capture_refuses_customer_without_stable_identity(monkeypatch):
    db = _LeadCaptureFakeDb()

    async def resolve_company(*_args, **_kwargs):
        return "company-1"

    monkeypatch.setattr(webhooks, "_resolve_inbound_company_id", resolve_company)

    result = await webhooks._auto_capture_lead(
        db,
        "whatsapp",
        "Unknown",
        "",
        "hello",
        {
            "company_id": "company-1",
            "raw_sender_id": "222436977021015",
            "raw_wa_id": "222436977021015",
            "provider_sender_id": "222436977021015@lid",
        },
    )

    assert result is None
    assert db.customer_inserts == 0


class _NormalizerFakeDb:
    async def fetchval(self, query, *args):
        if "SELECT id FROM customers WHERE company_id=$1 AND phone=$2" in query and args[1] == "923445563662":
            return "cust-legacy-digits"
        return None

    async def fetch(self, *_args):
        return []


class _NullIdentityCache:
    async def get_json(self, *_args, **_kwargs):
        return None

    async def set_json(self, *_args, **_kwargs):
        return None


def test_normalizer_resolves_whatsapp_legacy_digits_phone(monkeypatch):
    asyncio.run(_run_normalizer_resolves_whatsapp_legacy_digits_phone(monkeypatch))


async def _run_normalizer_resolves_whatsapp_legacy_digits_phone(monkeypatch):
    monkeypatch.setattr("shared.cache.get_cache_client", lambda namespace="": _NullIdentityCache())
    message = UnifiedMessage(
        message_id="wamid.in-legacy",
        tenant_id="company-legacy",
        external_user_id="+923445563662",
        channel_type=ChannelType.WHATSAPP,
        direction=MessageDirection.INBOUND,
        content="hello",
    )

    normalized = await MessageNormalizer().normalize(message, _NormalizerFakeDb())

    assert normalized.resolved_customer_id == "cust-legacy-digits"


class _FakeTransaction:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class _FakeDb:
    def __init__(self):
        self.conversations = {}
        self.messages = {}
        self.rollup = {}

    def transaction(self):
        return _FakeTransaction(self)

    async def fetchrow(self, query, *args):
        if "SELECT * FROM conversations WHERE customer_id" in query:
            return None
        if "SELECT * FROM conversations WHERE id=$1" in query:
            return self.conversations.get(args[0])
        return None

    async def fetch(self, *_args):
        return []

    async def fetchval(self, *_args):
        return None

    async def execute(self, query, *args):
        if query.startswith("INSERT INTO conversations"):
            convo_id, company_id, customer_id, customer_name = args[0], args[1], args[2], args[3]
            channel = args[5]
            channel_id = args[9]
            self.conversations[convo_id] = {
                "id": convo_id,
                "company_id": company_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "channel": channel,
                "channel_id": channel_id,
                "ai_handled": True,
                "message_count": 0,
                "unread_count": 0,
            }
        elif query.startswith("INSERT INTO messages"):
            msg_id, company_id, convo_id, content, sender_id, sender_name = args[:6]
            self.messages[msg_id] = {
                "id": msg_id,
                "company_id": company_id,
                "conversation_id": convo_id,
                "content": content,
                "sender_type": "customer",
                "sender_id": sender_id,
                "sender_name": sender_name,
                "external_message_id": args[10],
            }
        elif query.startswith("UPDATE conversations SET last_message"):
            last_message = args[0]
            convo_id = args[2] if len(args) > 2 else args[1]
            convo = self.conversations[convo_id]
            convo["last_message"] = last_message
            convo["message_count"] += 1
            convo["unread_count"] += 1
            self.rollup[convo_id] = {
                "last_message": last_message,
                "message_count": convo["message_count"],
                "unread_count": convo["unread_count"],
            }


def test_valid_whatsapp_web_message_persists_rollup_and_emits(monkeypatch):
    asyncio.run(_run_valid_whatsapp_web_message_persists_rollup_and_emits(monkeypatch))


async def _run_valid_whatsapp_web_message_persists_rollup_and_emits(monkeypatch):
    db = _FakeDb()
    emitted = []

    async def auto_capture(*_args, **_kwargs):
        return {
            "customer": {
                "id": "cust-1",
                "company_id": "company-1",
                "name": "Bilal",
                "phone": "+923445563662",
            },
            "lead_id": "",
            "lead": {},
            "company_id": "company-1",
        }

    def close_detached_task(_db, coro, **_kwargs):
        if hasattr(coro, "close"):
            coro.close()

    async def validate_company(_db, cid, **_kwargs):
        return cid

    monkeypatch.setattr(webhooks, "_auto_capture_lead", auto_capture)
    monkeypatch.setattr(webhooks, "_validate_resolved_company_id", validate_company)
    monkeypatch.setattr(webhooks, "relaxed_billing_env", lambda: True)
    monkeypatch.setattr(webhooks, "insert_conversation_usage_relaxed", lambda *_args, **_kwargs: _async_none())
    monkeypatch.setattr(webhooks, "save_message_attachments", lambda *_args, **_kwargs: _async_value([]))
    monkeypatch.setattr(webhooks, "insert_chat_history_record", lambda *_args, **_kwargs: _async_none())
    monkeypatch.setattr(webhooks, "create_safe_detached_task", close_detached_task)
    monkeypatch.setattr(webhooks, "fetch_messages_with_attachments", lambda *_args, **_kwargs: _async_value([]))
    monkeypatch.setattr(webhooks, "orchestrate_message_workflow", lambda *_args, **_kwargs: _async_value({}))
    monkeypatch.setattr(webhooks, "_extract_workflow_outputs", lambda *_args, **_kwargs: ({}, {}, {}, {}))
    monkeypatch.setattr(webhooks, "is_company_ai_enabled", lambda *_args, **_kwargs: _async_value(False))
    monkeypatch.setattr(webhooks, "_load_message_with_attachments", lambda _db, msg_id: _async_value(db.messages[msg_id]))
    monkeypatch.setattr(webhooks, "emit_new_message", lambda convo_id, message: _record_emit(emitted, convo_id, message))

    processed = await webhooks._process_incoming_message(
        db,
        "whatsapp",
        "Bilal",
        "+923445563662",
        "hello from whatsapp",
        metadata={
            "company_id": "company-1",
            "source": "whatsapp_web_bridge",
            "normalized_sender_id": "+923445563662",
            "provider_sender_id": "222436977021015@lid",
            "inbound_external_message_id": "AC123",
        },
    )

    assert processed["message_id"] in db.messages
    assert db.messages[processed["message_id"]]["sender_type"] == "customer"
    assert db.messages[processed["message_id"]]["sender_id"] == "cust-1"
    assert db.rollup[processed["conversation_id"]]["last_message"] == "hello from whatsapp"
    assert db.rollup[processed["conversation_id"]]["message_count"] == 1
    assert db.rollup[processed["conversation_id"]]["unread_count"] == 1
    assert emitted == [(processed["conversation_id"], db.messages[processed["message_id"]])]


class _OutboundFakeDb(_FakeDb):
    async def fetchrow(self, query, *args):
        if "WHERE company_id=$1 AND external_message_id=$2" in query:
            company_id, external_id = args[0], args[1]
            for message in self.messages.values():
                if message.get("company_id") == company_id and message.get("external_message_id") == external_id:
                    return {"id": message["id"], "conversation_id": message["conversation_id"]}
            return None
        if "SELECT * FROM conversations WHERE customer_id" in query:
            return None
        if "SELECT * FROM conversations WHERE id=$1" in query:
            return self.conversations.get(args[0])
        if "SELECT * FROM messages WHERE id=$1" in query:
            return self.messages.get(args[0])
        return None

    async def execute(self, query, *args):
        if query.startswith("SELECT pg_advisory_xact_lock"):
            return None
        if query.startswith("INSERT INTO conversations"):
            convo_id, company_id, customer_id, customer_name = args[0], args[1], args[2], args[3]
            self.conversations[convo_id] = {
                "id": convo_id,
                "company_id": company_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "channel": args[5],
                "channel_id": args[9],
                "ai_handled": False,
                "message_count": 0,
                "unread_count": 0,
            }
            return None
        if query.startswith("INSERT INTO messages"):
            self.messages[args[0]] = {
                "id": args[0],
                "company_id": args[1],
                "conversation_id": args[2],
                "content": args[3],
                "sender_type": "agent",
                "sender_id": args[4],
                "sender_name": args[5],
                "external_message_id": args[6],
                "idempotency_key": args[7],
                "delivery_status": "sent",
                "read": True,
                "created_at": args[8],
            }
            return None
        if query.startswith("UPDATE conversations SET last_message"):
            preview, created_at, convo_id = args[0], args[1], args[2]
            convo = self.conversations[convo_id]
            convo["last_message"] = preview
            convo["last_message_at"] = created_at
            convo["message_count"] += 1
            convo["ai_handled"] = False
            self.rollup[convo_id] = {
                "last_message": preview,
                "message_count": convo["message_count"],
                "unread_count": convo["unread_count"],
            }
            return None
        return await super().execute(query, *args)


def test_whatsapp_bridge_outbound_message_persists_as_agent_without_unread(monkeypatch):
    asyncio.run(_run_whatsapp_bridge_outbound_message_persists_as_agent_without_unread(monkeypatch))


async def _run_whatsapp_bridge_outbound_message_persists_as_agent_without_unread(monkeypatch):
    db = _OutboundFakeDb()
    emitted = []

    async def auto_capture(*_args, **_kwargs):
        return {
            "customer": {
                "id": "cust-1",
                "company_id": "company-1",
                "name": "Bilal",
                "phone": "+923445563662",
            },
            "lead_id": "",
            "lead": {},
            "company_id": "company-1",
        }

    async def validate_company(_db, cid, **_kwargs):
        return cid

    monkeypatch.setattr(webhooks, "_auto_capture_lead", auto_capture)
    monkeypatch.setattr(webhooks, "_validate_resolved_company_id", validate_company)
    monkeypatch.setattr(webhooks, "_ensure_messages_idempotency_schema", lambda *_args, **_kwargs: _async_none())
    monkeypatch.setattr(webhooks, "save_message_attachments", lambda *_args, **_kwargs: _async_value([]))
    monkeypatch.setattr(webhooks, "insert_chat_history_record", lambda *_args, **_kwargs: _async_none())
    monkeypatch.setattr(webhooks, "apply_message_stage_transition", lambda *_args, **_kwargs: _async_none())
    monkeypatch.setattr(webhooks, "_load_message_with_attachments", lambda _db, msg_id: _async_value(db.messages[msg_id]))
    monkeypatch.setattr(webhooks, "emit_new_message", lambda convo_id, message: _record_emit(emitted, convo_id, message))

    processed = await webhooks._process_unified_outbound_bridge_message(
        db,
        UnifiedMessage(
            message_id="wamid.mobile-1",
            tenant_id="company-1",
            external_user_id="+923445563662",
            channel_type=ChannelType.WHATSAPP,
            direction=MessageDirection.OUTBOUND,
            content="sent from mobile",
            timestamp=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
            metadata={
                "company_id": "company-1",
                "source": "whatsapp_web_bridge",
                "direction": "outbound",
                "bridge_user_id": "user-1",
                "outbound_external_message_id": "wamid.mobile-1",
            },
        ),
        sender_name="Bilal",
        sender_contact="+923445563662",
    )

    message = db.messages[processed["message_id"]]
    assert message["sender_type"] == "agent"
    assert message["sender_id"] == "user-1"
    assert message["external_message_id"] == "wamid.mobile-1"
    assert message["delivery_status"] == "sent"
    assert db.rollup[processed["conversation_id"]]["last_message"] == "sent from mobile"
    assert db.rollup[processed["conversation_id"]]["unread_count"] == 0
    assert emitted == [(processed["conversation_id"], message)]


def test_whatsapp_alias_candidates_include_lid_phone_and_chat():
    aliases = webhooks._whatsapp_alias_candidates(
        {
            "target_lid_jid": "222436977021015@lid",
            "target_phone_digits": "923445563662",
            "msg_to": "923445563662@c.us",
            "chat_id": "923445563662@c.us",
        },
        sender_contact="222436977021015@lid",
        channel_binding="923445563662@c.us",
    )

    normalized = {(item["identity_type"], item["identity_value_normalized"]) for item in aliases}
    assert ("lid_jid", "222436977021015@lid") in normalized
    assert ("phone", "923445563662") in normalized
    assert ("jid", "923445563662@s.whatsapp.net") in normalized


class _DedupFakeDb:
    def __init__(self):
        self.rows = []
        self.updated = 0
        self.executed = []

    async def fetchrow(self, query, *args):
        if "FROM whatsapp_event_dedup" in query and "provider_event_id=$4" in query:
            company_id, account_id, event_type, provider_event_id = args[:4]
            for row in self.rows:
                if (
                    row["company_id"] == company_id
                    and row["account_id"] == account_id
                    and row["event_type"] == event_type
                    and row["provider_event_id"] == provider_event_id
                ):
                    return row
        if "FROM whatsapp_event_dedup" in query and "idempotency_key=$2" in query:
            company_id, idempotency_key = args[:2]
            for row in self.rows:
                if row["company_id"] == company_id and row["idempotency_key"] == idempotency_key:
                    return row
        return None

    async def execute(self, query, *args):
        self.executed.append((query, args))
        if query.startswith("INSERT INTO whatsapp_event_dedup"):
            self.rows.append(
                {
                    "id": args[0],
                    "company_id": args[1],
                    "account_id": args[2],
                    "event_type": args[3],
                    "provider_event_id": args[4],
                    "idempotency_key": args[5],
                    "attempts": 1,
                }
            )
        if query.startswith("UPDATE whatsapp_event_dedup SET attempts"):
            self.updated += 1
        return None


def test_whatsapp_event_dedup_skips_duplicate_message(monkeypatch):
    asyncio.run(_run_whatsapp_event_dedup_skips_duplicate_message(monkeypatch))


async def _run_whatsapp_event_dedup_skips_duplicate_message(monkeypatch):
    monkeypatch.setattr(webhooks, "_WHATSAPP_IDENTITY_SCHEMA_READY", True)
    db = _DedupFakeDb()
    first = await webhooks._record_whatsapp_event_dedup(
        db,
        "company-1",
        {"business_account_id": "waba-1"},
        event_type="message_create",
        provider_event_id="wamid.mobile-1",
        idempotency_key="whatsapp:company-1:out:wamid.mobile-1",
        payload={"id": "wamid.mobile-1"},
    )
    second = await webhooks._record_whatsapp_event_dedup(
        db,
        "company-1",
        {"business_account_id": "waba-1"},
        event_type="message_create",
        provider_event_id="wamid.mobile-1",
        idempotency_key="whatsapp:company-1:out:wamid.mobile-1",
        payload={"id": "wamid.mobile-1"},
    )

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert len(db.rows) == 1
    assert db.updated == 1


class _PendingFakeDb:
    def __init__(self):
        self.pending_inserts = []
        self.executed = []

    async def execute(self, query, *args):
        self.executed.append((query, args))
        if query.startswith("INSERT INTO whatsapp_pending_messages"):
            self.pending_inserts.append(args)
        return None


def test_unresolved_lid_outbound_is_persisted_pending(monkeypatch):
    asyncio.run(_run_unresolved_lid_outbound_is_persisted_pending(monkeypatch))


async def _run_unresolved_lid_outbound_is_persisted_pending(monkeypatch):
    monkeypatch.setattr(webhooks, "_WHATSAPP_IDENTITY_SCHEMA_READY", True)
    alias_calls = []

    async def record_alias(*args, **kwargs):
        alias_calls.append((args, kwargs))
        return "identity-1"

    monkeypatch.setattr(webhooks, "_upsert_whatsapp_identity_aliases", record_alias)
    db = _PendingFakeDb()

    result = await webhooks._store_pending_whatsapp_message(
        db,
        company_id="company-1",
        metadata_payload={
            "business_account_id": "waba-1",
            "target_lid_jid": "222436977021015@lid",
        },
        direction="outbound",
        provider_event_id="wamid.lid-1",
        raw_identity="222436977021015@lid",
        payload={"content": "hello"},
    )

    assert result["pending_identity"] is True
    assert db.pending_inserts
    assert db.pending_inserts[0][4] == "wamid.lid-1"
    assert db.pending_inserts[0][5] == "222436977021015@lid"
    assert alias_calls


async def _async_none():
    return None


async def _async_value(value):
    return value


async def _record_emit(emitted, conversation_id, message):
    emitted.append((conversation_id, message))
