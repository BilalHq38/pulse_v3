from routers.analytics import _dedupe_summary_items


def test_dedupe_summary_items_keeps_unique_customer_conversation_entries():
    rows = [
        {
            "id": "customer-row-1",
            "entity_type": "customer",
            "customer_id": "cust-1",
            "conversation_id": "conv-1",
            "date": "2026-05-19",
        },
        {
            "id": "customer-row-duplicate",
            "entity_type": "customer",
            "customer_id": "cust-1",
            "conversation_id": "conv-1",
            "date": "2026-05-19",
        },
        {
            "id": "customer-row-2",
            "entity_type": "customer",
            "customer_id": "cust-1",
            "conversation_id": "conv-2",
            "date": "2026-05-19",
        },
    ]

    deduped = _dedupe_summary_items(rows)

    assert [row["id"] for row in deduped] == ["customer-row-1", "customer-row-2"]
