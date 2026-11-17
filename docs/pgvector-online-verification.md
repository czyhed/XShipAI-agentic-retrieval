# PGVector 就绪改造线上验证步骤（2026-11-17）

适用环境：`110.42.51.234` 的 `/opt/shiploadai` 演示栈。检索入口 `http://110.42.51.234:18305/`，Chunk Lab 入口 `http://110.42.51.234:18304/`。

## 0. 部署顺序

先发 Chunk Lab（建表），再发检索端（读取新健康字段）：

```bash
ssh root@110.42.51.234
cd /opt/shiploadai
bash deploy-app.sh chunk-lab
bash deploy-app.sh retrieval
```

`deploy-app.sh` 失败时会打印日志和回滚命令；先处理失败，不要继续下一步。

## 1. 检查容器状态

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'chunk-lab|retrieval|postgres'
```

预期两个应用都是 `Up`，PostgreSQL 为 `healthy`。

## 2. 检查 Chunk Lab 启动日志

```bash
docker logs --tail 100 $(docker ps --format '{{.Names}}' | grep chunk-lab)
```

- 没有 `PGVector schema pre-initialization failed`：预初始化成功。
- 出现该 warning：看同一行的 `profileId` 和错误。常见原因是 `pg_search` 扩展缺失（BM25 索引创建失败），此时表、`documents`、HNSW 索引通常已经建好，检索健康检查仍可通过，但 BM25/Hybrid 检索会失败。

## 3. 检查数据库结构

先从 `.env` 确认库名、账号与 profile 配置（不要回显密码到聊天）：

```bash
grep -E 'DATABASE_|RAG_PG_|DATABASE_PROFILES_JSON' /opt/shiploadai/.env
```

再查表：

```bash
docker exec shiploadai-postgres-1 psql -U <user> -d <database> -c "\dt rag.*" -c "\di rag.*"
```

预期至少存在：

- `rag.documents`
- `rag.vector_records_v2_d1024`
- `rag.vector_records_v2_d1024_embedding_cosine_idx`（HNSW）
- `rag.vector_records_v2_d1024_bm25_idx`（BM25，需 `pg_search`）

## 4. 检查检索服务健康检查

绕开网关，直接在服务器容器内请求：

```bash
docker exec $(docker ps --format '{{.Names}}' | grep retrieval) \
  python -c "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8090/healthz')), ensure_ascii=False, indent=2))"
```

### 预期结果

| 状态 | 关键字段 | 工作台表现 |
| --- | --- | --- |
| 表已建、0 条数据 | `connectionAvailable: true`、`schemaReady: true`、`corpusCount: 0`、`dataStatus: empty_corpus` | 黄点「已连接 · 语料为空」 |
| 表未建 | `connectionAvailable: true`、`schemaReady: false`、`dataStatus: schema_missing` | 黄点「已连接 · 表结构未初始化」 |
| 数据库连不上 | `connectionAvailable: false`、`dataStatus: unreachable`、`reason` 为 `connection_failed` 或 `credentials_invalid` | 红点「数据库连接失败」 |
| 有数据 | `schemaReady: true`、`corpusCount > 0`、`dataStatus: ready` | 绿点「PGVector 可用 · N 条」 |

`available` 只有在表结构和连接都正常时才为 `true`；新增字段用于区分具体状态。

## 5. 检查检索接口

```bash
docker exec $(docker ps --format '{{.Names}}' | grep retrieval) \
  python -c "import json,urllib.request; req=urllib.request.Request('http://127.0.0.1:8090/v1/retrieve', data=json.dumps({'query':'USB-C 快充','profileId':'hybrid','topK':5}).encode(), headers={'content-type':'application/json'}); print(json.dumps(json.load(urllib.request.urlopen(req)), ensure_ascii=False, indent=2))"
```

预期：

- 表未建：HTTP 200，`results: []`，`statuses.source: "schema_missing"`，`statuses.retrieval: "empty"`；
- 表已建、无数据：HTTP 200，`results: []`，`statuses.source: "ok"`，`statuses.retrieval: "ok"`；
- 有数据：`results` 非空。

## 6. 浏览器验收

1. 打开 `http://110.42.51.234:18300/`，用 `xshipai` 登录；
2. 从桌面打开「智能检索」；
3. 确认右上角健康灯符合第 4 步预期；
4. 点击「运行检索」：按钮可用；缺表时结果区显示「语料库未初始化」，空语料时显示「没有匹配证据」；
5. 切到「评测」页：表结构未就绪时「创建评测任务」为禁用；就绪后可用；
6. 刷新页面会重新请求 `/healthz`，健康灯随之更新。

## 7. 首次入库后复验

1. 打开 Chunk Lab，对目标文档执行「开始入库」；
2. 看到「入库成功，处理完成」；
3. 回到检索页刷新，健康灯应为「PGVector 可用 · N 条」；
4. 再跑一次第 5 步的检索，确认 `results` 非空。

## 8. 回滚

```bash
cd /opt/shiploadai
grep -n 'IMAGE_.*RETRIEVAL\|IMAGE_.*CHUNK' .env
# 把对应 IMAGE 标签改回上一版本后：
docker compose --env-file .env -f docker-compose.yml up -d --force-recreate retrieval
# 如 Chunk Lab 也需要回滚：
docker compose --env-file .env -f docker-compose.yml up -d --force-recreate chunk-lab
```

历史标签记录在 `/opt/shiploadai/backups/deploy.log`。

## 9. 验收清单

- [ ] Chunk Lab 容器 Up，启动日志无预初始化错误
- [ ] `rag.documents`、`rag.vector_records_v2_d1024`、HNSW 索引存在
- [ ] `/healthz` 返回 `connectionAvailable`、`schemaReady`、`corpusCount`、`dataStatus`
- [ ] 缺表或空语料时检索按钮可用，结果区有明确原因
- [ ] API 不可达时才禁用检索按钮
- [ ] 首次入库后健康灯变绿，检索返回结果
