from .base import Base
from .session import get_engine

# Ensure model metadata is registered before create_all/drop_all calls.
from . import models  # noqa: F401


async def create_postgres_schema(drop_existing: bool = False) -> None:
    engine = get_engine()
    async with engine.begin() as connection:
        if drop_existing:
            await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
