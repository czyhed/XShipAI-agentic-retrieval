from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


ContentType = Literal["text", "image", "table"]
ProfileId = Literal["dense-only", "bm25-only", "hybrid", "hybrid-rerank"]
RewriteMode = Literal["none", "multi_query"]
ProviderStatus = Literal["sent", "skipped", "failed", "degraded"]


class Provenance(BaseModel):
    documentId: str | None = None
    documentVersion: str | None = None
    sourceBlockIds: list[str] = Field(default_factory=list)
    page: int | None = None


class IndexRecord(BaseModel):
    chunkId: str
    contentType: ContentType
    text: str
    parentChunkId: str | None = None
    matchedText: str | None = None
    vector: list[float] | None = None
    provenance: Provenance = Field(default_factory=Provenance)
    assetRefs: list[str] = Field(default_factory=list)
    tableRef: str | None = None


class OverrideModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retrievers: list[Literal["dense", "bm25"]] | None = None
    rerank_enabled: bool | None = Field(default=None, alias="rerank.enabled")
    dense_topK: int | None = Field(default=None, alias="dense.topK")
    bm25_topK: int | None = Field(default=None, alias="bm25.topK")
    fusion_topK: int | None = Field(default=None, alias="fusion.topK")
    rerank_candidateTopK: int | None = Field(default=None, alias="rerank.candidateTopK")
    rerank_outputTopK: int | None = Field(default=None, alias="rerank.outputTopK")
    rrfK: int | None = Field(default=None, alias="fusion.rrfK")


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    profileId: ProfileId = "hybrid"
    rewriteMode: RewriteMode = "none"
    overrides: dict[str, Any] = Field(default_factory=dict)
    topK: int = Field(default=10, ge=1, le=100)


class Candidate(BaseModel):
    chunkId: str
    contentType: ContentType
    text: str
    parentChunkId: str | None = None
    matchedText: str | None = None
    rank: int
    finalScore: float
    stageScores: dict[str, float]
    provenance: Provenance
    assetRefs: list[str] = Field(default_factory=list)
    tableRef: str | None = None


class RetrieveResponse(BaseModel):
    requestId: str
    query: str
    rewrittenQueries: list[str]
    profileSnapshot: dict[str, Any]
    routeCounts: dict[str, int]
    candidateCount: int
    deduplicationRate: float
    results: list[Candidate]
    statuses: dict[str, str]
    timingsMs: dict[str, float]
    indexVersion: str
    modelVersion: dict[str, str]
    stages: list[dict[str, Any]] = Field(default_factory=list)
    traceId: str | None = None
    traceUrl: str | None = None


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasetId: str = "fake-eval-v1"
    profileId: ProfileId = "hybrid"
    topK: int = Field(default=10, ge=1, le=100)
    ragasEnabled: bool = False


class EvaluationDataset(BaseModel):
    id: str
    name: str
    queryCount: int


class EvaluationRun(BaseModel):
    runId: str
    datasetId: str
    profileId: ProfileId
    status: Literal["running", "completed", "failed"]
    createdAt: str = Field(default_factory=utc_now_iso)
    providers: dict[str, ProviderStatus] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    ragas: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    traceId: str | None = None
    traceUrl: str | None = None


class EvaluationRunSummary(BaseModel):
    runId: str
    datasetId: str
    profileId: ProfileId
    status: Literal["running", "completed", "failed"]
    createdAt: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class EvaluationRunListResponse(BaseModel):
    runs: list[EvaluationRunSummary]


class AnswerRequest(RetrieveRequest):
    answerModelProfile: str = "deterministic-answer-v1"
    judge: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    citationId: str
    chunkId: str
    documentId: str | None = None
    documentVersion: str | None = None
    sourceBlockIds: list[str] = Field(default_factory=list)
    page: int | None = None
    quotedText: str


class AnswerResponse(BaseModel):
    requestId: str
    status: Literal["answered", "degraded", "abstained", "failed"]
    answer: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    confidence: dict[str, Any]
    contextPack: list[dict[str, Any]] = Field(default_factory=list)
    retrieval: RetrieveResponse | None = None
    stages: list[dict[str, Any]] = Field(default_factory=list)
    providers: dict[str, str] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    traceId: str | None = None
    traceUrl: str | None = None
    judge: dict[str, Any] = Field(default_factory=dict)
