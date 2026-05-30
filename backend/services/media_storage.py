from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")

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

# ── S3 client (lazily initialised) ────────────────────────────────────────────

_S3_CLIENT = None


def _get_s3_client():
    global _S3_CLIENT
    if _S3_CLIENT is None:
        kwargs: dict = {"region_name": os.environ.get("AWS_S3_REGION", "us-east-1")}
        # In production use an IAM role (no explicit keys needed).
        # For local/dev supply explicit credentials via environment variables.
        if os.environ.get("AWS_ACCESS_KEY_ID"):
            kwargs["aws_access_key_id"] = os.environ["AWS_ACCESS_KEY_ID"]
            kwargs["aws_secret_access_key"] = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        _S3_CLIENT = boto3.client("s3", **kwargs)
    return _S3_CLIENT


# ── S3 helpers ─────────────────────────────────────────────────────────────────


async def store_media_bytes_s3(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    company_id: str,
) -> str:
    """Upload *file_bytes* to S3 and return the public URL."""
    bucket = os.environ.get("AWS_S3_BUCKET", "")
    if not bucket:
        raise ValueError("AWS_S3_BUCKET environment variable is not set")

    key = f"media/{company_id}/{filename}"

    def _upload() -> None:
        _get_s3_client().put_object(
            Bucket=bucket,
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )

    await asyncio.to_thread(_upload)

    cdn_base = os.environ.get("AWS_S3_CDN_BASE_URL", "").rstrip("/")
    if cdn_base:
        return f"{cdn_base}/{key}"

    region = os.environ.get("AWS_S3_REGION", "us-east-1")
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


async def delete_media_s3(key_or_url: str) -> None:
    """Delete a file from S3 by object key or full URL."""
    bucket = os.environ.get("AWS_S3_BUCKET", "")
    if not bucket:
        return

    key = key_or_url
    if key_or_url.startswith("http"):
        # Extract key from an s3.amazonaws.com URL
        parts = key_or_url.split(".amazonaws.com/", 1)
        if len(parts) == 2:
            key = parts[1]
        else:
            # Try to strip a CloudFront / CDN base URL
            cdn_base = os.environ.get("AWS_S3_CDN_BASE_URL", "").rstrip("/")
            if cdn_base and key_or_url.startswith(cdn_base):
                key = key_or_url[len(cdn_base):].lstrip("/")

    def _delete() -> None:
        try:
            _get_s3_client().delete_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            logger.warning("s3_delete_failed key=%s error=%s", key, exc)

    await asyncio.to_thread(_delete)


async def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    """Generate a presigned URL for temporary private access to an S3 object."""
    bucket = os.environ.get("AWS_S3_BUCKET", "")

    def _presign() -> str:
        return _get_s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    return await asyncio.to_thread(_presign)


# ── S3 dispatch for the existing public interface ─────────────────────────────


async def store_media(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    company_id: str = "",
) -> str:
    """Dispatch to S3 or local storage based on STORAGE_BACKEND env var.

    Returns a public URL string (S3) or a local storage path string (local).
    """
    if STORAGE_BACKEND == "s3":
        return await store_media_bytes_s3(file_bytes, filename, content_type, company_id)
    return await _store_media_local(file_bytes, filename, content_type, company_id)


async def _store_media_local(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    company_id: str,
) -> str:
    """Write *file_bytes* to the local upload directory and return the path."""
    safe_company = safe_path_segment(company_id, "company")
    directory = upload_root() / safe_company
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_bytes(file_bytes)
    return str(path)


# ── Local-storage helpers (original implementation, preserved) ────────────────


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


def serve_stored_media(*, category: str, company_id: str, filename: str) -> FileResponse:
    path = resolve_upload_path(category=category, company_id=company_id, filename=filename)
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "media not found")
    return FileResponse(path)
