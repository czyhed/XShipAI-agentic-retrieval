import os

import pytest

from rag_service.config import settings
from rag_service.main import app
from fastapi.testclient import TestClient


@pytest.mark.skipif(os.getenv("RAG_RUN_REAL_SMOKE") != "1", reason="set RAG_RUN_REAL_SMOKE=1 to run the real embedding smoke")
def test_real_embedding_smoke_against_formal_pgvector():
    if settings.embedding_provider != "openai-compatible" or not settings.embedding_base_url or not settings.embedding_api_key:
        pytest.skip("real embedding credentials are not configured")
    health = TestClient(app).get("/healthz").json()
    assert health.get("available") is True, "formal PGVector source is unavailable"
    response = TestClient(app).post("/v1/retrieve", json={"query": "USB-C 快充", "profileId": "dense-only", "topK": 3})
    assert response.status_code == 200
    payload = response.json()
    assert payload["statuses"]["retrieval"] in {"ok", "degraded"}
    assert payload["modelVersion"]["embedding"] == settings.embedding_model_version
    assert payload["indexVersion"].startswith("pgvector:")
