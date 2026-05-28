"""Backfill ingest-time summaries for existing knowledge_base rows.

Iterates every row that has non-empty `content` longer than the minimum
summary threshold and an empty `summary`, and enqueues (or directly runs)
the summary job. Designed to be safe to re-run — `summary_generated_at`
acts as a watermark and the job is idempotent.

Usage (inside the backend container or virtualenv):

    python -m scripts.backfill_kb_summaries [--company-id CID] [--limit 100] [--inline]

By default the script runs every job through the background queue. Pass
`--inline` to skip the queue and call `summarise_kb_row` directly (useful for
local testing without a Redis broker).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# Allow running from repo root: `python -m scripts.backfill_kb_summaries`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg

from services.ai_service.kb_summary_service import (
    enqueue_kb_summary_job,
    summarise_kb_row,
)


logger = logging.getLogger("backfill_kb_summaries")


async def _connect_db() -> asyncpg.Connection:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN")
    if not dsn:
        raise SystemExit("DATABASE_URL or POSTGRES_DSN must be set")
    return await asyncpg.connect(dsn)


async def run(*, company_id: str = "", limit: int = 500, inline: bool = False) -> dict:
    conn = await _connect_db()
    try:
        query = (
            "SELECT id, company_id FROM knowledge_base "
            "WHERE LENGTH(COALESCE(content, '')) >= 800 "
            "  AND BTRIM(COALESCE(summary, '')) = '' "
            "  AND summary_generated_at IS NULL"
        )
        args: list = []
        if company_id:
            query += " AND company_id = $1"
            args.append(company_id)
        query += " LIMIT $%d" % (len(args) + 1)
        args.append(int(limit))
        rows = await conn.fetch(query, *args)

        scanned = len(rows)
        enqueued = 0
        succeeded = 0
        for row in rows:
            kb_company = str(row["company_id"])
            kb_id = str(row["id"])
            if inline:
                ok = await summarise_kb_row(conn, company_id=kb_company, kb_id=kb_id)
                if ok:
                    succeeded += 1
            else:
                await enqueue_kb_summary_job(conn, company_id=kb_company, kb_id=kb_id)
                enqueued += 1
        return {"scanned": scanned, "enqueued": enqueued, "succeeded_inline": succeeded}
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", default="", help="restrict to one company_id")
    parser.add_argument("--limit", type=int, default=500, help="max rows per invocation")
    parser.add_argument(
        "--inline",
        action="store_true",
        help="run the summary call inline (no background queue)",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    result = asyncio.run(run(company_id=args.company_id, limit=args.limit, inline=args.inline))
    print(result)


if __name__ == "__main__":
    main()
