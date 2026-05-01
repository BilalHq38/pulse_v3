import base64

from services import media_storage
from services.db_helpers import normalize_attachment_payload


def _tiny_png_data_url() -> str:
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


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
