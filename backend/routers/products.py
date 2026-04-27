"""routers/products.py — Products, FAQs, Onboarding Docs — PostgreSQL."""

import base64
import json
import logging
import posixpath
import re
import zipfile
from io import BytesIO
from typing import Optional
from xml.etree import ElementTree as ET
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from openpyxl import load_workbook
from pydantic import ValidationError
from models.schemas import ProductCreate, ProductDescriptionRequest, ProductUpdate
from services.ai_service.facade import generate_product_description, get_active_llm_engines
from core.utils import make_id, now_ts, normalize_product_images
from services.db_helpers import r, rs, get_current_user_flexible

logger = logging.getLogger(__name__)
router = APIRouter()


def _db(req):
    return req.app.state.db


def _normalize_header(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _xml_tag_name(tag) -> str:
    return str(tag or "").rsplit("}", 1)[-1]


def _cell_ref_to_row_col(cell_ref: str) -> tuple[int | None, int]:
    match = re.match(r"([A-Za-z]+)(\d+)", str(cell_ref or "").strip())
    if not match:
        return None, 0
    letters, row_text = match.groups()
    col_num = 0
    for char in letters.upper():
        col_num = (col_num * 26) + (ord(char) - 64)
    return int(row_text), max(0, col_num - 1)


def _extract_anchor_row_col(anchor) -> tuple[int | None, int]:
    marker = getattr(anchor, "_from", None) or getattr(anchor, "from_", None)
    if marker is not None:
        try:
            return int(marker.row) + 1, int(getattr(marker, "col", 0) or 0)
        except Exception:
            pass
    row = getattr(anchor, "row", None)
    col = getattr(anchor, "col", None)
    if row is not None:
        try:
            return int(row), int(col or 0)
        except Exception:
            pass
    cell_ref = getattr(anchor, "coord", None) or getattr(anchor, "cell", None)
    if isinstance(cell_ref, str):
        match = re.match(r"([A-Za-z]+)(\d+)", cell_ref.strip())
        if match:
            letters, row_text = match.groups()
            col_num = 0
            for char in letters.upper():
                col_num = (col_num * 26) + (ord(char) - 64)
            return int(row_text), max(0, col_num - 1)
    return None, 0


def _read_zip_xml(archive: zipfile.ZipFile, path: str):
    try:
        return ET.fromstring(archive.read(path))
    except Exception:
        return None


def _resolve_rel_target(base_part: str, target: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_part), str(target or "").strip()))


def _guess_image_mime(path: str) -> str:
    lower = str(path or "").lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def _extract_active_sheet_path_from_archive(archive: zipfile.ZipFile) -> str | None:
    workbook_root = _read_zip_xml(archive, "xl/workbook.xml")
    rels_root = _read_zip_xml(archive, "xl/_rels/workbook.xml.rels")
    if workbook_root is None or rels_root is None:
        return None
    sheets = [node for node in workbook_root.iter() if _xml_tag_name(node.tag) == "sheet"]
    if not sheets:
        return None
    active_index = 0
    for node in workbook_root.iter():
        if _xml_tag_name(node.tag) == "workbookView":
            try:
                active_index = int(node.attrib.get("activeTab", 0) or 0)
            except Exception:
                active_index = 0
            break
    active_index = min(max(active_index, 0), len(sheets) - 1)
    sheet_rel_id = sheets[active_index].attrib.get(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    ) or sheets[active_index].attrib.get("id")
    rel_targets = {}
    for rel in rels_root.iter():
        if _xml_tag_name(rel.tag) != "Relationship":
            continue
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rel_id and target:
            rel_targets[rel_id] = _resolve_rel_target("xl/workbook.xml", target)
    return rel_targets.get(sheet_rel_id)


def _extract_richvalue_row_images(raw: bytes) -> dict[int, list[str]]:
    row_images: dict[int, list[tuple[int, str]]] = {}
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            sheet_path = _extract_active_sheet_path_from_archive(archive)
            metadata_root = _read_zip_xml(archive, "xl/metadata.xml")
            if not sheet_path or metadata_root is None:
                return {}

            sheet_root = _read_zip_xml(archive, sheet_path)
            if sheet_root is None:
                return {}

            vm_cells = []
            for cell in sheet_root.iter():
                if _xml_tag_name(cell.tag) != "c" or "vm" not in cell.attrib:
                    continue
                row_number, col_number = _cell_ref_to_row_col(cell.attrib.get("r", ""))
                if row_number is None:
                    continue
                try:
                    vm_index = int(cell.attrib.get("vm", ""))
                except Exception:
                    continue
                vm_cells.append((row_number, col_number, vm_index))
            if not vm_cells:
                return {}

            value_metadata = next(
                (node for node in metadata_root.iter() if _xml_tag_name(node.tag) == "valueMetadata"),
                None,
            )
            if value_metadata is None:
                return {}
            vm_to_rv = {}
            metadata_blocks = [node for node in list(value_metadata) if isinstance(node.tag, str)]
            for idx, block in enumerate(metadata_blocks):
                rich_value_index = None
                for node in block.iter():
                    attr_value = node.attrib.get("v")
                    if attr_value is not None and str(attr_value).strip().isdigit():
                        rich_value_index = int(str(attr_value).strip())
                        break
                    text = str(node.text or "").strip()
                    if _xml_tag_name(node.tag) in {"v", "t"} and text.isdigit():
                        rich_value_index = int(text)
                        break
                if rich_value_index is not None:
                    vm_to_rv[idx] = rich_value_index
            if not vm_to_rv:
                return {}

            rich_value_path = next(
                (name for name in archive.namelist() if name.lower().endswith("rdrichvalue.xml")),
                None,
            )
            rich_value_rels_path = next(
                (name for name in archive.namelist() if name.lower().endswith("richvaluerel.xml.rels")),
                None,
            )
            if not rich_value_path or not rich_value_rels_path:
                return {}
            rich_value_root = _read_zip_xml(archive, rich_value_path)
            rich_value_rels_root = _read_zip_xml(archive, rich_value_rels_path)
            if rich_value_root is None or rich_value_rels_root is None:
                return {}

            rel_targets = {}
            rel_source_part = rich_value_rels_path.replace("/_rels/", "/").removesuffix(".rels")
            for rel in rich_value_rels_root.iter():
                if _xml_tag_name(rel.tag) != "Relationship":
                    continue
                rel_id = rel.attrib.get("Id")
                target = rel.attrib.get("Target")
                if rel_id and target:
                    rel_targets[rel_id] = _resolve_rel_target(rel_source_part, target)
            ordered_rel_ids = sorted(
                rel_targets.keys(),
                key=lambda rel_id: int(re.search(r"(\d+)$", rel_id).group(1)) if re.search(r"(\d+)$", rel_id) else 0,
            )
            rv_nodes = [node for node in rich_value_root.iter() if _xml_tag_name(node.tag) == "rv"]
            rv_to_rel_id = {}
            for idx, rv_node in enumerate(rv_nodes):
                rel_id = None
                local_image_identifier = None
                for node in rv_node.iter():
                    for attr_value in node.attrib.values():
                        attr_text = str(attr_value or "").strip()
                        if re.fullmatch(r"rId\d+", attr_text):
                            rel_id = attr_text
                            break
                    if rel_id:
                        break
                    text = str(node.text or "").strip()
                    if re.fullmatch(r"rId\d+", text):
                        rel_id = text
                        break
                    if text.isdigit() and local_image_identifier is None:
                        local_image_identifier = int(text)
                if rel_id:
                    rv_to_rel_id[idx] = rel_id
                    continue
                if local_image_identifier is None:
                    continue
                guessed_rel_id = f"rId{local_image_identifier + 1}"
                if guessed_rel_id in rel_targets:
                    rv_to_rel_id[idx] = guessed_rel_id
                elif 0 <= local_image_identifier < len(ordered_rel_ids):
                    rv_to_rel_id[idx] = ordered_rel_ids[local_image_identifier]

            for row_number, col_number, vm_index in vm_cells:
                rich_value_index = vm_to_rv.get(vm_index)
                rel_id = rv_to_rel_id.get(rich_value_index)
                media_path = rel_targets.get(rel_id)
                if not media_path:
                    continue
                try:
                    payload = archive.read(media_path)
                except KeyError:
                    continue
                if not payload:
                    continue
                mime = _guess_image_mime(media_path)
                data_url = f"data:{mime};base64,{base64.b64encode(payload).decode('utf-8')}"
                row_images.setdefault(row_number, []).append((col_number, data_url))
    except Exception as exc:
        logger.warning(f"RichValue image extraction skipped: {exc}")
        return {}

    return {
        row_number: [data_url for _, data_url in sorted(items, key=lambda item: item[0])]
        for row_number, items in row_images.items()
    }


def _extract_sheet_row_images(sheet) -> dict[int, list[str]]:
    row_images: dict[int, list[tuple[int, str]]] = {}
    for image in getattr(sheet, "_images", []) or []:
        try:
            anchor = getattr(image, "anchor", None)
            row_number, col_number = _extract_anchor_row_col(anchor)
            if row_number is None:
                continue
            payload = image._data()
            if not payload:
                continue
            img_format = str(getattr(image, "format", "") or "png").lower()
            if img_format == "jpg":
                img_format = "jpeg"
            data_url = f"data:image/{img_format};base64,{base64.b64encode(payload).decode('utf-8')}"
            row_images.setdefault(row_number, []).append((col_number, data_url))
        except Exception as exc:
            logger.warning(f"Skipping embedded Excel image: {exc}")
    return {
        row_number: [data_url for _, data_url in sorted(items, key=lambda item: item[0])]
        for row_number, items in row_images.items()
    }


def _has_non_empty_image_input(values: list) -> bool:
    return any(str(value or "").strip() for value in values)


_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_SLUG_SANITIZE_RE = re.compile(r"[^a-z0-9_-]+")
_PRICE_SANITIZE_RE = re.compile(r"[^0-9.-]")
_ALLOWED_PRODUCT_STATUSES = {"active", "inactive", "archived"}


def _normalize_name(value, *, required: bool) -> str:
    name = str(value or "").strip()
    if required and not name:
        raise HTTPException(400, "name is required")
    if name and len(name) > 255:
        raise HTTPException(400, "name must be 255 characters or fewer")
    return name


def _normalize_text(value, *, default: str = "", max_len: int = 8000) -> str:
    text = str(value if value is not None else default).strip()
    if not text:
        return str(default or "").strip()
    return text[:max_len]


def _normalize_category(value) -> str:
    normalized = _SLUG_SANITIZE_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return normalized or "general"


def _normalize_product_type(value) -> str:
    normalized = _SLUG_SANITIZE_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return normalized or "standard"


def _normalize_status(value) -> str:
    status = str(value or "").strip().lower()
    if not status:
        return "active"
    if status not in _ALLOWED_PRODUCT_STATUSES:
        raise HTTPException(400, "status must be one of: active, inactive, archived")
    return status


def _normalize_currency(value) -> str:
    currency = str(value or "USD").strip().upper() or "USD"
    if not _CURRENCY_RE.fullmatch(currency):
        raise HTTPException(400, "price_currency must be a 3-letter ISO code")
    return currency


def _normalize_price(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = re.sub(r"[, ]+", "", raw)
    normalized = _PRICE_SANITIZE_RE.sub("", normalized)
    if normalized in {"", ".", "-", "-."}:
        raise HTTPException(400, "price must be a valid non-negative number")
    try:
        parsed = float(normalized)
    except ValueError as exc:
        raise HTTPException(400, "price must be a valid non-negative number") from exc
    if parsed < 0:
        raise HTTPException(400, "price must be a valid non-negative number")
    if parsed.is_integer():
        return str(int(parsed))
    return f"{parsed:.2f}".rstrip("0").rstrip(".")


def _normalize_images(images) -> list[str]:
    if images is None:
        return []
    if not isinstance(images, list):
        raise HTTPException(400, "images must be an array")
    return normalize_product_images(images, limit=3)


def _normalize_features(features) -> list[str]:
    if features is None:
        return []
    if not isinstance(features, list):
        raise HTTPException(400, "features must be an array")
    cleaned: list[str] = []
    for raw in features:
        value = str(raw or "").strip()
        if not value:
            continue
        cleaned.append(value[:180])
        if len(cleaned) >= 30:
            break
    return cleaned


def _normalize_product_payload_for_create(body: dict) -> tuple[dict, list[str], list[str]]:
    if not isinstance(body, dict):
        raise HTTPException(400, "invalid product payload")
    payload = {
        "name": _normalize_name(body.get("name"), required=True),
        "product_title": _normalize_text(body.get("product_title"), max_len=255),
        "description": _normalize_text(body.get("description"), max_len=8000),
        "price": _normalize_price(body.get("price")),
        "price_currency": _normalize_currency(body.get("price_currency")),
        "category": _normalize_category(body.get("category")),
        "product_type": _normalize_product_type(body.get("product_type")),
    }
    images = _normalize_images(body.get("images", []))
    features = _normalize_features(body.get("features", []))
    return payload, images, features


def _normalize_product_payload_for_update(body: dict) -> tuple[dict, list[str] | None, list[str] | None]:
    if not isinstance(body, dict):
        raise HTTPException(400, "invalid product payload")
    payload: dict = {}
    if "name" in body:
        payload["name"] = _normalize_name(body.get("name"), required=True)
    if "product_title" in body:
        payload["product_title"] = _normalize_text(body.get("product_title"), max_len=255)
    if "description" in body:
        payload["description"] = _normalize_text(body.get("description"), max_len=8000)
    if "price" in body:
        payload["price"] = _normalize_price(body.get("price"))
    if "price_currency" in body:
        payload["price_currency"] = _normalize_currency(body.get("price_currency"))
    if "category" in body:
        payload["category"] = _normalize_category(body.get("category"))
    if "product_type" in body:
        payload["product_type"] = _normalize_product_type(body.get("product_type"))
    if "status" in body:
        payload["status"] = _normalize_status(body.get("status"))

    images = _normalize_images(body.get("images")) if "images" in body else None
    features = _normalize_features(body.get("features")) if "features" in body else None
    return payload, images, features


def _coerce_json_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _hydrate_product_record(product: dict | None) -> dict | None:
    if not product:
        return None
    hydrated = dict(product)
    hydrated["images"] = _coerce_json_list(hydrated.get("images"))
    hydrated["features"] = _coerce_json_list(hydrated.get("features"))
    return hydrated


_PRODUCT_SELECT_SQL = (
    "SELECT p.id,p.company_id,p.name,p.product_title,p.description,p.price,p.price_currency,p.category,p.product_type,p.status,p.created_at,p.updated_at,"  # noqa: E501
    "COALESCE(img.images, '[]'::json) AS images,"
    "COALESCE(feat.features, '[]'::json) AS features "
    "FROM company_products p "
    "LEFT JOIN LATERAL ("
    "SELECT json_agg(image_url ORDER BY sort_order) AS images "
    "FROM product_images WHERE product_id=p.id"
    ") img ON TRUE "
    "LEFT JOIN LATERAL ("
    "SELECT json_agg(feature ORDER BY sort_order) AS features "
    "FROM product_features WHERE product_id=p.id"
    ") feat ON TRUE "
)


async def _get_product(db, product_id, cid):
    prod = r(
        await db.fetchrow(
            _PRODUCT_SELECT_SQL + "WHERE p.id=$1 AND p.company_id=$2 LIMIT 1",
            product_id,
            cid,
        )
    )
    return _hydrate_product_record(prod)


async def _list_products_page(db, cid: str, page_size: int, offset: int) -> list[dict]:
    rows = rs(
        await db.fetch(
            _PRODUCT_SELECT_SQL + "WHERE p.company_id=$1 ORDER BY p.created_at DESC, p.id DESC LIMIT $2 OFFSET $3",
            cid,
            page_size,
            offset,
        )
    )
    return [_hydrate_product_record(row) for row in rows]


async def _replace_product_assets(db, product_id: str, images: list[str] | None, features: list[str] | None) -> None:
    if images is not None:
        await db.execute("DELETE FROM product_images WHERE product_id=$1", product_id)
        if images:
            await db.executemany(
                "INSERT INTO product_images(id,product_id,image_url,sort_order,created_at) VALUES($1,$2,$3,$4,NOW())",
                [(make_id(), product_id, url, i) for i, url in enumerate(images)],
            )

    if features is not None:
        await db.execute("DELETE FROM product_features WHERE product_id=$1", product_id)
        if features:
            await db.executemany(
                "INSERT INTO product_features(id,product_id,feature,sort_order) VALUES($1,$2,$3,$4)",
                [(make_id(), product_id, feature, i) for i, feature in enumerate(features)],
            )


async def _create_product_row(db, cid: str, payload: dict, images: list[str], features: list[str]) -> str:
    pid = make_id()
    await db.execute(
        "INSERT INTO company_products(id,company_id,name,product_title,description,price,price_currency,category,product_type,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW())",  # noqa: E501
        pid,
        cid,
        payload["name"],
        payload.get("product_title", ""),
        payload.get("description", ""),
        payload.get("price", ""),
        payload.get("price_currency", "USD"),
        payload.get("category", "general"),
        payload.get("product_type", "standard"),
    )
    await _replace_product_assets(db, pid, images=images, features=features)
    return pid


async def _update_product_row(
    db,
    product_id: str,
    cid: str,
    payload: dict,
    images: list[str] | None,
    features: list[str] | None,
) -> None:
    update_payload = dict(payload)
    if update_payload or images is not None or features is not None:
        update_payload["updated_at"] = now_ts()
        set_parts = ", ".join(f"{column}=${index + 2}" for index, column in enumerate(update_payload))
        await db.execute(
            f"UPDATE company_products SET {set_parts} WHERE id=$1 AND company_id=${len(update_payload) + 2}",
            product_id,
            *update_payload.values(),
            cid,
        )

    await _replace_product_assets(db, product_id, images=images, features=features)


async def _find_bulk_match_product_id(db, cid: str, payload: dict) -> str | None:
    product_title = str(payload.get("product_title") or "").strip()
    if product_title:
        existing_id = await db.fetchval(
            "SELECT id FROM company_products WHERE company_id=$1 AND product_title=$2 ORDER BY updated_at DESC LIMIT 1",
            cid,
            product_title,
        )
        if existing_id:
            return str(existing_id)

    existing_id = await db.fetchval(
        "SELECT id FROM company_products WHERE company_id=$1 AND LOWER(name)=LOWER($2) AND category=$3 ORDER BY updated_at DESC LIMIT 1",  # noqa: E501
        cid,
        payload["name"],
        payload.get("category", "general"),
    )
    return str(existing_id) if existing_id else None


def _validation_error_message(exc: ValidationError) -> str:
    pieces: list[str] = []
    for issue in exc.errors():
        loc = ".".join(str(item) for item in issue.get("loc", []))
        msg = str(issue.get("msg") or "Invalid value")
        pieces.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(pieces) or "Invalid row payload"


def _coerce_row_number(value, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


@router.get("/products")
@router.get("/company-data/products")
async def list_products(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    legacy_mode = request.url.path.startswith("/company-data/")
    effective_page_size = page_size
    if legacy_mode and "page_size" not in request.query_params:
        effective_page_size = 200

    offset = (page - 1) * effective_page_size
    total = int(await db.fetchval("SELECT COUNT(*) FROM company_products WHERE company_id=$1", cid) or 0)
    products = await _list_products_page(db, cid, effective_page_size, offset)

    if legacy_mode:
        return products

    return {
        "items": products,
        "page": page,
        "page_size": effective_page_size,
        "total": total,
        "has_more": (offset + len(products)) < total,
    }


@router.post("/products")
@router.post("/company-data/products")
async def create_product(body: ProductCreate, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    normalized_body, images, features = _normalize_product_payload_for_create(body.model_dump())
    pid = await _create_product_row(db, cid, normalized_body, images, features)
    return await _get_product(db, pid, cid)


@router.post("/products/generate-description")
@router.post("/company-data/products/generate-description")
async def generate_product_desc_endpoint(body: ProductDescriptionRequest, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    payload = body.model_dump()
    name = str(payload.get("name", "") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    active_engines = await get_active_llm_engines(db, company_id=cid)
    desc = await generate_product_description(
        name=name,
        product_title=payload.get("product_title", ""),
        product_type=payload.get("product_type", ""),
        category=payload.get("category", "general"),
        price=payload.get("price", ""),
        price_currency=payload.get("price_currency", "USD"),
        images=payload.get("images", []),
        engines=active_engines,
        company_id=cid,
    )
    return {"description": desc}


@router.post("/products/bulk-upload")
@router.post("/company-data/products/bulk-upload")
async def bulk_upload_products(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(400, "request body must be valid JSON") from exc

    upsert = True
    if isinstance(body, dict):
        raw_items = body.get("items", [])
        upsert = bool(body.get("upsert", True))
    elif isinstance(body, list):
        raw_items = body
    else:
        raise HTTPException(400, "payload must be an array or an object containing items")

    if not isinstance(raw_items, list):
        raise HTTPException(400, "items must be an array")
    if not raw_items:
        raise HTTPException(400, "items must contain at least one row")
    if len(raw_items) > 1000:
        raise HTTPException(400, "items cannot exceed 1000 rows")

    created = 0
    updated = 0
    errors: list[dict] = []
    results: list[dict] = []

    for index, raw_item in enumerate(raw_items):
        fallback_row = index + 2
        row_number = fallback_row
        row_payload = raw_item

        if isinstance(raw_item, dict) and isinstance(raw_item.get("payload"), dict):
            row_payload = raw_item.get("payload") or {}
            row_number = _coerce_row_number(raw_item.get("rowNumber"), fallback_row)
        elif isinstance(raw_item, dict):
            row_number = _coerce_row_number(raw_item.get("rowNumber"), fallback_row)
            row_payload = dict(raw_item)
            row_payload.pop("rowNumber", None)

        if not isinstance(row_payload, dict):
            errors.append({"row": row_number, "error": "Row payload must be an object"})
            continue

        try:
            validated = ProductCreate.model_validate(row_payload)
            normalized_body, images, features = _normalize_product_payload_for_create(validated.model_dump())
            has_images = "images" in row_payload
            has_features = "features" in row_payload

            existing_id = await _find_bulk_match_product_id(db, cid, normalized_body) if upsert else None
            if existing_id:
                await _update_product_row(
                    db,
                    existing_id,
                    cid,
                    normalized_body,
                    images if has_images else None,
                    features if has_features else None,
                )
                updated += 1
                results.append({"row": row_number, "status": "updated", "id": existing_id})
            else:
                pid = await _create_product_row(db, cid, normalized_body, images, features)
                created += 1
                results.append({"row": row_number, "status": "created", "id": pid})
        except ValidationError as exc:
            errors.append({"row": row_number, "error": _validation_error_message(exc)})
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "Invalid row payload"
            errors.append({"row": row_number, "error": str(detail)})
        except Exception:
            logger.exception("Product bulk upload row failed row=%s company_id=%s", row_number, cid)
            errors.append({"row": row_number, "error": "Unexpected server error while processing row"})

    return {
        "success": len(errors) == 0,
        "created": created,
        "updated": updated,
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }


@router.get("/products/{product_id}")
@router.get("/company-data/products/{product_id}")
async def get_product(product_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    product = await _get_product(db, product_id, cid)
    if not product:
        raise HTTPException(404, "product not found")
    return product


@router.put("/products/{product_id}")
@router.put("/company-data/products/{product_id}")
async def update_product(product_id: str, body: ProductUpdate, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body_payload = body.model_dump(exclude_unset=True)
    body_payload.pop("_id", None)
    normalized_body, images, features = _normalize_product_payload_for_update(body_payload)

    if not normalized_body and images is None and features is None:
        existing = await _get_product(db, product_id, cid)
        if not existing:
            raise HTTPException(404, "product not found")
        return existing

    await _update_product_row(db, product_id, cid, normalized_body, images, features)
    product = await _get_product(db, product_id, cid)
    if not product:
        raise HTTPException(404, "product not found")
    return product


@router.delete("/products/{product_id}")
@router.delete("/company-data/products/{product_id}")
async def delete_product(product_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    await db.execute("DELETE FROM company_products WHERE id=$1 AND company_id=$2", product_id, cid)
    return {"status": "deleted"}


@router.get("/company-data/faqs")
async def list_faqs(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    return rs(
        await db.fetch(
            "SELECT * FROM company_faqs WHERE company_id=$1 ORDER BY created_at DESC",
            cid,
        )
    )


@router.post("/company-data/faqs")
async def create_faq(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    question = str(body.get("question", "") or "").strip()
    answer = str(body.get("answer", "") or "").strip()
    if not question:
        raise HTTPException(400, "question is required")
    if not answer:
        raise HTTPException(400, "answer is required")
    faq_id = make_id()
    await db.execute(
        "INSERT INTO company_faqs(id,company_id,question,answer,category,created_at) VALUES($1,$2,$3,$4,$5,NOW())",
        faq_id,
        cid,
        question,
        answer,
        body.get("category", "general"),
    )
    return r(await db.fetchrow("SELECT * FROM company_faqs WHERE id=$1", faq_id))


@router.delete("/company-data/faqs/{faq_id}")
async def delete_faq(faq_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    await db.execute("DELETE FROM company_faqs WHERE id=$1 AND company_id=$2", faq_id, cid)
    return {"status": "deleted"}


# Onboarding Docs
@router.get("/onboarding-docs")
async def list_onboarding_docs(request: Request, category: Optional[str] = None, search: Optional[str] = None):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    sql = "SELECT id,company_id,title,content,category,file_name,file_type,file_size,author_id,author_name,views,created_at,updated_at FROM onboarding_docs WHERE company_id=$1"  # noqa: E501
    args = [cid]
    if category:
        args.append(category)
        sql += f" AND category=${len(args)}"
    if search:
        args.append(f"%{search}%")
        title_idx = len(args)
        args.append(f"%{search}%")
        content_idx = len(args)
        sql += f" AND (title ILIKE ${title_idx} OR content ILIKE ${content_idx})"
    sql += " ORDER BY updated_at DESC LIMIT 200"
    docs = rs(await db.fetch(sql, *args))
    for doc in docs:
        doc["tags"] = [
            row["tag"] for row in await db.fetch("SELECT tag FROM onboarding_doc_tags WHERE doc_id=$1", doc["id"])
        ]
    return docs


@router.post("/onboarding-docs")
async def create_onboarding_doc(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    title = str(body.get("title", "") or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    did = make_id()
    await db.execute(
        "INSERT INTO onboarding_docs(id,company_id,title,content,category,file_name,file_type,file_size,author_id,author_name,views,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,0,$8,$9,0,NOW(),NOW())",  # noqa: E501
        did,
        cid,
        title,
        body.get("content", ""),
        body.get("category", "general"),
        body.get("file_name", ""),
        body.get("file_type", ""),
        cu["sub"],
        cu.get("name", ""),
    )
    for tag in body.get("tags") or []:
        await db.execute(
            "INSERT INTO onboarding_doc_tags(doc_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
            did,
            tag,
        )
    return r(
        await db.fetchrow(
            "SELECT id,company_id,title,content,category,file_name,file_type,author_id,author_name,views,created_at,updated_at FROM onboarding_docs WHERE id=$1",  # noqa: E501
            did,
        )
    )


@router.put("/onboarding-docs/{doc_id}")
async def update_onboarding_doc(doc_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    body.pop("_id", None)
    tags = body.pop("tags", None)
    body["updated_at"] = now_ts()
    set_parts = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(body))
    await db.execute(
        f"UPDATE onboarding_docs SET {set_parts} WHERE id=$1 AND company_id=${len(body) + 2}",
        doc_id,
        *body.values(),
        cid,
    )
    if tags is not None:
        await db.execute("DELETE FROM onboarding_doc_tags WHERE doc_id=$1", doc_id)
        if tags:
            await db.executemany(
                "INSERT INTO onboarding_doc_tags(doc_id,tag) VALUES($1,$2) ON CONFLICT DO NOTHING",
                [(doc_id, t) for t in tags],
            )
    return r(
        await db.fetchrow(
            "SELECT id,company_id,title,content,category,file_name,file_type,author_id,author_name,views,created_at,updated_at FROM onboarding_docs WHERE id=$1",  # noqa: E501
            doc_id,
        )
    )


@router.delete("/onboarding-docs/{doc_id}")
async def delete_onboarding_doc(doc_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    await db.execute("DELETE FROM onboarding_docs WHERE id=$1 AND company_id=$2", doc_id, cid)
    return {"status": "deleted"}


@router.post("/onboarding-docs/upload")
async def upload_onboarding_file(request: Request, file: UploadFile = File(...)):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    if not file.filename:
        raise HTTPException(400, "No file provided")
    ALLOWED = {"pdf", "doc", "docx", "txt", "md", "pptx", "xlsx", "xls", "csv"}
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED:
        raise HTTPException(400, f"File type .{ext} not supported.")
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(400, "File too large. Maximum size is 10 MB.")
    file_b64 = base64.b64encode(contents).decode("utf-8")
    text_content = (
        contents.decode("utf-8", errors="replace")[:5000]
        if ext in ("txt", "md", "csv")
        else f"(Uploaded {ext.upper()} file — {len(contents):,} bytes)"
    )
    did = make_id()
    await db.execute(
        "INSERT INTO onboarding_docs(id,company_id,title,content,category,file_name,file_type,file_data,file_size,author_id,author_name,views,created_at,updated_at) VALUES($1,$2,$3,$4,'onboarding',$5,$6,$7,$8,$9,$10,0,NOW(),NOW())",  # noqa: E501
        did,
        cid,
        file.filename,
        text_content,
        file.filename,
        ext,
        file_b64,
        len(contents),
        cu["sub"],
        cu.get("name", ""),
    )
    await db.execute(
        "INSERT INTO onboarding_doc_tags(doc_id,tag) VALUES($1,'uploaded') ON CONFLICT DO NOTHING",
        did,
    )
    return r(
        await db.fetchrow(
            "SELECT id,company_id,title,content,category,file_name,file_type,file_size,author_id,author_name,views,created_at,updated_at FROM onboarding_docs WHERE id=$1",  # noqa: E501
            did,
        )
    )


@router.get("/onboarding-docs/{doc_id}/download")
async def download_onboarding_doc(doc_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    doc = r(
        await db.fetchrow(
            "SELECT * FROM onboarding_docs WHERE id=$1 AND company_id=$2 LIMIT 1",
            doc_id,
            cid,
        )
    )
    if not doc:
        raise HTTPException(404, "Document not found")
    if not doc.get("file_data"):
        raise HTTPException(404, "No file attached to this document")
    file_bytes = base64.b64decode(doc["file_data"])
    ct_map = {
        "pdf": "application/pdf",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls": "application/vnd.ms-excel",
        "csv": "text/csv",
        "txt": "text/plain",
        "md": "text/markdown",
    }
    ct = ct_map.get(doc.get("file_type", ""), "application/octet-stream")
    return Response(
        content=file_bytes,
        media_type=ct,
        headers={"Content-Disposition": f'attachment; filename="{doc.get("file_name", "download")}"'},
    )
