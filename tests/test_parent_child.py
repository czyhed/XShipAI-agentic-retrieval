import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rag_service.backend import PgVectorAdapter


class ParentChildRetrievalTests(unittest.TestCase):
    def test_child_vector_hit_returns_parent_context_and_provenance(self):
        settings = SimpleNamespace(pg_schema="rag", pg_vector_table="vector_records_v2_d2",
            embedding_dimensions=2, pg_embedding_profile_id="test", pg_host="host", pg_user="user",
            pg_password="fixture", pg_database="db", pg_port=5432, pg_sslmode="disable", pg_connect_timeout_seconds=1)
        rows = [{"chunk_id": "child-1", "document_id": "doc", "chunk_type": "text", "final_text": "Capacity 500 ml.",
            "source_segments": [{"blockId": "capacity"}], "score": 0.9,
            "content_payload": {"parentChunk": {"id": "parent-1", "text": "Capacity 500 ml. Wash by hand.",
                "sourceSegments": [{"blockId": "capacity"}, {"blockId": "washing"}]}}}]
        with patch("rag_service.backend.connect") as connect:
            connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = rows
            results = PgVectorAdapter(settings).dense_search([1, 0], 5)
        record, score = results[0]
        self.assertEqual(record.text, "Capacity 500 ml. Wash by hand.")
        self.assertEqual(record.chunkId, "child-1")
        self.assertEqual(record.provenance.sourceBlockIds, ["capacity", "washing"])
        self.assertEqual(score, 0.9)
        self.assertEqual(record.parentChunkId, "parent-1")
        self.assertEqual(record.matchedText, "Capacity 500 ml.")


if __name__ == "__main__":
    unittest.main()
