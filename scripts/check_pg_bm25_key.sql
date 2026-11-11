-- Read-only preflight for pg_search key_field selection.
SELECT
  count(*) AS live_rows,
  count(DISTINCT chunk_id) AS distinct_chunk_ids,
  (count(*) = count(DISTINCT chunk_id)) AS chunk_id_is_unique
FROM rag.vector_records_v2_d1024
WHERE deleted_at IS NULL;

-- The migration always provisions retrieval_key; chunk_id is retained only for diagnostics.
