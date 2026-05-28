BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE INDEX IF NOT EXISTS idx_context_memories_lookup
    ON context_memories(company_id, entity_id, memory_type, updated_at DESC, created_at DESC);

WITH ranked_embeddings AS (
    SELECT
        ctid,
        ROW_NUMBER() OVER (
            PARTITION BY company_id, source_type, source_id, chunk_index
            ORDER BY created_at DESC, id DESC
        ) AS row_num
    FROM embeddings
)
DELETE FROM embeddings
WHERE ctid IN (
    SELECT ctid
    FROM ranked_embeddings
    WHERE row_num > 1
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_embeddings_company_source_chunk
    ON embeddings(company_id, source_type, source_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_embeddings_vector_cosine
    ON embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

COMMIT;

ANALYZE context_memories;
ANALYZE embeddings;
