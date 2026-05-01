from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UPLOAD_ROOT = PROJECT_ROOT / "uploads"
ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_DATA_URL_RE = re.compile(r"^data:([^;,]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)
_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def upload_root() -> Path:
    configured = os.environ.get("UPLOAD_STORAGE_DIR", "").strip()
    return Path(configured) if configured else DEFAULT_UPLOAD_ROOT


def safe_path_segment(value: str, fallback: str = "default") -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("_", str(value or "").strip()).strip("._")
    return cleaned or fallback


def parse_image_data_url(data_url: str) -> tuple[str, bytes]:
    match = _DATA_URL_RE.match(str(data_url or "").strip())
    if not match:
        raise HTTPException(400, "image must be a base64 data URL")
    mime_type = match.group(1).lower()
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(400, "unsupported image type")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except Exception as exc:
        raise HTTPException(400, "invalid image data") from exc
    if not raw:
        raise HTTPException(400, "image is empty")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(400, f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    return mime_type, raw


def store_image_data_url(
    data_url: str,
    *,
    category: str,
    company_id: str,
    public_url_prefix: str,
    original_filename: str = "",
) -> dict[str, Any]:
    mime_type, raw = parse_image_data_url(data_url)
    extension = ALLOWED_IMAGE_MIME_TYPES[mime_type]
    safe_company = safe_path_segment(company_id, "company")
    safe_category = safe_path_segment(category, "media")
    file_name = f"{uuid4().hex}{extension}"
    directory = upload_root() / safe_category / safe_company
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / file_name
    path.write_bytes(raw)
    prefix = "/" + public_url_prefix.strip("/")
    return {
        "url": f"{prefix}/{safe_company}/{file_name}",
        "storage_path": str(path),
        "storage_key": f"{safe_category}/{safe_company}/{file_name}",
        "mime_type": mime_type,
        "file_size": len(raw),
        "file_name": original_filename.strip() or file_name,
    }


def store_image_bytes(
    raw: bytes,
    *,
    mime_type: str,
    category: str,
    company_id: str,
    public_url_prefix: str,
    original_filename: str = "",
) -> dict[str, Any]:
    normalized_mime = str(mime_type or "").strip().lower()
    if normalized_mime not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(400, "unsupported image type")
    if not raw:
        raise HTTPException(400, "image is empty")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(400, f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    data_url = f"data:{normalized_mime};base64,{base64.b64encode(raw).decode('ascii')}"
    return store_image_data_url(
        data_url,
        category=category,
        company_id=company_id,
        public_url_prefix=public_url_prefix,
        original_filename=original_filename,
    )


def resolve_upload_path(*, category: str, company_id: str, filename: str) -> Path:
    root = upload_root().resolve()
    path = (
        root
        / safe_path_segment(category, "media")
        / safe_path_segment(company_id, "company")
        / safe_path_segment(filename, "file")
    ).resolve()
    if root not in path.parents:
        raise HTTPException(404, "media not found")
    return path


def serve_stored_media(*, category: str, company_id: str, filename: str) -> FileResponse:
    path = resolve_upload_path(category=category, company_id=company_id, filename=filename)
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "media not found")
    return FileResponse(path)
