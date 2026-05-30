from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UPLOAD_ROOT = PROJECT_ROOT / "uploads"
ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
ALLOWED_VIDEO_MIME_TYPES = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}
ALLOWED_MEDIA_MIME_TYPES = {
    **ALLOWED_IMAGE_MIME_TYPES,
    **ALLOWED_VIDEO_MIME_TYPES,
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 16 * 1024 * 1024
_DATA_URL_RE = re.compile(r"^data:([^;,]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)
_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_S3_CLIENT = None


def storage_backend() -> str:
    backend = (os.environ.get("MEDIA_STORAGE_BACKEND") or "local").strip().lower()
    if backend not in {"local", "s3"}:
        raise RuntimeError("MEDIA_STORAGE_BACKEND must be 'local' or 's3'")
    if (os.environ.get("ENVIRONMENT") or os.environ.get("APP_ENV") or "").strip().lower() in {"prod", "production"}:
        if backend != "s3":
            raise RuntimeError("Production media storage requires MEDIA_STORAGE_BACKEND=s3")
    return backend


def _s3_client():
    global _S3_CLIENT
    if _S3_CLIENT is None:
        import boto3

        _S3_CLIENT = boto3.client("s3", region_name=(os.environ.get("AWS_REGION") or "").strip() or None)
    return _S3_CLIENT


def _s3_bucket() -> str:
    bucket = (os.environ.get("MEDIA_S3_BUCKET") or "").strip()
    if not bucket:
        raise RuntimeError("MEDIA_S3_BUCKET is required when MEDIA_STORAGE_BACKEND=s3")
    return bucket


def _media_url(*, storage_key: str, public_url_prefix: str, company_id: str, filename: str) -> str:
    public_base = (os.environ.get("MEDIA_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if public_base:
        return f"{public_base}/{storage_key}"
    prefix = "/" + public_url_prefix.strip("/")
    return f"{prefix}/{company_id}/{filename}"


def upload_root() -> Path:
    configured = os.environ.get("UPLOAD_STORAGE_DIR", "").strip()
    return Path(configured) if configured else DEFAULT_UPLOAD_ROOT


def safe_path_segment(value: str, fallback: str = "default") -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("_", str(value or "").strip()).strip("._")
    return cleaned or fallback


def parse_image_data_url(data_url: str) -> tuple[str, bytes]:
    mime_type, raw = parse_media_data_url(data_url, allowed_mime_types=ALLOWED_IMAGE_MIME_TYPES)
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(400, "unsupported image type")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(400, f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    return mime_type, raw


def parse_media_data_url(
    data_url: str,
    *,
    allowed_mime_types: dict[str, str] | None = None,
) -> tuple[str, bytes]:
    match = _DATA_URL_RE.match(str(data_url or "").strip())
    if not match:
        raise HTTPException(400, "media must be a base64 data URL")
    mime_type = match.group(1).lower()
    allowed = allowed_mime_types or ALLOWED_MEDIA_MIME_TYPES
    if mime_type not in allowed:
        raise HTTPException(400, "unsupported media type")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except Exception as exc:
        raise HTTPException(400, "invalid media data") from exc
    if not raw:
        raise HTTPException(400, "media is empty")
    limit = MAX_VIDEO_BYTES if mime_type.startswith("video/") else MAX_IMAGE_BYTES
    if len(raw) > limit:
        raise HTTPException(400, f"media exceeds {limit // (1024 * 1024)} MB")
    return mime_type, raw


def store_media_data_url(
    data_url: str,
    *,
    category: str,
    company_id: str,
    public_url_prefix: str,
    original_filename: str = "",
) -> dict[str, Any]:
    mime_type, raw = parse_media_data_url(data_url)
    return _store_media_bytes(
        raw,
        mime_type=mime_type,
        category=category,
        company_id=company_id,
        public_url_prefix=public_url_prefix,
        original_filename=original_filename,
    )


def _store_media_bytes(
    raw: bytes,
    *,
    mime_type: str,
    category: str,
    company_id: str,
    public_url_prefix: str,
    original_filename: str = "",
) -> dict[str, Any]:
    extension = ALLOWED_MEDIA_MIME_TYPES[mime_type]
    safe_company = safe_path_segment(company_id, "company")
    safe_category = safe_path_segment(category, "media")
    file_name = f"{uuid4().hex}{extension}"
    storage_key = f"{safe_category}/{safe_company}/{file_name}"
    path = None
    if storage_backend() == "s3":
        _s3_client().put_object(
            Bucket=_s3_bucket(),
            Key=storage_key,
            Body=raw,
            ContentType=mime_type,
            ServerSideEncryption=(os.environ.get("MEDIA_S3_SERVER_SIDE_ENCRYPTION") or "AES256").strip(),
        )
    else:
        directory = upload_root() / safe_category / safe_company
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / file_name
        path.write_bytes(raw)
    return {
        "url": _media_url(
            storage_key=storage_key,
            public_url_prefix=public_url_prefix,
            company_id=safe_company,
            filename=file_name,
        ),
        "storage_path": str(path or ""),
        "storage_key": storage_key,
        "mime_type": mime_type,
        "file_size": len(raw),
        "file_name": original_filename.strip() or file_name,
    }


def store_image_data_url(
    data_url: str,
    *,
    category: str,
    company_id: str,
    public_url_prefix: str,
    original_filename: str = "",
) -> dict[str, Any]:
    mime_type, raw = parse_image_data_url(data_url)
    return _store_media_bytes(
        raw,
        mime_type=mime_type,
        category=category,
        company_id=company_id,
        public_url_prefix=public_url_prefix,
        original_filename=original_filename,
    )


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


def serve_stored_media(*, category: str, company_id: str, filename: str) -> Response:
    if storage_backend() == "s3":
        storage_key = "/".join(
            (
                safe_path_segment(category, "media"),
                safe_path_segment(company_id, "company"),
                safe_path_segment(filename, "file"),
            )
        )
        url = _s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": _s3_bucket(), "Key": storage_key},
            ExpiresIn=max(60, int(os.environ.get("MEDIA_S3_PRESIGNED_URL_TTL_SECONDS", "300") or 300)),
        )
        return RedirectResponse(url=url, status_code=307)
    path = resolve_upload_path(category=category, company_id=company_id, filename=filename)
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "media not found")
    return FileResponse(path)
