# PGVector 就绪状态与工作台可用性改造（2026-11-17）

## 背景

`/healthz` 原来把「连接可用」和「语料结构就绪」合成一个 `available`：只要表结构没建好，整个工作台就显示「PGVector 不可用」并禁用全部按钮。实际原因是 Chunk Lab 尚未执行入库，数据库连接本身是正常的。服务不应该因为语料为空而表现得像故障。

本次改造只调整状态语义与工作台交互，**不改变数据读取范围、不写入数据库**。

## 修改前后对比

| 场景 | 修改前 | 修改后 |
| --- | --- | --- |
| 数据库连不上 / 密码错 | `available: false`，红点，按钮全禁 | `connectionAvailable: false`、`dataStatus: unreachable`，红点「数据库连接失败」，检索按钮仍可点，查询返回空结果 |
| 连接正常、表未建 | `available: false`，红点「不可用」，按钮全禁 | `connectionAvailable: true`、`schemaReady: false`、`dataStatus: schema_missing`，黄点「已连接 · 表结构未初始化」，按钮可点，接口返回 `statuses.source = schema_missing` |
| 连接正常、表在、0 条 | `available: true`，绿点「可用」 | `dataStatus: empty_corpus`，黄点「已连接 · 语料为空」，按钮可点 |
| 连接正常、有数据 | 绿点，可检索 | 绿点「PGVector 可用 · N 条」，可检索 |
| API 进程不可达 | 红点「API 不可用」，按钮全禁 | 不变 |

## `/healthz` 契约

`available` 语义保持不变（连接、表结构全部就绪才为 `true`），新增字段全部为加法：

```json
{
  "ok": true,
  "service": "python-rag-retrieval",
  "backend": "pgvector",
  "indexVersion": "pgvector:rag.vector_records_v2_d1024:qwen-embedding-1024",
  "available": true,
  "connectionAvailable": true,
  "schemaReady": true,
  "corpusCount": 12,
  "corpusCountCapped": false,
  "dataStatus": "ready"
}
```

- `connectionAvailable`：数据库连接与认证是否成功；
- `schemaReady`：表、`documents`、关键列、HNSW 索引是否齐全；
- `corpusCount`：符合检索范围（profile、维度、未删除）的记录数，最多统计 1000 条；
- `corpusCountCapped`：为 `true` 表示实际数量超过 1000；
- `dataStatus`：`ready` / `empty_corpus` / `schema_missing` / `unreachable`；
- `reason`：仅连接失败时返回 `credentials_invalid` 或 `connection_failed`，表结构问题不再与连接问题混用。

保留 `available` 原义是为了不破坏 CI 门禁（`.drone.yml` 仍要求 `available is true`）和已有 SDK 调用方。

## 检索接口行为

缺表时不再伪装成普通的「没有匹配证据」：

- `/v1/retrieve` 返回 `200`，`results: []`，`statuses.source = "schema_missing"`，`statuses.retrieval = "empty"`；
- `/v1/answers` 返回 `200`、`status = "failed"`，`reasons` 包含 `retrieval source schema missing`；
- 工作台在空结果区域显示「语料库未初始化，请先在 Chunk Lab 完成一次入库」。

表存在但 0 条数据时，接口正常返回空结果，工作台状态条显示「语料为空」。

## 改动文件

| 文件 | 改动 |
| --- | --- |
| `rag_service/backend.py` | 新增 `SchemaMissingError`；`_query` 识别 `42P01/3F000/42703`；健康检查拆分为 `_schema_ready()` + `_corpus_count()`，输出新字段 |
| `rag_service/pipeline.py` | 缺表路由标记为 `empty`；`statuses` 新增 `source`；缺表时 `retrieval = empty` |
| `rag_service/main.py` | 缺表时答案状态为 `failed`，原因写 `retrieval source schema missing` |
| `web/index.html` | 健康灯三态；仅 API 不可达时禁用按钮；评测按钮额外要求 `schemaReady`；缺表空结果提示 |
| `sdk/index.ts` / `scripts/generate_sdk.py` | `HealthResponse` 增加新字段与 `DataStatus` 类型 |
| `tests/test_api.py` | 覆盖连接失败、缺表、空语料、缺表检索/答案行为 |
| `docs/pgvector-unavailable-troubleshooting.md` | 增加本次改造说明 |
| `docs/workbench-guide.html` | 同步健康灯与按钮规则 |
| `docs/pgvector-online-verification.md` | 线上部署与验收步骤 |

## 验证

本地单元测试（不依赖真实数据库）：

```bash
python -m pytest tests/test_api.py -q -k "health or schema_missing or undefined_table or empty_corpus"
```

真实数据库验证（需要 Chunk Lab 已建表）：

```bash
curl -s http://127.0.0.1:8090/healthz
```

期望：`connectionAvailable: true`、`schemaReady: true`，`dataStatus` 为 `ready` 或 `empty_corpus`。

## Chunk Lab 侧配套改造

同一批次已在 `chunk-lab-main` 完成启动预初始化：`VectorRepository.ensureSchema()` + `prewarmDatabaseSchemas()`，在 `app.listen()` 前后台执行幂等迁移。表结构在 Chunk Lab 启动后即存在，数据仍由入库任务写入。实现细节和限制见 `chunk-lab-main/docs/pgvector-schema-prewarm-change.md`。

线上验证步骤见 [`pgvector-online-verification.md`](./pgvector-online-verification.md)。
