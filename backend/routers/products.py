"""routers/products.py — Products, FAQs, Onboarding Docs — PostgreSQL."""

import base64
import logging
import posixpath
import re
import zipfile
from io import BytesIO
from typing import Optional
from xml.etree import ElementTree as ET
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from openpyxl import load_workbook
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


async def _get_product(db, product_id, cid):
    prod = r(
        await db.fetchrow(
            "SELECT * FROM company_products WHERE id=$1 AND company_id=$2 LIMIT 1",
            product_id,
            cid,
        )
    )
    if prod:
        prod["images"] = rs(
            await db.fetch(
                "SELECT image_url FROM product_images WHERE product_id=$1 ORDER BY sort_order",
                product_id,
            )
        )
        prod["images"] = [row["image_url"] for row in prod["images"]]
        prod["features"] = [
            row["feature"]
            for row in await db.fetch(
                "SELECT feature FROM product_features WHERE product_id=$1 ORDER BY sort_order",
                product_id,
            )
        ]
    return prod


@router.get("/company-data/products")
async def list_products(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    prods = rs(
        await db.fetch(
            "SELECT * FROM company_products WHERE company_id=$1 ORDER BY created_at DESC LIMIT 200",
            cid,
        )
    )
    for p in prods:
        p["images"] = [
            row["image_url"]
            for row in await db.fetch(
                "SELECT image_url FROM product_images WHERE product_id=$1 ORDER BY sort_order",
                p["id"],
            )
        ]
        p["features"] = [
            row["feature"]
            for row in await db.fetch(
                "SELECT feature FROM product_features WHERE product_id=$1 ORDER BY sort_order",
                p["id"],
            )
        ]
    return prods


@router.post("/company-data/products")
async def create_product(request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    name = str(body.get("name", "") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    pid = make_id()
    await db.execute(
        "INSERT INTO company_products(id,company_id,name,product_title,description,price,price_currency,category,product_type,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW())",  # noqa: E501
        pid,
        cid,
        name,
        body.get("product_title", ""),
        body.get("description", ""),
        body.get("price", ""),
        body.get("price_currency", "USD"),
        body.get("category", "general"),
        body.get("product_type", "standard"),
    )
    images = normalize_product_images(body.get("images", []))
    if images:
        await db.executemany(
            "INSERT INTO product_images(id,product_id,image_url,sort_order,created_at) VALUES($1,$2,$3,$4,NOW())",
            [(make_id(), pid, url, i) for i, url in enumerate(images)],
        )
    features = body.get("features", []) or []
    if features:
        await db.executemany(
            "INSERT INTO product_features(id,product_id,feature,sort_order) VALUES($1,$2,$3,$4)",
            [(make_id(), pid, f, i) for i, f in enumerate(features)],
        )
    return await _get_product(db, pid, cid)


@router.post("/company-data/products/generate-description")
async def generate_product_desc_endpoint(request: Request):
    db = _db(request)
    await get_current_user_flexible(request)
    body = await request.json()
    name = str(body.get("name", "") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    active_engines = await get_active_llm_engines(db)
    desc = await generate_product_description(
        name=name,
        product_title=body.get("product_title", ""),
        product_type=body.get("product_type", ""),
        category=body.get("category", "general"),
        price=body.get("price", ""),
        price_currency=body.get("price_currency", "USD"),
        images=body.get("images", []),
        engines=active_engines,
    )
    return {"description": desc}


@router.put("/company-data/products/{product_id}")
async def update_product(product_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    body = await request.json()
    body.pop("_id", None)
    images = body.pop("images", None)
    features = body.pop("features", None)
    body["updated_at"] = now_ts()
    set_parts = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(body))
    await db.execute(
        f"UPDATE company_products SET {set_parts} WHERE id=$1 AND company_id=${len(body) + 2}",
        product_id,
        *body.values(),
        cid,
    )
    if images is not None:
        await db.execute("DELETE FROM product_images WHERE product_id=$1", product_id)
        clean = normalize_product_images(images)
        if clean:
            await db.executemany(
                "INSERT INTO product_images(id,product_id,image_url,sort_order,created_at) VALUES($1,$2,$3,$4,NOW())",
                [(make_id(), product_id, url, i) for i, url in enumerate(clean)],
            )
    if features is not None:
        await db.execute("DELETE FROM product_features WHERE product_id=$1", product_id)
        if features:
            await db.executemany(
                "INSERT INTO product_features(id,product_id,feature,sort_order) VALUES($1,$2,$3,$4)",
                [(make_id(), product_id, f, i) for i, f in enumerate(features)],
            )
    return await _get_product(db, product_id, cid)


@router.delete("/company-data/products/{product_id}")
async def delete_product(product_id: str, request: Request):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    await db.execute("DELETE FROM company_products WHERE id=$1 AND company_id=$2", product_id, cid)
    return {"status": "deleted"}


@router.post("/company-data/products/bulk-upload")
async def bulk_upload_products(request: Request, file: UploadFile = File(...)):
    db = _db(request)
    cu = await get_current_user_flexible(request)
    cid = cu.get("company_id", "")
    if not file.filename:
        raise HTTPException(400, "No file provided.")
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext != "xlsx":
        raise HTTPException(400, "Only .xlsx files are supported for bulk upload.")
    content_type = (file.content_type or "").lower()
    valid_types = {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    }
    if content_type and content_type not in valid_types:
        raise HTTPException(400, "Invalid Content-Type. Use multipart/form-data with an .xlsx file.")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Uploaded file is empty.")

    try:
        workbook = load_workbook(filename=BytesIO(raw), data_only=True)
    except Exception:
        raise HTTPException(400, "Invalid Excel file. Please use the provided .xlsx template.")

    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(400, "The Excel file has no rows.")
    embedded_images_by_row = _extract_sheet_row_images(sheet)
    richvalue_images_by_row = _extract_richvalue_row_images(raw)
    for row_number, row_images in richvalue_images_by_row.items():
        if not row_images:
            continue
        embedded_images_by_row.setdefault(row_number, []).extend(row_images)

    headers = [_normalize_header(cell) for cell in rows[0]]
    if not headers or "name" not in headers:
        raise HTTPException(400, "Invalid template. The header row must include a 'name' column.")

    created_count = 0
    errors = []
    warnings = []
    alias_map = {
        "product_name": "name",
        "title": "product_title",
        "product_code": "product_title",
        "code": "product_title",
        "sku": "product_title",
        "type": "product_type",
        "currency": "price_currency",
        "desc": "description",
        "image": "images",
        "image_urls": "images",
        "photos": "images",
        "image1": "image_1",
        "image2": "image_2",
        "image3": "image_3",
        "photo_1": "image_1",
        "photo_2": "image_2",
        "photo_3": "image_3",
    }

    for idx, row in enumerate(rows[1:], start=2):
        values = {headers[i]: row[i] for i in range(min(len(headers), len(row))) if headers[i]}
        normalized = {}
        for key, value in values.items():
            normalized[alias_map.get(key, key)] = value
        name = str(normalized.get("name") or "").strip()
        if not name:
            errors.append({"row": idx, "error": "Product name is required."})
            continue

        price = normalized.get("price", "")
        price_text = "" if price is None else str(price).strip()
        images = []
        declared_image_inputs = []
        for image_key in ["image_1", "image_2", "image_3"]:
            value = normalized.get(image_key)
            if value:
                cleaned = str(value).strip()
                images.append(cleaned)
                declared_image_inputs.append(cleaned)
        legacy_images = str(normalized.get("images") or "").strip()
        if legacy_images:
            split_images = [item.strip() for item in legacy_images.split(";") if item.strip()]
            images.extend(split_images)
            declared_image_inputs.extend(split_images)
        embedded_row_images = embedded_images_by_row.get(idx) or []
        if embedded_row_images:
            images.extend(embedded_row_images)
        clean_images = normalize_product_images(images, limit=3)
        if embedded_row_images and not clean_images:
            warnings.append(
                {
                    "row": idx,
                    "warning": "Embedded images were detected but could not be converted into valid product images.",
                }
            )
        elif _has_non_empty_image_input(declared_image_inputs) and not clean_images:
            warnings.append(
                {
                    "row": idx,
                    "warning": "Image values were provided, but only embedded Excel images or full public image URLs are supported.",  # noqa: E501
                }
            )
        elif not clean_images:
            warnings.append(
                {
                    "row": idx,
                    "warning": "Product imported without images. Use embedded workbook images or full public image URLs.",  # noqa: E501
                }
            )
        features_raw = str(normalized.get("features") or "").strip()
        features = [item.strip() for item in features_raw.split(";") if item.strip()]
        pid = make_id()
        try:
            await db.execute(
                "INSERT INTO company_products(id,company_id,name,product_title,description,price,price_currency,category,product_type,created_at,updated_at) "  # noqa: E501
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW(),NOW())",
                pid,
                cid,
                name,
                str(normalized.get("product_title") or "").strip(),
                str(normalized.get("description") or "").strip(),
                price_text,
                str(normalized.get("price_currency") or "USD").strip() or "USD",
                str(normalized.get("category") or "general").strip() or "general",
                str(normalized.get("product_type") or "standard").strip() or "standard",
            )
            if clean_images:
                await db.executemany(
                    "INSERT INTO product_images(id,product_id,image_url,sort_order,created_at) VALUES($1,$2,$3,$4,NOW())",  # noqa: E501
                    [(make_id(), pid, url, image_index) for image_index, url in enumerate(clean_images)],
                )
            if features:
                await db.executemany(
                    "INSERT INTO product_features(id,product_id,feature,sort_order) VALUES($1,$2,$3,$4)",
                    [(make_id(), pid, feature, feature_index) for feature_index, feature in enumerate(features)],
                )
            created_count += 1
        except Exception as exc:
            logger.error(f"Bulk product import failed on row {idx}: {exc}")
            errors.append({"row": idx, "error": "Failed to import this row."})

    return {
        "created_count": created_count,
        "error_count": len(errors),
        "errors": errors,
        "warning_count": len(warnings),
        "warnings": warnings,
        "template_columns": [
            "name",
            "product_title",
            "description",
            "price",
            "price_currency",
            "category",
            "product_type",
            "features",
            "image_1",
            "image_2",
            "image_3",
        ],
        "used_as_ai_context": True,
        "image_input_support": [
            "embedded_excel_images",
            "http_urls",
            "https_urls",
            "data_urls",
        ],
    }


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
