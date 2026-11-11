-- Execute only when check_pg_bm25_key.sql reports chunk_id_is_unique=false.
ALTER TABLE rag.vector_records_v2_d1024
  ADD COLUMN IF NOT EXISTS retrieval_key text;

UPDATE rag.vector_records_v2_d1024
SET retrieval_key = concat(target_id, ':', document_id, ':', target_spu_id, ':', chunk_id)
WHERE retrieval_key IS NULL;

ALTER TABLE rag.vector_records_v2_d1024
  ALTER COLUMN retrieval_key SET NOT NULL;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS vector_records_v2_d1024_retrieval_key_key
  ON rag.vector_records_v2_d1024 (retrieval_key);

CREATE INDEX CONCURRENTLY IF NOT EXISTS vector_records_v2_d1024_bm25_idx
ON rag.vector_records_v2_d1024
USING bm25 (retrieval_key, final_text)
WITH (key_field = 'retrieval_key');
