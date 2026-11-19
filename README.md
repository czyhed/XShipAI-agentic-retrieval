# Agentic Retrieval

文档导航：[docs/README.md](docs/README.md)。

独立 Python RAG 检索服务。正式检索由 Python 服务端通过只读 PGVector adapter 查询 Chunk Lab 发布的 PostgreSQL/pgvector 数据；默认 CI 连接真实 Chunk Lab PostgreSQL/pgvector，调用方不能提交检索 records 或向量。

## 启动

```powershell
uv sync --dev
uv run uvicorn rag_service.main:app --host 127.0.0.1 --port 8090
```

OpenAPI: `http://127.0.0.1:8090/docs`
健康检查: `GET /healthz`
工作台: `http://127.0.0.1:8090/`

正式检索配置：

```powershell
$env:RAG_RETRIEVAL_PROVIDER = "pgvector"
$env:RAG_PG_HOST = "your-pg-host"
$env:RAG_PG_PORT = "5432"
$env:RAG_PG_DATABASE = "your_database"
$env:RAG_PG_USER = "your_user"
$env:RAG_PG_SCHEMA = "rag"
$env:RAG_PG_VECTOR_TABLE = "vector_records_v2_d1024"
$env:RAG_PG_EMBEDDING_PROFILE_ID = "qwen3.7-text-embedding"
$env:RAG_EMBEDDING_DIMENSIONS = "1024"
$env:RAG_PG_BM25_INDEX = "vector_records_v2_d1024_bm25_idx"
$env:RAG_PG_BM25_KEY_FIELD = "retrieval_key"
uv run uvicorn rag_service.main:app --host 127.0.0.1 --port 8090
```

数据库密码必须通过本地 `.env` 或 CI Secret 注入。该连接只执行只读事务。正式 Chunk 向量由 Chunk Lab 发布流程写入一次，AgenticRetrival 不创建第二索引、不重新 embedding，也不写入向量表。

BM25 需要在 Chunk Lab 正式表上预先创建 `pg_search` 索引；正式迁移会创建全局唯一 `retrieval_key` 并建立 `vector_records_v2_d1024_bm25_idx`。可用 `scripts/check_pg_bm25_key.sql` 做只读核对，或用 `scripts/create_pg_bm25_index.sql` 补建索引。若索引不可用，BM25-only、Hybrid 和 Hybrid+Rerank 会返回明确的后端失败状态，不会降级为 Python 文本扫描。

## 测试

```powershell
uv run pytest -q
```

真实 embedding smoke 默认跳过；显式运行：

```powershell
$env:RAG_RUN_REAL_SMOKE = "1"
uv run pytest -q tests/test_real_model_smoke.py
```

评测 Run 通过 `POST /v1/evaluations/runs` 创建，并使用返回的 `runId` 轮询 `GET /v1/evaluations/runs/{runId}`。正式 Retrieval Source 不可用时，服务返回 `503`，不会切换到本地 fixture。

## Drone 部署

tag 触发 `.drone.yml` 后会依次执行真实 PG 验证、镜像构建和部署。Drone 需要配置 Secret `agentic_retrieval_pg_host`、`agentic_retrieval_pg_database`、`agentic_retrieval_pg_user` 和 `agentic_retrieval_pg_password`，部署主机需要提供 `/opt/agentic-retrieval/.env`（只保存服务端模型和可选 provider 配置，不提交到仓库）。容器名为 `agentic-retrieval`，监听 `127.0.0.1:8090`；只有容器内 `/healthz` 返回 `available=true` 才视为部署成功。

## TypeScript SDK

服务启动后运行（网络可用时）：

```powershell
npx --yes openapi-typescript@7.8.0 http://127.0.0.1:8090/openapi.json -o sdk/index.ts
```

无外网时可使用仓库内确定性生成器：

```powershell
uv run python scripts/generate_sdk.py
```

脚本也生成浏览器运行时 `sdk/index.js`；可用 `AGENTIC_RETRIEVAL_URL` 指定非默认服务地址。

SDK 只包含 OpenAPI 类型，不携带 LangChain 对象或服务端密钥。
