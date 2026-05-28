from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_database_url, postgres_debug


@lru_cache(maxsize=1)
def get_engine():
    return create_async_engine(
        get_database_url(),
        echo=postgres_debug(),
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    factory = session_factory()
    async with factory() as session:
        yield session


async def check_postgres_health() -> bool:
    engine = get_engine()
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return True
