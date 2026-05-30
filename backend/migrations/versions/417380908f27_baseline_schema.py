"""baseline_schema

Baseline migration — schema already applied via sql_schema.sql.
Future schema changes must be added as new migration files.

Revision ID: 417380908f27
Revises:
Create Date: 2026-05-30 06:16:48.419402

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '417380908f27'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Baseline: schema already exists in production.
    # This migration serves as the starting point for future migrations.
    pass


def downgrade() -> None:
    pass
