# 定时任务采集文章接入热点采集 · 接口文档示例

> 口径（2026-11 起）：**所有定时任务都是通用定时任务**，即用户脚本 `user-script.{scriptId}`；
> 内置任务类型（含旧的 `hot_topics.refresh`）已从代码移除，库里的历史行仅作兼容展示，不要照它接。
> 本文给出「用通用定时任务采集文章并接入热点采集」的接口契约与可运行示例。
>
> 基础路径：`/api/v1`　鉴权：`Authorization: Bearer <token>`（`POST /auth/login` 获取）
> 涉及权限点：`scheduled-script.admin`（脚本）、`schedules.use`（定时任务）、`hot_topics.view`（热点）

---

## 1. 接入模型

```
┌─ 采集源：文章接口 / 库内文章 ─────────────────────────────┐
│  ScriptAdapter.collect(ctx) -> CollectResult（HotRawItem[]）│  ← 新增文章源在这里
└──────────────────────────┬────────────────────────────────┘
                           │ 注册到 catalog.yaml（kind: script）
                           ▼
     通用定时任务脚本 run(params, ctx)
       · ctx.db            真实 DB 会话
       · app.services 全部可 import（进程内，无需 HTTP/token）
                           │
        hot_topics.refresh(ctx.db, platforms=["<文章源名>"])
                           │  采集 → 归一 → 去重 → 聚簇 → 评分 → 落库
                           ▼
      GET /hot-topics/clusters  /  /summary  /  /degraded
```

**两条触发路径，按场景选一条：**

| 路径 | 脚本内写法 | 冷却 | 鉴权 | 适用 |
|---|---|---|---|---|
| A·进程内直调（推荐） | `from app.services import hot_topics; hot_topics.refresh(ctx.db, ...)` | 无（定时路径本就不调 `reserve_refresh`） | 无需 token | 通用定时任务采集文章的主流接法 |
| B·HTTP 调接口 | `POST /api/v1/hot-topics/refresh` | 15s 冷却，命中返回 429 `too_frequent` | 需带 token | 跨服务/跨进程、必须走 HTTP 的场景 |

> 脚本运行在服务端进程内，有真实 DB 会话与全部依赖，路径 A 更省事也更可靠；
> 路径 B 的唯一价值是「跨进程」，代价是要处理鉴权与冷却。

---

## 2. 接口清单

### 2.1 用户脚本 `/schedules/scripts`（权限 `scheduled-script.admin`）

| 方法 | 路径 | 请求体 | 说明 |
|---|---|---|---|
| GET | `/schedules/scripts` | — | 我的脚本列表 |
| GET | `/schedules/scripts/examples` | — | 内置常用示例（含 `hot_snapshot`） |
| POST | `/schedules/scripts` | `{name, description?, draftCode, timeoutSeconds?, enabled?}` | 新建，仅存草稿 |
| PATCH | `/schedules/scripts/{id}` | 局部字段 | 改草稿/启停 |
| POST | `/schedules/scripts/{id}/test` | `{params?}` | 测 draft 代码，**savepoint 回滚不落库** |
| POST | `/schedules/scripts/{id}/publish` | `{draftCode?}` | 发布：编译+契约校验，写版本快照 |
| POST | `/schedules/scripts/{id}/run` | `{params?}` | 手动入队，返回 `{jobId}` |
| DELETE | `/schedules/scripts/{id}` | — | 删除脚本及其定时任务 |

### 2.2 通用定时任务 `/schedules`（权限 `schedules.use`）

| 方法 | 路径 | 请求体 | 说明 |
|---|---|---|---|
| GET | `/schedules/types` | — | 类型清单：仅已发布且启用的 `user-script.*` |
| POST | `/schedules` | 见下 | 新建；`taskType` 必须是 `user-script.{scriptId}` |
| PATCH | `/schedules/{id}` | 局部字段 | 改参数/时间/启停 |
| POST | `/schedules/{id}/run` | — | 立即执行一次，`source=manual` |
| GET | `/schedules/{id}/runs` | query: `limit/cursor/status` | 触发账本（keyset 分页） |
| POST | `/schedules/preview` | `{scheduleKind, cron?, intervalSeconds?, timezone?, count?}` | 预览未来 3 次触发时间 |

新建定时任务请求体：

```json
{
  "taskType": "user-script.<SCRIPT_ID>",
  "name": "采集文章 → 热点采集",
  "params": { "platforms": ["文章采集"], "limit": 50 },
  "scheduleKind": "cron",
  "cron": "0 8,12,18 * * *",
  "timezone": "Asia/Shanghai",
  "misfireGraceSeconds": 300,
  "enabled": true
}
```

约束：`interval` 下限 **60s**、上限 7 天；cron 五段；非法表达式直接 400，不落库。

### 2.3 热点采集 `/hot-topics`（权限 `hot_topics.view`）

| 方法 | 路径 | 请求/响应 | 说明 |
|---|---|---|---|
| GET | `/hot-topics/sources` | `{platforms[], enabled[], fallbackConfigured}` | 采集源清单与启用状态 |
| POST | `/hot-topics/refresh` | body `{date?}` → `HotRefreshOut` | 采集→聚簇→评分；429 `too_frequent` / 502 `all_sources_failed` |
| GET | `/hot-topics/clusters` | query: `date/minScore/track/platform/status/q/sort/limit` | 事件列表（按分数降序） |
| GET | `/hot-topics/clusters/{id}` | — | 详情：成员条目、名次采样、关联稿件 |
| GET | `/hot-topics/summary` | query `date?` | 素材/事件/≥70分/已出稿/赛道/平台分布 |
| GET | `/hot-topics/degraded` | query `date?` | 本轮数据不足的维度与原因 |

`HotRefreshOut` 响应示例：

```json
{
  "day": "2026-11-20",
  "capturedAt": "2026-11-20T08:00:12+08:00",
  "items": 128,
  "clusters": 36,
  "newClusters": 4,
  "matchedClusters": 32,
  "recommended": 7,
  "notes": ["文章采集：解析到 0 行，selector_may_changed"]
}
```

### 2.4 任务查询 `/jobs`

`GET /jobs/{jobId}` → `{id, queue, kind, status, progress, result, errorCode, errorMessage, ...}`；
终态 `succeeded / failed / cancelled / stopped`。手动「运行」只返回 `jobId`，靠轮询它取 `result`。

---

## 3. 端到端示例（curl）

```bash
BASE=http://localhost:8000/api/v1
TOKEN=$(curl -s -X POST $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"identifier":"you@example.com","password":"..."}' | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')
AUTH="Authorization: Bearer $TOKEN"
```

### 3.1 新建脚本（draftCode 见 §4）

```bash
curl -s -X POST $BASE/schedules/scripts -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{
    "name": "采集文章 → 热点采集",
    "description": "定时拉取文章源并触发热点采集、聚簇、评分",
    "draftCode": "<把 §4 脚本原文粘到这里>",
    "timeoutSeconds": 120,
    "enabled": true
  }'
# → {"id":"<SCRIPT_ID>", "publishedVersion":0, ...}
```

### 3.2 测试验证（不落库）+ 发布

```bash
curl -s -X POST $BASE/schedules/scripts/<SCRIPT_ID>/test -H "$AUTH" \
  -H 'Content-Type: application/json' -d '{"params":{"platforms":["文章采集"]}}'
# → {"ok":true,"result":{"summary":"...","clusters":..},"stdout":"","stderr":"","error":null}

curl -s -X POST $BASE/schedules/scripts/<SCRIPT_ID>/publish -H "$AUTH" \
  -H 'Content-Type: application/json' -d '{}'
# → {"publishedVersion":1,"publishedHash":"...", ...}
```

> 先测后发：测试走 savepoint，`hot_topics.refresh` 里的 `db.commit()` 只释放 savepoint，
> 退出统一回滚，**不会真的落库**；发布后定时任务才拿得到代码。

### 3.3 建定时任务并跑一次

```bash
curl -s -X POST $BASE/schedules -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"taskType":"user-script.<SCRIPT_ID>","name":"采集文章 → 热点采集",
       "params":{"platforms":["文章采集"]},"scheduleKind":"cron",
       "cron":"0 8,12,18 * * *","timezone":"Asia/Shanghai","enabled":true}'

curl -s -X POST $BASE/schedules/<TASK_ID>/run -H "$AUTH"
# → {"jobId":"<JOB_ID>", ...}

curl -s $BASE/jobs/<JOB_ID> -H "$AUTH"
# → 轮询到 status=succeeded，result 即脚本返回值
```

### 3.4 看结果

```bash
curl -s "$BASE/hot-topics/summary" -H "$AUTH"
curl -s "$BASE/hot-topics/clusters?minScore=55&sort=score&limit=20" -H "$AUTH"
```

---

## 4. 通用定时任务脚本模板

契约：`def run(params, ctx) -> dict`；`ctx.db` / `ctx.owner_user_id` / `ctx.task_id` / `ctx.run_id` / `ctx.job_id` / `ctx.task_type`；
可用帮助函数 `get_params()` / `get_context()` / `get_workspaces()` / `create_draft()` / `log()`。
界面摘要优先取返回值的 `summary`；返回含 `clusters` 时自动摘要成「采集 N 条 · 归并 M 个事件」。

```python
"""采集文章 → 接入热点采集（通用定时任务 / 用户脚本）。

前提：文章采集器已按 §5 注册为 catalog.yaml 的 script 源（例：name=文章采集）。
"""
from app.services import hot_topics


def run(params, ctx):
    platforms = params.get("platforms") or ["文章采集"]
    if not isinstance(platforms, list) or not platforms:
        raise ValueError("params.platforms 必须是非空数组")

    # 进程内直调服务层：无 HTTP、无 token、无 15s 冷却。
    # refresh 内部完成 采集 → 归一 → 去重 → 聚簇 → 评分 → commit。
    result = hot_topics.refresh(
        ctx.db,
        day=params.get("date"),        # 可选；不传取今天
        platforms=platforms,           # 只跑指定源；不传跑全部启用源
    )
    return {
        "summary": (
            f"采集 {result['items']} 条文章 → {result['clusters']} 个事件，"
            f"新建 {result['newClusters']} 个，≥70 分 {result['recommended']} 个"
        ),
        **result,
    }
```

**若要走 HTTP（路径 B）**，把中间一段换成（需在脚本里拿到 token，且可能吃 429）：

```python
import os, requests

def run(params, ctx):
    resp = requests.post(
        os.environ["API_BASE"] + "/hot-topics/refresh",
        headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]},
        json={"date": params.get("date")},
        timeout=60,
    )
    if resp.status_code == 429:
        raise RuntimeError("热点刷新冷却中，稍后重试")
    resp.raise_for_status()
    return {"summary": "已触发热点采集", **resp.json()}
```

---

## 5. 新增文章采集源（ScriptAdapter 契约）

采集器**只产出** `HotRawItem[]`，不做去重/合并/评分/标题归一；单源失败返回 `ok=false`，不抛异常。

`app/hot_sources/scripts/articles.py`：

```python
from app.hot_sources.contracts import CollectResult, HotRawItem


def collect(ctx) -> CollectResult:
    """ctx.safe_get / ctx.config / ctx.now() 是全部可用能力。"""
    source = ctx.config
    fetched_at = ctx.now()
    try:
        resp = ctx.safe_get(source["url"], headers=source.get("headers"))
        payload = resp.json()
    except Exception as exc:
        return CollectResult(
            platform=source["name"], ok=False,
            notes=[f"{source['name']}：抓取失败（{type(exc).__name__}）"],
            error_code="fetch_failed", fetched_at=fetched_at,
        )

    items = [
        HotRawItem(
            platform=source["name"],
            title=str(row.get("title", "")),
            rank=row.get("rank"),
            hot=row.get("hot"),
            url=str(row.get("url", "")),
            captured_at=fetched_at,       # 统一用批次时刻，禁止各自 now()
        )
        for row in (payload.get("data") or [])
        if row.get("title")
    ]
    notes = [f"{source['name']}：selector_may_changed"] if not items else []
    return CollectResult(
        platform=source["name"], ok=True, items=items,
        notes=notes, fetched_at=fetched_at,
    )
```

`app/hot_sources/catalog.yaml` 追加一条（`id` 落库后不改，`name` 是 `platforms` 过滤值）：

```yaml
  - id: articles
    name: 文章采集
    kind: script
    adapter: app.hot_sources.scripts.articles:collect
    enabled: true
    timeout_seconds: 12
    retry: 1
    requires_ssrf_safe: true
    tags: [article, custom]
    url: "https://example.com/api/articles"   # 仅 HTTPS；可用 ${ENV} 或 url_env 覆盖
```

数据契约：

| 对象 | 字段 |
|---|---|
| `HotRawItem` | `platform`, `title`, `captured_at` 必填；`rank`, `hot`, `url`, `extra` 可选 |
| `CollectResult` | `platform`, `ok`, `items[]`, `notes[]`, `error_code?`, `fetched_at` |

---

## 6. 约定与常见坑

| 项 | 说明 |
|---|---|
| 冷却 | 仅 HTTP `POST /hot-topics/refresh` 走 15s 冷却（429）；脚本内直调 `refresh()` 不受限，靠 cron + 互斥槽位防并发 |
| 并发互斥 | `active_job_id` 原子槽位：上一轮没落定时再触发会写 `skipped`，不重复采集 |
| 幂等 | 条目 `item_uid = platform + title_norm` 唯一；簇按 fingerprint 复用；重复跑不产生重复数据 |
| 失败暂停 | 连续 3 次失败自动 `enabled=false` 并写 `pausedReason`；修好后 PATCH 恢复即清零计数 |
| 全源失败 | 所有源都失败 → `all_sources_failed`（HTTP 502 / 脚本内抛 `HotTopicsError`），不会返回「0 条」的假成功 |
| 快照 | 发布写版本快照，入队锁版本；排队期间改脚本不影响已入队的执行 |
| 写权限 | 定时任务统一 `scope=system`，写权限由 `SCHEDULER_SYSTEM_WRITE` 控制（默认 `dev` 仅开发环境可改） |
| 测试不落库 | `/scripts/{id}/test` 与 `/schedules/test-run` 都走 savepoint 回滚；要真落库用 `/scripts/{id}/run` 或定时任务 |

---

## 7. 参考实现位置

- 通用定时任务：`docs/scheduled-tasks.md`、`modules/schedules.py`、`services/scheduled_tasks.py`
- 用户脚本：`modules/script_schedules.py`、`services/script_runtime.py`、`resources/script_examples.py`
- 热点采集：`docs/hot-topics.md`、`modules/hot_topics.py`、`services/hot_topics.py`
- 采集源适配：`app/hot_sources/`（`contracts.py` / `catalog.yaml` / `adapters.py`）
