# 热榜采集 · 通用平台接入规范（Adapter Spec）

> 状态：**规范草案，未实现**。本文是需求 01 的"规范文档"：定义统一数据契约与采集器接口，
> 目标做到**新增一个平台 = 提供一个符合契约的采集脚本 + 一行注册，不改采集主流程代码**。
> 关联现状：`apps/api/app/services/hot_sources.py`、`hot_topics.py`、`hot_ranking.py`，
> 定时任务链路 `app/resources/scripts/manifest.yaml` + `services/scheduled_tasks.py`。

---

## 1. 为什么要做（现状 RCA）

| 现状问题 | 证据 | 后果 |
|---|---|---|
| 无统一 Provider 抽象 | `hot_sources.py` 的 `collect()` 是 8 段近乎复制的 for 循环（直取/聚合/AIHOT/Markdown/微信10w/微信搜索/开源榜/UAPI） | 新增一类源要改函数体；超时/重试/并发策略无法统一 |
| 源目录无单一真相 | `DIRECT_SOURCES`/`AGG_SOURCES`/`UAPI_SOURCES`… 是散落模块常量；`docs/hot-topics.md` 写"7 路/11 平台"，代码 `ALL_PLATFORMS` 已约 22 个 | 文档漂移，接入方不知道该对齐谁 |
| 解析脆弱且静默成功 | HTML/Markdown 正则解析（微信搜索、开源榜、AIHOT agent），改版返回 `[]` 后只要有一路成功就不报错 | 榜单"悄悄少一个平台"，无告警 |
| 串行 + 无缓存 | 22 个请求串行，单源最长 12s；无 ETag/快照复用 | 冷启动刷新延迟大、每次全量重打 |
| 两条执行路径并存 | 热点 refresh/digest 既有 `job_runner._handle_hot_topics` 专用 handler，又有 manifest `runner` 薄封装 | 同一逻辑两份入口，易漂移 |
| 首页缺位 | `OverviewView.vue` 不消费任何热点接口，热榜只在 `/hot-topics` 等三处孤立出现 | 需求提到"首页热榜"，需补 `/dashboard` 聚合 |

**三条必须保留的不变量**（来自 `services/hot_topics.py` 文件头，重构不得破坏）：可复现（按 `captured_at` 评分）、幂等（`platform+title_norm` 身份、采样唯一、fingerprint 复用）、不伪造（单源失败记 notes、全源失败抛错）。

---

## 2. 统一数据契约（核心）

### 2.1 采集器输出：标准化行 `HotRawItem`

所有采集器（无论 JSON/HTML/Markdown/自定义脚本）**必须且只需**产出下列结构。这是"新平台按规范格式处理即可接入"的落点。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `platform` | string | ✅ | 平台标识，必须在源注册表登记（见 §3），如 `微博`、`B站` |
| `rank` | int \| null | ❌ | 榜内名次，从 1 开始；无名次语义可为 null |
| `title` | string | ✅ | 原始标题，1~300 字；采集器不做归一化（归一化由下游统一做） |
| `hot` | int \| null | ❌ | 热度值（原始量纲）；归一化由 `hot_ranking` 按平台内排名处理，采集器不换算 |
| `url` | string | ❌ | 原文链接，≤500 字符；无原文（如聚合源）留空字符串；采集器需去跟踪参数 |
| `captured_at` | string | ✅ | 采集时刻 ISO-8601（带时区），评分基准，禁止用 `now()` 事后重算 |
| `extra` | object | ❌ | 可选扩展字段（如摘要、作者、封面），下游忽略、不落主表 |

> 约束：采集器**只负责抓取与结构化**，不做去重、不做跨源合并、不做评分、不做归一化标题——这些是 `hot_topics._ingest` / `hot_ranking` 的职责，避免两套逻辑。

### 2.2 采集器返回：批次结果 `CollectResult`

```jsonc
{
  "platform": "微博",
  "ok": true,
  "items": [ /* HotRawItem[] */ ],
  "notes": [],            // 降级/部分失败说明，会进入 /hot-topics/degraded
  "error_code": null,     // ok=false 时必填，如 fetch_failed / parse_empty / ssrf_blocked
  "fetched_at": "2026-11-19T08:00:00+08:00"
}
```

- **单源失败不抛异常**：返回 `ok=false` + `error_code`，由编排器汇总进 notes。
- **空列表要显式标注**：`parse_empty`（页面结构可能改版）与"平台本来就没数据"要能区分——建议采集器在解析到 0 行时填 `notes: ["selector_may_changed"]`，供健康检查预警。

---

## 3. 源注册表（单一真相源）

新增一个 `hot_sources/catalog.yaml`（或 Python 常量模块），每个平台一条声明，替代当前散落的 `*_SOURCES` 元组：

```yaml
version: 1
sources:
  - id: weibo                      # 稳定标识，写入 hot_items.platform，落库后不改
    name: 微博                      # 展示名
    kind: json                     # json | html | markdown | script
    adapter: app.hot_sources.weibo:collect   # 采集器入口，见 §4
    enabled: true
    timeout_seconds: 12
    tags: [social, general]
    # 以下为通用策略默认值，采集器可覆盖；编排器统一执行
    retry: 1
    requires_ssrf_safe: true       # 强制走 core/ssrf.safe_request（HTTPS/443/不跟随跳转）
  - id: zhihu
    name: 知乎
    kind: json
    adapter: app.hot_sources.zhihu:collect
    enabled: true
```

- `enabled_platforms()` 与 `source_catalog()` 统一读此表 → 修复"`HOT_TOPICS_PLATFORMS` 白名单 vs 代码常量"的漂移。
- `docs/hot-topics.md` 的平台表改为从此表生成或明确指向它，杜绝手写清单。

---

## 4. 采集器接口（Adapter Interface）

### 4.1 契约（Python）

```python
def collect(ctx: CollectContext) -> CollectResult:
    """ctx 提供受控能力：safe_get / safe_post / config / now。
    只返回 CollectResult，不直接写库、不抛未捕获异常。"""
```

`CollectContext` 由编排器注入，至少提供：

| 能力 | 说明 |
|---|---|
| `ctx.safe_get(url, headers=...)` | 走 `core/ssrf.safe_request`，统一超时/重试/UA；采集器不自行 `requests.get` |
| `ctx.config` | 该源在 catalog 里的声明 + 环境变量（如微信快照 URL、关键词） |
| `ctx.now()` | 统一采集时刻，保证同一批次 `captured_at` 一致 |

### 4.2 三类内置采集器模板（覆盖现有全部源）

| 模板 | 适用 | 复用现状 |
|---|---|---|
| `JsonAdapter` | 直取 JSON / 聚合 JSON / AIHOT JSON / UAPI | `hot_sources._rows_direct/_rows_agg/_rows_aihot/_rows_uapi`，配置 `items_path`、`title_key`、`hot_key`、`url_key`、`rank` 策略 |
| `TextPatternAdapter` | Markdown / HTML 正则 | `_rows_aihot_agent` / `_rows_wechat_search` / `_rows_openrank`，配置 selector/正则；解析 0 行报 `parse_empty` |
| `ScriptAdapter` | **自定义数据处理脚本** | 见 §5，加载用户脚本函数，输出 `HotRawItem[]` |

### 4.3 编排器改造

把 `hot_sources.collect()` 的 8 段循环替换为：

```
for source in catalog.enabled_sources(platforms_filter):
    result = run_with_timeout_retry(source.adapter, ctx)   # 统一策略
    merge result.items / result.notes / result.error_code
    # 可选：并发（ThreadPool，默认 4~6 路），受 SSRF 逐跳校验约束
```

- 保留"全源失败抛 `HotTopicsError(all_sources_failed)`"的语义。
- 新增能力：并发度可配、响应快照（按日+源缓存原始响应，失败时回落上一次快照并在 notes 标注 `stale`）。

---

## 5. 自定义数据处理脚本（用户可扩展）

需求明确提到"自定义数据处理脚本"。**安全边界**：现有 manifest 的设计哲学是"受控的进程内扩展点，不是在线执行任意代码"（`docs/scheduled-tasks.md §2.1`）。据此给两种方案：

| 方案 | 机制 | 适用 | 安全 |
|---|---|---|---|
| A（推荐，受控） | 脚本模块放进 `app/hot_sources/scripts/`，在 catalog 注册 `kind: script` + `adapter: 模块:函数`；随代码部署、加载时 import 校验 | 可信维护者新增平台 | 高：走代码评审 + 受控 ctx |
| B（谨慎，在线） | DB 存脚本文本，运行时受限执行（沙箱） | 终端用户自助写脚本 | 低：需沙箱、资源限额、禁止文件/网络穿越 SSRF；**不建议首期做** |

**首期采用 A**：与 manifest "写脚本模块 + 一行声明、重启生效"的既有范式完全一致，用户/维护者按 §2 契约产出 `HotRawItem[]` 即可，不改采集主流程。

---

## 6. 定时任务接入（每天定时拉取更新）

复用现有调度基础设施，避免再造轮子：

```
catalog.yaml（源注册）  ──►  script_registry / manifest.yaml（任务类型）
        │                              │
        ▼                              ▼
hot_topics.refresh 任务（scope=system）  用户可建多个刷新任务，params.platforms 限定源
  默认 cron: 0 8,12,18 * * *          定时触发 → job_runner → scheduled_scripts/hot_topics:refresh
        │
        ▼
  services/hot_topics.refresh() → 新编排器 collect() → ingest → rank → 落库
```

| 环节 | 现状能力（可直接复用） | 需新增 |
|---|---|---|
| 任务类型注册 | `manifest.yaml` 声明 + `script_registry.py` 动态生成参数表单 | 无（现有 `hot_topics.refresh` 即可）；若拆"按平台定时"可加 field |
| 定时触发 | cron/interval、时区、CAS `claim_trigger`、租约判活、并发判定 | 无 |
| 执行/回写 | `job_runner` 三队列、`_write_terminal` 统一终态、`scheduled_task_runs` 账本、连续失败 3 次自动暂停 | 收敛 refresh 双入口：删专用 handler 或删 runner 薄封装，二选一 |
| 失败可视 | 任务 `last_status`/`consecutive_failures`、页面标红 | 把 §2.2 的 `notes`（如 `parse_empty`）写入 job.result，供首页/降级页展示 |

**建议默认节奏**：批量刷新 `0 8,12,18 * * *`（保留现状）；需要"每天定时"的用户场景可直接用现有任务配置，无需新机制。

---

## 7. 首页热榜接入（需求触发点）

需求开头提到"首页热榜"。现状首页 `/` 不消费热点数据，需补：

- 后端 `GET /dashboard` 增加 `hotHighlights` 字段：复用 `hot_topics.summary()` + `list_clusters(sort=score, limit=5)`，返回标题/分数/档位/平台数。
- 前端 `OverviewView.vue` 增加"今日热点"卡片，点击进 `/hot-topics`；复用 `HotClusterCard.vue` 的分数/档位展示片段。
- 无热点数据时展示空态，不报错。

---

## 8. 数据模型影响

| 表 | 变化 |
|---|---|
| `hot_items` / `hot_rank_history` / `hot_clusters` | **不改结构**（契约对齐现有写入字段即可），保持幂等与历史可复现 |
| 新增 `hot_source_catalog`（可选） | 若注册表入库以便 UI 管理启停，则建表；首期可用 YAML 文件不改库 |
| 新增 `hot_source_snapshots`（可选） | 若做"响应快照回落"，存 `source_id/day/raw_json/fetched_at`；不做缓存则可省 |
| `scheduled_tasks` / `scheduled_task_runs` | 不改 |

---

## 9. 分阶段实施

| 阶段 | 内容 | 产出 |
|---|---|---|
| M1 契约与抽象 | 定义 `HotRawItem`/`CollectResult`/`CollectContext`；建 `catalog.yaml`；把 8 段循环迁到 3 类模板 | 采集层可插拔，源目录单一真相 |
| M2 健壮性 | 并发+超时重试统一；`notes/parse_empty` 上报；响应快照回落 | 单源失败不再静默；刷新更快 |
| M3 扩展点 | `ScriptAdapter` + catalog 注册流程；写一份"如何新增平台"的 README | 新增平台 = 加脚本+注册 |
| M4 定时任务收敛 | refresh/digest 二选一入口；notes 写入 job.result；URL/环境变量示例对齐 | 单一执行路径、失败可视 |
| M5 首页接入 | `/dashboard.hotHighlights` + 首页热点卡片 | 首页可见热榜 |

---

## 10. 验收标准

1. 新增一个 JSON 源：只写 1 个采集器函数（或 0 个，用 `JsonAdapter` 配置）+ catalog 加一行，`collect()` 主代码零改动即可产出数据。
2. 制造单源 500/解析空：其余源照常出数据，该源在 `/hot-topics/degraded` 有明确 notes；全源失败时 refresh 返回错误而非空列表。
3. 同一批 `captured_at` 一致；刷新两次不产生重复 `hot_items`（`item_uid` 幂等）。
4. `docs/hot-topics.md` 平台数与 catalog 一致；`infra/.env.example`、`apps/api/.env.example`、README、AGENT.md 的热点变量四处对齐（当前不一致）。

---

## 11. 决策结论（✅ 已确认）

- Q1 源注册表：**YAML 文件 `catalog.yaml`，由管理员维护**（随代码部署、走评审），首期不做 UI 管理页、不建新表。新增平台 = 管理员改 catalog + 提供采集器。
- Q2 响应快照回落：**首期不做**（M2 评估）。单源失败按现状记 notes，不引入 stale 数据，避免"用旧数据冒充新数据"。
- Q3 环境变量：本次**不**收敛进 `Settings`；但需把 `infra/.env.example`、`apps/api/.env.example`、README、AGENT.md 四处补齐对齐（见验收标准 4）。
- Q4 多租户：**热榜全局共享，管理员维护源注册**，不按用户/工作区隔离（总览 D1 已定）。
