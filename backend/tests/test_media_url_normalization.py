import os
import pytest
from unittest.mock import patch, MagicMock
from core.request_helpers import normalize_public_media_url
from services.messaging_service import _normalize_outbound_attachments

@pytest.fixture
def mock_env():
    with patch.dict(os.environ, {"PUBLIC_BACKEND_URL": "https://domain.com"}, clear=True):
        yield

def test_absolute_url_remains_unchanged(mock_env):
    assert normalize_public_media_url("https://external.com/image.png") == "https://external.com/image.png"
    assert normalize_public_media_url("http://insecure.com/image.png") == "http://insecure.com/image.png"

def test_relative_url_normalization_with_leading_slash(mock_env):
    assert normalize_public_media_url("/api/products/media/a.png") == "https://domain.com/api/products/media/a.png"

def test_relative_url_normalization_without_leading_slash(mock_env):
    assert normalize_public_media_url("media/products/a.png") == "https://domain.com/media/products/a.png"

def test_query_params_are_preserved(mock_env):
    assert normalize_public_media_url("/api/media/a.png?v=123&force=true") == "https://domain.com/api/media/a.png?v=123&force=true"

def test_internal_urls_are_rewritten(mock_env):
    assert normalize_public_media_url("http://localhost:3000/api/media/a.png") == "https://domain.com/api/media/a.png"
    assert normalize_public_media_url("http://127.0.0.1:3000/api/media/a.png") == "https://domain.com/api/media/a.png"
    assert normalize_public_media_url("http://api-gateway:8000/api/products/media/b.png") == "https://domain.com/api/products/media/b.png"

@patch.dict(os.environ, {}, clear=True)
def test_missing_public_base_url_skips_media():
    attachments = [{"url": "/api/products/media/a.png", "product_id": "p-123"}]
    with patch("services.messaging_service.logger.warning") as mock_logger:
        normalized = _normalize_outbound_attachments(attachments)
        assert len(normalized) == 0
        mock_logger.assert_called_once()
        assert "outbound_media_skipped" in mock_logger.call_args[0][0]
        assert mock_logger.call_args[0][1] == "whatsapp"
        assert mock_logger.call_args[0][2] == "/api/products/media/a.png"

def test_whatsapp_dispatch_receives_only_absolute_media_urls(mock_env):
    attachments = [
        {"url": "https://external.com/pic.jpg"},
        {"url": "/api/products/media/local.jpg"}
    ]
    normalized = _normalize_outbound_attachments(attachments)
    assert len(normalized) == 2
    assert normalized[0]["url"] == "https://external.com/pic.jpg"
    assert normalized[1]["url"] == "https://domain.com/api/products/media/local.jpg"

def test_whatsapp_dispatch_normalizes_internal_absolute_urls(mock_env):
    attachments = [
        {"url": "http://localhost:3000/api/conversations/attachments/media/img.png"}
    ]
    normalized = _normalize_outbound_attachments(attachments)
    assert len(normalized) == 1
    assert normalized[0]["url"] == "https://domain.com/api/conversations/attachments/media/img.png"

def test_product_image_data_url_passes_through():
    attachments = [
        {"data_url": "data:image/png;base64,iVBOR", "url": ""}
    ]
    normalized = _normalize_outbound_attachments(attachments)
    assert len(normalized) == 1
    assert normalized[0]["data_url"] == "data:image/png;base64,iVBOR"
    assert normalized[0]["url"] == ""

@pytest.mark.asyncio
async def test_product_image_attachments_from_db_successfully_sent_to_meta(mock_env):
    from services.messaging_service import send_whatsapp_message
    
    # Mocking _send_via_bridge or _send_via_tenant_meta to ensure they receive absolute URLs
    with patch("services.messaging_service._send_via_bridge") as mock_send:
        mock_send.return_value = (True, "", "msg_123")
        
        # Simulating attachments created by _normalize_ai_attachments in response_generator.py
        attachments = [{"url": "/api/products/media/product_1.jpg", "product_id": "prod-1"}]
        
        # This will call _normalize_outbound_attachments internally
        with patch("services.messaging_service._use_bridge", return_value=True), \
             patch("services.messaging_service._bridge_session_snapshot", return_value={"state": "ready"}):
             
             await send_whatsapp_message("1234567890", "Check this product", attachments=attachments)
             
             assert mock_send.call_count == 1
             # We check that the attachments argument passed down to _send_via_bridge has absolute URL
             passed_attachments = mock_send.call_args.kwargs.get("attachments")
             assert passed_attachments[0]["url"] == "https://domain.com/api/products/media/product_1.jpg"
