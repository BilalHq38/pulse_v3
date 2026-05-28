from __future__ import annotations

import re
from io import BytesIO, StringIO

import pandas as pd
from fastapi import HTTPException

from core.phone_normalization import phone_region_from_country_hint

SUPPORTED_TABULAR_UPLOAD_EXTENSIONS = {"xlsx", "xls", "csv"}
MAX_TABULAR_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_TABULAR_UPLOAD_ROWS = 2000
PHONE_REGION_UPLOAD_KEYS = (
    "phone_region",
    "default_phone_region",
    "region",
    "country",
    "country_code",
    "phone_country",
    "phone_country_code",
    "dial_code",
    "calling_code",
    "mobile_country_code",
    "whatsapp_country_code",
)


def normalize_header(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _unique_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    unique: list[str] = []
    for index, header in enumerate(headers, start=1):
        base = header or f"column_{index}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        unique.append(base if count == 1 else f"{base}_{count}")
    return unique


def split_multi_value(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"[,;\n|]+", text)
    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        item = str(part or "").strip()
        if not item:
            continue
        normalized = item.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(item)
    return deduped


def phone_region_from_upload_row(row: dict) -> str:
    return phone_region_from_country_hint(*(str(row.get(key) or "") for key in PHONE_REGION_UPLOAD_KEYS)) or ""


def parse_tabular_upload(filename: str, payload: bytes) -> list[dict]:
    safe_filename = str(filename or "").strip()
    if not safe_filename:
        raise HTTPException(400, "A spreadsheet file is required.")

    ext = safe_filename.rsplit(".", 1)[-1].lower() if "." in safe_filename else ""
    if ext not in SUPPORTED_TABULAR_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_TABULAR_UPLOAD_EXTENSIONS))
        raise HTTPException(400, f"Unsupported file type '.{ext}'. Upload one of: {allowed}.")

    if len(payload) > MAX_TABULAR_UPLOAD_BYTES:
        raise HTTPException(400, f"File is too large. Maximum size is {MAX_TABULAR_UPLOAD_BYTES // (1024 * 1024)} MB.")

    try:
        if ext == "csv":
            dataframe = pd.read_csv(StringIO(payload.decode("utf-8-sig", errors="replace")), dtype=str)
        else:
            dataframe = pd.read_excel(BytesIO(payload), dtype=str)
    except Exception as exc:
        raise HTTPException(400, f"Could not read spreadsheet: {exc}") from exc

    if dataframe is None or dataframe.empty:
        raise HTTPException(400, "The spreadsheet is empty.")

    normalized_headers = _unique_headers([normalize_header(column) for column in list(dataframe.columns)])
    dataframe = dataframe.fillna("")
    dataframe.columns = normalized_headers

    rows: list[dict] = []
    for row_number, record in enumerate(dataframe.to_dict(orient="records"), start=2):
        data = {
            header: str(value or "").strip()
            for header, value in record.items()
        }
        if any(data.values()):
            rows.append({"row_number": row_number, "data": data})

    if not rows:
        raise HTTPException(400, "The spreadsheet only contains blank rows.")
    if len(rows) > MAX_TABULAR_UPLOAD_ROWS:
        raise HTTPException(400, f"Upload up to {MAX_TABULAR_UPLOAD_ROWS} rows at a time.")

    return rows
