from __future__ import annotations

import os

import uvicorn

from services.identity_service.app.server import app

__all__ = ["app"]


try:
    from shared.config import service_port
except Exception:  # pragma: no cover - standalone fallback

    def service_port(default: int = 8010) -> int:
        raw = os.environ.get("PORT")
        if raw is None:
            return int(default)
        try:
            return int(raw)
        except ValueError:
            return int(default)


if __name__ == "__main__":
    uvicorn.run(
        "services.identity_service.main:app",
        host="0.0.0.0",
        port=service_port(8010),
    )
