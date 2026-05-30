"""embedding vector uniqueness indexes and HNSW upgrade

Extracts runtime DDL from services/db_helpers.py::ensure_embedding_vector_optimizations
into a proper Alembic migration. Also upgrades the vector index from IVFFlat
to HNSW for better recall at lower query cost.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-05-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f5a6b7c8d9e0'
down_revision: Union[str, Sequence[str], None] = 'e4f5a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove duplicate embeddings before creating the unique constraint
    op.execute("""
        DELETE FROM embeddings e1
        USING embeddings e2
        WHERE e1.id > e2.id
          AND e1.company_id = e2.company_id
          AND e1.source_type = e2.source_type
          AND e1.source_id = e2.source_id
          AND e1.chunk_index = e2.chunk_index
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_embeddings_company_source_chunk
        ON embeddings(company_id, source_type, source_id, chunk_index)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_embeddings_company_source_lookup
        ON embeddings(company_id, source_type, source_id)
    """)

    # HNSW index for cosine similarity — better recall than IVFFlat, no retraining needed.
    # Conditional: only if pgvector extension is available.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                -- Drop old IVFFlat index if present
                DROP INDEX IF EXISTS idx_embeddings_vector_cosine;
                -- Create HNSW index with cosine operator class
                CREATE INDEX IF NOT EXISTS idx_embeddings_vector_hnsw
                ON embeddings USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64);
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_embeddings_company_source_chunk")
    op.execute("DROP INDEX IF EXISTS idx_embeddings_company_source_lookup")
    op.execute("DROP INDEX IF EXISTS idx_embeddings_vector_hnsw")
