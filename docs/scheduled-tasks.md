# 通用定时任务

> 页面：`/schedules`　接口：`/api/v1/schedules/*`　代码：`modules/schedules.py`、`services/scheduled_tasks.py`、`services/scheduler.py`

## 1. 它解决什么

工作台里有一批「到点就该发生」的事。本能力提供**一个通用的触发器**：把「什么时候该跑」和「跑什么」拆开。时间与脚本都在界面上配——**一切定时任务都是用户脚本**（2026-11 起：内置 manifest 任务类型已整体移除，拉热点等内置定时脚本不复存在，热点页的手动刷新接口不受影响）。

边界：

- **不是发布排队。** `publish_jobs.scheduled_at` 是「这篇稿件几点发」，是业务实体上的一个字段，由发布流程读；本能力是「这个动作几点执行」，与稿件无关。两者不共用表、不共用状态机。
- **不是任务执行器。** 执行仍然是 `background_jobs` + `job_runner`。定时器只负责「到点了，投一个 job」。重试、进度、取消、租约回收一行不改，手动触发与定时触发走同一条代码路径。
- **不做分布式调度。** 多副本靠数据库 CAS 抢占去重，不引入 broker、不在多个节点间分配任务。
- **不假装告警。** 项目没有邮件/IM 通知渠道，失败只在界面标红 + 连续失败自动暂停。

## 2. 链路

```
用户配置（脚本代码 + 参数 + 时间）
        ↓
scheduled_tasks 表（唯一真相，统一 scope=system）
        ↓  启动时装载 / 增删改后同步
APScheduler（内存 jobstore，只算时间）
        ↓  到点回调
CAS 推进 next_run_at ──失败──→ 另一副本已抢到，本次直接返回
        ↓  成功（rowcount == 1）
CAS 抢互斥槽位 active_job_id ──已被占用──→ 记 skipped，写明原因
        ↓  抢到（同一事务）
写 run + create_job（flush）+ 绑定槽位 → commit → dispatch
        ↓
background_jobs → job_runner → user-script handler：快照执行（payload 锁版本）
        ↓  终态
回写 scheduled_task_runs（同一处写）+ 释放槽位 + 累计连续失败次数
```

### 2.1 任务类型：只剩用户脚本

定时任务只有一种类型：`user-script.{scriptId}`。类型由 `scheduled_tasks.dynamic_task_type(key)`
动态生成，无需注册表声明。`services/scheduled_tasks.py` 的 `TaskType` 注册机制保留
（`register()` 供测试与后续扩展用），但不再有内置类型，也没有 manifest——
`app/resources/scripts/manifest.yaml` 与 `services/script_registry.py` 已删除。

内存中的类型结构：

```python
@dataclass(frozen=True)
class TaskType:
    key: str                    # "user-script.{scriptId}"
    title: str
    scope: str                  # 类型元数据；落库的定时配置统一 scope=system
    queue: str                  # background_jobs.queue（user-script）
    kind: str                   # background_jobs.kind（= key）
    params_model: type[BaseModel]
    build_payload: Callable[[BaseModel], dict]
    description: str = ""
    default_cron: str = ""
```

### 2.1.1 用户脚本（`/schedules/scripts/*`）

脚本（`ScheduledScript`）由用户在界面上编写、保存草稿、发布后成为
`user-script.{scriptId}` 类型的定时任务。**统一执行通道**：服务端进程内执行、真实 DB 会话、
项目全部依赖可用（httpx / jieba / app.services 都可以直接 import）。
没有独立沙箱进程、没有 import 白名单、没有 SDK 子进程回调。

- **发布校验（契约制）**：`services/script_runtime.py` 只做两件事——能编译、顶层定义了
  `run(params, ctx)`（`run()` / `run(params)` 可省参）。校验失败返回
  `script_compile_failed` / `script_run_missing`。
- **统一契约**：`run(params, ctx) -> dict`，同一 `ScriptContext`
  （`ctx.db` / `ctx.owner_user_id` / `ctx.task_id` / `ctx.run_id` / `ctx.job_id` / `ctx.task_type`）。
  帮助函数（`get_params` / `get_context` / `get_workspaces` / `create_draft` / `log`）直接注入
  命名空间。
- **超时**：工作线程等待实现，超时任务按 `script_timeout` 失败（可重试）。线程无法强杀，
  只能等脚本自然结束——不要写死循环。
- **进程内全信任执行**：脚本持有 DB 会话，等于服务端 RCE。`scheduled-script.admin`
  本身就是高信任角色；多租户 SaaS 化前必须重新评估这条。

**版本快照（执行一致性）**：每次发布都写一条 `ScheduledScriptVersion`（version 递增 + 代码全量），
并在 `published_version` / `published_hash` 上留指针。投递（定时触发或手动「运行」）时把
`scriptVersion` / `scriptHash` / `scriptCode` **锁进 job payload**：

- 排队期间脚本被修改、停用、重新发布，都不影响已入队的这次执行——worker 只按 payload 里的
  版本号从 `ScheduledScriptVersion` 取快照代码；
- 快照行被清理找不到时，job 明确失败（`script_snapshot_missing`），不静默回退到最新版；
- 停用只影响之后的触发，不追溯已入队的执行。快照路径不做 `enabled` 检查（投递时准入已校验过）；
  旧 payload（升级前投递、无快照字段）回退读当前发布版并保留停用检查。

**测试验证（不落库回滚执行）**：发布/定时之前先验证「参数 + 代码」能不能跑出结果。

| 入口 | 测什么 |
|---|---|
| `POST /schedules/scripts/{id}/test` | 脚本编辑器用：测 **draft** 代码（发布前验证），可传 `params` |
| `POST /schedules/test-run` | 任务级统一入口：测 `user-script.*` 的**发布版**（非脚本类型一律 400） |

两个入口共享 `script_runtime.script_test_session()`：独立连接 + 外部事务 + savepoint 会话，
脚本里的 commit 只释放 savepoint、退出统一回滚——**对数据的任何改动都不落库**，也不写 run
账本、不投 job。返回 `{ok, result, stdout, stderr, durationMs, error}`，契约/执行失败都折进
`error: {code, message}`。

> 实现注意：SQLite 的 pysqlite 驱动默认破坏 SAVEPOINT（隐式提交），测试会话走专用引擎
> （`script_runtime._get_test_session_factory`，显式事务配方 + WAL + busy_timeout）。
> 该配方**不能**套在主引擎上——显式 BEGIN 会让读事务持快照，与并发写者相撞时产生
> 立即的 `SQLITE_BUSY_SNAPSHOT`（等不动，直接 locked）。主引擎保持隐式事务语义。

**手动运行**：`POST /schedules/scripts/{id}/run` 只返回 `{jobId}` 入队回执；前端轮询
`GET /jobs/{jobId}` 到终态后再展示 `result`（summary / stdout / stderr / scriptVersion）。

#### 常用脚本示例

`app/resources/script_examples.py` 是示例的唯一真相源，`GET /schedules/scripts/examples` 下发，
前端脚本编辑器「插入常用示例」直接取用。每个示例都被 `tests/test_script_examples.py`
逐个「编译 + 契约 + 回滚执行」验证——改示例请同步跑测试。

| key | 用途 | 演示点 |
|---|---|---|
| `content_stats` | 按状态统计我的稿件数量 | `ctx.db` + SQLAlchemy 聚合 |
| `content_weekly` | 最近 N 天更新的稿件清单 | 时间窗口查询 + summary |
| `hot_snapshot` | 当前热榜 Top N | 读系统级数据 `hot_items` |
| `http_fetch` | GET 外部 JSON 接口带回结果 | requests 全依赖可用 + 参数校验 |
| `batch_drafts` | 按标题清单批量建稿（同名跳过） | 写业务数据（ContentItem + 版本） |

### 2.2 调度器装配（`services/scheduler.py`）

| 项 | 做法 |
|---|---|
| 实现 | `AsyncIOScheduler`（APScheduler 3.11.3），绑定 FastAPI 那个事件循环 |
| 为什么不用 `BackgroundScheduler` | 它会自己起线程和事件循环，`uvicorn --reload` 下父子进程各起一份，同一任务被触发两次 |
| jobstore | 内存（`MemoryJobStore`）。**表的定义仍是唯一真相**，jobstore 只是运行期投影 |
| 装载时机 | `lifespan` 启动时全量装载 `enabled=True` 的条目；接口增删改后做单条 `add_job(..., replace_existing=True)` / `remove_job` |
| 停机关闭 | `lifespan` 退出时 `shutdown(wait=False)`，不等在跑的回调 |

**装载失败不阻塞启动**：某条任务的 cron 在库里坏了（手改库、旧数据），只记 `warning` 并跳过该条，其余照常装载。

### 2.3 回调里必须做的三件事

**① CAS 抢占。** 内存 jobstore 意味着每个 API 副本都有一份定时器，多副本会同时「到点」。只有把 `next_run_at` 从读到的旧值推进成功的那个才允许继续：

```sql
UPDATE scheduled_tasks
   SET next_run_at = :next, last_fired_at = :now
 WHERE id = :id AND enabled = 1 AND next_run_at = :observed
```

`rowcount != 1` 说明别的副本已经处理过，本次静默返回——不是错误，不写 run 记录。

**② 并发判定（互斥槽位）。** APScheduler 的 `max_instances=1` 保护的是**回调函数**，不是任务执行。回调投递完 job 就返回了，所以 `max_instances` 几乎永远不会生效。真正的互斥必须自己做：

- `scheduled_tasks.active_job_id` 是**原子互斥槽位**：`_launch()` 用一条 `UPDATE … SET active_job_id = :job WHERE id = :id AND active_job_id IS NULL` 的 CAS 抢占，`rowcount != 1` 说明上一轮还没落定。定时与手动两条入口共用这个槽位，谁先抢到谁投递，另一个写 `status="skipped"` 的 run，`skip_reason` 写明「上一次仍在执行」。
- 查询-然后-插入（先 SELECT 有没有未落定 run 再决定投递）在两个入口同时到达时会双双通过——这就是为什么判定必须是单条 UPDATE，而不是应用层判断。
- 槽位在 run 落终态时释放（`sync_run_from_job`，含取消路径），启动对账 `reconcile_runs` 也会把 job 已终态但槽位未释放的悬空记录清掉。僵尸防护保留：queued 超过 `job_lease_seconds` 仍未开始、或 `running` 的 `started_at` 早于租约窗口的 run，对账时按失败处理并放行后续触发。

**③ 投递（同事务提交后才 dispatch）。** `_launch()` 在**同一个事务**里写 run、job（`queue.create_job()`，只 flush 不 commit）和任务级状态，commit 成功后才调 `queue.dispatch(job.id)`。不能反过来：`queue.enqueue()` 是「commit + dispatch」的组合，若先用它再补 `run.job_id`，一个快 worker 会在 dispatch 内同步把 job 跑完——而此刻 run 还没关联 jobId，终态回写找不到对象，run 永远停在 `queued`。

payload 组成：`{**build_payload(params), "scheduledTaskId": task.id, "scheduledRunId": run.id}`，脚本任务再加 `scriptId / scriptVersion / scriptHash / scriptCode / scriptTimeoutSeconds / scriptParams`。`owner_user_id` 取任务的 `owner_user_id`（创建者）——`background_jobs.owner_user_id` 是必填外键。

### 2.4 时间语义

| 项 | 决定 | 理由 |
|---|---|---|
| 表达式 | cron 五段 / 固定间隔（秒） | cron 表达「每天 8 点」，间隔表达「每 30 分钟」。不支持自然语言，也不支持秒级 cron |
| 时区 | 表里存 `timezone`，cron 按它解释；时间戳仍存 UTC | 「早上 8 点」是本地 8 点。新建任务默认继承 `users.timezone` |
| 过期合并 | `coalesce=True` | 停机 6 小时醒来补跑 12 次拉取没有任何意义 |
| 错过宽限 | `misfire_grace_time` 默认 300s，可按任务覆盖 | 超过宽限的点直接丢弃，记 `missed` 而非补跑 |
| 单次并发 | `max_instances=1` + 自研判定（见 2.3②） | 采集源自带冷却，并发打进去只会 429 |
| 间隔下限 | 60 秒 | 防误配成「每 1 秒拉一次外网」。低于下限返回 400，不静默截断 |
| 间隔上限 | 7 天 | 更长的周期用 cron 表达更清楚 |
| 非法表达式 | `invalid_cron` / `invalid_timezone` / `interval_too_small`，400 | 用 `CronTrigger.from_crontab()` 与 `ZoneInfo()` 真解析一次，解析不了就报错，不存进去等运行期炸 |

**表里的 `next_run_at` 与 APScheduler 的算法关系**：时间只由一处算——APScheduler 的 trigger。回调里用 `job.trigger.get_next_fire_time(observed, max(observed, now))` 取下一次并写回表。表里的值只做两件事：界面展示、CAS 令牌。绝不在业务代码里另写一份「下次该什么时候跑」的算法。

### 2.5 失败与自动暂停

- 失败落在 `background_jobs`（`error_code` / `error_message`），同时回写对应 run。
- 任务级累计：`consecutive_failures` 在 run 终态为 `failed` 时 +1，`succeeded` 时归零。
- 达到 `SCHEDULER_MAX_CONSECUTIVE_FAILURES`（默认 3）→ `enabled=False`、`paused_reason` 写明「连续 3 次失败，最后错误：…」。
- 自动暂停**只在界面上体现**，不推送任何通知。用户改完配置点「恢复」即清零计数。

### 2.6 对账

runs 是触发账本，`background_jobs` 是执行真相。runs 的 `status` 由 `job_runner` 写终态时**同一处**回写。仍可能漏（进程在两次写之间被杀），因此启动时扫一遍：

- runs 仍是 `queued`/`running`，但对应 job 已终态 → 以 job 为准修正，记 `warning`。
- runs 有 `job_id` 但 job 行不存在 → 标 `failed`，`error_code="job_missing"`。
- 悬空超过 24 小时且无法对账的 run → 标 `failed` 并注明，不让它永远显示「执行中」。

## 3. 接口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/schedules/types` | 登录 | 类型清单：仅已发布且启用的用户脚本（`user-script.*`） |
| GET | `/schedules` | 登录 | 全量定时配置（统一 system，全局共享） |
| POST | `/schedules` | 登录 | 新建（`taskType` 必须是 `user-script.*` 且已发布启用） |
| PATCH | `/schedules/{id}` | 登录 | 改名/参数/时间/启停。改了时间立即重新装载 |
| DELETE | `/schedules/{id}` | 登录 | 删除，同时删该任务的 runs（级联） |
| POST | `/schedules/{id}/run` | 登录 | 立即执行一次，走同一条投递路径，`source="manual"` |
| GET | `/schedules/{id}/runs` | 登录 | keyset 分页，复用 `core/pagination`，`status` 可筛 |
| POST | `/schedules/preview` | 登录 | 传表达式 + 时区，返回**未来 3 次触发时间**或明确的解析错误 |
| POST | `/schedules/test-run` | 登录 | 脚本测试验证（统一入口）：`user-script.*` 回滚执行不落库 |
| GET | `/schedules/scripts/examples` | 登录 | 常用脚本示例清单（前端「插入示例」数据源） |
| POST | `/schedules/scripts/{id}/test` | `scheduled-script.admin` | 脚本编辑器测试验证：测 draft 代码，回滚执行不落库 |

`preview` 存在的理由：cron 表达式最容易配错，让用户在保存前就看见「下一次是明天 8:00」比存进去之后猜要便宜得多。

**写权限**：定时配置统一 `scope=system`，写权限由 `SCHEDULER_SYSTEM_WRITE` 决定：

| `SCHEDULER_SYSTEM_WRITE` | 行为 |
|---|---|
| `dev`（默认） | 仅 `environment=development` 允许经接口改；生产环境只读 |
| `any` | 任何登录用户可改（单租户部署时显式打开） |
| `never` | 接口只读 |

## 4. 数据表

### 4.1 `scheduled_tasks`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(32) | 主键 |
| `scope` | VARCHAR(16) | 统一 `system`：接口创建的所有定时配置都落 `system`，全局共享；`user` 仅剩兼容含义（历史数据） |
| `owner_user_id` | VARCHAR(32) | 外键 users.id（创建者，job 投递的 owner） |
| `task_type` | VARCHAR(64) | `user-script.{scriptId}`，脚本必须已发布且启用 |
| `name` | VARCHAR(120) | 用户可见名称 |
| `params` | JSON | 运行参数（脚本经 `get_params()` / `params` 读取） |
| `schedule_kind` | VARCHAR(16) | `cron` / `interval` |
| `cron` | VARCHAR(120) | 五段表达式，`schedule_kind=cron` 时必填 |
| `interval_seconds` | INTEGER | `schedule_kind=interval` 时必填，60 ~ 604800 |
| `timezone` | VARCHAR(64) | 默认继承 `users.timezone` |
| `misfire_grace_seconds` | INTEGER | 默认 300 |
| `enabled` | BOOLEAN | 停用后从调度器移除，不再触发 |
| `next_run_at` | TIMESTAMP | 展示 + CAS 令牌 |
| `last_fired_at` | TIMESTAMP | 最近一次触发（含跳过） |
| `last_status` | VARCHAR(24) | 最近一次 run 的终态，用于列表标红 |
| `consecutive_failures` | INTEGER | 连续失败计数 |
| `paused_reason` | VARCHAR(300) | 自动暂停原因；手动停用为空 |
| `active_job_id` | VARCHAR(32) | 互斥槽位：当前未落定执行的 jobId，NULL 表示空闲 |
| `created_at` / `updated_at` | TIMESTAMP | `TimestampMixin` |

### 4.2 `scheduled_task_runs`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(32) | 主键 |
| `task_id` | VARCHAR(32) | 外键 scheduled_tasks.id，`ON DELETE CASCADE` |
| `job_id` | VARCHAR(32) | 对应 `background_jobs.id`。跳过的 run 为空 |
| `triggered_at` | TIMESTAMP | 触发时刻（UTC） |
| `source` | VARCHAR(16) | `schedule` / `manual` |
| `status` | VARCHAR(24) | `queued` / `running` / `succeeded` / `failed` / `skipped` / `cancelled` / `missed` |
| `skip_reason` | VARCHAR(300) | `skipped` 时必填 |
| `error_code` | VARCHAR(64) | 与 job 的 error_code 一致 |
| `error_message` | TEXT | 与 job 的 error_message 一致 |
| `duration_ms` | INTEGER | 由 job 的 `started_at`/`completed_at` 算 |
| `finished_at` | TIMESTAMP | 终态时刻 |

索引（走 `core/db.py` 的 `_INDEXES` 幂等创建）：

- `scheduled_tasks (enabled, next_run_at)` — 装载与对账扫描
- `scheduled_tasks (scope, owner_user_id, last_fired_at DESC)` — 列表
- `scheduled_task_runs (task_id, triggered_at DESC, id DESC)` — 历史分页
- `scheduled_task_runs (status)` — 对账扫未落定

## 5. 页面

`apps/web/src/views/SchedulesView.vue`：单一统一列表——内置任务已移除，列表里只有脚本
（用户脚本 + 可能残留的历史定时配置）。新建/编辑脚本弹窗与时间配置一体（保存 = 存草稿 +
发布 + 建/改定时配置，「到点自动执行」开关可只手动跑）；「测试验证」回滚执行不落库；
「历史」分页拉 `/runs`；「立即执行」走 `/run` 或（未配置定时的脚本）手动入队轮询。

## 6. 测试

`apps/api/tests/test_scheduled_tasks.py`：

**纯函数（不打网、不依赖时钟）**

- cron / interval / 时区解析：合法通过、非法给出确切错误码
- `next_run_at` 取自 `trigger.get_next_fire_time`，与手算一致（固定 `now`）
- 参数 schema 校验：类型错、越界（间隔 < 60s）都被拒
- 未知任务类型 400（不静默落库一条跑不起来的任务）

**路由**

- 建、改、删、启停；改 cron 后 `next_run_at` 跟着变
- `preview` 返回 3 个时间且升序
- `run` 产生一条 `source=manual` 的 run，且 `background_jobs` 里出现对应 job
- `SCHEDULER_SYSTEM_WRITE` 写权限矩阵
- runs 分页 keyset 正确、`status` 筛选生效
- 任务归属统一 system：一个用户建的另一个用户可见可改

**调度语义（自注册测试类型 `test.noop` 驱动，不依赖真实等待）**

- CAS：两个副本抢同一令牌，只有一个成功
- 并发判定：已有 `running` run → 写 `skipped` 且不投递
- 僵尸判定：`queued` 且超过租约 → 照常触发
- 连续 3 次失败 → `enabled=False` 且 `paused_reason` 非空
- 对账：runs 卡 `running` 但 job 已 `failed` → 以 job 为准修正；job 行不存在 → `job_missing`

**热点服务层**（`build_digest`，手动接口的底层，不依赖调度）

- 只挑命中赛道的簇；`min_score` 以下的、别的赛道的都不出现
- 建稿后簇的 `content_item_id` / `status` 保持不变
- 同一标题第二次运行 → `draftsCreated=0`、`draftsReused=1`
- 库里一条热点都没有 → `no_hot_data` 失败

## 7. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | `false` 时完全不装载（只留接口与表，用于排障） |
| `SCHEDULER_MISFIRE_GRACE` | `300` | 错过宽限秒数的默认值，任务可覆盖 |
| `SCHEDULER_MAX_CONSECUTIVE_FAILURES` | `3` | 连续失败多少次自动暂停，0 表示不自动暂停 |
| `SCHEDULER_SYSTEM_WRITE` | `dev` | 定时配置写权限：`dev` / `any` / `never` |

## 8. 已知取舍

| 取舍 | 代价 | 为什么接受 |
|---|---|---|
| 内存 jobstore | 每个副本都有一份定时器，靠 CAS 去重 | 换来「表是唯一真相」，重启不丢任务 |
| 回调精确到秒级但可能延迟 | 线程池/事件循环繁忙时触发晚几秒 | 这类任务对秒级精度没有需求 |
| 不做任务依赖 | 无法表达「A 成功后再跑 B」 | 那是工作流引擎，会把这层薄触发器变成重框架 |
| 失败只标红不通知 | 用户可能几天后才发现坏了 | 项目没有通知渠道。连续失败自动暂停把「持续烧配额」压到 3 次内 |
| 定时配置统一 system 全局共享 | 多用户下任何登录用户都能改全部门定时配置 | 单租户自部署场景；多租户化前需要重新引入归属隔离 |
| 用户脚本进程内全信任执行 | 脚本持有 DB 会话与全部依赖，等于服务端 RCE | `scheduled-script.admin` 本身就是高信任角色；单租户自部署场景下，能力收益（真实 DB / 全依赖）大于隔离损失 |
| 脚本超时用线程等待实现 | 超时后线程无法强杀，只能等脚本自然结束 | 可信脚本 + 明确文档化（别写死循环）是可接受代价 |

## 9. 实施记录

| 关注点 | 位置 |
|---|---|
| 类型结构 / 时间语义 / CAS / 对账 | `apps/api/app/services/scheduled_tasks.py`（`user-script.*` 走 `dynamic_task_type`） |
| 调度器装配（装载、单条同步、missed 监听） | `apps/api/app/services/scheduler.py` |
| 接口（types/CRUD/run/runs/preview/test-run） | `apps/api/app/modules/schedules.py` |
| 表 | `ScheduledTask` / `ScheduledTaskRun`（`models.py`） |
| 终态回写 | `job_runner._write_terminal`（唯一入口，含取消路径；同时释放互斥槽位） |
| 互斥槽位准入 | `scheduled_tasks._launch`（CAS 抢 `active_job_id` + 同事务建 run/job + 提交后 dispatch） |
| 用户脚本 handler / 快照执行 | `job_runner._handle_user_script`（payload 快照优先，旧 payload 回退） |
| 用户脚本接口 / 发布快照 / 测试验证 | `modules/script_schedules.py` |
| 脚本运行时（契约校验 + 进程内执行 + 测试会话） | `services/script_runtime.py` |
| ScriptContext / ScriptFailure 公共契约 | `app/scheduled_scripts/base.py`（内置 runner 已删除，仅留公共定义） |
| 常用脚本示例 | `app/resources/script_examples.py` + `tests/test_script_examples.py` |
| 页面 | `apps/web/src/views/SchedulesView.vue` |
| 测试 | `apps/api/tests/test_scheduled_tasks.py` + `test_script_schedules.py` + `test_script_examples.py` |

**2026-11 变更记录**：

1. **执行通道统一**：内置 manifest 脚本与用户脚本合并为同一契约 `run(params, ctx) -> dict`、
   同一进程内执行通道（真实 DB / 全依赖）；沙箱子进程与 SDK 回调删除。
2. **测试验证**：新增两个不落库回滚执行的测试入口（见 §2.1.1）。
3. **归属统一**：定时配置不再区分 system/user，全部落 `scope=system` 全局共享；播种链路
   （`ensure_system_tasks` / `SCHEDULER_SEED_SYSTEM_TASKS`）随内置类型移除一并删除。
4. **内置任务整体移除**：manifest / `script_registry.py` / `scheduled_scripts/hot_topics.py` /
   `health_check.py` / `job_runner` 的 `hot-topics` / `topic-digest` / `script` handler 全部删除。
   热点能力保留为手动接口（`POST /hot-topics/refresh` 等，`modules/hot_topics.py`）。
   内置定时需求一律改写成用户脚本实现（参考 §2.1.1 示例）。
