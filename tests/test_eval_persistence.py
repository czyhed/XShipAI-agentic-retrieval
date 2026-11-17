import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

import rag_service.main as main_module
from rag_service.eval_store import FileEvaluationRunStore
from rag_service.evaluation import EvaluationRunRegistry
from rag_service.models import EvaluationRequest, EvaluationRun

client = TestClient(main_module.app)


class FakeBackend:
    def health(self) -> dict:
        return {"available": True}


class FakePipeline:
    backend = FakeBackend()

    def invoke(self, request):
        return SimpleNamespace(
            results=[
                SimpleNamespace(
                    chunkId="text-001",
                    text="支持 65W 快充。",
                    contentType="text",
                    rank=1,
                    provenance=SimpleNamespace(sourceBlockIds=["block-1"]),
                )
            ]
        )


def wait_for_status(registry: EvaluationRunRegistry, run_id: str, timeout: float = 5.0) -> EvaluationRun:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = registry.get(run_id)
        assert run is not None
        if run.status != "running":
            return run
        time.sleep(0.01)
    raise AssertionError(f"evaluation run {run_id} did not finish")


def test_run_survives_registry_rebuild(tmp_path):
    store_dir = tmp_path / "runs"
    registry = EvaluationRunRegistry(FakePipeline(), store=FileEvaluationRunStore(store_dir))

    created = registry.create(EvaluationRequest(datasetId="fake-eval-v1", profileId="hybrid", topK=3))
    assert created.status == "running"
    assert created.createdAt.endswith("Z")
    assert (store_dir / f"{created.runId}.json").exists()

    completed = wait_for_status(registry, created.runId)
    assert completed.status == "completed"
    assert {"recallAtK", "precisionAtK", "mrr", "ndcgAtK"} <= set(completed.metrics)

    rebuilt = EvaluationRunRegistry(FakePipeline(), store=FileEvaluationRunStore(store_dir))
    restored = rebuilt.get(created.runId)
    assert restored is not None
    assert restored.status == "completed"
    assert restored.createdAt == created.createdAt
    assert restored.metrics == completed.metrics

    listed = rebuilt.list_runs(10)
    assert [item.runId for item in listed] == [created.runId]
    assert listed[0].createdAt == created.createdAt


def test_list_is_newest_first_and_honours_limit(tmp_path):
    store = FileEvaluationRunStore(tmp_path)
    timestamps = [
        "2026-01-01T00:00:00.000Z",
        "2026-02-01T00:00:00.000Z",
        "2026-03-01T00:00:00.000Z",
    ]
    for index, timestamp in enumerate(timestamps):
        store.save(
            EvaluationRun(
                runId=f"eval-{index}",
                datasetId="fake-eval-v1",
                profileId="hybrid",
                status="completed",
                createdAt=timestamp,
                metrics={"recallAtK": index / 10},
            )
        )

    newest = store.list(2)
    assert [item.runId for item in newest] == ["eval-2", "eval-1"]
    assert newest[0].createdAt == timestamps[2]
    assert store.list(10) and [item.runId for item in store.list(10)] == ["eval-2", "eval-1", "eval-0"]
    assert store.list(0) == []


def test_store_skips_corrupt_files_and_rejects_path_traversal(tmp_path):
    store = FileEvaluationRunStore(tmp_path)
    store.save(
        EvaluationRun(
            runId="eval-good",
            datasetId="fake-eval-v1",
            profileId="hybrid",
            status="failed",
            createdAt="2026-04-01T00:00:00.000Z",
            error="boom",
        )
    )
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    assert [item.runId for item in store.list(10)] == ["eval-good"]
    assert store.get("../../etc/passwd") is None
    assert store.get("broken") is None


def test_openapi_exposes_persisted_run_list():
    schema = main_module.app.openapi()
    methods = schema["paths"]["/v1/evaluations/runs"]
    assert {"get", "post"} <= set(methods)
    parameters = methods["get"].get("parameters", [])
    assert any(parameter["name"] == "limit" for parameter in parameters)
    assert "EvaluationRunListResponse" in schema["components"]["schemas"]
    assert "createdAt" in schema["components"]["schemas"]["EvaluationRun"]["properties"]


def test_api_list_endpoint_reads_persisted_runs_after_rebuild(monkeypatch, tmp_path):
    store_dir = tmp_path / "api-runs"
    registry = EvaluationRunRegistry(FakePipeline(), store=FileEvaluationRunStore(store_dir))
    monkeypatch.setattr(main_module, "evaluation_runs", registry)

    created = client.post(
        "/v1/evaluations/runs",
        json={"datasetId": "fake-eval-v1", "profileId": "hybrid", "topK": 3, "ragasEnabled": False},
    )
    assert created.status_code == 202
    run_id = created.json()["runId"]
    assert created.json()["createdAt"].endswith("Z")
    wait_for_status(registry, run_id)

    rebuilt = EvaluationRunRegistry(FakePipeline(), store=FileEvaluationRunStore(store_dir))
    monkeypatch.setattr(main_module, "evaluation_runs", rebuilt)

    fetched = client.get(f"/v1/evaluations/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "completed"

    listing = client.get("/v1/evaluations/runs?limit=10")
    assert listing.status_code == 200
    runs = listing.json()["runs"]
    assert [item["runId"] for item in runs] == [run_id]
    assert {
        "runId",
        "datasetId",
        "profileId",
        "status",
        "createdAt",
        "metrics",
    } <= set(runs[0])

    assert client.get("/v1/evaluations/runs?limit=0").status_code == 422
    assert client.get("/v1/evaluations/runs?limit=201").status_code == 422
    saved = json.loads((store_dir / f"{run_id}.json").read_text(encoding="utf-8"))
    assert saved["status"] == "completed"
