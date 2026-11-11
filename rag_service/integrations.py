import base64
import json
import time
import uuid
from urllib.request import Request, urlopen
from .config import Settings


def rerank(settings: Settings, query: str, texts: list[str]) -> tuple[list[float] | None, str, float]:
    if not settings.rerank_url or not settings.rerank_api_key:
        return None, "skipped", 0.0
    started = time.perf_counter()
    try:
        body = json.dumps({"model": settings.rerank_model_version, "input": {"query": query, "documents": texts}, "parameters": {"return_documents": True, "top_n": len(texts)}}, ensure_ascii=False).encode("utf-8")
        req = Request(settings.rerank_url, data=body, headers={"Authorization": f"Bearer {settings.rerank_api_key}", "Content-Type": "application/json"})
        with urlopen(req, timeout=settings.rerank_timeout_ms / 1000) as response:
            payload = json.loads(response.read().decode("utf-8"))
        output = payload.get("output")
        results = output.get("results") if isinstance(output, dict) else None
        if not isinstance(results, list) or len(results) != len(texts):
            raise ValueError("invalid rerank response")
        scores: list[float | None] = [None] * len(texts)
        for result in results:
            index = result.get("index") if isinstance(result, dict) else None
            score = result.get("relevance_score") if isinstance(result, dict) else None
            if not isinstance(index, int) or not 0 <= index < len(texts) or scores[index] is not None:
                raise ValueError("invalid rerank response")
            scores[index] = float(score)
        if any(score is None for score in scores):
            raise ValueError("invalid rerank response")
        return [float(score) for score in scores], "sent", (time.perf_counter() - started) * 1000
    except Exception:
        return None, "failed", (time.perf_counter() - started) * 1000


def optional_status(settings: Settings, provider: str) -> str:
    # The deterministic local adapters are intentionally opt-in. A configured
    # provider is reported as pending/skipped until a real adapter call exists.
    if provider == "ragas":
        return "skipped" if not settings.ragas_url else "degraded"
    if provider == "langfuse":
        return "skipped" if not settings.langfuse_host else "degraded"
    return "skipped"


class RagasAdapter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def evaluate(self, query: str, retrieved_contexts: list[str], response: str | None, reference: str | None) -> dict:
        if not self.settings.ragas_url:
            return {"status": "skipped"}
        started = time.perf_counter()
        payload = {"query": query, "retrieved_contexts": retrieved_contexts, "response": response, "reference": reference}
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if self.settings.ragas_api_key:
                headers["Authorization"] = f"Bearer {self.settings.ragas_api_key}"
            request = Request(self.settings.ragas_url, data=body, headers=headers)
            with urlopen(request, timeout=self.settings.ragas_timeout_ms / 1000) as result:
                data = json.loads(result.read().decode("utf-8"))
            metrics = {key: float(data[key]) for key in ("contextPrecision", "contextRecall", "contextRelevancy", "faithfulness", "answerRelevancy") if key in data}
            return {"status": "sent", "metrics": metrics, "latencyMs": (time.perf_counter() - started) * 1000}
        except Exception:
            return {"status": "failed", "reason": "ragas provider request failed", "latencyMs": (time.perf_counter() - started) * 1000}


class JudgeAdapter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def evaluate(self, query: str, context_pack: list[dict], answer: str | None, reference: str | None, citation_validation: dict) -> dict:
        if not self.settings.judge_url:
            return {"status": "skipped", "modelVersion": self.settings.judge_model_version}
        started = time.perf_counter()
        payload = {"query": query, "contextPack": context_pack, "answer": answer, "reference": reference, "citationValidation": citation_validation, "mode": "shadow"}
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if self.settings.judge_api_key:
                headers["Authorization"] = f"Bearer {self.settings.judge_api_key}"
            request = Request(self.settings.judge_url, data=body, headers=headers)
            with urlopen(request, timeout=self.settings.judge_timeout_ms / 1000) as result:
                data = json.loads(result.read().decode("utf-8"))
            return {"status": "sent", "modelVersion": self.settings.judge_model_version, "faithfulness": data.get("faithfulness"), "answerRelevancy": data.get("answerRelevancy"), "citationCorrectness": data.get("citationCorrectness"), "citationCompleteness": data.get("citationCompleteness"), "shouldAbstain": bool(data.get("shouldAbstain", False)), "reasons": data.get("reasons", []), "latencyMs": (time.perf_counter() - started) * 1000}
        except Exception:
            return {"status": "failed", "modelVersion": self.settings.judge_model_version, "reason": "judge provider request failed", "latencyMs": (time.perf_counter() - started) * 1000}


class LangfuseAdapter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._traces: dict[str, dict] = {}

    def start_trace(self, query: str, profile_snapshot: dict) -> dict:
        trace_id = str(uuid.uuid4())
        if not self.settings.langfuse_host or not self.settings.langfuse_public_key or not self.settings.langfuse_secret_key:
            return {"status": "skipped", "traceId": trace_id, "observations": []}
        started = time.perf_counter()
        payload = {"batch": [{"id": trace_id, "type": "trace-create", "body": {"id": trace_id, "name": "agentic-retrieval", "input": {"query": query, "profileSnapshot": profile_snapshot}}}]}
        token = base64.b64encode(f"{self.settings.langfuse_public_key}:{self.settings.langfuse_secret_key}".encode()).decode()
        request = Request(self.settings.langfuse_host.rstrip("/") + "/api/public/ingestion", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"})
        try:
            with urlopen(request, timeout=self.settings.langfuse_timeout_ms / 1000):
                pass
            trace = {"status": "sent", "traceId": trace_id, "observations": [], "latencyMs": (time.perf_counter() - started) * 1000}
            self._traces[trace_id] = trace
            return trace
        except Exception:
            trace = {"status": "failed", "traceId": trace_id, "observations": [], "reason": "langfuse provider request failed", "latencyMs": (time.perf_counter() - started) * 1000}
            self._traces[trace_id] = trace
            return trace

    def observe(self, trace: dict, name: str, payload: dict) -> dict:
        trace.setdefault("observations", []).append(name)
        if trace.get("status") == "sent":
            body = json.dumps({"batch": [{"id": str(uuid.uuid4()), "type": "observation-create", "body": {"traceId": trace["traceId"], "name": name, "input": payload}}]}).encode()
            token = base64.b64encode(f"{self.settings.langfuse_public_key}:{self.settings.langfuse_secret_key}".encode()).decode()
            request = Request(self.settings.langfuse_host.rstrip("/") + "/api/public/ingestion", data=body, headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"})
            try:
                with urlopen(request, timeout=self.settings.langfuse_timeout_ms / 1000):
                    pass
            except Exception:
                trace["status"] = "degraded"
        return trace

    def observe_by_id(self, trace_id: str | None, name: str, payload: dict) -> dict | None:
        if not trace_id:
            return None
        trace = self._traces.get(trace_id)
        return self.observe(trace, name, payload) if trace else None
