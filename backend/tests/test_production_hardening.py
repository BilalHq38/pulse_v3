from pathlib import Path

import pytest

from services.media_storage import storage_backend
from shared.config import gateway_allowed_origins
from shared.database import _assert_encrypted_database_configuration


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_production_cors_requires_explicit_origins(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "")
    monkeypatch.setenv("APP_URL", "")
    monkeypatch.setenv("GATEWAY_ALLOWED_ORIGINS", "")
    gateway_allowed_origins.cache_clear()
    with pytest.raises(RuntimeError, match="explicit production origins"):
        gateway_allowed_origins()
    gateway_allowed_origins.cache_clear()


def test_production_media_storage_requires_s3(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    with pytest.raises(RuntimeError, match="requires MEDIA_STORAGE_BACKEND=s3"):
        storage_backend()


def test_production_database_configuration_requires_tls(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("POSTGRES_SSLMODE", "disable")
    with pytest.raises(RuntimeError, match="require, verify-ca, or verify-full"):
        _assert_encrypted_database_configuration("postgresql://user:pass@db/app")


def test_compose_does_not_publish_internal_services_or_mount_cloud_credentials():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for blocked in (
        "8003:8003",
        "8011:8011",
        "5433}:5432",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "gcp-vertex-sa.json",
        '"noeviction"',
    ):
        assert blocked not in compose


def test_frontend_csp_allows_configured_google_fonts():
    nginx_conf = (REPO_ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")

    assert "https://fonts.googleapis.com" in nginx_conf
    assert "https://fonts.gstatic.com" in nginx_conf


def test_frontend_csp_allows_local_api_media_in_dev():
    nginx_conf = (REPO_ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")

    assert "img-src 'self' data: blob: https: http://localhost:8000 http://127.0.0.1:8000" in nginx_conf
