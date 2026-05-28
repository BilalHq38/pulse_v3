-- Vertex AI embedding cutover for Session 1.
-- This intentionally clears every embedding row, across every source_type, so
-- product, company, knowledge, and conversation-context vectors are not mixed
-- across incompatible embedding models.

ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS embedding_provider TEXT NOT NULL DEFAULT '';
ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS embedding_model TEXT NOT NULL DEFAULT '';
ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER NOT NULL DEFAULT 768;

DROP INDEX IF EXISTS uq_embeddings_company_source_chunk;
CREATE UNIQUE INDEX IF NOT EXISTS uq_embeddings_company_source_chunk
    ON embeddings(company_id, source_type, source_id, chunk_index, embedding_provider, embedding_model, embedding_dimension);

CREATE INDEX IF NOT EXISTS idx_embeddings_model
    ON embeddings(company_id, embedding_provider, embedding_model, embedding_dimension);

TRUNCATE TABLE embeddings;
