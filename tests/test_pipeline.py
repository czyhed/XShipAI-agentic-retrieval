import time
from types import SimpleNamespace

from rag_service.models import IndexRecord, Provenance, RetrieveRequest
from rag_service.pipeline import RetrievalPipeline


class RecordingBackend:
    version = "fake-index"

    def __init__(self):
        self.dense_calls = 0
        self.lexical_calls = 0
        self.records = [
            IndexRecord(chunkId="original", contentType="text", text="original", provenance=Provenance()),
            IndexRecord(chunkId="expanded", contentType="text", text="expanded", provenance=Provenance()),
            IndexRecord(chunkId="punctuation", contentType="text", text="punctuation", provenance=Provenance()),
        ]

    def dense_search(self, query_vector, limit):
        record = self.records[min(self.dense_calls, len(self.records) - 1)]
        self.dense_calls += 1
        return [(record, 1.0)]

    def lexical_search(self, query, limit):
        record = self.records[min(self.lexical_calls, len(self.records) - 1)]
        self.lexical_calls += 1
        return [(record, 1.0)]

    def health(self):
        return {"available": True}


def fake_settings():
    return SimpleNamespace(
        embedding_provider="fake",
        embedding_dimensions=8,
        embedding_model_version="fake-embedding",
        rewrite_model_version="fake-rewrite",
        rerank_model_version="fake-rerank",
        langfuse_host=None,
        langfuse_public_key=None,
        langfuse_secret_key=None,
        rerank_url=None,
        rerank_api_key=None,
    )


def test_multi_query_uses_all_rewritten_queries_for_dense_and_bm25():
    backend = RecordingBackend()
    pipeline = RetrievalPipeline(backend, fake_settings())
    response = pipeline.invoke(RetrieveRequest(query="多少瓦?", profileId="hybrid", rewriteMode="multi_query", topK=10))
    assert len(response.rewrittenQueries) == 3
    assert backend.dense_calls == 3
    assert backend.lexical_calls == 3
    assert {item.chunkId for item in response.results} == {"original", "expanded", "punctuation"}


def test_pipeline_reports_measured_stage_latencies(monkeypatch):
    backend = RecordingBackend()
    original_embed_many = RetrievalPipeline._embed_many

    def delayed_embed_many(self, texts):
        time.sleep(0.002)
        return original_embed_many(self, texts)

    original_dense_search = backend.dense_search
    original_lexical_search = backend.lexical_search
    backend.dense_search = lambda query_vector, limit: (time.sleep(0.002), original_dense_search(query_vector, limit))[1]
    backend.lexical_search = lambda query, limit: (time.sleep(0.002), original_lexical_search(query, limit))[1]
    monkeypatch.setattr(RetrievalPipeline, "_embed_many", delayed_embed_many)

    response = RetrievalPipeline(backend, fake_settings()).invoke(
        RetrieveRequest(query="鼠标DPI怎么调", profileId="hybrid", topK=5)
    )
    stages = {stage["name"]: stage["latencyMs"] for stage in response.stages}

    assert stages["dense_retrieval"] >= 3.0
    assert stages["bm25_retrieval"] >= 1.0
    assert stages["fusion"] > 0.0


def test_optional_rerank_failure_degrades_but_keeps_retrieval_results(monkeypatch):
    settings = fake_settings()
    settings.rerank_url = "https://rerank.example"
    settings.rerank_api_key = "secret"
    monkeypatch.setattr("rag_service.pipeline.rerank", lambda *args: (None, "failed", 2.0))

    response = RetrievalPipeline(RecordingBackend(), settings).invoke(
        RetrieveRequest(query="鼠标DPI怎么调", profileId="hybrid-rerank", topK=5)
    )

    assert response.statuses["rerank"] == "failed"
    assert response.statuses["retrieval"] == "degraded"
    assert response.results
