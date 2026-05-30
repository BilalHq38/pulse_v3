"""move data pipeline tables to data_pipeline_service schema

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: str | Sequence[str] | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_SCHEMA = "analytics_service"
_NEW_SCHEMA = "data_pipeline_service"
_TABLES = (
    "raw_events",
    "raw_messages",
    "raw_leads",
    "analytics_events",
    "lead_metrics",
    "conversation_metrics",
    "sentiment_logs",
)


def _move_table_if_needed(table: str, *, source_schema: str, target_schema: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{source_schema}.{table}') IS NOT NULL
               AND to_regclass('{target_schema}.{table}') IS NULL THEN
                ALTER TABLE {source_schema}.{table} SET SCHEMA {target_schema};
            END IF;
        END $$;
        """
    )


def _apply_rls_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {_NEW_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_NEW_SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS p_{table}_tenant ON {_NEW_SCHEMA}.{table}")
    op.execute(
        f"""
        CREATE POLICY p_{table}_tenant ON {_NEW_SCHEMA}.{table}
        USING (company_id = current_setting('app.current_company', true))
        WITH CHECK (company_id = current_setting('app.current_company', true))
        """
    )


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_NEW_SCHEMA}")
    op.execute(
        f"COMMENT ON SCHEMA {_NEW_SCHEMA} IS "
        "'Raw event ingestion, ETL state, and derived pipeline metrics.'"
    )
    for table in _TABLES:
        _move_table_if_needed(table, source_schema=_OLD_SCHEMA, target_schema=_NEW_SCHEMA)
        op.execute(
            f"""
            DO $$
            BEGIN
                IF to_regclass('{_NEW_SCHEMA}.{table}') IS NULL THEN
                    RAISE EXCEPTION 'missing data pipeline table %.%', '{_NEW_SCHEMA}', '{table}';
                END IF;
            END $$;
            """
        )
        _apply_rls_policy(table)


def downgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_OLD_SCHEMA}")
    for table in reversed(_TABLES):
        _move_table_if_needed(table, source_schema=_NEW_SCHEMA, target_schema=_OLD_SCHEMA)
