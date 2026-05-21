import base64

import pytest
from fastapi import HTTPException
from fastapi.responses import PlainTextResponse

from routers import conversations
from services import media_storage
from services.db_helpers import normalize_attachment_payload


def _tiny_png_data_url() -> str:
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _tiny_mp4_data_url() -> str:
    return "data:video/mp4;base64," + base64.b64encode(b"\x00\x00\x00\x18ftypmp42").decode("ascii")


def test_store_image_data_url_writes_file_and_returns_media_url(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(tmp_path))

    stored = media_storage.store_image_data_url(
        _tiny_png_data_url(),
        category="message-attachments",
        company_id="company-1",
        public_url_prefix="/api/conversations/attachments/media",
        original_filename="photo.png",
    )

    assert stored["url"].startswith("/api/conversations/attachments/media/company-1/")
    assert stored["mime_type"] == "image/png"
    assert stored["file_size"] > 0
    assert (tmp_path / stored["storage_key"]).exists()


def test_normalize_attachment_payload_preserves_image_metadata():
    payload = normalize_attachment_payload(
        {
            "type": "image",
            "data_url": _tiny_png_data_url(),
            "name": "photo.png",
            "mime_type": "image/png",
            "size": 123,
            "provider_media_id": "wamedia-1",
            "raw_metadata": {"source": "whatsapp"},
        }
    )

    assert payload["type"] == "image"
    assert payload["url"].startswith("data:image/png")
    assert payload["mime_type"] == "image/png"
    assert payload["provider_media_id"] == "wamedia-1"
    assert payload["raw_metadata"]["source"] == "whatsapp"


def test_store_video_data_url_writes_file_and_returns_media_url(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_STORAGE_DIR", str(tmp_path))

    stored = media_storage.store_media_data_url(
        _tiny_mp4_data_url(),
        category="message-attachments",
        company_id="company-1",
        public_url_prefix="/api/conversations/attachments/media",
        original_filename="clip.mp4",
    )

    assert stored["url"].startswith("/api/conversations/attachments/media/company-1/")
    assert stored["mime_type"] == "video/mp4"
    assert stored["file_name"] == "clip.mp4"
    assert (tmp_path / stored["storage_key"]).exists()


def test_normalize_attachment_payload_detects_video_data_url():
    payload = normalize_attachment_payload(
        {
            "type": "video",
            "data_url": _tiny_mp4_data_url(),
            "name": "clip.mp4",
            "mime_type": "video/mp4",
        }
    )

    assert payload["type"] == "video"
    assert payload["url"].startswith("data:video/mp4")
    assert payload["mime_type"] == "video/mp4"


class _DummyRequest:
    pass


_COMPANY_1 = "11111111-1111-1111-1111-111111111111"
_COMPANY_2 = "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_conversation_media_route_denies_unauthenticated(monkeypatch):
    async def deny_auth(_request):
        raise HTTPException(status_code=401, detail="Authentication required")

    monkeypatch.setattr(conversations, "get_current_user_flexible", deny_auth)

    with pytest.raises(HTTPException) as exc:
        await conversations.get_conversation_attachment_media(_COMPANY_1, "photo.png", _DummyRequest())

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_conversation_media_route_denies_wrong_company(monkeypatch):
    async def fake_auth(_request):
        return {"id": "user-1", "company_id": _COMPANY_2}

    monkeypatch.setattr(conversations, "get_current_user_flexible", fake_auth)

    with pytest.raises(HTTPException) as exc:
        await conversations.get_conversation_attachment_media(_COMPANY_1, "photo.png", _DummyRequest())

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_conversation_media_route_allows_same_company(monkeypatch):
    captured = {}

    async def fake_auth(_request):
        return {"id": "user-1", "company_id": _COMPANY_1}

    def fake_serve_stored_media(**kwargs):
        captured.update(kwargs)
        return PlainTextResponse("ok")

    monkeypatch.setattr(conversations, "get_current_user_flexible", fake_auth)
    monkeypatch.setattr(conversations, "serve_stored_media", fake_serve_stored_media)

    response = await conversations.get_conversation_attachment_media(_COMPANY_1, "photo.png", _DummyRequest())

    assert response.body == b"ok"
    assert captured == {
        "category": "message-attachments",
        "company_id": _COMPANY_1,
        "filename": "photo.png",
    }


@pytest.mark.asyncio
async def test_conversation_media_route_denies_path_traversal(monkeypatch):
    called = False

    async def fake_auth(_request):
        return {"id": "user-1", "company_id": _COMPANY_1}

    def fake_serve_stored_media(**_kwargs):
        nonlocal called
        called = True
        return PlainTextResponse("ok")

    monkeypatch.setattr(conversations, "get_current_user_flexible", fake_auth)
    monkeypatch.setattr(conversations, "serve_stored_media", fake_serve_stored_media)

    with pytest.raises(HTTPException) as exc:
        await conversations.get_conversation_attachment_media(_COMPANY_1, "../secret.png", _DummyRequest())

    assert exc.value.status_code == 404
    assert called is False
