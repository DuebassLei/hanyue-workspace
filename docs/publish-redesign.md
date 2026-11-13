# 发布中心重构设计方案

> 状态：待确认 → 确认后按第 12 节阶段实施
> 关联：`docs/design.md`（全局设计说明书）、`AGENT.md`（发布安全原则）

## 0. 决策记录（Q1–Q14）

| # | 决策 | 结论 |
|---|---|---|
| Q1 | 主线 | 发布流程整合为主线，信息架构重构并重 |
| Q2 | 范围 | 前端为主 + 最小后端补充 |
| Q3 | 信息架构 | 队列为主体 + 账号侧栏，去掉双 Tab |
| Q4 | 发布入口 | 发布中心统一发布向导，三模式并列；创作空间入口保留 |
| Q5 | 实时性 | 活跃任务存在时轮询（12s，退避至 60s） |
| Q6 | 交付 | 设计文档先行，确认后实施 |
| Q7 | 一稿多发 | 支持多选账号；前端循环单建接口；队列按稿件分组 |
| Q8 | 任务详情 | 右侧详情抽屉 + 完整状态流转历史（`status_history`） |
| Q9 | 组件架构 | 抽核心发布流组件族；创作空间抽屉与发布中心向导双薄壳 |
| Q10 | 统计区 | 撤大数字卡片 → 带计数的状态筛选 chips |
| Q11 | IP 白名单 | 上下文化（绑定/验证失败场景展开）；侧栏用就绪度标签 |
| Q12 | 后端清单 | jobs 分页筛选 / 详情 / summary / status_history；批量创建不加端点 |
| Q13 | 改期 | 新增 `PATCH /publishing/jobs/{id}`（approved/queued 可改） |
| Q14 | 审批 | 新增 `POST /publishing/jobs/{id}/approve`，UI 提供待审入口 |

## 1. 目标与心智模型

重构围绕一条清晰的主线：

```
账号就绪 → 发起发布（三模式） → 追踪队列 → 处理结果（回填/重试/改期）
```

设计原则：
1. **一切状态可解释**——8 种状态机状态在 UI 上都有名字、时间线与下一步建议，用户永远知道「为什么停在这」。
2. **一个心智模型**——草稿箱推送 / 排期任务 / 登记已发布，同源组件、同一套流程，不再两处两套。
3. **安静、清晰**（对齐 design.md 视觉方向）——卡片不再嵌套，信息密度服务于「重复操作高效」。

页面命名统一为「发布中心」（与 design.md 导航一致，现页面标题「发布管理」随之更改）。

## 2. 信息架构

### 2.1 桌面（≥768px）

```
┌────────────────────────────────────────────────────────────────┐
│ 发布中心                                  [刷新] [＋ 新建发布]   │
│ 账号就绪度摘要：2/3 就绪 · 1 需处理（点击定位侧栏对应卡片）         │
├───────────────────────────────────────────────────┬────────────┤
│ [全部 24][待处理 3][排期中 2][进行中 1][需处理 2][成功 16][已取消 0]│  ← 状态 chips（带计数，单选筛选）
│ [平台 ▾] [账号 ▾] [搜索__________]                 （轮询状态点）  │
│                                                                  │
│ ▾ 《A 文章》 2 个任务 · 1 需处理                    ← 按稿件分组    │
│   ● 公众号·主号     排期中 · 计划 11-14 09:00      [取消]         │
│   ● 公众号·分公司号 需核实 · 超时结果未知           [回填结果]      │
│                                                                  │
│ ● 《B 单发任务》 公众号·主号  发布中 · 尝试 1 次    （行点击→详情） │
│                                                                  │
│ （列表容器内滚动，keyset 分页加载更多）              │ 账号侧栏     │
│                                                  │ 公众号·已接入 │
│                                                  │ 能力: 草稿箱   │
│                                                  │ ● 主号 已就绪  │
│                                                  │ ● 分公司号 需验证│
│                                                  │ [绑定账号]     │
│                                                  │ ──────────    │
│                                                  │ 小红书 未接入  │
└───────────────────────────────────────────────────┴────────────┘
```

- 主操作「＋ 新建发布」常驻页头（不再随 Tab 切换变化）。
- 行内只保留 1 个「上下文主操作」（见 4.3），其余操作收进详情抽屉。
- 侧栏宽约 300px，与队列同屏；就绪度摘要条在页头下方，异常时点击滚动定位。

### 2.2 移动端（<768px，对齐 design.md 3.x）

- 页头保留标题 + 主按钮「＋ 发布」。
- 账号侧栏 → 顶部「账号就绪 N/M」折叠条，点击展开为全宽区块。
- chips 横向滚动；平台/账号筛选收入「筛选」展开面板；搜索独立一行。
- 任务行 → 卡片式（标题→状态→平台/时间→主操作，操作按钮 ≥44×44）。
- 向导与详情抽屉全屏；确认按钮固定在安全区上方（`env(safe-area-inset-bottom)`）。

## 3. 统一发布向导（三步 + 三模式）

全屏抽屉（桌面右滑入 max-width 720px；移动全屏）。顶部步骤条可回退，底部 sticky 确认条。

### Step 1 内容来源
- **选择创作空间稿件**：可搜索列表，展示标题/状态/摘要；选中自动填充标题与正文。
- **手动填写**：标题（必填，≤64 字）+ 正文（非空）。
- 从创作空间进入时预填并锁定本步。
- 持续展示「改用手动填写 / 返回选稿件」切换入口。

### Step 2 目标与方式
- **账号多选**（按平台分组，仅 `connected`；不可选的置灰并标注原因——未验证/凭据异常）。
- **方式单选**（radio 卡片）：
  1. **推送草稿箱**（公众号专属）：所选账号须全为 `wechat_official`；复用 `useWechatPublish` 的主题选择、封面/正文图配置与实时排版预览；多账号 = 逐账号推送，凭据独立。
  2. **排期任务**：立即 或 定时（`datetime-local`）；子选项「手动执行（发布后回填结果）」/「自动执行」——自动执行仅在满足五条件（能力核验 + 接口支持 + OAuth + 账号开启 + 已人工审核）时可选，否则置灰展示缺失项原因。
  3. **登记已发布结果**：为「已在平台侧手动发完、只想补记录」的场景；提交后立即展开回填表单（平台内容 ID / 链接），一次流完成「建任务 + 记录」。仅能选已绑定账号（数据模型要求 `connected_account_id`，为已知限制，侧栏文案说明）。

### Step 3 预检与确认
- 自动调用 `POST /publishing/preview`（每账号一次），问题分级：**阻塞项**（禁止提交）与**提示项**（可继续，黄色标注）。
- 草稿箱模式展示排版预览（复用渲染链路）。
- 确认条明示 **平台 · 账号 · 时间**（对齐 design.md 3.10），例：「向 公众号·主号、分公司号 推送草稿箱 · 立即执行」。
- 提交：循环调用单建接口，每个任务独立幂等键；进度反馈「正在提交 2/3」。
- 结果面板：成功列表 / 失败列表（原因 + 单项重试）；失败保留全部输入，可回上步修改。

## 4. 队列区规格

### 4.1 状态 chips（替代 4 张统计卡片）

| chip | 覆盖状态 | 色 |
|---|---|---|
| 全部 | — | 中性 |
| 待处理 | pending_review + approved | 中性（pending_review>0 时红点） |
| 排期中 | queued | 中性 |
| 进行中 | publishing | 中性 |
| 需处理 | failed + needs_attention | 警示色（count>0 时加粗） |
| 成功 | succeeded | 成功色 |
| 已取消 | cancelled | 中性 |

计数来自 `GET /publishing/jobs/summary`；chip 单选（`aria-pressed`），点击即筛选。

### 4.2 筛选与搜索
- 平台（仅任务中真实出现的平台）、账号、搜索（服务端 `q`，防抖 300ms）。
- 「刷新」按钮保留在工具行右侧（轮询的兜底）。
- 筛选状态写入 URL query（可分享、可回退）。

### 4.3 任务行与上下文主操作
行字段顺序对齐 design.md 内容卡片规范：**标题 → 状态 → 平台/账号/目标 → 时间 → 错误摘要 → 操作**。

上下文主操作映射（每行最多 1 个主按钮，行点击打开详情抽屉）：

| 状态 | 主操作 |
|---|---|
| pending_review | 审核通过（拒绝在抽屉内） |
| approved（manual 模式） | 回填结果 |
| approved（auto 模式） | 无（显示「等待系统执行」） |
| queued | 取消 |
| publishing | 无（显示执行中） |
| failed（可重试） | 重试 |
| failed（不可重试） | 查看原因（开抽屉） |
| needs_attention | 回填结果 |
| succeeded | 外链 permalink（有则显示） |
| cancelled | 删除 |

### 4.4 按稿件分组
- `content_item_id` 相同且任务数 >1 → 成组；组头 = 稿件标题（链到创作空间）+「N 个任务 · M 需处理」。
- 组内按计划时间/创建时间排序；全部终态的组默认折叠。
- 无稿件关联的任务平铺，不参与分组。

### 4.5 详情抽屉（JobDetailDrawer）
- 头部：标题 · 状态 · 平台/账号/目标/方式。
- **状态时间线**：`status_history` 正序，每步 = 时间 · 状态名 · 触发方（用户/系统）· 备注（错误码）。
- **结果记录**：PublishRecord 列表（result_status、remote id、permalink、publishedAt）。
- **错误详情卡**：错误码 + 完整 message + **下一步建议**（见 4.6）。
- 内容快照：标题/正文折叠预览 +「在创作空间打开」；草稿箱任务显示主题 tokens 快照说明。
- 操作区（按状态可用性渲染）：审核通过/拒绝、改期、取消、重试、回填结果、删除；改期为内联表单（时间 + 可选标题/正文）。

### 4.6 错误码 → 建议文案映射（对齐 AGENT.md 原则）

| error_code | 建议 |
|---|---|
| timeout / network | 「结果未知。请到平台后台核实：已发布→回填结果；未发布→可重试。」**绝不自动重发** |
| rate_limited | 平台限流，稍后可重试 |
| server_error | 平台服务异常，可重试 |
| auth_expired / forbidden | 授权失效——引导到账号侧栏改凭据/重新验证 |
| validation_failed / content_rejected | 内容未通过平台校验，修改后重建任务（不盲目重试） |
| adapter_not_implemented | 该平台自动发布适配器未实现，请手动发布后回填 |

## 5. 账号侧栏规格

- 按平台分组：平台名 + 接入标签（已接入/未接入）+ 能力标签（草稿箱/自动发布/内容数据）+ 接入方式（AppSecret/未配置）。
- 账号行：名称 + **就绪度标签** + 操作（验证 / 改凭据 / 解绑——解绑需二次确认）。
- 就绪度规则（前端计算，不新增端点）：
  - `connected` 且 `authSource ≠ demo` → **已就绪**
  - `authSource = demo` → **演示账号（无凭据）**
  - 本会话内验证失败 → **凭据异常**（已知限制：会话级记忆；后续可加 `last_probe_error` 字段，本期不做）
  - 公众号且最近验证错误指向 IP 白名单 → **IP 待配置**
- 未接入平台：单行占位「暂未接入 · 接入后可绑定」，不展开。
- **IP 白名单上下文化**（EgressIpWidget）：出现在 (a) 绑定弹窗说明区（折叠）(b) 验证失败且错误指向 IP 时的 banner 内 (c) 账号卡「IP 待配置」展开区。不再常驻页面。

## 6. 组件拆分与复用关系

```
apps/web/src/features/publish/
├── flow/                          # 核心发布流（双入口共用内核）
│   ├── usePublishFlow.js          # 步骤状态机 + 提交编排（含多账号循环/幂等键）
│   ├── PublishWizardShell.vue     # 全屏抽屉壳 + 步骤条
│   ├── StepContentSource.vue      # Step 1
│   ├── StepTargetMode.vue         # Step 2（账号多选 + 三模式卡）
│   └── StepReviewConfirm.vue      # Step 3（预检 + 确认条 + 结果面板）
├── queue/
│   ├── QueueFilterChips.vue
│   ├── QueueToolbar.vue
│   ├── JobGroupList.vue           # 分组逻辑（>1 才分组）
│   ├── JobRow.vue                 # 行 + 上下文主操作
│   ├── JobDetailDrawer.vue        # 详情抽屉
│   └── JobTimeline.vue            # status_history 时间线
├── accounts/
│   ├── AccountSidebar.vue         # 平台分组 + 就绪度
│   ├── AccountCard.vue
│   ├── WechatBindModal.vue        # 从 PublishingView 抽出
│   ├── CredentialModal.vue
│   └── EgressIpWidget.vue         # 上下文化 IP 组件
└── composables/
    └── useJobsPolling.js          # 活跃任务轮询

composables/useWechatPublish.js    # 保持不变，被 flow/ 复用
features/publish/PublishDrawer.vue # 薄壳化：预填 Step1 + 锁定草稿箱模式
views/PublishingView.vue           # 组装层，目标 <300 行
```

- **创作空间 PublishDrawer** = 预填并锁定 Step1、只开放草稿箱模式的向导薄壳（体验不变）。
- **发布中心向导** = 完整三步薄壳。
- `StatusTag` 扩展映射，覆盖全部 8 种状态（图标 + 文字 + 色，不单靠颜色）。

## 7. 后端接口变更（7 项）

| # | 变更 | 说明 |
|---|---|---|
| 1 | `GET /publishing/jobs` 升级 | 加 `limit`/`cursor`（keyset，复用 `core/pagination.py`）+ `status`/`platform`/`accountId`/`contentItemId`/`q` 筛选；status 支持逗号分隔多值（`failed,needs_attention`）。响应保持裸数组，下一页游标沿用仓库既有的 `X-Next-Cursor` 响应头约定（实施时与仓库分页基建对齐，替代原 `{items, nextCursor}` 方案，前端是唯一消费方） |
| 2 | `GET /publishing/jobs/summary` | `{total, byStatus}` 按状态计数 |
| 3 | `GET /publishing/jobs/{id}` | 详情：job + PublishRecord 列表 + status_history |
| 4 | `PublishJob.status_history` | JSON 列（default `[]`）；新增 `publish_state.apply_transition(job, target, actor, note=None)` 包装 `assert_transition` 并追加 `{status, at, actor, note}`；创建/approve/cancel/retry/job_runner 提交/回填/草稿推送各变更点全部接入；存量数据读时以 `{status: 当前值, at: updated_at, actor: "system", note: "历史初始化"}` 兜底 |
| 5 | `PATCH /publishing/jobs/{id}` | 改 `scheduledAt`/`title`/`body`；仅 `approved`/`queued` 且未开始执行（409 拒绝其他状态）；校验复用创建逻辑 |
| 6 | `POST /publishing/jobs/{id}/approve` | `pending_review → approved`（`assert_transition` 校验），记录 `reviewed_by`；「拒绝」= 既有 cancel |
| 7 | 批量创建 | **不加端点**，前端循环单建（每任务独立幂等键） |

实施时顺带修复的两个状态机缺口（P1 交付内容）：
- **失败状态悬空**：`_on_failure` 此前只写错误字段，发布任务会永远卡在 publishing/queued；现按「远端已有内容→succeeded、致命错误→failed、结果未知（超时/网络/限流）→needs_attention」推进，`needs_attention` 首次真实可达。
- **重试不重新入队**：`retry` 此前只改状态，草稿箱/自动任务重试后会永久滞留 queued；现按创建时门槛重新投递后台队列（draft 直接入队；auto 保持「能力核验 + OAuth」双门槛；manual 由用户手动执行不入队）。
- 状态机补两条真实流转：`queued → approved`（排期到点提升）、`approved → publishing`（Worker 领取标记执行中）。

数据迁移：SQLite 开发库需 `ALTER TABLE publish_jobs ADD COLUMN status_history JSON DEFAULT '[]'`（或删库重建）；启动时做轻量 ensure-column（沿用仓库现有做法）。测试：`test_publish_state.py` / `test_api.py` 扩展并保持 pytest 全绿。

## 8. 轮询与实时性（useJobsPolling）

- 触发条件：存在 `queued`/`publishing` 任务 **且** 页面可见。
- 间隔 12s；连续失败指数退避 ×2，上限 60s，成功后恢复。
- `visibilitychange`：隐藏暂停；恢复可见立即刷新。
- 静默刷新不打断交互：summary 永远刷新；列表在筛选条件未变化时刷新；详情抽屉打开时同步刷新该任务详情。
- 竞态策略：以服务端响应为准；用户操作 in-flight 的行不被轮询结果覆盖（乐观更新 + 冲突以服务器为准）。

## 9. 空 / 加载 / 错误状态

| 场景 | 处理 |
|---|---|
| 无任务 | 「还没有发布任务」+ CTA「新建发布」；无账号时改为引导绑定 |
| 筛选无结果 | 「没有匹配的任务」+「清除筛选」 |
| 无账号 | 侧栏引导绑定；向导 Step2 空态说明 |
| 首次加载 | 骨架屏（列表 3 行 + 侧栏 2 卡） |
| 接口错误 | banner + 重试；保留用户输入 |
| 提交部分失败 | 结果面板区分成功/失败，失败可单项重试，不整体回滚 |

## 10. 移动端规格

- <768px 单列；侧栏折叠条；chips 横滑；任务卡片化。
- 向导 / 详情抽屉全屏；步骤条简化为「1/3」。
- 确认按钮固定于安全区上方；触控目标 ≥44×44 CSS px；`env(safe-area-inset-*)` 支持。
- 复杂区域（时间线、预览）在局部容器内滚动，320px 宽与 200% 缩放无整页横向溢出。

## 11. 无障碍要点

- chips：`role=group` + `aria-pressed`，计数包含在可读名称内。
- 任务行可键盘聚焦，Enter 打开抽屉；主操作为独立按钮。
- 抽屉/向导：`role=dialog` + `aria-modal`、焦点陷阱、Esc 关闭、焦点返回触发元素（复用 AppModal 基础设施行为）。
- 时间线：`<ol>` 语义列表；触发方用文字（用户/系统），不只用图标。
- 状态三通道：图标 + 文字 + 颜色。
- 确认按钮 `aria-describedby` 指向「平台 · 账号 · 时间」摘要。
- 尊重 `prefers-reduced-motion`：抽屉/向导动画降级为即时切换。

## 12. 实施阶段与验收标准

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1 后端 | status_history + apply_transition + 端点 1–6 + 测试 | pytest 全绿；Swagger 可试；keyset 翻页正确 |
| P2 组件拆分 | 纯重构不改行为：抽 accounts/* 与 queue/* 骨架，PublishingView 变组装层 | 现有功能手动回归一致；`npm run build` 通过 |
| P3 发布流内核 | usePublishFlow + 三步组件 + 双入口薄壳化 | 创作空间草稿箱推送全链回归；发布中心可完成三模式；多账号批量提交/部分失败重试 |
| P4 队列重构 | chips/分组/详情抽屉/时间线/轮询/上下文操作/改期/审批 | 全状态流转 UI 正确；轮询生效；时间线含触发方；改期与审批走通 |
| P5 打磨 | 移动端、无障碍、错误文案映射、空态 | 320px/200% 缩放无溢出；键盘走查；reduced-motion；错误建议文案与映射表一致 |

每阶段独立 commit；P3、P4 完成后各做一次全链路手动验收（绑定 → 发布 → 追踪 → 回填）。

### P2 实施记录（已完成）

实际拆出（`features/publish/` 下新增 8 个文件，`PublishingView.vue` 1201 → 约 770 行，模板/样式逐行搬运不改行为）：

| 文件 | 职责 |
|---|---|
| `queue/jobActions.js` | 行操作可用性纯函数（canRecord/canRetry/canCancel/canDelete/jobTargetLabel），JobRow 展示与页面 deleteJob 守卫共用同一份规则（新增，原计划未列出） |
| `queue/JobRow.vue` | 任务行 + 上下文操作按钮（事件交还页面） |
| `queue/QueueToolbar.vue` | 状态 Tab + 平台/账号筛选 + 搜索，全受控（props + `update:` 事件，页面 `v-model:xxx`） |
| `accounts/PlatformCard.vue` | 单平台卡片：能力标签 + 账号列表 + 绑定入口，`connectorModeLabel` 内聚 |
| `accounts/EgressIpWidget.vue` | IP 白名单小部件；探测状态仍由页面级单次探测持有（prop 下发 + 事件上行） |
| `accounts/WechatBindModal.vue` | 绑定表单 + `/wechat-mp/bind` 请求内聚，`bound/error/close` 事件交还页面处理 notice/banner/reload |
| `accounts/CredentialModal.vue` | 改凭据表单 + 保存请求；请求失败留在弹窗内展示（与原行为一致），成功交还页面 |

实施偏差与发现：

1. **AccountSidebar 延后到 P4**：当前账号区块尚未有独立的侧栏形态（P4 才引入），现在抽出只能得到空转发壳，违背「骨架=行为宿主」原则；P4 落地侧栏时一并实现。
2. **删除死代码**：视图中原 `.egress-*` CSS 块（约 60 行）对应元素已不存在，属历史遗留，直接删除。
3. **`.btn-xs` 发现**：该类仅定义于 `StudioView.vue` 的 scoped 样式，发布中心的「重新检测/复制」按钮实际渲染为默认尺寸。P2 保持原样（类名保留、不加规则）以确保零视觉变化；是否将 `.btn-xs` 提升到 `theme.css` 属视觉决策，留待 P5 打磨统一处理。
4. **scoped 样式归属**：`.section-subtitle` 在页面（队列卡头）与 PlatformCard（平台卡头）各自声明；`.queue-toolbar/.queue-filters/.queue-search` 及其媒体查询随模板迁入 QueueToolbar。

### P3 实施记录（部分完成）

已完成共享发布流内核与发布中心入口：

- `flow/usePublishFlow.js`：三步状态、稿件读取、账号多选、按账号预检、幂等键、草稿箱/排期/自动/登记四种模式、逐账号提交、部分失败结果与单项重试。
- `flow/StepContentSource.vue`、`StepTargetMode.vue`、`StepReviewConfirm.vue`：三步内容、目标/模式、预检确认 UI。
- `flow/PublishWizardShell.vue`：发布中心统一向导壳；发布中心页头「新建发布任务」已切换到该入口，旧单步发布弹窗已删除。
- 自动执行按能力矩阵与账号状态置灰：必须同时满足平台 `confirmed + publish`、账号开启自动发布、OAuth；公众号当前 `publish=false`，不会误开放自动群发。
- 草稿箱模式继续调用 `/wechat-mp/drafts/preview` 与 `/wechat-mp/drafts`；普通模式调用 `/publishing/preview` 与 `/publishing/jobs`；登记模式创建任务后调用 `record-manual-result`。

创作空间入口已进一步收敛：`PublishDrawer.vue` 现在是薄适配壳，原公众号专属实现整体迁至 `WechatDraftPanel.vue`，StudioView 的 props/emits 契约不变，主题、封面/正文图片选择器、排版渲染、`asset://` hydration 和草稿推送链路均保留。

P3 核心实现现已完成：`WechatDraftPanel` 通过 draft adapter 接入 `usePublishFlow` 的预检、逐账号提交、结果收集和重试；微信主题/素材/排版状态仍由专属面板维护。正式发布前仍需手动验证创作空间草稿箱真实账号链路。

### P4/P5 实施记录（已完成代码交付）

- P4：新增状态 chips（summary 计数）、服务端筛选与 300ms 搜索防抖、URL query 同步、活跃任务轮询（12s，失败退避至 60s）、按稿件分组、详情抽屉、状态时间线、结果记录、错误建议、审批/改期/取消/重试/回填/删除操作，以及账号就绪摘要。
- P5：任务行键盘聚焦与 Enter 打开详情、`aria-pressed` chips、对话框沿用 AppModal、移动端队列/侧栏单列布局、轮询状态提示和错误建议文案。
- 已知测试基线：前端 `npm run build` 通过；后端 216 项通过，2 个既有平台能力断言失败（平台目录已包含 xiaohongshu/douyin，但旧测试仍断言只有公众号），与本次发布重构无关。


## 13. 风险与兼容

| 风险 | 对策 |
|---|---|
| jobs 响应形态 breaking | 前端唯一消费方；同步更新测试与文档 |
| status_history JSON 体积 | 每任务流转次数受重试上限约束，无膨胀风险 |
| PublishDrawer 薄壳化回归 | P3 单独验收创作空间全链路后再切换入口 |
| 轮询与用户操作竞态 | 以服务端为准 + in-flight 行保护 |
| 老库加列 | 提供 ALTER 语句；开发态可删库重建 |
