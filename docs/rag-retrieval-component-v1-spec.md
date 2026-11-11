# RAG Retrieval Component V1 Specification

## Problem Statement

调用方需要一个独立、可复用的 RAG 服务：输入用户问题和受控 Pipeline Profile，服务通过 Python 服务端只读 PGVector adapter 从 Chunk Lab 正式 PostgreSQL/pgvector 数据召回并精排证据，再生成有引用的正式答案。历史 TS 检索实现与服务边界不一致，不能进入正式链路。

本 spec 将已确认的 PRD 转换为可执行的服务契约。它不修改 Chunk Lab 的解析、切分、人工修订或导出语义；Chunk Lab 仅增加读取 `AGENTIC_RETRIEVAL_UI_URL` 的无参数新标签页跳转，不保留或执行 TS 检索逻辑。

## Solution

建设独立 Python 进程，使用 FastAPI 暴露 OpenAPI REST 契约。服务端通过只读 PGVector adapter 直接查询 Chunk Lab 正式表 `rag.vector_records_v2_d1024`，使用 LangChain Python 1.x Runnable/Retriever Pipeline 执行 Query Rewrite、Dense/BM25 召回、标准化、稳定去重、RRF 和可选外部 Rerank。

精排后的候选经过 Evidence Gate 形成 Context Pack，Context Pack 是 Answer Generator 唯一的事实性上下文。Answer Generator 返回结构化答案和 citation ID；Citation Validator 从服务端索引回填真实文档、版本、页码、源 Block 和引用文本，并拒绝不可追溯引用。

服务提供检索、正式答案和异步评测 HTTP 入口。公共 API 只提供全局检索，不包含 validation task 或 Chunk Lab 商品范围字段。每次检索和答案请求都产生可关联的阶段状态和耗时；Langfuse 记录 Trace/Observation，Ragas 和 LLM-as-a-Judge 负责显式异步或离线质量评测，不成为主答案链路的单点故障。

## User Stories

1. As an internal product caller, I want to submit a query and a registered profile, so that I receive deterministic retrieval results without submitting records.
2. As an internal product caller, I want to choose Dense-only, BM25-only, Hybrid, or Hybrid+Rerank, so that I can compare retrieval strategies.
3. As an internal product caller, I want to enable `none` or `multi_query` rewrite, so that query expansion is explicit and the original query is retained.
4. As an internal product caller, I want route TopK, fusion TopK, RRF K, rerank candidate TopK, and output TopK to be configurable through a whitelist, so that tuning cannot inject arbitrary code.
5. As an internal product caller, I want image and table summaries to behave as ordinary indexed content, so that they can be retrieved without separate search semantics.
6. As an internal product caller, I want every result to include content type and provenance, so that I can inspect the original evidence.
7. As an internal product caller, I want a formal answer endpoint, so that I do not need to implement answer generation around raw retrieval results.
8. As an internal product caller, I want the formal answer to use only reranked Context Pack entries, so that unsupported model knowledge is not silently presented as sourced fact.
9. As an internal product caller, I want an abstention result when evidence is insufficient, so that the system does not fabricate a confident answer.
10. As an internal product caller, I want degraded status when evidence conflicts or an optional provider is unavailable, so that partial service behavior is explicit.
11. As an internal product caller, I want answer citations to reference stable citation IDs, so that citations cannot point to chunks that were not sent to the Answer Generator.
12. As an internal product caller, I want the server to provide real document version, 1-based page, source Block IDs, and quoted text, so that I can locate the source without trusting model-generated locations.
13. As an internal product caller, I want citation validation to reject unknown IDs and missing required provenance, so that an unverified answer is not returned as answered.
14. As an internal product caller, I want confidence level, score, signals, reasons, and threshold version, so that answer quality is explainable and calibration status is visible.
15. As an internal product caller, I want conflicts between supporting sources to be surfaced with separate citations, so that ranking does not conceal factual disagreement.
16. As an internal product caller, I want Answer Generator failures to preserve retrieval diagnostics without inventing an answer, so that failures are actionable.
17. As an internal product caller, I want each answer stage to expose status and latency, so that I can diagnose rewrite, retrieval, fusion, rerank, gating, generation, and citation failures.
18. As an operator, I want a Langfuse Trace with stage Observations, so that the actual reranked Context Pack sent to the LLM is inspectable.
19. As an evaluator, I want deterministic qrels metrics, so that profile comparisons are reproducible in CI.
20. As an evaluator, I want Ragas to evaluate the actual retrieved contexts and generated response, so that context and answer quality are measured separately.
21. As an evaluator, I want LLM-as-a-Judge to run in shadow or offline mode, so that judge failures do not block the primary answer path.
22. As an evaluator, I want a human-labeled query/evidence/reference-answer set, so that confidence thresholds and Judge agreement can be calibrated rather than guessed.
23. As an operator, I want missing Ragas, Langfuse, or external Rerank providers to return explicit skipped, failed, or degraded states, so that optional integrations do not masquerade as success.
24. As an SDK consumer, I want a TypeScript SDK generated from OpenAPI, so that clients depend on stable JSON types and HTTP methods instead of Python or LangChain objects.
25. As a maintainer, I want the Python service to be the only retrieval implementation, so that historical TypeScript retrieval cannot become a fallback or proxy.
26. As a maintainer, I want indexVersion, modelVersion, profileSnapshot, and thresholdVersion persisted in responses and traces, so that runs remain explainable after configuration changes.
27. As a maintainer, I want deterministic fake index data and fake model adapters, so that tests do not depend on paid APIs.
28. As a maintainer, I want the service to avoid account, role, permission, tenant, knowledge-base administration, mobile, graph, Text-to-SQL, and Agentic V2 scope, so that V1 remains a focused retrieval-and-answer component.
29. As a Chunk Lab user, I want one verification button to open AgenticRetrival, so that I can inspect retrieval and answer behavior without duplicating retrieval logic in Chunk Lab.
30. As a service operator, I want AgenticRetrival to query the released Chunk Lab PGVector records through a controlled read-only adapter, so that formal retrieval never rebuilds a second index.
31. As a verifier, I want the AgenticRetrival UI to show the answer and retrieval diagnostics in one consistent Operate-style surface, so that I can compare evidence and answer quality.

## Implementation Decisions

- The public boundary is OpenAPI served by FastAPI. The required operations are `POST /v1/retrieve`, `POST /v1/answers`, and `POST /v1/evaluations/runs`; health and profile discovery are supporting operations.
- `/v1/retrieve` returns raw retrieval diagnostics and ranked candidates. `/v1/answers` runs the same retrieval pipeline, then Evidence Gate, Context Pack assembly, Answer Generator, and Citation Validator. It must not duplicate retrieval logic.
- Requests contain query, registered profile ID, bounded topK, and whitelisted overrides. Requests never contain retrieval records, executable code, database SDK objects, secrets, or arbitrary provider configuration.
- The server registers exactly Dense and BM25 retrievers for V1 and supports Dense-only, BM25-only, Hybrid, and Hybrid+Rerank profiles.
- Query Rewrite supports `none` and `multi_query`; the original query is always retained in the response and trace.
- Candidate normalization uses a stable chunk ID, content type, safe text, stage scores, provenance, optional asset references, and optional table reference. Image and table summaries remain ordinary indexed content.
- Hybrid fusion uses stable de-duplication by chunk ID and Reciprocal Rank Fusion. Dense and BM25 raw scores are not directly mixed.
- External Rerank receives only fused candidates. Missing, timed-out, malformed, or failed Rerank calls return explicit `skipped`, `failed`, or `degraded` status and never fabricate a rerank score.
- Evidence Gate is deterministic and configuration-driven. Initial thresholds are labeled `uncalibrated-v1`; the response exposes `high`, `medium`, or `low` confidence plus score, signals, reasons, and threshold version.
- `answered` means deterministic Evidence Gate and Citation Validator passed. `degraded` means an answer may be returned with a visible conflict, incomplete provenance, or optional-provider degradation. `abstained` means evidence is insufficient or citations cannot be validated; no factual answer is retained. `failed` means the service or required model operation could not complete.
- Evidence Gate requires at least one reranked candidate, relevant evidence, traceable citation IDs, and required provenance (`documentId`, `documentVersion`, `sourceBlockIds`, and 1-based `page`) for high-confidence evidence. Missing page data is never guessed.
- Context Pack contains only the candidates actually sent to Answer Generator. Each entry receives a server-generated citation ID.
- Answer Generator accepts the query and Context Pack and returns structured JSON containing answer text, citation IDs, abstain flag, and claims with citation IDs. It cannot author quoted text, page numbers, or provenance.
- Citation Validator checks citation membership, claim coverage, and provenance. It uses provenance returned by the PGVector adapter. Validation failure produces `abstained`.
- Conflicting evidence may produce an answer only as `degraded`; the answer must state the conflict and cite each competing source.
- `/v1/answers` accepts `answerModelProfile` and `judge.enabled`; Judge is disabled by default for online blocking. Answer Generator failure returns `failed` with retrieval and Context Pack diagnostics but no fabricated answer.
- Langfuse instrumentation creates one Trace per request and Observations for `query_rewrite`, `dense_retrieval`, `bm25_retrieval`, `fusion`, `rerank`, `evidence_gate`, `context_assembly`, `answer_generation`, `citation_validation`, `answer_judge`, and `evaluation`.
- Observation payloads include request ID, query, profile snapshot, index/model versions, candidate IDs and ranks, statuses, durations, Context Pack, answer output, citation validation, and scores. No additional permissions, tenant model, or data-sensitivity validation system is introduced by this spec.
- Ragas runs offline for full datasets and asynchronously for optional production samples. It evaluates the actual reranked contexts, response, and optional reference. Ragas status is `sent`, `skipped`, `failed`, or `degraded`.
- LLM-as-a-Judge is a shadow/offline evaluator. Its input is query, Context Pack, answer output, Citation Validator result, and optional reference answer. Its output records faithfulness, answer relevancy, citation correctness/completeness, abstention recommendation, reasons, model version, status, and latency. Judge output does not change online answer status in V1.
- A human-labeled set contains query, relevant chunk IDs with 0–3 grades, reference answer, expected citations, and optional conflict/abstention label. It is used to calibrate thresholds and compare Judge decisions with human decisions.
- Deterministic metrics include Recall@K, Precision@K, MRR, nDCG@K, P50/P95/P99 latency, de-duplication rate, rerank gain/latency, image/table hit rate, and provenance completeness.
- Dependency versions are pinned to compatible concrete releases and installed from a lock file. CI connects to the configured Chunk Lab PostgreSQL/pgvector service; unavailable or schema-incompatible storage fails CI. External model integrations remain explicit and are not required for deterministic non-model checks.
- OpenAPI generation produces a TypeScript SDK with stable JSON types and HTTP methods only. The SDK contains no Python objects, database SDK types, retrieval algorithm, or server credentials.
- The implementation must not import, call, proxy, or fall back to historical TypeScript retrieval code.
- Chunk Lab opens the configured AgenticRetrival UI URL in a new tab without query parameters, request payloads, records, vectors, filters, secrets, model configuration, or return URL.
- Formal retrieval reads released records from `rag.vector_records_v2_d1024` through a read-only PGVector adapter. The adapter filters `deleted_at IS NULL`, preserves cross-document results, and never writes or re-embeds formal records.
- BM25 retrieval uses the installed `pg_search` BM25 index and `|||`/`pdb.score` query path. Before provisioning, `chunk_id` uniqueness is checked read-only; if it is not globally unique, a stable `retrieval_key` is required. If the configured BM25 index is absent or unavailable, the service returns an explicit degraded/failed status and never falls back to Python text scoring.
- Each deployment configures one controlled read-only Retrieval Source. The public API is source-neutral and global-only; the current adapter reads `rag.vector_records_v2_d1024`.
- Chunk Lab configures `AGENTIC_RETRIEVAL_UI_URL` per environment. AgenticRetrival does not hardcode Chunk Lab addresses, accept arbitrary return URLs, or require a Chunk Lab entry flow.
- The AgenticRetrival verification UI owns query, profile, rewrite, TopK, rerank, retrieval, answer, confidence, Context Pack, citations, provenance, timings, and provider status display. It uses only the generated TypeScript SDK and never accesses the vector index directly.
- Test queries and qrels remain deterministic, but default CI connects to the configured Chunk Lab PostgreSQL/pgvector service and fails when it is unavailable. Formal production retrieval never uses a snapshot or rebuilds an index. Real embedding smoke verifies query embedding against the configured formal PGVector data.

## Testing Decisions

- Tests exercise external behavior at the HTTP contract seam wherever possible. They should assert response shape, statuses, ordering, provenance, profile snapshots, version metadata, and failure behavior rather than private helper implementation.
- Contract tests cover OpenAPI availability, health, profile discovery, request validation, rejection of records in requests, all four profiles, rewrite modes, whitelist overrides, stable de-duplication, RRF ordering, and image/table provenance.
- Answer endpoint tests cover Context Pack membership, Answer Generator structured output, citation validation, missing-page abstention, conflict degradation, model failure, and preservation of retrieval diagnostics.
- Evaluation tests use a committed deterministic qrels fixture and assert exact reproducibility for Recall@K, Precision@K, MRR, nDCG@K, latency percentile shape, image/table hit rate, and provenance completeness.
- Optional integration tests use fake Rerank, Ragas, Langfuse, and Judge adapters to assert `sent`, `skipped`, `failed`, and `degraded` outcomes without network or paid credentials.
- Observability tests assert that a single request produces the required stage names and that `context_assembly` contains the exact reranked Context Pack passed to Answer Generator.
- SDK contract tests regenerate or inspect the OpenAPI-derived TypeScript client and assert the retrieve, answer, evaluation, and health methods without embedding retrieval logic.
- Threshold calibration tests compare deterministic confidence levels with human-labeled expected outcomes and record the threshold version; they must not claim calibration when only heuristic thresholds are available.
- HTTP tests are the highest seam. Unit tests are reserved for pure metric functions, tokenization/embedding fakes, normalization, and citation validation rules where an HTTP setup would obscure the behavior.
- The current repository implementation has retrieval and evaluation endpoints but not the `/v1/answers` flow. Tests for the answer contract must be red before implementation and green only after the answer path is implemented.
- Cross-service tests cover the Chunk Lab new-tab button, environment URL configuration, parameter-free navigation, PG availability, and the prohibition on browser-submitted records, vectors, filters, secrets, or model configuration.
- UI tests cover the single AgenticRetrival verification surface and its display of answer, status, confidence, Context Pack, citations, real page/sourceBlock/quotedText, stage timings, and provider states.

## Out of Scope

- Modifying unrelated parts of the Chunk Lab repository. Only the verification button, environment URL configuration, and new-tab navigation are in scope.
- Reusing, calling, proxying, or retaining TypeScript retrieval as a fallback.
- Running TS/Fastify and Python retrieval implementations as a dual track.
- Accounts, roles, permissions, authentication, tenants, multi-tenancy, knowledge-base administration, or back-office management.
- Mobile UI and mobile acceptance.
- Neo4j graph retrieval, image-to-image search, visual embeddings, Text-to-SQL, structured table filtering/aggregation/calculation, and arbitrary LangChain code execution.
- Agentic V2, LangGraph orchestration, autonomous retriever selection, multi-turn ReAct loops, checkpoints, or human-in-the-loop workflow.
- Answer generation from model memory or from chunks not included in Context Pack.
- Treating LLM-as-a-Judge, Ragas, or Langfuse as mandatory synchronous dependencies or as the sole online quality gate.
- Guessing page numbers, source blocks, document versions, quoted text, or citations when the index does not provide them.
- Claiming calibrated confidence before a human-labeled set has been evaluated.
- Building a second verification UI or retrieval implementation inside Chunk Lab.

## Further Notes

- The PRD/spec source path supplied in the earlier discussion was missing from the workspace; this spec therefore follows the confirmed PRD in the AgenticRetrival repository and the decisions recorded in the conversation.
- The highest testing seam is the public HTTP API. Index, model, rerank, Ragas, Langfuse, and Judge implementations should be replaceable behind server-side adapters without changing the OpenAPI contract.
- The service can return a formal answer while still exposing the exact retrieval and Context Pack diagnostics needed to audit why that answer was produced.
- `high` confidence is a quality classification, not a probability of truth. The response must preserve component signals and reasons so later calibration can revise thresholds without changing the public status vocabulary.
- Ragas and Langfuse are observability/evaluation integrations. Their outages must be visible in status fields but must not prevent deterministic retrieval, deterministic evaluation, or a valid answer that passed Evidence Gate and Citation Validator.
- The next implementation gate is to remove validation-task and product-specific scope fields, complete `/v1/answers` with real-provider and deterministic fake adapters, add Langfuse and asynchronous Ragas evaluation, then build the generated-SDK workbench and minimal Chunk Lab jump entry.
