"""Install the production schema baseline.

Revision ID: 20260530_0001
Revises:
"""
from pathlib import Path

from alembic import op

revision = "20260530_0001"
down_revision = None
branch_labels = None
depends_on = None

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _execute_sql_file(relative_path: str) -> None:
    sql = (_BACKEND_ROOT / relative_path).read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _execute_sql_file("sql_schema.sql")
    _execute_sql_file("shared/db/bootstrap_microservices.sql")


def downgrade() -> None:
    raise RuntimeError("The production baseline is irreversible; restore from backup instead")
