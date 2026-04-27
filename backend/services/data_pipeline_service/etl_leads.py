"""Lead ETL cleanup helpers.

Reads rows from the ``leads`` table for a tenant, normalizes contact fields,
removes duplicates, fills nulls, and writes the cleaned subset into the
``leads_clean`` mirror table.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


_PHONE_NON_DIGIT = re.compile(r"\D+")
CleanLeadRow = dict[str, Any]


def _normalize_phone(value: Any) -> str:
    if value is None:
        return ""
    candidate = str(value).strip()
    if not candidate:
        return ""
    digits = _PHONE_NON_DIGIT.sub("", candidate)
    if not digits:
        return ""
    return digits[-10:] if len(digits) >= 10 else digits


def _normalize_email(value: Any) -> str:
    if value is None:
        return ""
    candidate = str(value).strip().lower()
    if "@" not in candidate:
        return ""
    local, _, domain = candidate.partition("@")
    local = local.split("+", 1)[0]
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.replace(".", "")
    return f"{local}@{domain}" if local else ""


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    return not str(value).strip()


def _normalize_text(value: Any, *, default: str = "") -> str:
    if _is_blank(value):
        return default
    return str(value).strip()


def _coerce_created_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, str):
        candidate = value.strip()
        if candidate:
            if candidate.endswith("Z"):
                candidate = f"{candidate[:-1]}+00:00"
            try:
                parsed = datetime.fromisoformat(candidate)
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)

    return datetime.now(timezone.utc)


def clean_leads_dataframe(rows: list[dict[str, Any]]) -> tuple[list[CleanLeadRow], dict[str, int]]:
    """Return normalized rows plus ETL stats."""
    if not rows:
        return [], {"rows_in": 0, "rows_out": 0, "duplicates_removed": 0, "null_fills": 0}

    rows_in = len(rows)
    null_fill_count = 0
    normalized_rows: list[CleanLeadRow] = []

    for index, raw_row in enumerate(rows):
        null_fill_count += int(_is_blank(raw_row.get("name")))
        null_fill_count += int(_is_blank(raw_row.get("email")))
        null_fill_count += int(_is_blank(raw_row.get("phone")))
        null_fill_count += int(_is_blank(raw_row.get("status")))
        null_fill_count += int(_is_blank(raw_row.get("source")))

        normalized_rows.append(
            {
                "id": str(raw_row.get("id") or ""),
                "company_id": str(raw_row.get("company_id") or ""),
                "name": _normalize_text(raw_row.get("name")),
                "email_normalized": _normalize_email(raw_row.get("email")),
                "phone_digits": _normalize_phone(raw_row.get("phone")),
                "status": _normalize_text(raw_row.get("status"), default="new"),
                "source": _normalize_text(raw_row.get("source"), default="web_chat"),
                "created_at": _coerce_created_at(raw_row.get("created_at")),
                "_source_order": index,
            }
        )

    normalized_rows.sort(key=lambda row: (row["created_at"], row["_source_order"]))

    seen_triplets: set[tuple[str, str, str]] = set()
    seen_email: set[tuple[str, str]] = set()
    seen_phone: set[tuple[str, str]] = set()
    cleaned_rows: list[CleanLeadRow] = []
    duplicates_removed = 0

    for row in normalized_rows:
        company = row["company_id"]
        email_key = (company, row["email_normalized"])
        phone_key = (company, row["phone_digits"])
        triplet_key = (company, row["email_normalized"], row["phone_digits"])

        if triplet_key in seen_triplets:
            duplicates_removed += 1
            continue

        if row["email_normalized"] and email_key in seen_email:
            duplicates_removed += 1
            continue

        if row["phone_digits"] and phone_key in seen_phone:
            duplicates_removed += 1
            continue

        seen_triplets.add(triplet_key)
        if row["email_normalized"]:
            seen_email.add(email_key)
        if row["phone_digits"]:
            seen_phone.add(phone_key)

        if not row["email_normalized"] and not row["phone_digits"]:
            continue

        cleaned_rows.append(
            {
                "id": row["id"],
                "company_id": company,
                "name": row["name"],
                "email_normalized": row["email_normalized"],
                "phone_digits": row["phone_digits"],
                "status": row["status"],
                "source": row["source"],
                "created_at": row["created_at"],
            }
        )

    return cleaned_rows, {
        "rows_in": int(rows_in),
        "rows_out": len(cleaned_rows),
        "duplicates_removed": int(duplicates_removed),
        "null_fills": int(null_fill_count),
    }


async def write_clean_leads(db: Any, rows: list[CleanLeadRow]) -> int:
    """Upsert cleaned rows into ``leads_clean``. Returns the number of rows written."""
    if not rows:
        return 0

    query = """
        INSERT INTO leads_clean
            (id, company_id, name, email_normalized, phone_digits,
             status, source, created_at, cleaned_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            email_normalized = EXCLUDED.email_normalized,
            phone_digits = EXCLUDED.phone_digits,
            status = EXCLUDED.status,
            source = EXCLUDED.source,
            created_at = EXCLUDED.created_at,
            cleaned_at = NOW()
    """
    args_list = [
        (
            str(row["id"]),
            str(row["company_id"]),
            str(row["name"]),
            str(row["email_normalized"]),
            str(row["phone_digits"]),
            str(row["status"]),
            str(row["source"]),
            row["created_at"],
        )
        for row in rows
    ]

    try:
        await db.executemany(query, args_list)
        return len(args_list)
    except Exception as exc:
        logger.warning("etl_leads.write_clean_leads batch write failed: %s", exc)

    rows_written = 0
    for row_args, row in zip(args_list, rows):
        try:
            await db.execute(query, *row_args)
            rows_written += 1
        except Exception as row_exc:
            logger.warning("etl_leads.write_clean_leads failed for id=%s: %s", row.get("id"), row_exc)
    return rows_written
