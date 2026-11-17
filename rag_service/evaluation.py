from __future__ import annotations
import logging
import math
import threading
import time
import uuid
from .answering import AnswerGenerator
from .config import settings
from .eval_store import EvaluationRunStore, FileEvaluationRunStore
from .integrations import LangfuseAdapter, RagasAdapter
from .models import EvaluationRequest, EvaluationRun, EvaluationRunSummary, utc_now_iso
from .pipeline import RetrievalPipeline

logger = logging.getLogger(__name__)


DATASETS = {
    "fake-eval-v1": {
        "name": "Deterministic retrieval evaluation",
        "items": [
            {"query": "支持多少瓦快充", "relevant": {"text-001": 3}},
            {"query": "USB-C 接口示意图", "relevant": {"image-001": 3}},
            {"query": "额定功率", "relevant": {"table-001": 3}},
        ],
    }
}


def list_datasets() -> list[dict]:
    return [
        {"id": dataset_id, "name": dataset["name"], "queryCount": len(dataset["items"])}
        for dataset_id, dataset in DATASETS.items()
    ]


def evaluate(request: EvaluationRequest, pipeline: RetrievalPipeline, run_id: str, answer_generator: AnswerGenerator | None = None, ragas: RagasAdapter | None = None, langfuse: LangfuseAdapter | None = None, created_at: str | None = None) -> EvaluationRun:
    dataset = DATASETS.get(request.datasetId)
    if dataset is None:
        raise ValueError(f"unknown dataset: {request.datasetId}")
    rows = []
    latencies = []
    ragas_results = []
    trace = langfuse.start_trace("evaluation", {"id": request.profileId, "datasetId": request.datasetId}) if langfuse else {"status": "skipped", "traceId": None}
    for item in dataset["items"]:
        started = time.perf_counter()
        from .models import RetrieveRequest
        result = pipeline.invoke(RetrieveRequest(query=item["query"], profileId=request.profileId, topK=request.topK))
        context_pack = [candidate.text for candidate in result.results]
        response_text = None
        if answer_generator is not None:
            generated = answer_generator.generate(item["query"], [{"citationId": f"S{index}", "chunkId": candidate.chunkId, "text": candidate.text} for index, candidate in enumerate(result.results, 1)], "deterministic-answer-v1")
            if generated.get("status") == "sent":
                response_text = generated.get("answer")
        if request.ragasEnabled and ragas is not None:
            ragas_results.append(ragas.evaluate(item["query"], context_pack, response_text, item.get("reference")))
        latencies.append((time.perf_counter() - started) * 1000)
        relevant = item["relevant"]
        hits = [candidate for candidate in result.results if candidate.chunkId in relevant]
        recall = len(hits) / len(relevant)
        precision = len(hits) / request.topK
        mrr = 1 / hits[0].rank if hits else 0.0
        dcg = sum((2 ** relevant[c.chunkId] - 1) / math.log2(c.rank + 1) for c in hits)
        ideal = sorted(relevant.values(), reverse=True)[: request.topK]
        idcg = sum((2 ** grade - 1) / math.log2(index + 2) for index, grade in enumerate(ideal))
        rows.append({"query": item["query"], "recallAtK": recall, "precisionAtK": precision, "mrr": mrr, "ndcgAtK": dcg / idcg if idcg else 0.0, "imageHit": any(c.contentType == "image" for c in hits), "tableHit": any(c.contentType == "table" for c in hits), "provenanceComplete": all(c.provenance.sourceBlockIds for c in hits)})
    def percentile(values: list[float], q: float) -> float:
        values = sorted(values)
        return values[min(len(values) - 1, int(round((q / 100) * (len(values) - 1))))]
    count = len(rows)
    context_precision = sum(r["precisionAtK"] for r in rows) / count
    context_recall = sum(r["recallAtK"] for r in rows) / count
    context_relevancy = sum((r["precisionAtK"] + r["recallAtK"]) / 2 for r in rows) / count
    ragas_status = "skipped"
    ragas_payload: dict = {"status": "skipped"}
    if request.ragasEnabled and ragas_results:
        provider_states = {item.get("status") for item in ragas_results}
        if provider_states == {"skipped"}:
            ragas_status = "skipped"
        elif provider_states == {"sent"}:
            ragas_status = "sent"
        elif "failed" in provider_states:
            ragas_status = "failed"
        else:
            ragas_status = "degraded"
        ragas_payload = {"status": ragas_status, "runs": ragas_results}
    if langfuse:
        langfuse.observe(trace, "evaluation", {"datasetId": request.datasetId, "rowCount": len(rows)})
    trace_url = f"{langfuse.settings.langfuse_host.rstrip('/')}/trace/{trace['traceId']}" if langfuse and langfuse.settings.langfuse_host and trace.get("status") == "sent" else None
    return EvaluationRun(
        runId=run_id,
        datasetId=request.datasetId,
        profileId=request.profileId,
        status="completed",
        createdAt=created_at or utc_now_iso(),
        providers={"ragas": ragas_status, "langfuse": trace.get("status", "skipped")},
        metrics={"recallAtK": context_recall, "precisionAtK": context_precision, "mrr": sum(r["mrr"] for r in rows) / count, "ndcgAtK": sum(r["ndcgAtK"] for r in rows) / count, "latencyMs": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95), "p99": percentile(latencies, 99)}, "imageHitRate": sum(r["imageHit"] for r in rows) / count, "tableHitRate": sum(r["tableHit"] for r in rows) / count, "provenanceCompleteness": sum(r["provenanceComplete"] for r in rows) / count},
        ragas=ragas_payload,
        rows=rows,
        traceId=trace.get("traceId"),
        traceUrl=trace_url,
    )


class EvaluationRunRegistry:
    def __init__(self, pipeline: RetrievalPipeline, answer_generator: AnswerGenerator | None = None, ragas: RagasAdapter | None = None, langfuse: LangfuseAdapter | None = None, store: EvaluationRunStore | None = None):
        self.pipeline = pipeline
        self.answer_generator = answer_generator
        self.ragas = ragas
        self.langfuse = langfuse
        self.store = store or FileEvaluationRunStore(settings.rag_eval_store_dir)

    def create(self, request: EvaluationRequest) -> EvaluationRun:
        if request.datasetId not in DATASETS:
            raise ValueError(f"unknown dataset: {request.datasetId}")
        if not self.pipeline.backend.health().get("available"):
            raise RuntimeError("retrieval source unavailable; evaluation run is disabled")
        run = EvaluationRun(
            runId=f"eval-{uuid.uuid4()}",
            datasetId=request.datasetId,
            profileId=request.profileId,
            status="running",
            createdAt=utc_now_iso(),
            providers={"ragas": "skipped", "langfuse": "skipped"},
            ragas={"status": "skipped"},
        )
        try:
            self.store.save(run)
        except OSError as error:
            raise RuntimeError("evaluation run store unavailable") from error
        threading.Thread(target=self._execute, args=(run.runId, request, run.createdAt), daemon=True).start()
        return run.model_copy(deep=True)

    def get(self, run_id: str) -> EvaluationRun | None:
        return self.store.get(run_id)

    def list_runs(self, limit: int = 50) -> list[EvaluationRunSummary]:
        return self.store.list(limit)

    def _execute(self, run_id: str, request: EvaluationRequest, created_at: str) -> None:
        try:
            result = evaluate(request, self.pipeline, run_id, self.answer_generator, self.ragas, self.langfuse, created_at)
        except Exception as error:
            result = EvaluationRun(
                runId=run_id,
                datasetId=request.datasetId,
                profileId=request.profileId,
                status="failed",
                createdAt=created_at,
                providers={"ragas": "skipped", "langfuse": "skipped"},
                ragas={"status": "skipped"},
                error=str(error),
            )
        try:
            self.store.save(result)
        except OSError:
            logger.exception("failed to persist evaluation run %s", run_id)
