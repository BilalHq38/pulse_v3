import asyncio

import routers.webhooks as webhooks
from routers.webhooks import _extract_sender_contact_fields, _is_whatsapp_web_bridge_payload


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
    assert fields["channel_id"] == ""


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
            last_message, convo_id = args[0], args[1]
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


async def _async_none():
    return None


async def _async_value(value):
    return value


async def _record_emit(emitted, conversation_id, message):
    emitted.append((conversation_id, message))
