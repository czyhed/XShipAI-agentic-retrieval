# 通用 RAG 检索组件 PRD V1

## 1. 文档状态

- 版本：PRD V1
- 状态：已确认，已授权实施
- 适用对象：需要复用检索能力的内部产品
- 首个接入方：Chunk Lab
- 核心实现：Python 检索服务 + OpenAPI + TypeScript SDK
- 技术基线：LangChain 1.x；Agentic V2 使用 LangGraph 1.x
- 观测与实验：Langfuse
- 评测引擎：Ragas + 确定性评测器

本 PRD 只定义检索与答案组件，不修改 Chunk Lab 已确认的文档解析、切分、人工修订和导出语义。Chunk Lab 只增加一个读取 `AGENTIC_RETRIEVAL_UI_URL` 的新标签页跳转入口，不创建验证任务、不传递业务参数，也不得保留或执行 TS 检索逻辑。

## 2. 产品目标

1. 验证 Chunk Lab 生成的 Chunk 是否能够被有效召回和排序。
2. 将检索能力抽象为其他产品可复用的通用组件。
3. 支持可配置的查询改写、Dense 召回、BM25 召回、粗排和可选精排。
4. 让图片摘要和表格摘要参与普通检索，并保留原始来源引用。
5. 支持离线评测、Profile 对比和线上链路诊断。
6. 生成有证据约束、可验证引用的正式答案，并让检索到答案的全过程可监测。
7. 为后续 LLM 自主编排和 ReAct 循环保留稳定的模块接口。

## 3. V1 范围

### 3.1 包含

- Query Rewrite：`none`、`multi_query`；
- Dense Retriever；
- BM25 Retriever；
- 图片摘要检索；
- 表格摘要检索；
- 候选去重；
- RRF 粗排；
- 外部模型 API 精排；
- Answer Generator 正式答案生成；
- Evidence Gate 证据质量门控；
- Citation Validator 引用校验；
- 答案置信度等级、可解释信号和 `answered/degraded/abstained/failed` 状态；
- 精排后 Context Pack 作为 Answer Generator 的唯一上下文；
- LLM-as-a-Judge shadow 评测与人工标注集校准接口；
- 精排开关；
- 召回路由开关；
- 各阶段 TopK 配置；
- Ragas 语义评测；
- Recall@K、Precision@K、MRR、nDCG@K 等确定性指标；
- Langfuse Trace、Dataset Run 和 Score 记录；
- REST API 和自动生成的 TypeScript SDK。
- AgenticRetrival 通用检索工作台，以及 Chunk Lab 到该界面的无参数最小跳转入口；
- AgenticRetrival 通过 Python 服务端只读 PGVector adapter 查询 Chunk Lab 正式 PostgreSQL/pgvector 数据。

### 3.2 不包含

- Neo4j 图谱检索；
- 以图搜图或视觉 Embedding；
- 表格条件过滤、聚合和计算；
- Text-to-SQL；
- LLM 自主选择流程；
- 多轮 ReAct 检索循环；
- 任意 LangChain 代码执行；
- 多租户、账号、权限和生产知识库管理。
- 多数据源选择器、动态字段映射或浏览器上传 qrels。

## 4. 技术路线与版本策略

### 4.1 LangChain 与 LangGraph 分工

V1 是确定性的检索链路，使用 LangChain 1.x 的 Runnable、Retriever 和模型接口完成模块组合，不直接依赖原始 LangGraph 图编排。

V2 增加 Agentic Orchestrator，使用 LangGraph 1.x 管理有状态流程、循环、检查点、暂停恢复、超时和人工介入。LangChain 1.x 的 Agent API 可作为高层入口；需要精确控制状态和边界时使用显式 `StateGraph`。

```text
V1:
API → Pipeline Profile → LangChain Runnable Retrieval Pipeline → Evidence Gate → Context Pack → Answer Generator → Citation Validator → AnswerResult

V2:
API → LangGraph Agentic Orchestrator → V1 Pipeline Tools → RetrievalResult
```

V2 不使用已弃用的 `create_react_agent`，循环必须有最大步数、超时、费用和重复调用限制。

### 4.2 依赖版本

- LangChain 相关核心包使用 1.x 主版本范围；
- LangGraph 仅作为 Agentic V2 的运行时依赖，使用 1.x 主版本范围；
- Ragas、Langfuse 及模型供应商适配包选择与当前 LangChain 1.x 兼容的版本；
- 不使用无约束的 `latest`；安装时解析具体版本并写入锁文件；
- CI 必须基于锁文件安装，升级需要单独的依赖变更记录；
- 不使用 `langchain-classic`，除非后续确实需要迁移遗留链路。

## 5. V1 用户场景

### 5.1 单次检索

调用方提交 Query 和 Pipeline Profile，得到排序后的候选 Chunk、阶段分数和来源证据。

### 5.2 正式答案

调用方提交 Query 和 Pipeline Profile，服务完成检索、精排和证据门控后，将精排后的 Context Pack 发送给 Answer Generator，返回正式答案、可验证引用、置信度和全过程状态。Answer Generator 不得使用 Context Pack 之外的事实性内容。

### 5.3 Profile 对比

调用方对同一 Query 或评测集运行不同 Profile，比较召回、排序、延迟和精排收益。

### 5.4 Chunk Lab 跳转

Chunk Lab 只提供一个“检索验证”按钮，在新标签页打开纯 `AGENTIC_RETRIEVAL_UI_URL`。浏览器不携带 validation task、records、向量、过滤条件、密钥、模型配置或 return URL。AgenticRetrival 也可独立打开，并通过 Python 只读 adapter 查询当前部署配置的正式 Retrieval Source。

## 6. V1 检索链路

```text
Query
  ↓
Query Rewrite
  ↓
Dense Retriever ─┐
                 ├─ 候选标准化 → 去重 → RRF 粗排
BM25 Retriever ──┘                         ↓
                                   可选外部 Rerank API
                                             ↓
                                      Evidence Gate
                                             ↓
                                        Context Pack
                                             ↓
                                      Answer Generator
                                             ↓
                                     Citation Validator
                                             ↓
                                      Answer / Abstain
```

图片和表格不建立独立 Retriever：

- 图片的 caption、OCR、VLM 摘要和关联事实形成可检索摘要；
- 表格的标题、表头、摘要和必要关联信息形成可检索摘要；
- 两者都进入 Dense/BM25 的普通索引；
- 返回结果必须保留 `contentType` 和原始资产/来源引用。

## 7. Pipeline Profile

### 7.1 Profile 示例

```json
{
  "id": "hybrid-rerank-v1",
  "version": 1,
  "rewrite": {
    "enabled": true,
    "strategy": "multi_query",
    "maxQueries": 3
  },
  "retrievers": ["dense", "bm25"],
  "dense": {
    "topK": 50
  },
  "bm25": {
    "topK": 50
  },
  "fusion": {
    "strategy": "rrf",
    "rrfK": 60,
    "topK": 30
  },
  "rerank": {
    "enabled": true,
    "profileId": "external-rerank-v1",
    "candidateTopK": 30,
    "outputTopK": 10
  }
}
```

### 7.2 可配置项

- 是否启用 Query Rewrite；
- Rewrite 策略和最大改写数量；
- 启用 `dense`、`bm25` 或两者；
- Dense 和 BM25 各自的 TopK；
- RRF 的 `rrfK` 和粗排 TopK；
- 是否启用精排；
- 精排 Profile；
- 精排候选数量和最终输出数量。

### 7.3 请求级覆盖

请求可以覆盖白名单字段，但不能提交任意代码：

```json
{
  "profileId": "hybrid-rerank-v1",
  "overrides": {
    "retrievers": ["dense"],
    "rerank.enabled": false,
    "dense.topK": 20
  }
}
```

覆盖值必须经过服务端校验，并记录到本次运行的 Profile Snapshot。

## 8. 模块契约

### 8.1 Query Rewrite

输入原始 Query，输出一个或多个改写 Query。必须保留原始 Query，不得静默替换。

```text
RewriteResult:
  originalQuery
  rewrittenQueries[]
  strategy
  modelProfile
  latencyMs
```

### 8.2 Retriever

每个 Retriever 接收一个 Query，返回标准化候选，不暴露底层数据库 SDK 类型。

```text
RetrieverResult:
  retriever
  query
  candidates[]
  latencyMs
  indexVersion
```

V1 只注册 `dense` 和 `bm25`。未来的 Graph Retriever 可以作为新增注册模块，不改变公共候选契约。

### 8.3 粗排

- 合并所有启用召回路的候选；
- 按稳定的 `chunkId` 去重；
- 使用 RRF，不直接混合 Dense 和 BM25 原始分数；
- 记录各路排名、命中次数和融合分数。

### 8.4 精排

- 只接收粗排后的候选；
- 调用外部 Rerank API；
- API 密钥只保存在 Python 服务端；
- 精排失败时必须返回明确状态，不得伪造精排分数；
- 关闭精排时直接返回粗排结果。

### 8.8 正式 Retrieval Source

V1 每个部署只配置一个受控、只读 Retrieval Source，不提供数据源选择器或知识库管理。当前正式 adapter 查询 `rag.vector_records_v2_d1024`。公共 API 和工作台只提供全局检索，不暴露 Chunk Lab 专用的 `targetId`、`targetSpuId` 或其他商品过滤字段。

### 8.5 Evidence Gate

Evidence Gate 判断精排候选是否足以支持正式答案。初始规则为配置化启发式阈值，记录 `thresholdVersion: uncalibrated-v1`，不得声称已人工校准。至少检查：

- 至少有一个精排后候选；
- 至少一个候选具备 `documentId`、`documentVersion`、`sourceBlockIds` 和真实 1-based `page`；
- 候选与原始 Query 达到服务端相关性阈值；
- Context Pack 中每个引用 ID 均可追溯；
- 未检测到无法解释的关键冲突。

门控结果使用 `answered`、`degraded`、`abstained`、`failed`。`abstained` 必须返回固定拒答文本和原因，不得用模型常识补全答案。

### 8.6 Context Pack 与 Answer Generator

Context Pack 只包含实际发送给 Answer Generator 的精排后候选，每个候选分配服务端生成的 `citationId`。Answer Generator 只返回结构化 JSON：

```json
{
  "answer": "最高支持 100W 快充。",
  "citations": ["S1"],
  "abstain": false,
  "claims": [{"text": "最高输出为 100W", "citations": ["S1"]}]
}
```

服务端校验 citation ID 必须属于本次 Context Pack；每个事实性 claim 至少有一个 citation；模型不得生成或修改 `quotedText`、页码或 provenance。模型输出解析失败时不得把原始文本直接作为正式答案。

### 8.7 Citation Validator

Citation Validator 从正式 PostgreSQL/pgvector 记录回填真实文档、版本、页码、源 Block 和引用文本。页码必须来自正式记录，统一为原始文档 1-based 页码；缺失页码不得伪造。校验失败时返回 `abstained`，不保留未经验证的事实答案。

## 9. 统一候选模型

```json
{
  "chunkId": "table-001",
  "contentType": "text | image | table",
  "text": "USB-C 最大输出 100W",
  "rank": 1,
  "finalScore": 0.982,
  "stageScores": {
    "dense": 0.91,
    "bm25": 8.42,
    "rrf": 0.033,
    "rerank": 0.982
  },
  "provenance": {
    "documentId": "doc-001",
    "documentVersion": "v8",
    "sourceBlockIds": ["block-31"],
    "page": 4
  },
  "assetRefs": [],
  "tableRef": "table-version-08"
}
```

要求：

- `text` 是用于排序和展示的安全文本；
- `contentType` 必须来自服务端索引记录；
- 图片结果可携带 `assetRefs`；
- 表格结果可携带 `tableRef`；
- 高置信度答案必须具备真实 `documentId`、`documentVersion`、`sourceBlockIds`、1-based `page` 和服务端回填的引用文本；
- 不把原始 HTML、诊断字段、请求头或密钥放入候选文本。

### 9.1 答案置信度

对外返回 `high`、`medium`、`low` 等级及综合 `score`、`thresholdVersion`、信号和原因。信号至少包含检索相关性、精排头部间隔、证据覆盖率、provenance 完整性和冲突标记。`score` 是质量门控综合分，不是答案正确率；初始阈值记录为 `uncalibrated-v1`，后续使用人工标注集校准。

## 10. API 契约

### 10.1 检索

```http
POST /v1/retrieve
```

```json
{
  "query": "这款产品支持多少瓦快充？",
  "profileId": "hybrid-rerank-v1",
  "overrides": {
    "rerank.enabled": false
  },
  "topK": 10
}
```

响应至少包含：

- `requestId`；
- 原始 Query 和改写 Query；
- 实际使用的 Profile Snapshot；
- 每路召回数量；
- 粗排和精排数量；
- 最终候选及 provenance；
- 各阶段耗时、模型和索引版本；
- 明确的失败或降级状态。

### 10.2 评测

```http
POST /v1/evaluations/runs
```

```json
{
  "datasetId": "chunk-lab-eval-v1",
  "profileId": "hybrid-rerank-v1"
}
```

### 10.3 正式答案

```http
POST /v1/answers
```

请求复用 `query`、`profileId`、`overrides`、`topK`，并支持 `answerModelProfile` 与 `judge.enabled`。响应至少包含 `status`、`answer`、`citations`、`confidence`、检索结果、实际 Context Pack、各阶段状态和失败原因。Answer Generator 失败时返回 `failed`，保留检索结果和 Context Pack，但不伪造答案；证据不足时返回 `abstained`。

### 10.4 评测 Run

`POST /v1/evaluations/runs` 创建异步 Evaluation Run，立即返回 `runId` 和运行状态。确定性指标先完成；Ragas 仅在显式启用时离线执行，并通过 `GET /v1/evaluations/runs/{runId}` 查询 `running/completed/failed` 及 provider 状态。服务端注册数据集，浏览器不能上传任意 qrels。

## 11. 评测设计

### 11.1 qrels

```json
{
  "query": "这款产品支持多少瓦快充？",
  "relevant": [
    { "chunkId": "table-001", "relevance": 3 },
    { "chunkId": "text-022", "relevance": 2 }
  ]
}
```

相关等级使用 0-3：

- `0`：不相关；
- `1`：弱相关或辅助信息；
- `2`：相关但不完整；
- `3`：直接、完整回答所需信息。

### 11.2 主指标

- Recall@K；
- Precision@K；
- MRR；
- nDCG@K；
- P50/P95/P99 延迟；
- 候选去重率；
- 精排带来的指标增益和延迟增量。

### 11.3 Ragas 指标

Ragas 作为辅助评测器，使用 Context Precision、Context Recall、Context Relevancy、Faithfulness 和 Answer Relevancy。输入必须包含精排后实际发送的 `retrieved_contexts`、生成的 `response` 及可选 `reference`。离线 Dataset Run 全量运行，线上仅允许异步抽样；不可用时返回 `skipped/failed/degraded`，不阻断主答案。

### 11.4 人工标注集与 LLM-as-a-Judge

人工标注集是由人标注的“query—相关 Chunk—参考答案—预期引用”样本，用于校准置信度阈值并验证 Judge 与人工判断的一致性，不是权限或敏感性审核。

LLM-as-a-Judge 首期为 shadow 评测，默认关闭在线阻断。输入包括原始 Query、精排后 Context Pack、Answer Generator 输出、Citation Validator 结果和可选 reference answer；输出包括 `faithfulness`、`answerRelevancy`、`citationCorrectness`、`citationCompleteness`、`shouldAbstain`、`reasons`、`modelVersion` 和 `sent/skipped/failed/degraded` 状态。Judge 结果只写入评测和 Langfuse Score，不改变在线答案状态。

### 11.5 领域指标

- 图片摘要命中率；
- 表格摘要命中率；
- `sourceBlockIds` 完整性；
- 图片资产引用完整性；
- 表格版本引用完整性。

### 11.6 真实模型与假数据

查询、qrels、文档内容、图片摘要、表格摘要和测试样本使用 Chunk Lab 正式 PostgreSQL/pgvector 数据。正式数据向量只在 Chunk Lab 发布流程中生成一次；AgenticRetrival 不重新 embedding 或写入正式索引。默认 CI 必须连接真实 Chunk Lab PostgreSQL/pgvector，连接不可用或 schema 不匹配时失败。真实模型 smoke test 显式验证真实 query embedding 和正式 PGVector 查询，并可按配置验证 Rewrite、Rerank、Answer Generator、Judge、Ragas 和 Langfuse；缺少凭据返回 `skipped`，失败返回 `failed` 或 `degraded`，不得伪造答案、分数或引用。

## 12. Langfuse 观测

一次检索请求对应一条 Trace，阶段对应 Observation：

```text
Trace
  ├─ query_rewrite
  ├─ dense_retrieval
  ├─ bm25_retrieval
  ├─ fusion
  ├─ rerank
  ├─ evidence_gate
  ├─ context_assembly
  ├─ answer_generation
  ├─ citation_validation
  ├─ answer_judge
  └─ evaluation
```

记录：

- Query、改写 Query；
- Profile Snapshot；
- 索引版本和模型版本；
- 候选 ID、排名和分数；
- 每阶段耗时；
- 实际发送给 Answer Generator 的 Context Pack；
- Answer Generator 输出、模型版本、Token 和耗时；
- Citation Validator 结果；
- Ragas、确定性指标、人工评分和 Judge Score。

仅记录本 PRD 定义的业务观测字段，不额外引入权限、账号、租户或数据敏感性校验系统。Langfuse 不可用时，检索和答案主流程仍应可运行；观测失败只能产生 `skipped/failed/degraded` 状态。

## 13. TypeScript 对接

- Python 服务使用 FastAPI 暴露 REST API；
- 以 OpenAPI 生成 TypeScript SDK；
- TS 端只依赖 API 类型，不依赖 LangChain Python 对象；
- API 使用 `/v1` 版本前缀；
- 返回候选、分数、来源和阶段状态的稳定 JSON；
- 流式能力暂不属于 V1，后续 Agentic V2 再评估 SSE。
- 检索服务复用 Chunk Lab `.env` 中已配置的 Embedding Profile 和 Context 模型配置；网页端不接触模型密钥。
- 通用工作台属于 AgenticRetrival，采用 Operate 型工作界面；Chunk Lab 仅提供无参数新标签页跳转按钮。
- Chunk Lab 运行时只配置 `AGENTIC_RETRIEVAL_UI_URL`，不在业务代码中硬编码环境地址。

## 14. 安全与可靠性

- Rerank、Rewrite 和 Embedding 的密钥只存服务端；
- 不允许调用方传入任意 LangChain 代码或任意 Cypher；
- 所有 Profile 和 Retriever 必须来自服务端注册表；
- 请求级覆盖必须白名单校验；
- 外部模型超时、限流和错误必须隔离；
- 精排失败不得伪造排序分数；
- 历史运行保留 Profile Snapshot、模型版本和索引版本；
- 评测使用确定性 Fake，避免 CI 依赖付费 API。

## 15. V1 验收标准

1. 可以分别运行 Dense-only、BM25-only、Hybrid 三种 Profile。
2. 可以独立开启或关闭 Query Rewrite。
3. 可以独立开启或关闭精排。
4. 可以调整每路召回 TopK、粗排 TopK、精排候选数和最终输出数。
5. 图片摘要和表格摘要能够通过 Dense/BM25 被召回。
6. 检索结果保留 `contentType` 和 provenance。
7. 同一评测集可以比较不同 Profile。
8. Recall@K、MRR、nDCG@K 计算结果可复现。
9. Ragas 分数可以写入统一评测结果。
10. Langfuse 能看到一次请求的完整阶段轨迹。
11. Langfuse 或外部 Rerank 暂时不可用时，系统返回明确降级状态，不伪造成功。
12. OpenAPI 能生成可用的 TypeScript SDK。
13. `/v1/answers` 能返回正式答案、Context Pack、真实页码溯源和结构化 citations。
14. 精排后实际发送给 Answer Generator 的 Chunk 可在 Langfuse `context_assembly` Observation 中看到。
15. Citation Validator 能拒绝不存在或不可追溯的引用；证据不足时返回 `abstained`。
16. LLM-as-a-Judge、Ragas、Langfuse 不可用时，主检索和确定性答案流程仍可成功，并返回明确状态。
17. Chunk Lab 点击验证按钮后，在新标签页打开纯 `AGENTIC_RETRIEVAL_UI_URL`，不创建任务或传递参数。
18. 工作台只通过生成的 TypeScript SDK 调用 Python API，并展示答案、状态、confidence、Context Pack、真实页码、sourceBlockIds、quotedText、阶段耗时和外部服务状态。
19. 正式 Retrieval Source 不可用时禁止新运行，并明确显示失败；不得静默回退本地语料、旧结果或其他数据源。
20. 跨服务验收证明 Chunk Lab 不提交 records、向量或过滤参数，Python 服务是唯一检索实现。
21. 工作台包含检索、Profile 对比和评测三个视图；对比结果只保留在当前浏览器会话。
22. Langfuse 返回真实 trace 状态和受控 Trace 链接；Ragas 只通过异步离线 Evaluation Run 显式执行。

## 16. V2 Agentic 路线

V2 在 V1 Pipeline 之上增加 Agentic Orchestrator，不改变 Retriever 和评测契约：

```text
Query
  ↓
LLM 判断是否需要改写、Dense、BM25 或 Rerank
  ↓
调用 V1 Pipeline 工具
  ↓
Assess：信息是否足够
  ├─ 足够 → Finish
  └─ 不足 → Refine → 再次调用受限工具
```

V2 必须使用显式状态和边界：

- 最大循环次数；
- 最大工具调用次数；
- 单次请求超时；
- Token 和费用预算；
- 重复 Query/重复候选检测；
- 工具白名单；
- 每一步的输入、输出和原因；
- 可恢复状态和必要的人工中断。

V2 的 LangGraph 状态只保存结构化事实和候选证据，不把格式化日志作为状态源。Agent 可以选择 V1 Profile，但不能绕过 Profile 执行任意代码。

## 17. 实施顺序

1. 固化领域模型、全局检索和 OpenAPI 契约；
2. 实现索引适配和统一候选模型；
3. 实现 Dense、BM25 和 RRF 粗排；
4. 接入 Query Rewrite；
5. 接入外部 Rerank API 和开关；
6. 实现 Evidence Gate、Context Pack、Answer Generator 和 Citation Validator；
7. 实现 qrels、确定性指标和 Ragas Adapter；
8. 接入 Langfuse Trace、Dataset Run、Observation 和 Score；
9. 用 Chunk Lab 数据建立首个人工标注评测集并校准阈值；
10. 实现 AgenticRetrival 检索、对比和评测工作台；
11. 增加 Chunk Lab 无参数新标签页跳转入口；
12. 完成 TS SDK 和跨服务端到端答案验收；
13. V1 验收通过后，另立 V2 Agentic PRD。

本 PRD 已获得单独实现授权。
