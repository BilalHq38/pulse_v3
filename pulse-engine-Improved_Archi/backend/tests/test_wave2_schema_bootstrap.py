"""Wave 2 — schema bootstrap checks.

These tests guard the SQL schema mirror in ``backend/sql_schema.sql`` against
silent drift from the Wave 2 migration files. They run with no live database:
each test reads ``sql_schema.sql`` as text and asserts that the table, column,
constraint, and index declarations introduced by migrations 016/017/018 are
present. End-to-end DB tests (FK enforcement, RLS round-trip) belong with the
Wave 3+ application code that exercises the tables.
"""

from __future__ import annotations

from pathlib import Path

import pytest


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "sql_schema.sql"
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "sql_migrations"


@pytest.fixture(scope="module")
def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def _assert_all_present(text: str, fragments: list[str]) -> None:
    missing = [f for f in fragments if f not in text]
    assert not missing, f"sql_schema.sql is missing: {missing}"


def test_wave2_migrations_exist_on_disk():
    expected = (
        "016_order_lifecycle.sql",
        "017_conversation_engine.sql",
        "018_followups_and_relationships.sql",
    )
    present = {p.name for p in MIGRATIONS_DIR.iterdir()}
    for name in expected:
        assert name in present, f"missing migration file: {name}"


def test_orders_status_check_includes_shipped_and_delivered(schema_sql: str):
    # Wave 2 extends the lifecycle with shipped/delivered. Both must appear in
    # the orders_status_check definition that ships in sql_schema.sql.
    marker = "ADD CONSTRAINT orders_status_check"
    idx = schema_sql.rfind(marker)
    assert idx != -1, "orders_status_check definition not found in schema"
    block = schema_sql[idx : idx + 600]
    for state in ("shipped", "delivered", "pending", "confirmed", "cancelled"):
        assert state in block, f"orders_status_check missing state: {state}"


def test_order_lifecycle_events_table(schema_sql: str):
    _assert_all_present(
        schema_sql,
        [
            "CREATE TABLE IF NOT EXISTS order_lifecycle_events",
            "from_status  TEXT NOT NULL DEFAULT ''",
            "to_status    TEXT NOT NULL",
            "actor_type   TEXT NOT NULL DEFAULT 'system'",
            "chk_order_lifecycle_actor_type",
            "idx_order_lifecycle_company_order",
            "idx_order_lifecycle_to_status",
            "uq_order_lifecycle_event_idempotent",
            "p_order_lifecycle_events_tenant",
        ],
    )


def test_ai_conversation_turns_table(schema_sql: str):
    _assert_all_present(
        schema_sql,
        [
            "CREATE TABLE IF NOT EXISTS ai_conversation_turns",
            "turn_index      INTEGER NOT NULL",
            "sources_used    JSONB NOT NULL DEFAULT '[]'::jsonb",
            "product_links   JSONB NOT NULL DEFAULT '[]'::jsonb",
            "confidence      NUMERIC(5,4)",
            "mode            TEXT NOT NULL DEFAULT 'reactive'",
            "chk_ai_conversation_turns_mode",
            "idx_ai_turns_session",
            "idx_ai_turns_customer",
            "p_ai_conversation_turns_tenant",
        ],
    )


def test_ai_conversation_turns_archive_table(schema_sql: str):
    _assert_all_present(
        schema_sql,
        [
            "CREATE TABLE IF NOT EXISTS ai_conversation_turns_archive",
            "(LIKE ai_conversation_turns INCLUDING ALL)",
            "p_ai_conversation_turns_archive_tenant",
        ],
    )


def test_ai_conversation_summaries_table(schema_sql: str):
    _assert_all_present(
        schema_sql,
        [
            "CREATE TABLE IF NOT EXISTS ai_conversation_summaries",
            "covers_through_turn INTEGER NOT NULL",
            "CONSTRAINT uq_summary_session UNIQUE (company_id, session_id)",
            "p_ai_conversation_summaries_tenant",
        ],
    )


def test_response_templates_table_and_default_index(schema_sql: str):
    _assert_all_present(
        schema_sql,
        [
            "CREATE TABLE IF NOT EXISTS response_templates",
            "style_prompt TEXT NOT NULL",
            "is_default   BOOLEAN NOT NULL DEFAULT FALSE",
            "CONSTRAINT uq_response_templates_company_name UNIQUE (company_id, name)",
            # Partial unique index — at most one default per company.
            "uq_response_templates_one_default",
            "WHERE is_default",
            "p_response_templates_tenant",
        ],
    )


def test_knowledge_base_gains_summary_columns(schema_sql: str):
    _assert_all_present(
        schema_sql,
        [
            "ADD COLUMN IF NOT EXISTS summary              TEXT NOT NULL DEFAULT ''",
            "ADD COLUMN IF NOT EXISTS summary_generated_at TIMESTAMPTZ",
        ],
    )


def test_ai_followups_table(schema_sql: str):
    _assert_all_present(
        schema_sql,
        [
            "CREATE TABLE IF NOT EXISTS ai_followups",
            "workflow_kind   TEXT NOT NULL",
            "status          TEXT NOT NULL DEFAULT 'scheduled'",
            "scheduled_for   TIMESTAMPTZ NOT NULL",
            "idempotency_key TEXT NOT NULL DEFAULT ''",
            "chk_ai_followups_workflow_kind",
            "chk_ai_followups_status",
            "idx_followups_due",
            "uq_followups_one_active_per_order",
            "uq_followups_idempotency_key",
            "p_ai_followups_tenant",
        ],
    )


def test_customer_feedback_table(schema_sql: str):
    _assert_all_present(
        schema_sql,
        [
            "CREATE TABLE IF NOT EXISTS customer_feedback",
            "sentiment    TEXT NOT NULL DEFAULT ''",
            "rating       INTEGER",
            "chk_customer_feedback_rating",
            "idx_feedback_company_customer",
            "p_customer_feedback_tenant",
        ],
    )


def test_customer_engagement_table(schema_sql: str):
    _assert_all_present(
        schema_sql,
        [
            "CREATE TABLE IF NOT EXISTS customer_engagement",
            "opted_out         BOOLEAN NOT NULL DEFAULT FALSE",
            "last_contacted_at TIMESTAMPTZ",
            "followup_count    INTEGER NOT NULL DEFAULT 0",
            "PRIMARY KEY (company_id, customer_id)",
            "p_customer_engagement_tenant",
        ],
    )


def test_automation_log_table(schema_sql: str):
    _assert_all_present(
        schema_sql,
        [
            "CREATE TABLE IF NOT EXISTS automation_log",
            "workflow_kind TEXT NOT NULL",
            "duration_ms   INTEGER NOT NULL DEFAULT 0",
            "idx_automation_log_company_created_at",
            "p_automation_log_tenant",
        ],
    )


def test_product_relationships_table(schema_sql: str):
    _assert_all_present(
        schema_sql,
        [
            "CREATE TABLE IF NOT EXISTS product_relationships",
            "relation_kind TEXT NOT NULL DEFAULT 'complementary'",
            "weight        NUMERIC(4,2) NOT NULL DEFAULT 0.5",
            "uq_product_relationships",
            "chk_product_relationship_distinct",
            "chk_product_relationship_kind",
            "idx_product_relationships_product",
            "p_product_relationships_tenant",
        ],
    )


def test_wave3b_company_settings_use_engine_column(schema_sql: str):
    """The opt-in flag for the conversation engine must appear in the mirror.

    Wave 6 flipped the default to TRUE — both the original DEFAULT FALSE
    column definition (migration 019) and the DEFAULT TRUE update
    (migration 020) appear in sql_schema.sql so a fresh bootstrap matches
    either incremental migrate path."""
    _assert_all_present(
        schema_sql,
        [
            "ai_use_conversation_engine BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER COLUMN ai_use_conversation_engine SET DEFAULT TRUE",
        ],
    )


def test_wave3b_migration_file_exists():
    assert (MIGRATIONS_DIR / "019_company_settings_use_engine.sql").is_file()


def test_wave6_default_on_migration_file_exists():
    assert (MIGRATIONS_DIR / "020_default_conversation_engine.sql").is_file()


def test_wave2_migrations_match_schema_mirror():
    """Every CREATE TABLE in the Wave 2 migrations must also appear in
    sql_schema.sql so a fresh bootstrap installs the same shape as an
    incremental migrate-up."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    for name in ("016_order_lifecycle.sql", "017_conversation_engine.sql", "018_followups_and_relationships.sql"):
        text = (MIGRATIONS_DIR / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("CREATE TABLE IF NOT EXISTS "):
                table = stripped.split("CREATE TABLE IF NOT EXISTS ", 1)[1].split()[0]
                assert f"CREATE TABLE IF NOT EXISTS {table}" in schema, (
                    f"{table} declared in {name} but missing from sql_schema.sql"
                )
