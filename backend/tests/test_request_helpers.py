from types import SimpleNamespace

from core.request_helpers import (
    build_facebook_redirect_uri,
    build_google_redirect_uri,
    resolve_backend_base_url,
    sanitize_frontend_origin,
)


def test_sanitize_frontend_origin_allows_gateway_allowed_origin(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "")
    monkeypatch.setenv("APP_URL", "")
    monkeypatch.setenv("GATEWAY_ALLOWED_ORIGINS", "http://3.80.164.154,https://app.example.test")

    assert sanitize_frontend_origin("http://3.80.164.154") == "http://3.80.164.154"
    assert sanitize_frontend_origin("https://app.example.test") == "https://app.example.test"
    assert sanitize_frontend_origin("http://untrusted.example.test") == ""


def test_oauth_redirect_uri_prefers_explicit_provider_env(monkeypatch):
    request = SimpleNamespace(
        headers={},
        url=SimpleNamespace(scheme="http"),
        base_url="http://gateway:8000/",
        client=None,
    )
    monkeypatch.setenv("PUBLIC_BACKEND_URL", "https://pulse-engine.dev")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://pulse-engine.dev")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://oauth.example.test/google/callback")
    monkeypatch.setenv("FACEBOOK_REDIRECT_URI", "https://oauth.example.test/facebook/callback")

    assert build_google_redirect_uri(request) == "https://oauth.example.test/google/callback"
    assert build_facebook_redirect_uri(request) == "https://oauth.example.test/facebook/callback"


def test_backend_base_url_prefers_public_backend_over_frontend_app_url(monkeypatch):
    request = SimpleNamespace(
        headers={},
        url=SimpleNamespace(scheme="http"),
        base_url="http://gateway:8000/",
        client=None,
    )
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.setenv("APP_URL", "http://3.80.164.154")
    monkeypatch.setenv("PUBLIC_BACKEND_URL", "https://pulse-engine.dev")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://pulse-engine.dev")

    assert resolve_backend_base_url(request) == "https://pulse-engine.dev"
    assert build_google_redirect_uri(request) == "https://pulse-engine.dev/api/auth/google/callback"
