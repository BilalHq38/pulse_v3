"""
One-off: normalize existing leads.phone and customers.phone to E.164 digits (no +) where
libphonenumber can validate the value.

Usage (from repo root, with DATABASE_URL in env or .env):
  python -m backend.scripts.backfill_phone_e164
  # or: cd backend && python -m scripts.backfill_phone_e164

Requires: session role can UPDATE rows (RLS: sets app.current_company per tenant).
Rows that cannot be parsed to a valid number are left unchanged; IDs are printed on stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Load .env from repo root
for candidate in (BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"):
    if candidate.is_file():
        from dotenv import load_dotenv

        load_dotenv(candidate)
        break

import asyncpg  # noqa: E402

from core.phone_normalization import strict_normalize_to_e164_digits  # noqa: E402


async def _set_company(conn: asyncpg.Connection, company_id: str) -> None:
    await conn.execute("SELECT set_config('app.current_company', $1, true)", company_id)


async def backfill_table(
    conn: asyncpg.Connection,
    *,
    table: str,
    dry_run: bool,
) -> tuple[int, int, list[str]]:
    company_rows = await conn.fetch("SELECT id AS company_id FROM companies WHERE is_active IS DISTINCT FROM false")
    updated = 0
    skipped = 0
    failed_ids: list[str] = []
    for row in company_rows:
        company_id = row["company_id"]
        await _set_company(conn, company_id)
        rows = await conn.fetch(
            f"SELECT id, phone FROM {table} WHERE company_id = $1 AND btrim(phone) <> ''",
            company_id,
        )
        for r in rows:
            old = (r["phone"] or "").strip()
            if not old:
                continue
            new = strict_normalize_to_e164_digits(old)
            if not new or new == old:
                if not new:
                    skipped += 1
                    failed_ids.append(f"{table}:{r['id']}")
                continue
            if not dry_run:
                await conn.execute(
                    f"UPDATE {table} SET phone = $1, updated_at = NOW() WHERE id = $2 AND company_id = $3",
                    new,
                    r["id"],
                    company_id,
                )
            updated += 1
    return updated, skipped, failed_ids


async def run(dry_run: bool) -> None:
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        raise SystemExit(1)
    conn = await asyncpg.connect(dsn)
    try:
        cu, su, cfail = await backfill_table(conn, table="customers", dry_run=dry_run)
        lu, luu, lfail = await backfill_table(conn, table="leads", dry_run=dry_run)
        mode = "DRY-RUN" if dry_run else "APPLIED"
        print(f"[{mode}] customers: updated {cu} rows, skipped (unparseable) {su}; leads: {lu} / {luu}")
        if cfail or lfail:
            n = cfail + lfail
            print(
                f"Note: {len(n)} row(s) could not be parsed (unchanged). First few: {n[:20]}",
                file=sys.stderr,
            )
    finally:
        await conn.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill E.164 phone columns for customers and leads.")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only, do not UPDATE.",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(dry_run=args.dry_run))
