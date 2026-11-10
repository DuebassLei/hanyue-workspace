# hanyue-workspace 实施计划与完成度

依据 `docs/design.md` 交付可运行的纵向切片：真实可用的后端 API + 主题化前端 + Agent 受控执行。

图例：✅ 已交付并验证　🟡 部分交付（有明确缺口）　⬜ 未开始

## 阶段 1：工程骨架 ✅

- 目录：`apps/api`（FastAPI）、`apps/web`（Vue 3 + Vite）、`apps/worker`（Python Worker）、`infra`、`docs`。
- 配置：`DATABASE_URL`（默认 SQLite，可切 PostgreSQL）、`SECRET_KEY`、`STORAGE_DIR`、`CORS_ORIGINS`。
- 加列迁移：`core/db.py` 的 `_apply_additive_migrations()` 负责 SQLite 已有表的补列（`create_all` 不会加列）。

## 阶段 2：后端领域模型与迁移 ✅

- 用户/工作区、选题、稿件/版本/平台版本、日历、素材、模型连接与目录、AI 生成记录。
- 平台账号、发布任务/记录、指标快照、同步任务、图像任务。
- Agent 会话/运行/事件/工具调用/审批、Skill 注册表。
- 图文卡片新增：`card_templates`（`owner_user_id` + `config` JSON），保存底纹、边框、配色与 padding 等整份模板配置。

## 阶段 3：后端接口 ✅

- `/auth` 开发登录与会话；`/workspaces`、`/topics`、`/content`、`/calendar`、`/reviews`。
- `/models` 连接与目录（连接含 PATCH 编辑与 `provider_type`，目录含 PATCH/DELETE）；`/ai` 文本（SSE）与图像任务。
- `/platforms` 能力矩阵、授权（开发态 mock）；`/publishing` 预览、创建、队列、取消、重试（幂等）。
- `/assets`、`/image-jobs`。
- `/agent` 会话、运行、审批；`/agent/status` 运行状态与数据路径；`/agent/policy` 工具风险分级；`/skills` 注册与启停。
- `/cards/paginate` 分页 AST 契约、`/cards/presets` 画幅与内置模板、`/cards/templates` 自定义模板 CRUD（按 owner 隔离）。

## 阶段 4：前端 ✅

- 设计系统：浅色/暗色/跟随系统 Token，跟随 `data-theme`，跨会话持久化。
- 布局：桌面侧栏 + 移动底部导航；320px 无整页横向溢出；触控 44px。
- 页面（共 8 个）：总览、选题、创作（编辑器+版本）、日历、图文卡片、发布中心、素材与配图、设置。
- 设置页：模型连接的新增/编辑/测试/删除（编辑时密钥留空表示保留）、Agent 运行状态卡、数据路径卡（含复制）、工具权限分级卡。

## 阶段 5：验收 ✅

- pytest 全套：幂等键、发布状态机、权限隔离、Agent 风险分级、模型连接编辑、Agent 状态三态、卡片分页与模板隔离。
- 浏览器端到端：明暗主题对比度与无横向溢出已量测。
- 图文卡片端到端：`/cards` 渲染 6 张卡片、模板切换即时生效、控制台零报错；单张导出实测得到 1080×1440 PNG。

## 阶段 6：图文卡片工作台 ✅

- 前端 `src/features/cards/`：`constants.js`（4 画幅 / 8 模板 / 7 底纹 / 6 边框 / 3 字体 / 样例）、`paginator.js`、`CardCanvas.vue`、`MarkCardStudio.vue`、`BackendContractModal.vue`。
- 后端 `services/card_paginator.py` 与前端分页器同构；`modules/cards.py` 提供分页、预设与模板 CRUD。
- 导出：`html-to-image` 3x 单张 PNG + `JSZip` 全量图集（含 `card_manifest.json`）；导出前等待字体就绪。
- 双引擎：默认纯前端；切到后端模式后按 280ms 防抖调用 `/cards/paginate`，后端不可用时回退本地结果。
- 样式：不引入 Tailwind，使用 scoped CSS + 既有 `--color-*` token，界面随明暗主题切换。

## 待办（按优先级）

| 项 | 状态 | 说明 |
|---|---|---|
| 图像生成供应商 | ⬜ | 接入文生图服务；未接入时任务明确失败并给出 `image_provider_unavailable` |
| 平台真实 OAuth 与发布 | ⬜ | 需逐平台核实能力矩阵后填 `confirmed=true`；当前只有手动发布与记录 |
| 平台数据采集 | ⬜ | 目前是手动补记 + 同步任务占位 |
| Agent 步骤规划的 LLM 化 | 🟡 | `plan_steps` 刻意保留确定性实现（稳定、可测试）；工具调用侧已是真实 LLM |
| Agent 本地文件/命令能力 | ⬜ | 设计上明确不做，`run_shell` / `raw_sql` / `read_local_files` 保持禁止级 |
| 卡片服务端批量出图 | ⬜ | `/cards/paginate` 已返回 AST，但未接渲染队列；当前批量导出在浏览器内完成，长文大图集会占用主线程 |
| 卡片前后端 AST 对比用例 | 🟡 | 两份分页器同构但无跨语言对拍；改动估算公式时需人工保证一致 |
