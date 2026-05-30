import pytest

from routers import webhooks
from services import db_helpers
from services.db_helpers import fetch_messages_with_attachments, save_message_reaction


class ReactionDB:
    def __init__(self):
        self.messages = {
            "msg-1": {
                "id": "msg-1",
                "company_id": "company-1",
                "conversation_id": "convo-1",
                "external_message_id": "provider-msg-1",
                "content": "hello",
                "sender_type": "agent",
                "created_at": "2026-05-01T00:00:00Z",
            }
        }
        self.reactions = {}

    async def execute(self, query, *args):
        return None

    async def fetchrow(self, query, *args):
        if "FROM messages" in query and "external_message_id=$2" in query:
            company_id, external_id = args[0], args[1]
            for message in self.messages.values():
                if message["company_id"] == company_id and (
                    message["external_message_id"] == external_id or message["id"] == external_id
                ):
                    return message
            return None
        if "FROM messages" in query and "id=$2" in query:
            company_id, message_id = args[0], args[1]
            message = self.messages.get(message_id)
            return message if message and message["company_id"] == company_id else None
        if "FROM message_reactions" in query and "provider_message_id=$3" in query and query.strip().upper().startswith("SELECT"):
            company_id, channel, provider_message_id = args[0], args[1], args[2]
            for reaction in self.reactions.values():
                if (
                    reaction["company_id"] == company_id
                    and reaction["channel"] == channel
                    and reaction["provider_message_id"] == provider_message_id
                ):
                    return reaction
            return None
        if query.strip().upper().startswith("INSERT INTO MESSAGE_REACTIONS"):
            row = {
                "id": args[0],
                "company_id": args[1],
                "conversation_id": args[2],
                "message_id": args[3],
                "provider_message_id": args[4],
                "target_provider_message_id": args[5],
                "channel": args[6],
                "actor_type": args[7],
                "actor_id": args[8],
                "emoji": args[9],
                "action": args[10],
                "raw_payload": args[11],
            }
            self.reactions[row["id"]] = row
            return row
        if query.strip().upper().startswith("UPDATE MESSAGE_REACTIONS"):
            reaction_id = args[8]
            row = self.reactions[reaction_id]
            row.update(
                {
                    "conversation_id": args[0],
                    "message_id": args[1],
                    "target_provider_message_id": args[2],
                    "actor_type": args[3],
                    "actor_id": args[4],
                    "emoji": args[5],
                    "action": args[6],
                    "raw_payload": args[7],
                }
            )
            return row
        return None

    async def fetch(self, query, *args):
        if "FROM messages" in query:
            return list(self.messages.values())
        if "FROM message_attachments" in query:
            return []
        if "FROM message_reactions" in query:
            message_ids = set(args[0])
            return [
                reaction
                for reaction in self.reactions.values()
                if reaction["message_id"] in message_ids and reaction["action"] != "removed"
            ]
        return []


class MissingReactionTableDB(ReactionDB):
    def __init__(self):
        super().__init__()
        self.reaction_table_exists = False
        self.create_attempts = 0

    async def fetchval(self, query, *args):
        if "current_schema" in query:
            return "customer_service"
        if "to_regclass('message_reactions')" in query:
            return self.reaction_table_exists
        return None

    async def execute(self, query, *args):
        if query.strip().upper().startswith("CREATE TABLE IF NOT EXISTS MESSAGE_REACTIONS"):
            self.create_attempts += 1
            self.reaction_table_exists = True
        return None

    async def fetch(self, query, *args):
        if "FROM message_reactions" in query and not self.reaction_table_exists:
            class UndefinedTableError(Exception):
                pass

            raise UndefinedTableError('relation "message_reactions" does not exist')
        return await super().fetch(query, *args)


@pytest.mark.asyncio
async def test_message_reaction_is_idempotent_and_returned_with_message():
    db = ReactionDB()

    first = await save_message_reaction(
        db,
        company_id="company-1",
        channel="whatsapp",
        provider_message_id="reaction-event-1",
        target_provider_message_id="provider-msg-1",
        actor_type="customer",
        actor_id="+15551234567",
        emoji="👍",
        raw_payload={"type": "reaction"},
    )
    second = await save_message_reaction(
        db,
        company_id="company-1",
        channel="whatsapp",
        provider_message_id="reaction-event-1",
        target_provider_message_id="provider-msg-1",
        actor_type="customer",
        actor_id="+15551234567",
        emoji="❤️",
        action="updated",
        raw_payload={"type": "reaction"},
    )

    assert first["id"] == second["id"]
    assert second["emoji"] == "❤️"
    messages = await fetch_messages_with_attachments(db, "convo-1")
    assert messages[0]["reactions"][0]["emoji"] == "❤️"


@pytest.mark.asyncio
async def test_fetch_messages_recreates_missing_reaction_table_after_stale_ready_flag():
    db = MissingReactionTableDB()
    db_helpers._message_reaction_schema_ready.add("customer_service")

    messages = await fetch_messages_with_attachments(db, "convo-1")

    assert db.create_attempts == 1
    assert messages[0]["reactions"] == []


def test_reaction_payload_extractors_support_whatsapp_and_meta_shapes():
    whatsapp = webhooks._extract_whatsapp_reaction(
        {"id": "reaction-1", "type": "reaction", "reaction": {"message_id": "wamid.1", "emoji": "👍"}}
    )
    facebook = webhooks._extract_messenger_reaction(
        {"sender": {"id": "psid-1"}, "reaction": {"id": "r1", "message_id": "mid.1", "emoji": "😮"}}
    )

    assert whatsapp["target_provider_message_id"] == "wamid.1"
    assert whatsapp["action"] == "added"
    assert facebook["target_provider_message_id"] == "mid.1"
    assert facebook["emoji"] == "😮"
