import asyncio

from core import socket as socket_module


def test_new_message_socket_event_payload_and_rooms(monkeypatch):
    emitted = []

    async def fake_resolve(conversation_id):
        assert conversation_id == "convo-1"
        return "company-1"

    async def fake_emit(event, payload, room=None):
        emitted.append((event, payload, room))

    monkeypatch.setattr(socket_module, "_resolve_conversation_company_id", fake_resolve)
    monkeypatch.setattr(socket_module.sio, "emit", fake_emit)

    asyncio.run(socket_module.emit_new_message("convo-1", {"id": "msg-1", "content": "hello"}))

    assert emitted[0] == (
        "new_message",
        {"conversation_id": "convo-1", "message": {"id": "msg-1", "content": "hello"}},
        "convo_convo-1",
    )
    assert emitted[1] == (
        "conversation_updated",
        {"conversation_id": "convo-1"},
        "company_company-1",
    )
