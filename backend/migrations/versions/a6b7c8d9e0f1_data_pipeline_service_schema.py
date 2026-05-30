"""Move data pipeline tables to data_pipeline_service schema

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-05-30

Moves raw_events, raw_messages, raw_leads, and analytics_events from the
analytics_service schema into data_pipeline_service, reapplies RLS, and
re-creates indexes and grants. Downgrades restore the original state.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("raw_events", "raw_messages", "raw_leads", "analytics_events")
_OLD_SCHEMA = "analytics_service"
_NEW_SCHEMA = "data_pipeline_service"
_APP_ROLE = "pulse_app"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_NEW_SCHEMA}")

    for table in _TABLES:
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = '{_OLD_SCHEMA}'
                    AND table_name = '{table}'
                ) THEN
                    ALTER TABLE {_OLD_SCHEMA}.{table}
                        SET SCHEMA {_NEW_SCHEMA};
                END IF;
            END
            $$;
        """)

    # Re-enable RLS on moved tables
    for table in _TABLES:
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = '{_NEW_SCHEMA}'
                    AND table_name = '{table}'
                ) THEN
                    ALTER TABLE {_NEW_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY;
                    ALTER TABLE {_NEW_SCHEMA}.{table} FORCE ROW LEVEL SECURITY;
                END IF;
            END
            $$;
        """)

    # Recreate tenant-isolation RLS policies
    for table in _TABLES:
        policy = f"p_{table}_company"
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = '{_NEW_SCHEMA}'
                    AND table_name = '{table}'
                ) THEN
                    DROP POLICY IF EXISTS "{policy}" ON {_NEW_SCHEMA}.{table};
                    CREATE POLICY "{policy}" ON {_NEW_SCHEMA}.{table}
                    USING (
                        company_id::text = current_setting('app.current_company', true)
                        OR current_setting('app.platform_admin_mode', true) = 'on'
                    )
                    WITH CHECK (
                        company_id::text = current_setting('app.current_company', true)
                    );
                END IF;
            END
            $$;
        """)

    # Grant schema-level permissions to the application role
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                GRANT USAGE ON SCHEMA {_NEW_SCHEMA} TO {_APP_ROLE};
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON ALL TABLES IN SCHEMA {_NEW_SCHEMA} TO {_APP_ROLE};
                ALTER DEFAULT PRIVILEGES IN SCHEMA {_NEW_SCHEMA}
                    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP_ROLE};
            END IF;
        END
        $$;
    """)

    # Drop old schema only when it has no remaining tables
    op.execute(f"""
        DO $$
        DECLARE
            tbl_count integer;
        BEGIN
            SELECT COUNT(*) INTO tbl_count
            FROM information_schema.tables
            WHERE table_schema = '{_OLD_SCHEMA}';
            IF tbl_count = 0 THEN
                DROP SCHEMA IF EXISTS {_OLD_SCHEMA};
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_OLD_SCHEMA}")

    for table in _TABLES:
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = '{_NEW_SCHEMA}'
                    AND table_name = '{table}'
                ) THEN
                    ALTER TABLE {_NEW_SCHEMA}.{table}
                        SET SCHEMA {_OLD_SCHEMA};
                END IF;
            END
            $$;
        """)

    # Re-enable RLS on restored tables
    for table in _TABLES:
        op.execute(f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = '{_OLD_SCHEMA}'
                    AND table_name = '{table}'
                ) THEN
                    ALTER TABLE {_OLD_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY;
                    ALTER TABLE {_OLD_SCHEMA}.{table} FORCE ROW LEVEL SECURITY;
                END IF;
            END
            $$;
        """)

    op.execute(f"""
        DO $$
        DECLARE
            tbl_count integer;
        BEGIN
            SELECT COUNT(*) INTO tbl_count
            FROM information_schema.tables
            WHERE table_schema = '{_NEW_SCHEMA}';
            IF tbl_count = 0 THEN
                DROP SCHEMA IF EXISTS {_NEW_SCHEMA};
            END IF;
        END
        $$;
    """)
