# 通用定时任务

> 页面：`/schedules`　接口：`/api/v1/schedules/*`　代码：`modules/schedules.py`、`services/scheduled_tasks.py`、`services/scheduler.py`

## 1. 它解决什么

工作台里有一批「到点就该发生」的事：拉一遍热点榜单、扫一遍平台账号数据、清一次临时目录。这些事目前只能靠人点按钮，于是它们的价值取决于人记不记得点。

本能力提供**一个通用的触发器**：把「什么时候该跑」和「跑什么」拆开。时间由用户在界面上配，跑什么由代码注册的任务类型决定。首期只接一个类型——拉热点；后续新增类型是「写一个 handler + 注册」，不改调度器、不改表结构。

边界：

- **不是发布排队。** `publish_jobs.scheduled_at` 是「这篇稿件几点发」，是业务实体上的一个字段，由发布流程读；本能力是「这个动作几点执行」，与稿件无关。两者不共用表、不共用状态机。混用会让「一条发布任务」和「一个定时触发器」变成同一个东西，谁都说不清它到底在等什么。
- **不是任务执行器。** 执行仍然是 `background_jobs` + `job_runner`。定时器只负责「到点了，投一个 job」。重试、进度、取消、租约回收一行不改，手动触发与定时触发走同一条代码路径。
- **不做分布式调度。** 多副本靠数据库 CAS 抢占去重，不引入 broker、不在多个节点间分配任务。当前规模用不上，且会让「这个任务谁跑的」变得难查。
- **不假装告警。** 项目没有邮件/IM 通知渠道，失败只在界面标红 + 连续失败自动暂停。不会显示「已通知你」这类不存在的行为。

## 2. 链路

```
用户配置（类型 + 参数 + 时间）
        ↓
scheduled_tasks 表（唯一真相）
        ↓  启动时装载 / 增删改后同步
APScheduler（内存 jobstore，只算时间）
        ↓  到点回调
CAS 推进 next_run_at ──失败──→ 另一副本已抢到，本次直接返回
        ↓  成功（rowcount == 1）
并发判定 ──有未落定 run──→ 记 skipped，写明原因
        ↓
queue.enqueue() → background_jobs → job_runner → 按 queue 分发 handler
        ↓                                   ├─ hot-topics / topic-digest：专用 handler
        │                                   └─ script：通用 handler → manifest 指定的脚本 run()
        ↓  终态
回写 scheduled_task_runs（同一处写）+ 累计连续失败次数
```

### 2.1 任务类型注册表：manifest 声明驱动

任务是**通用执行器 + 受控脚本**。类型清单由唯一数据源 `app/resources/scripts/manifest.yaml` 声明，
进程启动时由 `services/script_registry.py` 编译成内存里的 `TaskType` 注册表。前端
`GET /schedules/types` 拿到什么类型、什么参数字段就渲染什么表单，不含任何写死的任务知识。

> 历史：早期类型写死在 Python 里（`@dataclass TaskType` + 若干 `register(...)`）。
> 现在这条注册表仍在 `services/scheduled_tasks.py`，但**内容由 manifest 生成**，
> `register()` 的「重复 key 即抛错」约束不变。新增任务类型不要再改 Python 代码。

内存中的类型结构：

```python
@dataclass(frozen=True)
class TaskType:
    key: str                    # "hot_topics.refresh" / "script.health_check"
    title: str                  # "拉取热点榜单"
    scope: str                  # "system" | "user"
    queue: str                  # background_jobs.queue
    kind: str                   # background_jobs.kind
    params_model: type[BaseModel]   # 由 manifest fields 动态 create_model 生成
    build_payload: Callable[[BaseModel], dict]
    description: str = ""
    default_cron: str = ""
```

约束：

- manifest 里 **key 重复、runner 导不到、字段类型未知、options_from 不存在** 都会在启动时抛
  `ManifestError` 让进程起不来，绝不留到运行期才炸。
- `scope` 由声明决定，不由创建者选择。热点是全局数据（`hot_items` / `hot_clusters` 无
  `owner_user_id`），只能是 `system`。
- 参数校验走动态生成的 Pydantic 模型 + manifest `options` 枚举校验。失败返回 400 并指出具体字段。
- `build_payload` 产出必须是纯 JSON 可序列化且不含密钥；它进 `background_jobs.payload`。

已接入的类型：

| key | 归属 | 队列 | 默认时间 | 脚本 / 产出 |
|---|---|---|---|---|
| `hot_topics.refresh` | `system` | `hot-topics` | `0 8,12,18 * * *` | `scheduled_scripts/hot_topics.py:refresh` → 全局榜单 |
| `hot_topics.digest` | `user` | `topic-digest` | `30 8 * * *` | `scheduled_scripts/hot_topics.py:digest` → 个人草稿 |
| `script.health_check` | `user` | `script` | `0 9 * * 1` | `scheduled_scripts/health_check.py:run` → 示例通用脚本 |

`hot_topics.digest` 的三条边界（写脚本前必须守住）：

1. **只读榜单，不触发采集。** 采集是 `hot_topics.refresh` 的事；简报再打一遍外网就是重复请求，而且会把系统任务的采样节奏打乱。
2. **不碰簇的全局状态。** 不写 `content_item_id`、不改 `status`——那是热点页所有人共享的状态，个人定时任务无权替所有人改。
3. **判重按 owner + 标题**，不用 `cluster.content_item_id`。后者是单指针，按它判重会让第二个用户永远拿不到自己的草稿（热点数据不分用户）。同名草稿已存在时记 `draftsReused` 而非重复创建。

第 2、3 条也解释了为什么 `hot_topics.digest` 必须是 `user` 级：产出是私有的，10 个用户各跑一份不会各自去打 10 遍采集源——因为它根本不采集。

#### 新增一种定时任务（脚本扩展指南）

不用改任何框架代码、不用改前端。两步：

**1. 写脚本** —— 在 `app/scheduled_scripts/` 下加一个模块，暴露 `run(params, ctx) -> dict`：

```python
from .base import ScriptContext, ScriptFailure

def run(params: dict, ctx: ScriptContext) -> dict:
    # params 已按 manifest 校验、补过默认值
    # ctx.db 是当前数据库会话；还有 owner_user_id / task_id / run_id / job_id / task_type
    if 无法继续:
        raise ScriptFailure("my_code", "人话错误", retryable=False)  # 映射成 job 失败
    return {"summary": "一句人话摘要", "任意键": 任意值}  # 原样落 background_jobs.result
```

约定：可预期失败抛 `ScriptFailure`（`retryable=True` 会走现有重试）；返回值放 `summary` 键，
历史列表的「结果摘要」优先显示它，否则退回 JSON 截断——所以加新脚本不用改 `_result_summary`。

**2. 加声明** —— 在 `manifest.yaml` 的 `scripts` 下加一条：

```yaml
- key: my.new_task          # 落库后不可改；改了等于造一条新类型
  title: 我的新任务
  scope: user               # user | system
  queue: script             # 通用脚本统一走 'script'；专用队列需在 job_runner 注册 handler
  kind: my.new_task         # runner_for_kind 用它找脚本函数
  description: 给用户看的一句话说明
  default_cron: "0 9 * * 1"
  runner: app.scheduled_scripts.my_module:run   # 模块路径:函数名
  fields:                   # 前端据此渲染表单；type ∈ string/integer/number/boolean/array
    - name: target
      title: 目标
      type: string
      required: true
      default: 默认值
      description: 填写说明
    - name: mode
      title: 模式
      type: string
      default: 快速
      options: [快速, 完整]  # 或 options_from: some_wordlist（见 script_registry._OPTION_SOURCES）
      item_label: 模式       # 报错时显示「未知模式：xxx」
```

重启服务即生效：类型下拉和参数表单自动出现，登录用户的 `ensure_system_tasks` 也会按 `system`
声明补种。这是**服务端进程内的受控扩展点**：manifest 只能由部署方维护，不是给用户在线写任意代码的地方
（用户表没有角色字段，做不到安全的多租户代码隔离）。

**信任边界**：脚本运行在 API/worker 进程内，拥有数据库会话。只放可信代码；不要让终端用户上传脚本或编辑 manifest。


### 2.2 调度器装配（`services/scheduler.py`）

| 项 | 做法 |
|---|---|
| 实现 | `AsyncIOScheduler`（APScheduler 3.11.3），绑定 FastAPI 那个事件循环 |
| 为什么不用 `BackgroundScheduler` | 它会自己起线程和事件循环，`uvicorn --reload` 下父子进程各起一份，同一任务被触发两次 |
| jobstore | 内存（`MemoryJobStore`）。**表的定义仍是唯一真相**，jobstore 只是运行期投影 |
| 装载时机 | `lifespan` 启动时全量装载 `enabled=True` 的条目；接口增删改后做单条 `add_job(..., replace_existing=True)` / `remove_job` |
| 停机关闭 | `lifespan` 退出时 `shutdown(wait=False)`，不等在跑的回调 |

**装载失败不阻塞启动**：某条任务的 cron 在库里坏了（手改库、旧数据），只记 `warning` 并跳过该条，其余照常装载。一条脏数据让整个调度器起不来是不可接受的。

### 2.3 回调里必须做的三件事

回调很快（投递完就返回），但它承担全部正确性：

**① CAS 抢占。** 内存 jobstore 意味着每个 API 副本都有一份定时器，多副本会同时「到点」。只有把 `next_run_at` 从读到的旧值推进成功的那个才允许继续：

```sql
UPDATE scheduled_tasks
   SET next_run_at = :next, last_fired_at = :now
 WHERE id = :id AND enabled = 1 AND next_run_at = :observed
```

`rowcount != 1` 说明别的副本已经处理过，本次静默返回——不是错误，不写 run 记录。

**② 并发判定。** 注意一个容易搞错的点：APScheduler 的 `max_instances=1` 保护的是**回调函数**，不是任务执行。回调投递完 job 就返回了，所以 `max_instances` 几乎永远不会生效。真正的互斥必须自己做：

- 查该任务是否存在 `status in (queued, running)` 的 run；
- **并按租约判活**：`queued` 超过 `job_lease_seconds` 仍未开始、或 `running` 的 `started_at` 早于租约窗口，视为僵尸，记 `warning` 后照常触发。不加这条，一个卡死在 `queued` 的 job（典型原因：Redis 模式漏了 worker 消费）会让该任务永远跳过，且界面上看不出原因——静默死亡比失败更糟。
- 确实忙 → 写一条 `status="skipped"` 的 run，`skip_reason` 写明「上一次仍在执行（runId=…）」。

**③ 投递。** `queue.enqueue(db, owner_user_id=..., queue=type.queue, kind=type.kind, payload={**build_payload(params), "scheduledTaskId": task.id, "scheduledRunId": run.id})`。

`owner_user_id` 取任务的 `owner_user_id`；`system` 任务取**系统账号**（首个用户或专用 seed 用户）。`background_jobs.owner_user_id` 是必填外键，不能让 system 任务留空——所以 system 任务在播种时必须绑定一个真实用户，文档与代码里都不出现「空 owner」这种半截状态。

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

**表里的 `next_run_at` 与 APScheduler 的算法关系**：时间只由一处算——APScheduler 的 trigger。回调里用 `job.trigger.get_next_fire_time(None, now)` 取下一次并写回表。表里的值只做两件事：界面展示、CAS 令牌。绝不在业务代码里另写一份「下次该什么时候跑」的算法，两套算法一定有偏差。

### 2.5 失败与自动暂停

- 失败落在 `background_jobs`（`error_code` / `error_message`），同时回写对应 run。
- 任务级累计：`consecutive_failures` 在 run 终态为 `failed` 时 +1，`succeeded` 时归零。
- 达到 `SCHEDULER_MAX_CONSECUTIVE_FAILURES`（默认 3）→ `enabled=False`、`paused_reason` 写明「连续 3 次失败，最后错误：…」。
- 自动暂停**只在界面上体现**，不推送任何通知。用户改完配置点「恢复」即清零计数——不保留历史失败计数去干扰下一次判断。

原因：这类失败通常不会自愈（关键词配错、采集源下线、凭据过期）。每 30 分钟无意义重试只是持续打外网。

### 2.6 对账

runs 是触发账本，`background_jobs` 是执行真相。runs 的 `status` 由 `job_runner` 写终态时**同一处**回写。仍可能漏（进程在两次写之间被杀），因此启动时扫一遍：

- runs 仍是 `queued`/`running`，但对应 job 已终态 → 以 job 为准修正，记 `warning`。
- runs 有 `job_id` 但 job 行不存在 → 标 `failed`，`error_code="job_missing"`。
- 悬空超过 24 小时且无法对账的 run → 标 `failed` 并注明，不让它永远显示「执行中」。

## 3. 接口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/schedules/types` | 登录 | 类型清单：key、title、scope、参数 JSON Schema、默认参数 |
| GET | `/schedules` | 登录 | 列表：`system` 全部 + 自己的 `user` 任务；按 `last_fired_at DESC` |
| POST | `/schedules` | 登录 | 新建。system 类型受写权限约束（见下） |
| PATCH | `/schedules/{id}` | 登录 | 改名/参数/时间/启停。改了时间立即重新装载 |
| DELETE | `/schedules/{id}` | 登录 | 删除，同时删该任务的 runs（级联） |
| POST | `/schedules/{id}/run` | 登录 | 立即执行一次，走同一条投递路径，`source="manual"` |
| GET | `/schedules/{id}/runs` | 登录 | keyset 分页，复用 `core/pagination`，`status` 可筛 |
| POST | `/schedules/preview` | 登录 | 传表达式 + 时区，返回**未来 3 次触发时间**或明确的解析错误 |

`preview` 存在的理由：cron 表达式最容易配错，让用户在保存前就看见「下一次是明天 8:00」比存进去之后猜要便宜得多。

**system 任务的写权限**：`users` 表没有角色字段（`design.md` §2.1 的首期角色只有「工作区所有者」和「系统运维」，没有落成字段），所以不虚构一个管理员角色。改用配置：

| `SCHEDULER_SYSTEM_WRITE` | 行为 |
|---|---|
| `dev`（默认） | 仅 `environment=development` 允许经接口改 system 任务；生产环境这些任务只读，改由 seed 脚本 |
| `any` | 任何登录用户可改（单租户部署时显式打开） |
| `never` | 接口只读，system 任务一律由 seed 维护 |

不管哪种模式，**用户都不能创建 `scope=system` 的新任务**——类型自己的 `scope` 说了算，接口只校验「这个类型的任务允许被这个请求改吗」。

## 4. 数据表

### 4.1 `scheduled_tasks`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(32) | 主键 |
| `scope` | VARCHAR(16) | `system` / `user` |
| `owner_user_id` | VARCHAR(32) | 外键 users.id。system 任务也必须有真实 owner（见 2.3③） |
| `task_type` | VARCHAR(64) | 注册表里的 key，落库前必须存在 |
| `name` | VARCHAR(120) | 用户可见名称 |
| `params` | JSON | 类型参数，已通过 `params_model` 校验 |
| `schedule_kind` | VARCHAR(16) | `cron` / `interval` |
| `cron` | VARCHAR(120) | 五段表达式，`schedule_kind=cron` 时必填 |
| `interval_seconds` | INTEGER | `schedule_kind=interval` 时必填，60 ~ 604800 |
| `timezone` | VARCHAR(64) | 默认继承 `users.timezone` |
| `misfire_grace_seconds` | INTEGER | 默认 300 |
| `enabled` | BOOLEAN | 停用后从调度器移除，不再触发 |
| `next_run_at` | TIMESTAMP | 展示 + CAS 令牌。唯一算法来源见 2.4 |
| `last_fired_at` | TIMESTAMP | 最近一次触发（含跳过） |
| `last_status` | VARCHAR(24) | 最近一次 run 的终态，用于列表标红 |
| `consecutive_failures` | INTEGER | 连续失败计数 |
| `paused_reason` | VARCHAR(300) | 自动暂停原因；手动停用为空 |
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

新表由 `Base.metadata.create_all()` 建（现有机制），无需手写 DDL；旧库不存在这两张表时自动补建。

## 5. 页面

`apps/web/src/views/SchedulesView.vue`，`router.js` 加一行：

```js
{ path: '/schedules', name: 'schedules', component: () => import('./views/SchedulesView.vue'), meta: { title: '定时任务', icon: '⏱' } }
```

- **列表**：名称 / 类型 / 时间（人话：`每天 08:00、12:00、18:00`，不直接摊 cron）/ 下次触发 / 最近状态 / 开关。失败标红并显示 `error_message`；自动暂停的行显示 `paused_reason` 与「恢复」按钮。
- **新建/编辑抽屉**：先选类型（下拉，含类型说明）→ 按参数 JSON Schema 渲染表单 → 选时间（常用预设 + 手填 cron + 实时 `preview` 显示未来 3 次）→ 保存。
  - 可选类型 = 全部 `user` 类型 + **尚未落地**的 `system` 类型。`system` 类型全库只能一条，已存在时新建下拉里就不再出现它，避免点进去才被 409 拒绝。
  - 删除按钮只对 `user` 类型渲染：`system` 类型删掉之后只能靠重启/seed 重建，不给这个入口。
  - 参数的 `options`（如赛道路径）随字段一起下发，前端渲染成下拉而不是让用户手打——打错一个字的后果是一份空简报。
- **展开历史**：分页拉 `/runs`，每行给状态、耗时、错误、`skip_reason`，以及「打开对应任务」跳现有任务详情。
- **立即执行**：调 `/run` 后立刻在历史里出现一条 `source=manual` 的记录，前端轮询同一个 `/jobs/{id}` 获取进度。
- 空态区分「还没有任务」和「全部停用」，各给对应出口。
- 窄屏（≤560px）：列表卡片化，时间与状态各占一行，操作收进「更多」。

不做：拖拽排序、任务依赖（A 完成后跑 B）、可视化 cron 编辑器。任务依赖会把它变成工作流引擎，那是另一个东西。

## 6. 测试

`apps/api/tests/test_scheduled_tasks.py`：

**纯函数（不打网、不依赖时钟）**

- cron / interval / 时区解析：合法通过、非法给出确切错误码
- `next_run_at` 取自 `trigger.get_next_fire_time`，与手算一致（固定 `now`）
- 参数 schema 校验：缺字段、类型错、越界（间隔 < 60s）都被拒
- `scope` 由类型决定：请求里传 `scope=system` 但类型是 `user` → 400

**路由**

- 建、改、删、启停；改 cron 后 `next_run_at` 跟着变
- `preview` 返回 3 个时间且升序
- `run` 产生一条 `source=manual` 的 run，且 `background_jobs` 里出现对应 job
- system 任务在不同 `SCHEDULER_SYSTEM_WRITE` 下的写权限矩阵
- runs 分页 keyset 正确、`status` 筛选生效

**调度语义（用假 scheduler 记录调用，不真等时间）**

- CAS：两次并发回调，只有一个能 enqueue（模拟 `rowcount`）
- 并发判定：已有 `running` run → 写 `skipped` 且不 enqueue
- 僵尸判定：`queued` 且超过租约 → 照常触发并记 warning
- 连续 3 次失败 → `enabled=False` 且 `paused_reason` 非空
- 对账：runs 卡 `running` 但 job 已 `failed` → 启动后以 job 为准修正

**首期类型**

- 拉热点：handler 调用 `hot_topics.refresh`，结果写进 job.result；单源全失败时 job 进 `failed` 且 `error_code=all_sources_failed`（**不能**是 succeeded 带 0 条）
- 选题简报：
  - 只挑命中赛道的簇；`min_score` 以下的、别的赛道的都不出现
  - 建稿后簇的 `content_item_id` / `status` 保持不变（不替所有人改全局状态）
  - 同一标题第二次运行 → `draftsCreated=0`、`draftsReused=1`，不重复建稿
  - 库里一条热点都没有 → `no_hot_data` 失败；有数据但没命中赛道 → 成功且 `total=0`（这是正常结果，不是错误）
  - 投递链：`POST /run` 的 job 落在 `topic-digest` 队列，handler 把参数原样交给 `build_digest`
- 用户任务：别人的任务在列表里不可见，patch/delete 返回 404（不暴露存在性）

## 7. 落地顺序

| 阶段 | 内容 | 可验证结果 |
|---|---|---|
| 1 | `requirements.txt` 加 `apscheduler==3.11.3`；两张表 + 索引；类型注册表 + 首期类型 | 建表成功，注册表可枚举 |
| 2 | 调度器装配（lifespan）+ CAS 回调 + 并发/僵尸判定 | 单测覆盖语义，不依赖真实等待 |
| 3 | `modules/schedules.py` 全部接口 + 权限矩阵 | Swagger 可建可改可预览 |
| 4 | `job_runner` 增 `hot-topics` queue handler + runs 终态回写 + 对账 | 定时跑通一条真实拉取 |
| 5 | 冷却逻辑从 `modules/hot_topics.py` 下沉到服务层 | 旧接口 429 行为不变（回归测试守着） |
| 6 | `SchedulesView.vue` + 路由 + api 封装 | 界面可完成全流程 |
| 7 | 本文档 + `README.md` / `docs/setup.md` 的环境变量表 | 文档与代码一致 |

阶段 4 的顺带改动：`apps/worker/worker.py` 的 `QUEUES` 加上 `hot-topics`。

> **修正（实现时验证过）**：本文早期版本写的是「不加就会永远 queued」，这不成立。Worker 用 `BLPOP` 消费**同一个** Redis list（`queue.QUEUE_KEY`），与队列名无关；`QUEUES` 只是 `--queue` 的 CLI 白名单，不加的唯一后果是 `--queue hot-topics` 被 argparse 拒绝。原判断来自「队列名会被当作独立 list」的直觉，实测不成立，已按事实改写。

## 8. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | `false` 时完全不装载（只留接口与表，用于排障） |
| `SCHEDULER_MISFIRE_GRACE` | `300` | 错过宽限秒数的默认值，任务可覆盖 |
| `SCHEDULER_MAX_CONSECUTIVE_FAILURES` | `3` | 连续失败多少次自动暂停，0 表示不自动暂停 |
| `SCHEDULER_SYSTEM_WRITE` | `dev` | system 任务写权限：`dev` / `any` / `never` |

## 9. 后续接入项

按「加一个 handler + 注册」的路径，这些可以后接，均无需改调度器：

| 任务类型 | 落地方式 | 依赖 |
|---|---|---|
| 平台数据定时同步 | 复用 `_handle_platform_sync`，遍历 `connected_accounts` 逐个入队 | 已有连接器与游标（`design.md` §7） |
| 热点→选题摘要 | `user` scope，按用户赛道生成选题建议 | 需要文本模型连接 |
| 稿件草稿自动保存快照 | `user` scope | `content_items.draft_*` 字段已有 |

以上几项现在都可直接按 §2.1「新增一种定时任务」实现：一个脚本 + 一条 manifest，无需改框架代码。

这些都要求同一条纪律：**失败必须落在 `background_jobs.error_code` 上并写明原因**，不能用「跑完了，0 条」冒充成功。

## 10. 已知取舍

| 取舍 | 代价 | 为什么接受 |
|---|---|---|
| 内存 jobstore | 每个副本都有一份定时器，靠 CAS 去重 | 换来「表是唯一真相」，重启不丢任务；jobstore 持久化会引入第二套状态 |
| 回调精确到秒级但可能延迟 | 线程池/事件循环繁忙时触发晚几秒 | 拉热点、同步数据这类任务对秒级精度没有需求 |
| 不做任务依赖 | 无法表达「A 成功后再跑 B」 | 那是工作流引擎，会把这层薄触发器变成重框架 |
| 失败只标红不通知 | 用户可能几天后才发现坏了 | 项目没有通知渠道，假装有会比没有更糟。连续失败自动暂停把「持续烧配额」压到 3 次内 |
| system 任务生产环境只读 | 改频率要动 seed 脚本 | 没有角色字段时不虚构管理员；单租户部署可显式设 `SCHEDULER_SYSTEM_WRITE=any` |
| 脚本是进程内受控扩展，非在线代码执行 | 加任务要改 manifest + 重启，终端用户不能自建脚本 | 脚本持数据库会话且有 RCE 风险；用户表无角色字段，做不到安全多租户隔离，只信任部署方 |

## 11. 实施记录

设计落成后的实际代码位置（与上文口径一致，便于对照）：

| 关注点 | 位置 |
|---|---|
| 类型注册表 / 时间语义 / CAS / 对账 | `apps/api/app/services/scheduled_tasks.py` |
| 调度器装配（装载、单条同步、missed 监听） | `apps/api/app/services/scheduler.py` |
| 接口（types/CRUD/run/runs/preview） | `apps/api/app/modules/schedules.py` |
| 表 | `ScheduledTask` / `ScheduledTaskRun`（`models.py`） |
| 索引 | `core/db.py` 的 `_INDEXES` 四条 |
| 终态回写 | `job_runner._write_terminal`（唯一入口，含取消路径 `modules/jobs.py`） |
| 拉热点 handler | `job_runner._handle_hot_topics`（queue=`hot-topics`） |
| 选题简报 handler | `job_runner._handle_hot_topics_digest`（queue=`topic-digest`） |
| 简报逻辑 / 建稿 | `hot_topics.build_digest` / `_new_draft`（与「热点→稿件」共用建稿代码） |
| 页面 | `apps/web/src/views/SchedulesView.vue` + `router.js` |
| 测试 | `apps/api/tests/test_scheduled_tasks.py`（30 项） |

实现过程中被实测推翻或补充的三处：

1. **Worker 队列白名单**：见 §7 的修正块——`QUEUES` 与真实消费无关。
2. **冷却的适用范围**：下沉到 `services/hot_topics.reserve_refresh()` 后，只有交互路径调用它。
   定时路径**不**调用——cron 频率与触发互斥已经限速，若因为「15 秒冷却」被判失败，
   任务会连败三次后自己暂停，比限速本身更糟。这不是遗漏，是取舍。
3. **`get_next_fire_time` 的边界**：`(None, now)` 在「回调正好落在触发点」时会算出同一个时间，
   `next_run_at` 不前进、CAS 令牌失效、任务原地自循环。必须用
   `(observed, max(observed, now))`，让基准严格晚于已触发点。

两处为可测性做的结构选择：

- CAS 抽成 `claim_trigger(db, task_id, observed, next_run)`，测试可以直接喂同一个令牌两次，
  验证「第二个副本必须失败」。
- 回调 `fire()` 是普通同步函数：`AsyncIOExecutor` 对同步函数会自动落到线程池
  （`run_in_executor`），不需要写 `async def` 再加一层 `to_thread`。
