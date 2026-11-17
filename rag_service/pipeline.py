from __future__ import annotations
import time
import uuid
import json
from urllib.request import Request, urlopen
from typing import Any

from langchain_core.runnables import RunnableLambda

from .config import Settings
from .backend import RetrievalBackend, SchemaMissingError
from .integrations import LangfuseAdapter, rerank, optional_status
from .models import Candidate, RetrieveRequest, RetrieveResponse
from .retrieval_math import embed


PROFILES: dict[str, dict[str, Any]] = {
    "dense-only": {"retrievers": ["dense"], "rerank": False},
    "bm25-only": {"retrievers": ["bm25"], "rerank": False},
    "hybrid": {"retrievers": ["dense", "bm25"], "rerank": False},
    "hybrid-rerank": {"retrievers": ["dense", "bm25"], "rerank": True},
}


def rewrite(query: str) -> list[str]:
    candidates = [query.replace("多少", "数值"), query.replace("？", "").replace("?", "")]
    return [query, *dict.fromkeys(item for item in candidates if item and item != query)][:3]


class RetrievalPipeline:
    def __init__(self, backend: RetrievalBackend, settings: Settings):
        self.backend = backend
        self.settings = settings
        self.runnable = RunnableLambda(self._run)
        self.langfuse = LangfuseAdapter(settings)

    def invoke(self, request: RetrieveRequest) -> RetrieveResponse:
        return self.runnable.invoke(request)

    def _run(self, request: RetrieveRequest) -> RetrieveResponse:
        started = time.perf_counter()
        profile = dict(PROFILES[request.profileId])
        override = request.overrides
        allowed = {"retrievers", "rerank.enabled", "dense.topK", "bm25.topK", "fusion.topK", "rerank.candidateTopK", "rerank.outputTopK", "fusion.rrfK"}
        unknown = sorted(set(override) - allowed)
        if unknown:
            raise ValueError(f"unsupported overrides: {', '.join(unknown)}")
        routes = override.get("retrievers", profile["retrievers"])
        if not routes:
            raise ValueError("at least one retriever is required")
        rerank_enabled = bool(override.get("rerank.enabled", profile["rerank"]))
        top_k = request.topK
        dense_top_k = int(override.get("dense.topK", max(top_k, 20)))
        bm25_top_k = int(override.get("bm25.topK", max(top_k, 20)))
        fusion_top_k = int(override.get("fusion.topK", max(top_k, 20)))
        rrf_k = int(override.get("fusion.rrfK", 60))
        rewrite_started = time.perf_counter()
        queries = rewrite(request.query) if request.rewriteMode == "multi_query" else [request.query]
        rewrite_ms = (time.perf_counter() - rewrite_started) * 1000
        trace = self.langfuse.start_trace(request.query, {"id": request.profileId, "version": 1, "retrievers": routes, "rerank": rerank_enabled})
        self.langfuse.observe(trace, "query_rewrite", {"queries": queries})
        route_results: dict[str, list[tuple[Any, float, int]]] = {}
        route_counts: dict[str, int] = {}
        route_statuses: dict[str, str] = {}
        route_timings: dict[str, float] = {}
        query_vectors: list[list[float]] = []
        for route in routes:
            route_started = time.perf_counter()
            limit = dense_top_k if route == "dense" else bm25_top_k
            try:
                route_hits = []
                if route == "dense":
                    query_vectors = self._embed_many(queries)
                    for query_vector in query_vectors:
                        route_hits.extend(self.backend.dense_search(query_vector, limit))
                else:
                    for query in queries:
                        route_hits.extend(self.backend.lexical_search(query, limit))
                best_by_chunk: dict[str, tuple[Any, float]] = {}
                for record, score in route_hits:
                    existing = best_by_chunk.get(record.chunkId)
                    if existing is None or score > existing[1]:
                        best_by_chunk[record.chunkId] = (record, score)
                scored = sorted(best_by_chunk.values(), key=lambda item: (-item[1], item[0].chunkId))[:limit]
                route_statuses[route] = "ok"
            except SchemaMissingError:
                scored = []
                route_statuses[route] = "empty"
            except RuntimeError:
                scored = []
                route_statuses[route] = "failed"
            route_timings[route] = (time.perf_counter() - route_started) * 1000
            route_results[route] = [(record, score, rank + 1) for rank, (record, score) in enumerate(scored)]
            route_counts[route] = len(route_results[route])
            self.langfuse.observe(trace, f"{route}_retrieval", {"count": len(route_results[route]), "status": route_statuses[route]})
        fusion_started = time.perf_counter()
        merged: dict[str, dict[str, Any]] = {}
        before = sum(len(items) for items in route_results.values())
        for route, items in route_results.items():
            for record, score, rank in items:
                item = merged.setdefault(record.chunkId, {"record": record, "stageScores": {}, "rrf": 0.0})
                item["stageScores"][route] = score
                item["rrf"] += 1.0 / (rrf_k + rank)
        ranked = sorted(merged.values(), key=lambda item: (-item["rrf"], item["record"].chunkId))[:fusion_top_k]
        fusion_ms = (time.perf_counter() - fusion_started) * 1000
        self.langfuse.observe(trace, "fusion", {"candidateCount": len(ranked)})
        route_failed = any(status == "failed" for status in route_statuses.values())
        source_missing = any(status == "empty" for status in route_statuses.values())
        retrieval_status = "failed" if route_failed and not ranked else ("degraded" if route_failed else ("empty" if source_missing else "ok"))
        statuses = {"retrieval": retrieval_status, "source": "schema_missing" if source_missing else "ok", "rerank": "skipped", "ragas": "skipped", "langfuse": optional_status(self.settings, "langfuse"), **route_statuses}
        rerank_ms = 0.0
        if rerank_enabled:
            rerank_limit = int(override.get("rerank.candidateTopK", len(ranked)))
            rerank_items = ranked[:rerank_limit]
            scores, status, rerank_ms = rerank(self.settings, request.query, [item["record"].text for item in rerank_items])
            statuses["rerank"] = status
            if scores is not None:
                for item, score in zip(rerank_items, scores):
                    item["stageScores"]["rerank"] = score
                ranked = sorted(rerank_items, key=lambda item: (-item["stageScores"]["rerank"], item["record"].chunkId))
            else:
                statuses["retrieval"] = "degraded"
            self.langfuse.observe(trace, "rerank", {"status": status, "candidateCount": len(rerank_items)})
        output_limit = int(override.get("rerank.outputTopK", top_k)) if rerank_enabled else top_k
        results = []
        for rank, item in enumerate(ranked[:output_limit], 1):
            stage_scores = {**item["stageScores"], "rrf": item["rrf"]}
            final_score = stage_scores.get("rerank", item["rrf"])
            record = item["record"]
            results.append(Candidate(chunkId=record.chunkId, contentType=record.contentType, text=record.text, parentChunkId=record.parentChunkId, matchedText=record.matchedText, rank=rank, finalScore=final_score, stageScores=stage_scores, provenance=record.provenance, assetRefs=record.assetRefs, tableRef=record.tableRef))
        elapsed = (time.perf_counter() - started) * 1000
        if statuses["retrieval"] == "ok" and statuses["rerank"] == "sent":
            statuses["retrieval"] = "ok"
        self.langfuse.observe(trace, "retrieval", {"status": statuses["retrieval"], "resultCount": len(results)})
        stages = [
            {"name": "query_rewrite", "status": "ok", "latencyMs": rewrite_ms},
            {"name": "dense_retrieval", "status": route_statuses.get("dense", "skipped"), "latencyMs": route_timings.get("dense", 0.0)},
            {"name": "bm25_retrieval", "status": route_statuses.get("bm25", "skipped"), "latencyMs": route_timings.get("bm25", 0.0)},
            {"name": "fusion", "status": "empty" if source_missing else ("degraded" if route_failed else "ok"), "latencyMs": fusion_ms},
            {"name": "rerank", "status": statuses["rerank"], "latencyMs": rerank_ms},
            {"name": "retrieval", "status": statuses["retrieval"], "latencyMs": elapsed},
        ]
        trace_url = f"{self.settings.langfuse_host.rstrip('/')}/trace/{trace['traceId']}" if self.settings.langfuse_host and trace.get("status") == "sent" else None
        return RetrieveResponse(requestId=str(uuid.uuid4()), query=request.query, rewrittenQueries=queries, profileSnapshot={"id": request.profileId, "version": 1, "retrievers": routes, "rerank": rerank_enabled, "rewriteMode": request.rewriteMode, "overrides": override}, routeCounts=route_counts, candidateCount=len(results), deduplicationRate=1 - (len(merged) / before if before else 0), results=results, statuses={**statuses, "langfuse": trace["status"]}, timingsMs={"total": elapsed, "queryRewrite": rewrite_ms, "denseRetrieval": route_timings.get("dense", 0.0), "bm25Retrieval": route_timings.get("bm25", 0.0), "fusion": fusion_ms, "rerank": rerank_ms}, indexVersion=self.backend.version, modelVersion={"embedding": self.settings.embedding_model_version, "rewrite": self.settings.rewrite_model_version, "rerank": self.settings.rerank_model_version}, stages=stages, traceId=trace.get("traceId"), traceUrl=trace_url)

    def _embed_many(self, texts: list[str]) -> list[list[float]]:
        if self.settings.embedding_provider == "fake":
            return [embed(text, self.settings.embedding_dimensions) for text in texts]
        if self.settings.embedding_provider != "openai-compatible":
            raise ValueError("RAG_EMBEDDING_PROVIDER must be fake or openai-compatible")
        if not self.settings.embedding_base_url or not self.settings.embedding_api_key:
            raise ValueError("real embedding requires RAG_EMBEDDING_BASE_URL and RAG_EMBEDDING_API_KEY")
        endpoint = self.settings.embedding_base_url.rstrip("/") + "/embeddings"
        body = json.dumps({"model": self.settings.embedding_model, "input": texts}).encode("utf-8")
        req = Request(endpoint, data=body, headers={"Authorization": f"Bearer {self.settings.embedding_api_key}", "Content-Type": "application/json"})
        with urlopen(req, timeout=self.settings.embedding_timeout_ms / 1000) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError("embedding response data length mismatch")
        vectors = [item.get("embedding") for item in sorted(data, key=lambda item: item.get("index", 0))]
        if any(not isinstance(vector, list) or len(vector) != self.settings.embedding_dimensions for vector in vectors):
            raise ValueError("embedding response dimension mismatch")
        return [[float(value) for value in vector] for vector in vectors]
