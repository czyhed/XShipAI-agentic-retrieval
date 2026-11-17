import inspect
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from rag_service.backend import PgVectorAdapter, SchemaMissingError
from rag_service.answering import OpenAICompatibleAnswerGenerator
from rag_service.integrations import JudgeAdapter, LangfuseAdapter, RagasAdapter, rerank
from rag_service.models import Candidate, Provenance, RetrieveResponse
import rag_service.main as main_module
from rag_service.main import app


client = TestClient(app)


def fake_retrieval() -> RetrieveResponse:
    return RetrieveResponse(
        requestId="run-1",
        query="USB-C",
        rewrittenQueries=["USB-C"],
        profileSnapshot={"id": "hybrid", "rewriteMode": "none", "retrievers": ["dense", "bm25"], "rerank": False, "overrides": {}},
        routeCounts={"dense": 1, "bm25": 1},
        candidateCount=1,
        deduplicationRate=0.5,
        results=[Candidate(chunkId="chunk-1", contentType="text", text="支持 USB-C 充电。", rank=1, finalScore=0.9, stageScores={"dense": 0.8, "bm25": 4.2, "rrf": 0.03}, provenance=Provenance(documentId="doc-1", documentVersion="v1", sourceBlockIds=["block-1"], page=1))],
        statuses={"retrieval": "ok", "dense": "ok", "bm25": "ok", "rerank": "skipped", "ragas": "skipped", "langfuse": "skipped"},
        timingsMs={"total": 12.0, "rerank": 0.0},
        indexVersion="pgvector:rag.vector_records_v2_d1024:qwen3.7-text-embedding",
        modelVersion={"embedding": "qwen3.7-text-embedding", "rewrite": "none", "rerank": "none"},
        stages=[
            {"name": "query_rewrite", "status": "ok", "latencyMs": 0.0},
            {"name": "dense_retrieval", "status": "ok", "latencyMs": 1.0},
            {"name": "bm25_retrieval", "status": "ok", "latencyMs": 1.0},
            {"name": "fusion", "status": "ok", "latencyMs": 0.0},
            {"name": "rerank", "status": "skipped", "latencyMs": 0.0},
            {"name": "retrieval", "status": "ok", "latencyMs": 12.0},
        ],
    )


def require_real_pg() -> dict:
    health = client.get("/healthz").json()
    assert health.get("backend") == "pgvector", health
    assert health.get("available") is True, health
    assert health.get("connectionAvailable") is True, health
    assert health.get("schemaReady") is True, health
    assert health.get("dataStatus") in {"ready", "empty_corpus"}, health
    return health


def test_real_chunk_lab_pgvector_is_required():
    require_real_pg()


def test_health_and_openapi():
    require_real_pg()
    schema = client.get("/openapi.json").json()
    assert "/v1/retrieve" in schema["paths"]
    assert "/v1/answers" in schema["paths"]
    assert "/v1/evaluations/runs" in schema["paths"]
    request_schema = schema["components"]["schemas"]["RetrieveRequest"]["properties"]
    assert "rewriteMode" in request_schema


def test_openapi_exposes_stage_and_judge_contracts():
    schema = client.get("/openapi.json").json()
    assert "stages" in schema["components"]["schemas"]["RetrieveResponse"]["properties"]
    assert "judge" in schema["components"]["schemas"]["AnswerResponse"]["properties"]


def test_public_contract_is_global_and_has_no_validation_tasks():
    schema = client.get("/openapi.json").json()
    assert not any(path.startswith("/v1/validation-tasks") for path in schema["paths"])
    properties = schema["components"]["schemas"]["RetrieveRequest"]["properties"]
    assert {"validationTaskId", "targetId", "targetSpuId"}.isdisjoint(properties)
    for legacy in [
        {"validationTaskId": "vt-1"},
        {"targetId": "target-1"},
        {"targetSpuId": "spu-1"},
    ]:
        response = client.post("/v1/retrieve", json={"query": "USB-C", **legacy})
        assert response.status_code == 422


def test_answer_provider_failure_never_returns_retrieved_text_as_answer(monkeypatch):
    monkeypatch.setattr(main_module.pipeline, "invoke", lambda request: fake_retrieval())

    class FailedGenerator:
        def generate(self, query, context_pack, profile):
            return {"status": "failed", "reason": "answer provider unavailable"}

    monkeypatch.setattr(main_module, "answer_generator", FailedGenerator(), raising=False)
    response = client.post("/v1/answers", json={"query": "USB-C", "profileId": "hybrid"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["answer"] is None
    assert payload["citations"] == []
    assert payload["contextPack"][0]["text"] == "支持 USB-C 充电。"
    assert payload["providers"]["answer"] == "failed"
    assert "answer provider unavailable" in payload["reasons"]
    assert {stage["name"] for stage in payload["stages"]} >= {"query_rewrite", "fusion", "context_assembly", "answer_generation", "citation_validation"}


def test_answer_returns_failed_when_retrieval_source_fails(monkeypatch):
    failed = fake_retrieval().model_copy(update={"results": [], "candidateCount": 0, "statuses": {"retrieval": "failed", "dense": "failed", "rerank": "skipped", "ragas": "skipped", "langfuse": "skipped"}})
    monkeypatch.setattr(main_module.pipeline, "invoke", lambda request: failed)
    response = client.post("/v1/answers", json={"query": "USB-C", "profileId": "hybrid"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["answer"] is None
    assert "retrieval" in " ".join(payload["reasons"]).lower()


def test_evaluation_runs_are_server_registered_and_pollable(monkeypatch):
    monkeypatch.setattr(main_module.pipeline, "invoke", lambda request: fake_retrieval())
    monkeypatch.setattr(main_module.pipeline.backend, "health", lambda: {"available": True})
    datasets = client.get("/v1/evaluation-datasets")
    assert datasets.status_code == 200
    assert any(item["id"] == "fake-eval-v1" for item in datasets.json()["datasets"])

    created = client.post("/v1/evaluations/runs", json={"datasetId": "fake-eval-v1", "profileId": "hybrid", "topK": 3, "ragasEnabled": False})
    assert created.status_code == 202
    payload = created.json()
    assert payload["status"] in {"running", "completed"}
    assert payload["providers"]["ragas"] == "skipped"

    for _ in range(50):
        current = client.get(f"/v1/evaluations/runs/{payload['runId']}")
        assert current.status_code == 200
        if current.json()["status"] == "completed":
            break
        time.sleep(0.01)
    result = current.json()
    assert result["status"] == "completed"
    assert {"recallAtK", "precisionAtK", "mrr", "ndcgAtK"} <= set(result["metrics"])

    ragas_created = client.post("/v1/evaluations/runs", json={"datasetId": "fake-eval-v1", "profileId": "hybrid", "topK": 3, "ragasEnabled": True})
    assert ragas_created.status_code == 202
    ragas_run_id = ragas_created.json()["runId"]
    for _ in range(50):
        ragas_current = client.get(f"/v1/evaluations/runs/{ragas_run_id}")
        if ragas_current.json()["status"] == "completed":
            break
        time.sleep(0.01)
    assert ragas_current.json()["providers"]["ragas"] == "skipped"


def test_all_profiles_use_real_database():
    require_real_pg()
    for profile in ["dense-only", "bm25-only", "hybrid", "hybrid-rerank"]:
        response = client.post("/v1/retrieve", json={"query": "USB-C", "profileId": profile, "topK": 3})
        assert response.status_code == 200
        payload = response.json()
        assert payload["indexVersion"].startswith("pgvector:")


def test_global_retrieval_is_cross_document():
    require_real_pg()
    response = client.post("/v1/retrieve", json={"query": "产品", "profileId": "dense-only", "topK": 100})
    assert response.status_code == 200
    documents = {item["provenance"]["documentId"] for item in response.json()["results"]}
    assert len(documents) >= 2


def test_answer_and_evaluation_use_real_pgvector():
    require_real_pg()
    answer = client.post("/v1/answers", json={"query": "产品", "profileId": "dense-only", "topK": 3})
    assert answer.status_code == 200
    assert answer.json()["retrieval"]["indexVersion"].startswith("pgvector:")
    evaluation = client.post("/v1/evaluations/runs", json={"datasetId": "fake-eval-v1", "profileId": "dense-only", "topK": 3})
    assert evaluation.status_code == 202
    run_id = evaluation.json()["runId"]
    for _ in range(50):
        status = client.get(f"/v1/evaluations/runs/{run_id}")
        assert status.status_code == 200
        if status.json()["status"] == "completed":
            break
        time.sleep(0.01)
    assert status.json()["status"] == "completed"


def test_removed_snapshot_and_validation_contracts_are_absent():
    schema = client.get("/openapi.json").json()
    assert not any(path.startswith("/v1/validation-tasks") for path in schema["paths"])
    assert "ValidationTaskCreateRequest" not in schema["components"]["schemas"]

    ui = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert "fixture" not in ui.lower()

    contract_paths = [
        Path(__file__).resolve().parents[1] / "docs" / "rag-retrieval-component-v1-spec.md",
        Path(__file__).resolve().parents[1] / "docs" / "rag-retrieval-component-prd-v1.md",
        Path(__file__).resolve().parents[1] / ".scratch" / "rag-retrieval-component-v1" / "issues" / "06-chunk-lab-validation-entry.md",
        Path(__file__).resolve().parents[1] / ".scratch" / "rag-retrieval-component-v1" / "issues" / "07-openapi-sdk-cross-service-e2e.md",
    ]
    contracts = "\n".join(path.read_text(encoding="utf-8") for path in contract_paths)
    for removed_contract in ["索引快照", "验证任务导入", "快照推送", "验证任务推送", "注册稳定上下文", "创建/推送失败"]:
        assert removed_contract not in contracts
    assert "rag.vector_records_v2_d1024" in contracts

    env_example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    config_source = (Path(__file__).resolve().parents[1] / "rag_service" / "config.py").read_text(encoding="utf-8")
    assert "RAG_PG_EMBEDDING_PROFILE_ID=qwen3.7-text-embedding" in env_example
    assert "RAG_PG_EMBEDDING_PROFILE_ID=default" not in env_example
    assert "rag_database" in config_source
    assert 'default="agentic_retrieval"' not in config_source
    assert "embedding_vectors_1024" not in config_source


def test_generated_sdk_exposes_typed_response_contracts():
    sdk = (Path(__file__).resolve().parents[1] / "sdk" / "index.ts").read_text(encoding="utf-8")
    openapi_types = (Path(__file__).resolve().parents[1] / "sdk" / "openapi.d.ts").read_text(encoding="utf-8")
    assert "export type RetrieveResponse = components['schemas']['RetrieveResponse'];" in sdk
    assert "export type Candidate = components['schemas']['Candidate'];" in sdk
    assert "export type AnswerResponse = components['schemas']['AnswerResponse'];" in sdk
    assert "Promise<Record<string, unknown>>" not in sdk
    assert '"/v1/retrieve"' in openapi_types
    assert '"/v1/evaluations/runs/{run_id}"' in openapi_types
    assert "validation-tasks" not in openapi_types


def test_pgvector_scope_sql_contains_required_filters():
    settings = SimpleNamespace(pg_schema="rag", pg_vector_table="vector_records_v2_d1024", embedding_dimensions=1024, pg_embedding_profile_id="profile", pg_host="h", pg_user="u", pg_password="p", pg_database="d", pg_port=5432, pg_sslmode="prefer", pg_connect_timeout_seconds=1, pg_bm25_index="bm25", pg_bm25_key_field="chunk_id")
    adapter = PgVectorAdapter(settings)
    captured = {}
    adapter._query = lambda sql, params: captured.update(sql=sql, params=params) or []
    adapter.dense_search([0.0] * 1024, 3)
    sql = captured["sql"]
    for fragment in ["r.deleted_at IS NULL", "r.schema_version = '2.0'", "d.deleted_at IS NULL", "r.embedding_profile_id = %s", "r.vector_dimensions = %s"]:
        assert fragment in sql
    assert "vector_currents" not in sql
    assert "vector_snapshots" not in sql
    assert len(captured["params"]) == sql.count("%s")
    assert str(captured["params"][0]).startswith("[")


def test_pgvector_bm25_has_no_text_scan_fallback():
    source = inspect.getsource(PgVectorAdapter.lexical_search)
    assert "ILIKE" not in source
    assert "bm25_scores" not in source
    assert "|||" in source
    assert "paradedb.score" in source


def test_pgvector_bm25_missing_index_is_explicit_failure():
    settings = SimpleNamespace(pg_schema="rag", pg_vector_table="vector_records_v2_d1024", embedding_dimensions=1024, pg_embedding_profile_id="profile", pg_host="h", pg_user="u", pg_password="p", pg_database="d", pg_port=5432, pg_sslmode="prefer", pg_connect_timeout_seconds=1, pg_bm25_index="bm25", pg_bm25_key_field="chunk_id")
    adapter = PgVectorAdapter(settings)
    adapter._query = lambda sql, params: [{"index_exists": False, "key_unique": True}]
    with pytest.raises(RuntimeError, match="BM25 index unavailable"):
        adapter.lexical_search("query", 3)


def test_optional_observability_adapters_have_explicit_statuses():
    disabled = SimpleNamespace(ragas_url=None, ragas_api_key=None, langfuse_host=None, langfuse_public_key=None, langfuse_secret_key=None)
    assert RagasAdapter(disabled).evaluate("q", ["ctx"], "answer", "ref")["status"] == "skipped"
    trace = LangfuseAdapter(disabled).start_trace("q", {"id": "hybrid"})
    assert trace["status"] == "skipped"


def test_optional_observability_adapter_failures_are_not_success(monkeypatch):
    settings = SimpleNamespace(ragas_url="http://ragas.invalid", ragas_api_key="secret", langfuse_host="http://langfuse.invalid", langfuse_public_key="public", langfuse_secret_key="secret", ragas_timeout_ms=1, langfuse_timeout_ms=1)
    monkeypatch.setattr("rag_service.integrations.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
    assert RagasAdapter(settings).evaluate("q", ["ctx"], "answer", None)["status"] == "failed"
    trace = LangfuseAdapter(settings).start_trace("q", {"id": "hybrid"})
    assert trace["status"] == "failed"


def test_rerank_uses_dashscope_response_indexes(monkeypatch):
    settings = SimpleNamespace(
        rerank_url="https://dashscope.example/api/v1/services/rerank/text-rerank/text-rerank",
        rerank_api_key="secret",
        rerank_model_version="qwen3-vl-rerank",
        rerank_timeout_ms=1000,
    )
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self):
            return b'{"output":{"results":[{"index":1,"relevance_score":0.8},{"index":0,"relevance_score":0.2}]}}'

    def fake_urlopen(request, **kwargs):
        captured["body"] = request.data
        return Response()

    monkeypatch.setattr("rag_service.integrations.urlopen", fake_urlopen)
    scores, status, latency_ms = rerank(settings, "query", ["first", "second"])

    assert status == "sent"
    assert scores == [0.2, 0.8]
    assert latency_ms >= 0.0
    body = __import__("json").loads(captured["body"])
    assert body == {
        "model": "qwen3-vl-rerank",
        "input": {"query": "query", "documents": ["first", "second"]},
        "parameters": {"return_documents": True, "top_n": 2},
    }


def test_answer_prompt_lists_allowed_citation_ids(monkeypatch):
    settings = SimpleNamespace(
        answer_base_url="https://context.example",
        answer_api_key="secret",
        answer_model="qwen3.7-max-2026-06-08",
        answer_timeout_ms=1000,
    )
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self):
            return '{"choices":[{"message":{"content":"{\\"answer\\":\\"支持调节\\",\\"citationIds\\":[\\"S1\\"],\\"claims\\":[],\\"abstain\\":false}"}}]}'.encode("utf-8")

    def fake_urlopen(request, **kwargs):
        captured["body"] = __import__("json").loads(request.data)
        return Response()

    monkeypatch.setattr("rag_service.answering.urlopen", fake_urlopen)
    result = OpenAICompatibleAnswerGenerator(settings).generate(
        "鼠标DPI怎么调",
        [{"citationId": "S1", "chunkId": "chunk-1", "text": "按DPI键调节", "contentType": "text", "rank": 1}],
        "qwen3.7-max-2026-06-08",
    )

    assert result["status"] == "sent"
    prompt = __import__("json").loads(captured["body"]["messages"][1]["content"])
    assert prompt["allowedCitationIds"] == ["S1"]
    assert "copied verbatim from allowedCitationIds" in prompt["instructions"]


def test_judge_is_shadow_only_and_explicitly_skipped_when_unconfigured(monkeypatch):
    monkeypatch.setattr(main_module.pipeline, "invoke", lambda request: fake_retrieval())
    class FakeGenerator:
        def generate(self, query, context_pack, profile):
            return {"status": "sent", "answer": "支持 USB-C 充电。", "citationIds": ["S1"], "abstain": False}
    monkeypatch.setattr(main_module, "answer_generator", FakeGenerator(), raising=False)
    response = client.post("/v1/answers", json={"query": "USB-C", "profileId": "hybrid", "judge": {"enabled": True}})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "answered"
    assert payload["providers"]["judge"] == "skipped"
    assert payload["judge"]["status"] == "skipped"


def test_answer_reports_measured_generation_latency(monkeypatch):
    monkeypatch.setattr(main_module.pipeline, "invoke", lambda request: fake_retrieval())

    class DelayedGenerator:
        def generate(self, query, context_pack, profile):
            time.sleep(0.002)
            return {"status": "sent", "answer": "支持 USB-C 充电。", "citationIds": ["S1"], "abstain": False}

    monkeypatch.setattr(main_module, "answer_generator", DelayedGenerator(), raising=False)
    payload = client.post("/v1/answers", json={"query": "USB-C", "profileId": "hybrid"}).json()
    stages = {stage["name"]: stage["latencyMs"] for stage in payload["stages"]}

    assert stages["context_assembly"] > 0.0
    assert stages["evidence_gate"] > 0.0
    assert stages["answer_generation"] >= 1.0
    assert stages["citation_validation"] > 0.0


def test_judge_adapter_failure_is_explicit(monkeypatch):
    settings = SimpleNamespace(judge_url="http://judge.invalid", judge_api_key="secret", judge_model_version="judge-v1", judge_timeout_ms=1)
    monkeypatch.setattr("rag_service.integrations.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
    result = JudgeAdapter(settings).evaluate("q", [{"citationId": "S1", "text": "ctx"}], "answer", None, {"status": "passed"})
    assert result["status"] == "failed"


def test_online_retrieval_does_not_run_ragas(monkeypatch):
    monkeypatch.setattr(main_module.pipeline, "invoke", lambda request: fake_retrieval())
    response = client.post("/v1/retrieve", json={"query": "USB-C", "profileId": "hybrid"})
    assert response.status_code == 200
    assert response.json()["statuses"]["ragas"] == "skipped"


def test_health_classifies_pg_auth_failure_without_leaking_error(monkeypatch):
    adapter = PgVectorAdapter(SimpleNamespace(pg_schema="rag", pg_vector_table="vector_records_v2_d1024", embedding_dimensions=1024, pg_embedding_profile_id="profile", pg_host="h", pg_user="u", pg_password="p", pg_database="d", pg_port=5432, pg_sslmode="prefer", pg_connect_timeout_seconds=1, pg_bm25_index="bm25", pg_bm25_key_field="chunk_id"))
    class AuthError(Exception):
        sqlstate = "28P01"
    def failed_query(sql, params):
        try:
            raise AuthError("secret password detail")
        except AuthError as cause:
            raise RuntimeError("PGVector backend unavailable") from cause
    monkeypatch.setattr(adapter, "_query", failed_query)
    result = adapter.health()
    assert result["available"] is False
    assert result["reason"] == "credentials_invalid"
    assert "secret" not in str(result).lower()


def pg_settings() -> SimpleNamespace:
    return SimpleNamespace(pg_schema="rag", pg_vector_table="vector_records_v2_d1024", embedding_dimensions=1024, pg_embedding_profile_id="profile", pg_host="h", pg_user="u", pg_password="p", pg_database="d", pg_port=5432, pg_sslmode="prefer", pg_connect_timeout_seconds=1, pg_bm25_index="bm25", pg_bm25_key_field="chunk_id")


def test_health_separates_connection_from_schema(monkeypatch):
    adapter = PgVectorAdapter(pg_settings())
    monkeypatch.setattr(adapter, "_query", lambda sql, params: [{"table_exists": False, "documents_exists": False, "columns_ok": False, "hnsw_ok": False}])
    result = adapter.health()
    assert result["available"] is False
    assert result["connectionAvailable"] is True
    assert result["schemaReady"] is False
    assert result["dataStatus"] == "schema_missing"
    assert result["corpusCount"] == 0


def test_health_reports_empty_corpus_as_available(monkeypatch):
    adapter = PgVectorAdapter(pg_settings())
    def query(sql, params):
        if "to_regclass" in sql:
            return [{"table_exists": True, "documents_exists": True, "columns_ok": True, "hnsw_ok": True}]
        return [{"count": 0}]
    monkeypatch.setattr(adapter, "_query", query)
    result = adapter.health()
    assert result["available"] is True
    assert result["connectionAvailable"] is True
    assert result["schemaReady"] is True
    assert result["corpusCount"] == 0
    assert result["dataStatus"] == "empty_corpus"


def test_query_maps_undefined_table_to_schema_missing(monkeypatch):
    adapter = PgVectorAdapter(pg_settings())
    class UndefinedTable(Exception):
        sqlstate = "42P01"
    def failed_connect():
        raise UndefinedTable("relation does not exist")
    monkeypatch.setattr(adapter, "_connect", failed_connect)
    with pytest.raises(SchemaMissingError):
        adapter._query("SELECT 1", ())


def test_retrieve_reports_schema_missing_without_error(monkeypatch):
    def missing(*args, **kwargs):
        raise SchemaMissingError("PGVector schema is not initialized")
    monkeypatch.setattr(main_module.pipeline.backend, "dense_search", missing)
    monkeypatch.setattr(main_module.pipeline.backend, "lexical_search", missing)
    response = client.post("/v1/retrieve", json={"query": "USB-C", "profileId": "hybrid"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["results"] == []
    assert payload["statuses"]["source"] == "schema_missing"
    assert payload["statuses"]["retrieval"] == "empty"


def test_answer_reports_schema_missing(monkeypatch):
    def missing(*args, **kwargs):
        raise SchemaMissingError("PGVector schema is not initialized")
    monkeypatch.setattr(main_module.pipeline.backend, "dense_search", missing)
    monkeypatch.setattr(main_module.pipeline.backend, "lexical_search", missing)
    response = client.post("/v1/answers", json={"query": "USB-C", "profileId": "hybrid"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "failed"
    assert "retrieval source schema missing" in payload["reasons"]


def test_langfuse_observations_are_sent_and_failures_degrade(monkeypatch):
    settings = SimpleNamespace(ragas_url=None, ragas_api_key=None, langfuse_host="http://langfuse.example", langfuse_public_key="public", langfuse_secret_key="secret", ragas_timeout_ms=1, langfuse_timeout_ms=1)
    calls = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False

    monkeypatch.setattr("rag_service.integrations.urlopen", lambda request, **kwargs: calls.append(request) or Response())
    adapter = LangfuseAdapter(settings)
    trace = adapter.start_trace("q", {"id": "hybrid"})
    adapter.observe(trace, "fusion", {"candidateCount": 1})
    assert trace["status"] == "sent"
    assert trace["observations"] == ["fusion"]
    assert len(calls) == 2

    monkeypatch.setattr("rag_service.integrations.urlopen", lambda request, **kwargs: calls.append(request) or Response())
    degraded = adapter.start_trace("q", {"id": "hybrid"})
    monkeypatch.setattr("rag_service.integrations.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
    adapter.observe(degraded, "fusion", {})
    assert degraded["status"] == "degraded"
