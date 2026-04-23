import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def get_database_url() -> str:
    url = (os.environ.get("DATABASE_URL", "") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL mode")
    return url


def postgres_debug() -> bool:
    return (os.environ.get("POSTGRES_ECHO", "false") or "false").strip().lower() == "true"
