import argparse
import asyncio
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from postgres.bootstrap import create_postgres_schema  # noqa: E402
from postgres.session import check_postgres_health, get_engine  # noqa: E402


async def run(drop_existing: bool = False) -> None:
    await create_postgres_schema(drop_existing=drop_existing)
    healthy = await check_postgres_health()
    print(f"[postgres] schema ready, health={healthy}")
    await get_engine().dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create PostgreSQL schema for Pulse Engine.")
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop existing schema tables before creating.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(drop_existing=args.drop_existing))
