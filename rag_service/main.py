from pathlib import Path
import time
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from .config import settings
from .answering import build_answer_generator
from .evaluation import EvaluationRunRegistry, list_datasets
from .integrations import JudgeAdapter, LangfuseAdapter, RagasAdapter
from .backend import PgVectorAdapter
from .models import AnswerRequest, AnswerResponse, Citation, EvaluationRequest, EvaluationRun, EvaluationRunListResponse, RetrieveRequest, RetrieveResponse
from .pipeline import PROFILES, RetrievalPipeline

app = FastAPI(title="AgenticRetrival Python RAG Service", version="0.1.0")
if settings.retrieval_provider != "pgvector":
    raise RuntimeError("RAG_RETRIEVAL_PROVIDER must be pgvector")
backend = PgVectorAdapter(settings)
pipeline = RetrievalPipeline(backend, settings)
answer_generator = build_answer_generator(settings)
evaluation_runs = EvaluationRunRegistry(pipeline, answer_generator, RagasAdapter(settings), LangfuseAdapter(settings))
judge = JudgeAdapter(settings)


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(Path(__file__).resolve().parent.parent / "web" / "index.html")


@app.get("/logo.png", include_in_schema=False)
def brand_logo():
    return FileResponse(Path(__file__).resolve().parent.parent / "web" / "logo.png", media_type="image/png")


@app.get("/sdk/index.js", include_in_schema=False)
def sdk_runtime():
    return FileResponse(Path(__file__).resolve().parent.parent / "sdk" / "index.js", media_type="application/javascript")


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "python-rag-retrieval", **backend.health()}


@app.get("/v1/profiles")
def profiles():
    return {"profiles": [{"id": key, **value} for key, value in PROFILES.items()]}


@app.post("/v1/retrieve", response_model=RetrieveResponse)
def retrieve(request: RetrieveRequest):
    try:
        return pipeline.invoke(request)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/v1/answers", response_model=AnswerResponse)
def answer(request: AnswerRequest):
    try:
        retrieval = pipeline.invoke(request)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    request_id = retrieval.requestId
    stages = list(retrieval.stages)
    context: list[dict] = []
    citation_index: dict[str, Citation] = {}
    reasons = []
    context_started = time.perf_counter()
    for index, candidate in enumerate(retrieval.results, 1):
        citation_id = f"S{index}"
        context.append({"citationId": citation_id, "chunkId": candidate.chunkId, "text": candidate.text, "contentType": candidate.contentType, "rank": candidate.rank})
        citation_index[citation_id] = Citation(citationId=citation_id, chunkId=candidate.chunkId, documentId=candidate.provenance.documentId, documentVersion=candidate.provenance.documentVersion, sourceBlockIds=candidate.provenance.sourceBlockIds, page=candidate.provenance.page, quotedText=candidate.text)
    context_assembly_ms = (time.perf_counter() - context_started) * 1000
    evidence_started = time.perf_counter()
    complete = bool(citation_index) and all(item.documentId and item.documentVersion and item.sourceBlockIds and item.page and item.page >= 1 for item in citation_index.values())
    evidence_gate_ms = (time.perf_counter() - evidence_started) * 1000
    provider_status = "skipped"
    answer_generation_ms = 0.0
    citation_validation_ms = 0.0
    judge_result = {"status": "skipped", "modelVersion": settings.judge_model_version}
    citations: list[Citation] = []
    if retrieval.statuses.get("source") == "schema_missing":
        status, answer_text = "failed", None
        reasons.append("retrieval source schema missing")
    elif retrieval.statuses.get("retrieval") == "failed":
        status, answer_text = "failed", None
        reasons.append("retrieval source failed")
    elif not context:
        status, answer_text = "abstained", None
        reasons.append("insufficient evidence")
    elif not complete:
        status, answer_text = "abstained", None
        reasons.append("citation provenance incomplete")
    else:
        answer_started = time.perf_counter()
        generated = answer_generator.generate(request.query, context, request.answerModelProfile)
        answer_generation_ms = (time.perf_counter() - answer_started) * 1000
        provider_status = str(generated.get("status", "failed"))
        if provider_status != "sent":
            status, answer_text = "failed", None
            reasons.append(str(generated.get("reason") or "answer provider failed"))
        elif generated.get("abstain"):
            status, answer_text = "abstained", None
            reasons.append("answer generator abstained")
        else:
            citation_started = time.perf_counter()
            requested_ids = [str(value) for value in generated.get("citationIds", [])]
            unknown_ids = [value for value in requested_ids if value not in citation_index]
            if unknown_ids or not requested_ids:
                status, answer_text = "abstained", None
                reasons.append("answer citations are invalid")
            else:
                citations = [citation_index[value] for value in requested_ids]
                answer_text = str(generated.get("answer") or "").strip() or None
                if not answer_text:
                    status = "failed"
                    reasons.append("answer provider returned no answer")
                else:
                    status = "degraded" if retrieval.statuses.get("rerank") in {"failed", "skipped"} and request.profileId == "hybrid-rerank" else "answered"
                    if status == "degraded":
                        reasons.append("optional rerank provider unavailable")
            citation_validation_ms = (time.perf_counter() - citation_started) * 1000
    if request.judge.get("enabled"):
        judge_result = judge.evaluate(request.query, context, answer_text, None, {"status": "passed" if citations else "failed"})
    stages.extend([{"name": "evidence_gate", "status": "passed" if complete else "abstained", "latencyMs": evidence_gate_ms}, {"name": "context_assembly", "status": "sent" if context else "skipped", "latencyMs": context_assembly_ms}, {"name": "answer_generation", "status": provider_status, "latencyMs": answer_generation_ms}, {"name": "citation_validation", "status": "passed" if citations else ("skipped" if status == "failed" else "failed"), "latencyMs": citation_validation_ms}])
    trace_state = pipeline.langfuse.observe_by_id(retrieval.traceId, "evidence_gate", {"status": "passed" if complete else "abstained"})
    trace_state = pipeline.langfuse.observe_by_id(retrieval.traceId, "context_assembly", {"contextPack": context}) or trace_state
    trace_state = pipeline.langfuse.observe_by_id(retrieval.traceId, "answer_generation", {"status": provider_status}) or trace_state
    trace_state = pipeline.langfuse.observe_by_id(retrieval.traceId, "citation_validation", {"status": "passed" if citations else ("skipped" if status == "failed" else "failed")}) or trace_state
    trace_state = pipeline.langfuse.observe_by_id(retrieval.traceId, "answer_judge", judge_result) or trace_state
    langfuse_status = trace_state.get("status", retrieval.statuses.get("langfuse", "skipped")) if trace_state else retrieval.statuses.get("langfuse", "skipped")
    confidence_level = "high" if status in {"answered", "degraded"} and citations else "low"
    stages.append({"name": "answer_judge", "status": judge_result.get("status", "skipped"), "latencyMs": judge_result.get("latencyMs", 0.0)})
    return AnswerResponse(requestId=request_id, status=status, answer=answer_text, citations=citations, confidence={"level": confidence_level, "score": 0.9 if confidence_level == "high" else 0.0, "thresholdVersion": "uncalibrated-v1", "signals": {"provenanceComplete": complete}, "reasons": reasons}, contextPack=context, retrieval=retrieval, stages=stages, providers={"answer": provider_status, "rerank": retrieval.statuses.get("rerank", "skipped"), "ragas": retrieval.statuses.get("ragas", "skipped"), "judge": judge_result.get("status", "skipped"), "langfuse": langfuse_status}, reasons=reasons, traceId=retrieval.traceId, traceUrl=retrieval.traceUrl, judge=judge_result)


@app.get("/v1/evaluation-datasets")
def evaluation_datasets():
    return {"datasets": list_datasets()}


@app.post("/v1/evaluations/runs", response_model=EvaluationRun, status_code=202)
def evaluation_run(request: EvaluationRequest):
    try:
        return evaluation_runs.create(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/v1/evaluations/runs", response_model=EvaluationRunListResponse)
def evaluation_run_list(limit: int = Query(default=50, ge=1, le=200)):
    return {"runs": evaluation_runs.list_runs(limit)}


@app.get("/v1/evaluations/runs/{run_id}", response_model=EvaluationRun)
def evaluation_status(run_id: str):
    run = evaluation_runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    return run
