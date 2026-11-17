# PGVector 不可用排查记录（2026-11-17）

> 后续改造：2026-11-17 已实施健康状态拆分，工作台不再因为缺表或空语料禁用按钮。修改后的行为与接口契约见 [`pgvector-readiness-change.md`](./pgvector-readiness-change.md)。本文保留为当时排查过程的记录。

## 症状

工作台 `http://110.42.51.234:18305/` 右上角显示「PGVector 不可用」，检索、生成答案、对比、评测按钮全部禁用。

## 直接结论

登录网关后请求 `/healthz`，后端返回：

```json
{
  "ok": true,
  "service": "python-rag-retrieval",
  "backend": "pgvector",
  "available": false,
  "indexVersion": "pgvector:rag.vector_records_v2_d1024:qwen-embedding-1024",
  "reason": "schema_mismatch"
}
```

`reason` 只有三种：`credentials_invalid`、`connection_failed`、`schema_mismatch`。本次是第三种，说明**数据库连接和账号密码都正常，是表结构校验没通过**。

## 判定逻辑

`rag_service/backend.py` 的 `health()` 要求以下四项同时成立，任一失败都统一返回 `schema_mismatch`：

1. 表 `rag.vector_records_v2_d1024` 存在；
2. 表 `rag.documents` 存在；
3. 关键列齐全：`target_id`、`target_spu_id`、`deleted_at`、`embedding_profile_id`、`vector_dimensions`、`embedding`、`source_segments`、`source_document_version`；
4. 向量表上有包含 `embedding` 的 HNSW 索引。

## 根因

`rag.*` 表不是检索服务创建的，而是 Chunk Lab **入库任务**执行迁移时创建的：

- `chunk-lab-main/src/database-job.ts` → `initialize()` 调用 `migrationStatements()`
- `chunk-lab-main/src/database-schema.ts` 创建 `rag.documents`、`rag.vector_records_v2_d1024`、HNSW 索引和 BM25 索引

排查时 Chunk Lab 的两个批次文档状态都是 `done`，但批次详情里**没有 `databaseJob` 字段**，即只完成了解析和切块，**从未执行过「入库」**。因此目标库里没有这些表，检索端一探测就判定 `schema_mismatch`。

配置本身是对的：检索端 Embedding profile 为 `qwen-embedding-1024`，Chunk Lab 的 Embedding profile id 相同，维度均为 1024；目标数据库档案 `xshipai-pgvector`（schema 2.0）存在。

## 修复步骤

1. 打开 Chunk Lab（`18304`）→ 批次 `e8e270db-…` → 文档 `测试文件_表格跨页_NOVAC_PB20K-P65.pdf`；
2. 点击「开始入库」（或在集成弹窗选择 Embedding `qwen-embedding-1024` + 目标数据库 `测试知识库 · xshipai-pgvector`，提交向量记录）；
3. 看到「入库成功，处理完成」后刷新检索页（`/healthz` 只在页面启动时请求一次），应变为绿点「PGVector 可用」。

## 修复后仍不可用的排查

- **两端连的不是同一个库**：Chunk Lab 的 `DATABASE_PROFILES_JSON` 与检索容器的 `RAG_PG_HOST` / `RAG_PG_DATABASE` 必须指向同一个 PostgreSQL 的同一个 database（当前应为 `rag_platform`）。

  ```sh
  docker exec chunk-lab printenv | grep -E 'DATABASE_PROFILES_JSON|RAG_PG_'
  docker exec $(docker ps --format '{{.Names}}' | grep retrieval) printenv | grep RAG_PG
  docker exec shiploadai-postgres-1 psql -U xshipai -d rag_platform -c "\dn" -c "\dt rag.*"
  ```

- **BM25 索引依赖 ParadeDB**：迁移中的 `CREATE INDEX … USING bm25` 需要 `pg_search` 扩展。普通 `postgres`/`pgvector` 镜像没有它，入库会在最后一步报错；此时前面的表和 HNSW 索引可能已建好，健康检查反而变绿，但 `bm25-only` / `hybrid` 检索会返回 `PGVector BM25 index unavailable`。
- **UI 不展示原因**：前端健康标签是写死的「PGVector 不可用」，建议把 `reason` 放进 tooltip，减少一轮排查。

## 是否只有 Chunk Lab 写入后服务才「打开」

不是。`/healthz` 只是启动时执行的一次只读探测，不是开关：

- 后端 API 不读取 `health()` 结果，检索请求不会被服务端拦截；
- 排查时前端的禁用条件是 `available !== true`（`web/index.html` 的 `syncControls()`），这是当时「打不开」的直接原因；改造后只在 API 不可达时禁用，缺表/空语料改为明确提示；
- 表结构一旦被第一次入库创建，就会持久化在数据库卷里，**之后不再需要 Chunk Lab 写入也一直可用**，服务重启、容器重建都不影响。

## 之前已入库的数据还能查吗

可以，只要满足一个前提：**数据在当前配置读取的那张表里**。检索只读：

- 库 `RAG_PG_DATABASE`、schema `rag`、表 `rag.vector_records_v2_d1024`；
- 过滤条件：`embedding_profile_id = 'qwen-embedding-1024'`、`vector_dimensions = 1024`、`schema_version = '2.0'`、`r.deleted_at IS NULL`、`d.deleted_at IS NULL`。

因此：

- 之前写进同一张 `rag.vector_records_v2_d1024` 且 profile/维度一致的数据，**仍然可以查到，不需要重新入库**；
- 写进了别的数据库（例如 Chunk Lab 自己的 PostgreSQL）、别的表、旧版 `rag_vector_records_v1`（schema 1.0），或 profile/维度不一致的数据，当前服务读不到；
- 当前「不可用」只代表健康探测失败，不会删除或改写任何已有数据。

判断方法：入库一次让表结构建好之后，若健康检查变绿，但查不到历史内容，再用上面的 SQL 核对历史数据所在的库、表和 `embedding_profile_id`。
