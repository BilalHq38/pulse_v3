"""ASGI entry re-export (optional); Docker uses ``services.identity_service.main:app``."""

from services.identity_service.app.server import app

__all__ = ["app"]
