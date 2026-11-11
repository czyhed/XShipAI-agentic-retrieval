-- The formal table uses retrieval_key because chunk_id is not globally unique.
CREATE INDEX CONCURRENTLY IF NOT EXISTS vector_records_v2_d1024_bm25_idx
ON rag.vector_records_v2_d1024
USING bm25 (retrieval_key, final_text)
WITH (key_field = 'retrieval_key');
