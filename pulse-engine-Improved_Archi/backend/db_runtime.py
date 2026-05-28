import copy
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from postgres.models import GenericDocument
from postgres.session import check_postgres_health, session_factory
from scripts import migrate_analytics_ai_phase4_to_postgres as phase4
from scripts import migrate_auth_core_to_postgres as phase1
from scripts import migrate_crm_phase2_to_postgres as phase2
from scripts import migrate_crm_phase3_to_postgres as phase3


logger = logging.getLogger(__name__)

_MISSING = object()


def _build_collection_plan() -> dict[str, dict[str, Any]]:
    plan: dict[str, dict[str, Any]] = {}

    for collection_name, model, mapper in phase1.MIGRATION_PLAN:
        plan[collection_name] = {
            "model": model,
            "mapper": mapper,
            "conflict_columns": ["id"],
        }

    for collection_name, model, mapper, conflict_columns, _required_fields in phase2.MIGRATION_PLAN:
        plan[collection_name] = {
            "model": model,
            "mapper": mapper,
            "conflict_columns": conflict_columns or ["id"],
        }

    for collection_name, model, mapper, conflict_columns, _required_fields in phase3.MIGRATION_PLAN:
        plan[collection_name] = {
            "model": model,
            "mapper": mapper,
            "conflict_columns": conflict_columns or ["id"],
        }

    for collection_name, model, mapper, conflict_columns, _required_fields in phase4.MIGRATION_PLAN:
        plan[collection_name] = {
            "model": model,
            "mapper": mapper,
            "conflict_columns": conflict_columns or ["id"],
        }

    return plan


COLLECTION_PLAN = _build_collection_plan()
MIGRATED_COLLECTIONS = set(COLLECTION_PLAN.keys())
_GENERIC_PLAN_CACHE: dict[str, dict[str, Any]] = {}
_GENERIC_TABLE_READY = False


@dataclass
class InsertOneResult:
    inserted_id: Any


@dataclass
class InsertManyResult:
    inserted_ids: list[Any]


@dataclass
class UpdateResult:
    matched_count: int
    modified_count: int
    upserted_id: Any = None


@dataclass
class DeleteResult:
    deleted_count: int


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _split_path(path: str) -> list[str]:
    return [part for part in (path or "").split(".") if part]


def _deep_get(doc: dict[str, Any], path: str, default: Any = _MISSING) -> Any:
    current: Any = doc
    for part in _split_path(path):
        if isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index < 0 or index >= len(current):
                return default
            current = current[index]
            continue
        return default
    return current


def _deep_set(doc: dict[str, Any], path: str, value: Any) -> None:
    parts = _split_path(path)
    if not parts:
        return
    current: Any = doc
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    if isinstance(current, dict):
        current[parts[-1]] = value


def _deep_delete(doc: dict[str, Any], path: str) -> None:
    parts = _split_path(path)
    if not parts:
        return
    current: Any = doc
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _values_equal(left: Any, right: Any) -> bool:
    left_dt = _parse_iso_datetime(left)
    right_dt = _parse_iso_datetime(right)
    if left_dt is not None and right_dt is not None:
        return left_dt == right_dt
    return left == right


def _compare_values(left: Any, right: Any) -> int:
    left_dt = _parse_iso_datetime(left)
    right_dt = _parse_iso_datetime(right)
    if left_dt is not None and right_dt is not None:
        if left_dt < right_dt:
            return -1
        if left_dt > right_dt:
            return 1
        return 0

    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if left < right:
            return -1
        if left > right:
            return 1
        return 0

    try:
        if left < right:
            return -1
        if left > right:
            return 1
        return 0
    except Exception:
        left_text = "" if left is None else str(left)
        right_text = "" if right is None else str(right)
        if left_text < right_text:
            return -1
        if left_text > right_text:
            return 1
        return 0


def _is_operator_dict(value: Any) -> bool:
    return isinstance(value, dict) and any(str(key).startswith("$") for key in value.keys())


def _match_scalar_with_operator(actual: Any, operator: str, expected: Any, full_condition: dict[str, Any]) -> bool:
    if operator == "$exists":
        should_exist = bool(expected)
        return (actual is not _MISSING) if should_exist else (actual is _MISSING)

    if actual is _MISSING:
        return False

    if operator == "$eq":
        return _values_equal(actual, expected)
    if operator == "$ne":
        return not _values_equal(actual, expected)
    if operator == "$in":
        options = expected if isinstance(expected, (list, tuple, set)) else [expected]
        if isinstance(actual, list):
            return any(_values_equal(item, option) for item in actual for option in options)
        return any(_values_equal(actual, option) for option in options)
    if operator == "$nin":
        options = expected if isinstance(expected, (list, tuple, set)) else [expected]
        if isinstance(actual, list):
            return all(not _values_equal(item, option) for item in actual for option in options)
        return all(not _values_equal(actual, option) for option in options)
    if operator == "$gt":
        return _compare_values(actual, expected) > 0
    if operator == "$gte":
        return _compare_values(actual, expected) >= 0
    if operator == "$lt":
        return _compare_values(actual, expected) < 0
    if operator == "$lte":
        return _compare_values(actual, expected) <= 0
    if operator == "$size":
        return isinstance(actual, list) and len(actual) == int(expected)
    if operator == "$regex":
        flags = 0
        options = str(full_condition.get("$options", ""))
        if "i" in options:
            flags |= re.IGNORECASE
        pattern = expected.pattern if isinstance(expected, re.Pattern) else str(expected)
        return re.search(pattern, str(actual), flags=flags) is not None
    if operator == "$elemMatch":
        if not isinstance(actual, list):
            return False
        return any(_match_value(item, expected) for item in actual)
    if operator == "$not":
        return not _match_value(actual, expected)
    return False


def _match_value(actual: Any, expected: Any) -> bool:
    if isinstance(expected, re.Pattern):
        if actual is _MISSING:
            return False
        return expected.search(str(actual)) is not None

    if _is_operator_dict(expected):
        for operator, operand in expected.items():
            if not _match_scalar_with_operator(actual, operator, operand, expected):
                return False
        return True

    if actual is _MISSING:
        return False

    if isinstance(actual, list) and not isinstance(expected, (list, tuple, dict, set, re.Pattern)):
        return any(_values_equal(item, expected) for item in actual)

    return _values_equal(actual, expected)


def _match_query(doc: dict[str, Any], query: dict[str, Any] | None) -> bool:
    if not query:
        return True

    for key, condition in query.items():
        if key == "$and":
            clauses = condition if isinstance(condition, list) else []
            if not all(_match_query(doc, clause) for clause in clauses):
                return False
            continue
        if key == "$or":
            clauses = condition if isinstance(condition, list) else []
            if not any(_match_query(doc, clause) for clause in clauses):
                return False
            continue
        if key == "$nor":
            clauses = condition if isinstance(condition, list) else []
            if any(_match_query(doc, clause) for clause in clauses):
                return False
            continue
        if key.startswith("$"):
            return False

        actual = _deep_get(doc, key, _MISSING)
        if not _match_value(actual, condition):
            return False

    return True


def _project_document(doc: dict[str, Any], projection: dict[str, Any] | None) -> dict[str, Any]:
    if not projection:
        return copy.deepcopy(doc)

    projection_without_id = {k: v for k, v in projection.items() if k != "_id"}
    include_mode = any(bool(value) for value in projection_without_id.values())

    if include_mode:
        output: dict[str, Any] = {}
        for field, include in projection_without_id.items():
            if not include:
                continue
            value = _deep_get(doc, field, _MISSING)
            if value is not _MISSING:
                _deep_set(output, field, copy.deepcopy(value))
        return output

    output = copy.deepcopy(doc)
    for field, include in projection_without_id.items():
        if include:
            continue
        _deep_delete(output, field)
    if projection.get("_id") == 0:
        output.pop("_id", None)
    return output


def _extract_upsert_seed(query: dict[str, Any] | None) -> dict[str, Any]:
    seed: dict[str, Any] = {}
    if not query:
        return seed

    for key, value in query.items():
        if key == "$and" and isinstance(value, list):
            for clause in value:
                seed.update(_extract_upsert_seed(clause))
            continue
        if key.startswith("$"):
            continue
        if isinstance(value, dict):
            if "$eq" in value:
                seed[key] = copy.deepcopy(value["$eq"])
            continue
        seed[key] = copy.deepcopy(value)
    return seed


def _ensure_list_field(doc: dict[str, Any], path: str) -> list[Any]:
    current = _deep_get(doc, path, _MISSING)
    if current is _MISSING or current is None:
        new_list: list[Any] = []
        _deep_set(doc, path, new_list)
        return new_list
    if isinstance(current, list):
        return current
    converted = [current]
    _deep_set(doc, path, converted)
    return converted


def _apply_update(doc: dict[str, Any], update: dict[str, Any]) -> bool:
    if not update:
        return False

    changed = False
    is_operator_update = any(key.startswith("$") for key in update.keys())

    if not is_operator_update:
        doc_id = doc.get("id")
        replacement = copy.deepcopy(update)
        if doc_id and "id" not in replacement:
            replacement["id"] = doc_id
        if doc != replacement:
            doc.clear()
            doc.update(replacement)
            changed = True
        return changed

    for operator, payload in update.items():
        if not isinstance(payload, dict):
            continue
        if operator == "$set":
            for path, value in payload.items():
                current = _deep_get(doc, path, _MISSING)
                if current is _MISSING or not _values_equal(current, value):
                    _deep_set(doc, path, copy.deepcopy(value))
                    changed = True
        elif operator == "$unset":
            for path in payload.keys():
                current = _deep_get(doc, path, _MISSING)
                if current is not _MISSING:
                    _deep_delete(doc, path)
                    changed = True
        elif operator == "$inc":
            for path, value in payload.items():
                increment = value if isinstance(value, (int, float)) else 0
                current = _deep_get(doc, path, 0)
                if not isinstance(current, (int, float)):
                    current = 0
                new_value = current + increment
                if not _values_equal(current, new_value):
                    _deep_set(doc, path, new_value)
                    changed = True
        elif operator == "$push":
            for path, value in payload.items():
                items = _ensure_list_field(doc, path)
                if isinstance(value, dict) and "$each" in value and isinstance(value["$each"], list):
                    items.extend(copy.deepcopy(value["$each"]))
                else:
                    items.append(copy.deepcopy(value))
                changed = True
        elif operator == "$addToSet":
            for path, value in payload.items():
                items = _ensure_list_field(doc, path)
                if isinstance(value, dict) and "$each" in value and isinstance(value["$each"], list):
                    for item in value["$each"]:
                        if not any(_values_equal(existing, item) for existing in items):
                            items.append(copy.deepcopy(item))
                            changed = True
                else:
                    if not any(_values_equal(existing, value) for existing in items):
                        items.append(copy.deepcopy(value))
                        changed = True
        elif operator == "$pull":
            for path, value in payload.items():
                items = _ensure_list_field(doc, path)
                original_len = len(items)
                items[:] = [item for item in items if not _match_value(item, value)]
                if len(items) != original_len:
                    changed = True

    return changed


def _eval_expression(expr: Any, doc: dict[str, Any]) -> Any:
    if isinstance(expr, str) and expr.startswith("$"):
        return _deep_get(doc, expr[1:], None)

    if isinstance(expr, dict):
        if "$substr" in expr:
            args = expr["$substr"] if isinstance(expr["$substr"], list) else []
            if len(args) != 3:
                return ""
            raw_text = _eval_expression(args[0], doc)
            text = "" if raw_text is None else str(raw_text)
            start = int(args[1] or 0)
            length = int(args[2] or 0)
            if length <= 0:
                return ""
            return text[start : start + length]

        if "$cond" in expr:
            args = expr["$cond"] if isinstance(expr["$cond"], list) else []
            if len(args) != 3:
                return None
            return _eval_expression(args[1], doc) if _eval_condition(args[0], doc) else _eval_expression(args[2], doc)

        if len(expr) == 1:
            operator = next(iter(expr.keys()))
            if operator in {"$gt", "$gte", "$lt", "$lte", "$eq", "$ne"}:
                value = expr[operator]
                if not isinstance(value, list) or len(value) != 2:
                    return False
                left = _eval_expression(value[0], doc)
                right = _eval_expression(value[1], doc)
                cmp_result = _compare_values(left, right)
                if operator == "$gt":
                    return cmp_result > 0
                if operator == "$gte":
                    return cmp_result >= 0
                if operator == "$lt":
                    return cmp_result < 0
                if operator == "$lte":
                    return cmp_result <= 0
                if operator == "$eq":
                    return _values_equal(left, right)
                if operator == "$ne":
                    return not _values_equal(left, right)

    return expr


def _eval_condition(expr: Any, doc: dict[str, Any]) -> bool:
    value = _eval_expression(expr, doc)
    return bool(value)


def _coerce_number(value: Any) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _make_hashable(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(
            (key, _make_hashable(subvalue)) for key, subvalue in sorted(value.items(), key=lambda item: item[0])
        )
    if isinstance(value, list):
        return tuple(_make_hashable(item) for item in value)
    return value


def _run_pipeline(docs: list[dict[str, Any]], pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [copy.deepcopy(doc) for doc in docs]

    for stage in pipeline:
        if "$match" in stage:
            rows = [row for row in rows if _match_query(row, stage["$match"])]
            continue

        if "$group" in stage:
            spec = stage["$group"]
            key_expr = spec.get("_id")
            accumulator_specs = {field: expr for field, expr in spec.items() if field != "_id"}
            grouped: dict[Any, dict[str, Any]] = {}

            for row in rows:
                key_value = _eval_expression(key_expr, row)
                key_hash = _make_hashable(key_value)
                bucket = grouped.setdefault(key_hash, {"_id": copy.deepcopy(key_value)})

                for field, expr in accumulator_specs.items():
                    if not isinstance(expr, dict) or not expr:
                        continue
                    if "$sum" in expr:
                        operand = expr["$sum"]
                        value = _eval_expression(operand, row)
                        bucket[field] = bucket.get(field, 0.0) + _coerce_number(value)
                    elif "$avg" in expr:
                        operand = expr["$avg"]
                        value = _eval_expression(operand, row)
                        sum_key = f"__sum__{field}"
                        count_key = f"__count__{field}"
                        bucket[sum_key] = bucket.get(sum_key, 0.0) + _coerce_number(value)
                        bucket[count_key] = bucket.get(count_key, 0) + 1

            grouped_rows: list[dict[str, Any]] = []
            for bucket in grouped.values():
                output: dict[str, Any] = {"_id": bucket.get("_id")}
                for field, expr in accumulator_specs.items():
                    if not isinstance(expr, dict) or not expr:
                        continue
                    if "$sum" in expr:
                        value = bucket.get(field, 0.0)
                        output[field] = int(value) if isinstance(value, float) and value.is_integer() else value
                    elif "$avg" in expr:
                        sum_key = f"__sum__{field}"
                        count_key = f"__count__{field}"
                        count_value = bucket.get(count_key, 0)
                        output[field] = (bucket.get(sum_key, 0.0) / count_value) if count_value else None
                grouped_rows.append(output)

            rows = grouped_rows
            continue

        if "$sort" in stage:
            sort_spec = stage["$sort"] if isinstance(stage["$sort"], dict) else {}
            for field, direction in reversed(list(sort_spec.items())):
                reverse = int(direction) < 0
                rows.sort(
                    key=lambda row: (
                        _deep_get(row, field, None) is None,
                        _deep_get(row, field, None),
                    ),
                    reverse=reverse,
                )
            continue

        if "$limit" in stage:
            limit_value = int(stage["$limit"])
            rows = rows[: max(limit_value, 0)]
            continue

        if "$project" in stage:
            projection = stage["$project"] if isinstance(stage["$project"], dict) else {}
            projected_rows: list[dict[str, Any]] = []
            include_mode = any(bool(value) for key, value in projection.items() if key != "_id")
            for row in rows:
                if include_mode:
                    output: dict[str, Any] = {}
                    for field, expression in projection.items():
                        if field == "_id":
                            continue
                        if expression in (1, True):
                            value = _deep_get(row, field, _MISSING)
                            if value is not _MISSING:
                                _deep_set(output, field, copy.deepcopy(value))
                        elif expression in (0, False):
                            continue
                        else:
                            output[field] = _eval_expression(expression, row)
                    projected_rows.append(output)
                else:
                    output = copy.deepcopy(row)
                    for field, expression in projection.items():
                        if expression in (0, False):
                            _deep_delete(output, field)
                    projected_rows.append(output)
            rows = projected_rows
            continue

        if "$count" in stage:
            field_name = str(stage["$count"])
            rows = [{field_name: len(rows)}]
            continue

    return rows


class PostgresCursor:
    def __init__(
        self, collection: "RuntimeCollection", query: dict[str, Any] | None, projection: dict[str, Any] | None
    ):
        self._collection = collection
        self._query = query or {}
        self._projection = projection
        self._sort_fields: list[tuple[str, int]] = []
        self._limit_value: int | None = None
        self._docs_cache: list[dict[str, Any]] | None = None
        self._iter_index = 0

    def sort(self, key_or_list: Any, direction: int = 1):
        if isinstance(key_or_list, list):
            parsed: list[tuple[str, int]] = []
            for item in key_or_list:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    parsed.append((str(item[0]), int(item[1])))
            self._sort_fields = parsed
        else:
            self._sort_fields = [(str(key_or_list), int(direction))]
        return self

    def limit(self, value: int):
        self._limit_value = int(value)
        return self

    async def _load_docs(self) -> list[dict[str, Any]]:
        if self._docs_cache is None:
            docs = await self._collection._pg_query_documents(self._query, self._projection)
            for field, direction in reversed(self._sort_fields):
                reverse = int(direction) < 0
                docs.sort(
                    key=lambda row: (
                        _deep_get(row, field, None) is None,
                        _deep_get(row, field, None),
                    ),
                    reverse=reverse,
                )
            if self._limit_value is not None and self._limit_value >= 0:
                docs = docs[: self._limit_value]
            self._docs_cache = docs
        return self._docs_cache

    async def to_list(self, length: int | None):
        docs = await self._load_docs()
        if length is None:
            return copy.deepcopy(docs)
        return copy.deepcopy(docs[: max(int(length), 0)])

    def __aiter__(self):
        self._iter_index = 0
        return self

    async def __anext__(self):
        docs = await self._load_docs()
        if self._iter_index >= len(docs):
            raise StopAsyncIteration
        item = copy.deepcopy(docs[self._iter_index])
        self._iter_index += 1
        return item


class PostgresAggregateCursor:
    def __init__(self, collection: "RuntimeCollection", pipeline: list[dict[str, Any]]):
        self._collection = collection
        self._pipeline = pipeline
        self._docs_cache: list[dict[str, Any]] | None = None
        self._iter_index = 0

    async def _load_docs(self) -> list[dict[str, Any]]:
        if self._docs_cache is None:
            source_docs = await self._collection._pg_query_documents({}, None)
            self._docs_cache = _run_pipeline(source_docs, self._pipeline)
        return self._docs_cache

    async def to_list(self, length: int | None):
        docs = await self._load_docs()
        if length is None:
            return copy.deepcopy(docs)
        return copy.deepcopy(docs[: max(int(length), 0)])

    def __aiter__(self):
        self._iter_index = 0
        return self

    async def __anext__(self):
        docs = await self._load_docs()
        if self._iter_index >= len(docs):
            raise StopAsyncIteration
        item = copy.deepcopy(docs[self._iter_index])
        self._iter_index += 1
        return item


class RuntimeCollection:
    def __init__(self, name: str):
        self.name = name

    def _plan_entry(self) -> dict[str, Any]:
        entry = COLLECTION_PLAN.get(self.name)
        if entry:
            return entry

        cached = _GENERIC_PLAN_CACHE.get(self.name)
        if cached:
            return cached

        def map_generic_document(doc: dict[str, Any]) -> dict[str, Any]:
            canonical = copy.deepcopy(doc)
            doc_id = str(canonical.get("id") or "").strip()
            if not doc_id:
                doc_id = uuid.uuid4().hex
                canonical["id"] = doc_id

            created_at = _parse_iso_datetime(canonical.get("created_at")) or datetime.now(timezone.utc)
            updated_at = _parse_iso_datetime(canonical.get("updated_at")) or created_at
            return {
                "id": doc_id,
                "collection_name": self.name,
                "created_at": created_at,
                "updated_at": updated_at,
                "raw_data": canonical,
            }

        generic_entry = {
            "model": GenericDocument,
            "mapper": map_generic_document,
            "conflict_columns": ["collection_name", "id"],
            "is_generic": True,
        }
        _GENERIC_PLAN_CACHE[self.name] = generic_entry
        return generic_entry

    async def _pg_all_documents(self) -> list[dict[str, Any]]:
        entry = self._plan_entry()
        model = entry["model"]
        await self._ensure_generic_table_if_needed(entry)
        factory = session_factory()
        async with factory() as session:
            stmt = select(model.raw_data)
            if entry.get("is_generic"):
                stmt = stmt.where(model.collection_name == self.name)
            result = await session.execute(stmt)
            rows = result.scalars().all()
        docs: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                docs.append(copy.deepcopy(row))
        return docs

    async def _pg_query_documents(
        self, query: dict[str, Any] | None, projection: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        docs = await self._pg_all_documents()
        filtered = [doc for doc in docs if _match_query(doc, query or {})]
        return [_project_document(doc, projection) for doc in filtered]

    async def _pg_upsert_document(self, doc: dict[str, Any]) -> dict[str, Any]:
        entry = self._plan_entry()
        model = entry["model"]
        mapper = entry["mapper"]
        conflict_columns = entry["conflict_columns"]
        await self._ensure_generic_table_if_needed(entry)

        canonical_doc = copy.deepcopy(doc)
        mapped_row = mapper(copy.deepcopy(canonical_doc))
        generated_id = mapped_row.get("id")
        if not generated_id:
            raise RuntimeError(f"Unable to generate id for collection '{self.name}'")
        if not canonical_doc.get("id"):
            canonical_doc["id"] = generated_id
        if entry.get("is_generic"):
            mapped_row["collection_name"] = self.name
        mapped_row["raw_data"] = copy.deepcopy(canonical_doc)

        stmt = pg_insert(model).values(mapped_row)
        update_columns = {
            column.name: getattr(stmt.excluded, column.name)
            for column in model.__table__.columns
            if column.name not in set(conflict_columns) and not column.primary_key
        }
        stmt = stmt.on_conflict_do_update(index_elements=conflict_columns, set_=update_columns)

        factory = session_factory()
        async with factory() as session:
            await session.execute(stmt)
            await session.commit()

        return canonical_doc

    async def _pg_update(self, query: dict[str, Any], update: dict[str, Any], upsert: bool, many: bool) -> UpdateResult:
        existing = await self._pg_query_documents(query, None)
        matched = len(existing)
        modified = 0
        upserted_id = None

        if matched == 0 and upsert:
            seeded = _extract_upsert_seed(query)
            changed = _apply_update(seeded, update)
            if changed or seeded:
                persisted = await self._pg_upsert_document(seeded)
                upserted_id = persisted.get("id")
                return UpdateResult(matched_count=0, modified_count=1, upserted_id=upserted_id)
            return UpdateResult(matched_count=0, modified_count=0, upserted_id=None)

        targets = existing if many else existing[:1]
        for doc in targets:
            original = copy.deepcopy(doc)
            changed = _apply_update(doc, update)
            if changed or doc != original:
                await self._pg_upsert_document(doc)
                modified += 1

        return UpdateResult(
            matched_count=(matched if many else min(matched, 1)), modified_count=modified, upserted_id=upserted_id
        )

    async def _pg_delete(self, query: dict[str, Any], many: bool) -> DeleteResult:
        matched_docs = await self._pg_query_documents(query, None)
        targets = matched_docs if many else matched_docs[:1]
        ids = [str(doc.get("id")) for doc in targets if doc.get("id")]
        if not ids:
            return DeleteResult(deleted_count=0)

        entry = self._plan_entry()
        model = entry["model"]
        await self._ensure_generic_table_if_needed(entry)
        factory = session_factory()
        async with factory() as session:
            stmt = delete(model).where(model.id.in_(ids))
            if entry.get("is_generic"):
                stmt = stmt.where(model.collection_name == self.name)
            result = await session.execute(stmt)
            await session.commit()
            count = int(result.rowcount or 0)
        return DeleteResult(deleted_count=count)

    async def _ensure_generic_table_if_needed(self, entry: dict[str, Any]) -> None:
        global _GENERIC_TABLE_READY

        if not entry.get("is_generic") or _GENERIC_TABLE_READY:
            return

        factory = session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS generic_documents (
                        row_id SERIAL PRIMARY KEY,
                        collection_name VARCHAR(128) NOT NULL,
                        id VARCHAR(128) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        raw_data JSON NOT NULL
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_generic_documents_collection_id
                    ON generic_documents (collection_name, id)
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_generic_documents_collection
                    ON generic_documents (collection_name)
                    """
                )
            )
            await session.commit()

        _GENERIC_TABLE_READY = True

    def find(self, query: dict[str, Any] | None = None, projection: dict[str, Any] | None = None):
        return PostgresCursor(self, query, projection)

    async def find_one(self, query: dict[str, Any] | None = None, projection: dict[str, Any] | None = None):
        docs = await self._pg_query_documents(query or {}, projection)
        return docs[0] if docs else None

    def aggregate(self, pipeline: list[dict[str, Any]]):
        return PostgresAggregateCursor(self, pipeline)

    async def count_documents(self, query: dict[str, Any] | None = None) -> int:
        docs = await self._pg_query_documents(query or {}, {"id": 1})
        return len(docs)

    async def insert_one(self, document: dict[str, Any]):
        doc_for_write = copy.deepcopy(document)
        doc_for_write = await self._pg_upsert_document(doc_for_write)

        inserted_id = doc_for_write.get("id")
        if inserted_id is None:
            inserted_id = document.get("id")
        return InsertOneResult(inserted_id=inserted_id)

    async def insert_many(self, documents: list[dict[str, Any]]):
        docs_for_write = [copy.deepcopy(doc) for doc in documents]
        persisted_docs: list[dict[str, Any]] = []
        for doc in docs_for_write:
            persisted_docs.append(await self._pg_upsert_document(doc))
        docs_for_write = persisted_docs

        inserted_ids = [doc.get("id") for doc in docs_for_write]
        return InsertManyResult(inserted_ids=inserted_ids)

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False):
        return await self._pg_update(query, update, upsert, many=False)

    async def update_many(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False):
        return await self._pg_update(query, update, upsert, many=True)

    async def delete_one(self, query: dict[str, Any]):
        return await self._pg_delete(query, many=False)

    async def delete_many(self, query: dict[str, Any]):
        return await self._pg_delete(query, many=True)

    async def create_index(self, keys: Any, **kwargs: Any):
        # Postgres indexes are managed at schema/model level.
        return None


class RuntimeDatabase:
    def __init__(self):
        self._collections: dict[str, RuntimeCollection] = {}

    def _get_collection(self, name: str) -> RuntimeCollection:
        if name not in self._collections:
            self._collections[name] = RuntimeCollection(name)
        return self._collections[name]

    def __getattr__(self, item: str) -> RuntimeCollection:
        if item.startswith("_"):
            raise AttributeError(item)
        return self._get_collection(item)

    def __getitem__(self, item: str) -> RuntimeCollection:
        return self._get_collection(item)

    async def command(self, *args: Any, **kwargs: Any):
        command_name = str(args[0]).lower() if args else ""

        if command_name == "ping":
            await check_postgres_health()
            return {"ok": 1}

        raise RuntimeError(f"Unsupported database command in postgres runtime: {command_name}")

    async def list_collection_names(self) -> list[str]:
        return sorted(set(MIGRATED_COLLECTIONS) | set(self._collections.keys()))

    async def create_collection(self, name: str, *args: Any, **kwargs: Any):
        return self._get_collection(name)


db = RuntimeDatabase()
