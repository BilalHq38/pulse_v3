import pytest

from services.ai_service import rag


@pytest.mark.asyncio
async def test_product_catalog_query_does_not_require_missing_optional_columns(monkeypatch):
    async def fake_cache_get(_key):
        return None

    monkeypatch.setattr(rag._CATALOG_CACHE, "get_json", fake_cache_get)
    monkeypatch.setattr(rag, "_COMPANY_PRODUCTS_COLUMNS", None)

    queries = []

    class FakeDB:
        async def fetch(self, query, *args):
            queries.append(query)
            if "information_schema.columns" in query:
                return [
                    {"column_name": column}
                    for column in (
                        "id",
                        "company_id",
                        "name",
                        "product_title",
                        "description",
                        "category",
                        "product_type",
                        "price",
                        "price_currency",
                        "status",
                        "created_at",
                        "updated_at",
                    )
                ]
            assert "NULL::jsonb AS tags" in query
            assert "''::text AS sku" in query
            assert "0::integer AS stock_quantity" in query
            return []

    assert await rag._load_product_catalog(FakeDB(), "company-1") == []
    assert any("information_schema.columns" in query for query in queries)
