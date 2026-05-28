from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from services.identity_service.app.db.database import get_db as _get_db
from services.identity_service.app.services.cache import CacheService, get_cache_service


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in _get_db():
        yield session


async def get_cache() -> AsyncGenerator[CacheService, None]:
    """Provide cache dependency for request handlers."""
    yield get_cache_service()
