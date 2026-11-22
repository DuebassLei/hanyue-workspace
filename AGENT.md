# AGENT.md — 汉月工作台开发工作流入口

> 本文件是 AI Agent 与开发者的统一上下文入口。每次开始新任务前先读此文件，再按需深入各子模块。

---

## 可用项目技能（自动引用）

项目技能放在 `.workbuddy/skills/`（已链接到 `.claude/skills/`，两处内容同源）。遇到下列任务时，先加载对应技能再动手：

| 技能 | 触发场景 | 内容位置 |
| --- | --- | --- |
| `project-analysis-team` | 「分析这个项目 / 项目体检 / 接手陌生仓库」出只读分析报告；或「用专家团实现这个规划 / 多角色并行开发」按计划落地 | `.claude/skills/project-analysis-team/SKILL.md` |

加载方式：优先用 Skill 工具按技能名调用；若该技能未出现在可用技能列表（缓存未刷新），直接读取上表 `SKILL.md` 并严格按其流程执行。技能的 `name`/`description` 与 WorkBuddy 兼容，无需改写。

---

## 项目一句话描述

**汉月工作台**：面向自媒体创作者的全栈 AI 创作平台。纵向切片：灵感 → AI 创作 → 配图 → 发布审核 → 数据复盘，含受控 Agent 执行层（原「选题库」与「创作空间」「创作规则」已下线，成稿由成稿台承接）。

---

## 技术栈速查

| 层   | 技术                                                                                 | 入口                                       |
| --- | ---------------------------------------------------------------------------------- | ---------------------------------------- |
| 前端  | Vue 3 + Vite + Pinia + Vue Router                                                  | `apps/web/`                              |
| 后端  | FastAPI + SQLAlchemy 2 + Pydantic v2                                               | `apps/api/`                              |
| 数据库 | PostgreSQL 16（本地 `infra/docker-compose.dev.yml` + 生产 compose）；SQLite 仅无 Docker 兜底  | `DATABASE_URL` env                       |
| 队列  | Redis + Worker / 内置线程池回退                                                           | `apps/worker/worker.py`                  |
| 图像  | Pillow（同步小图/队列大图）+ AI 生图适配器                                                        | `apps/api/app/services/image_service.py` |
| 容器  | Docker Compose（web + api + worker + redis + postgres），入口 `infra/scripts/deploy.sh` | `infra/docker-compose.yml`               |

---

## 目录地图

```
hanyue-workspace/
├── AGENT.md              ← 你在这里
├── README.md             ← 完整部署与功能说明
├── apps/
│   ├── api/              ← FastAPI 后端
│   │   ├── app/
│   │   │   ├── main.py           启动入口、lifespan、路由注册
│   │   │   ├── models.py         SQLAlchemy ORM 模型
│   │   │   ├── schemas.py        Pydantic 请求/响应 schema
│   │   │   ├── modules/          路由模块（19个，见下表）
│   │   │   ├── services/         领域服务（18个，见下表）
│   │   │   └── core/             基础设施（db/config/deps/logging/pagination/queue/security/ssrf）
│   │   ├── tests/                pytest 测试（232 项）
│   │   ├── requirements.txt      锁定版本
│   │   ├── requirements-prod.txt 含 psycopg[binary] 与 redis
│   │   └── .env.example          环境变量模板
│   ├── web/              ← Vue 3 前端
│   │   ├── src/
│   │   │   ├── views/            11 个页面视图
│   │   │   ├── features/         功能模块（按页面组织）
│   │   │   ├── components/       公共组件
│   │   │   ├── composables/      组合式函数
│   │   │   ├── stores/           Pinia 状态管理
│   │   │   ├── api/              后端 API 客户端（统一 /api/v1 前缀）
│   │   │   └── router.js         路由定义
│   │   └── package.json
│   └── worker/           ← 独立队列消费者
│       └── worker.py             复用 api 领域服务，Redis 模式专用
├── docs/
│   ├── design.md         产品设计说明书（权威来源）
│   ├── plan.md           实施计划
│   ├── setup.md          完整启动与配置手册
│   ├── docker-deploy.md  Docker 部署方案（发版/备份/迁移/排障）
│   ├── wechat-layout.md  公众号排版渲染管线说明
│   └── studio-redesign.md Studio 页重设计说明
└── infra/
    ├── docker-compose.yml
    ├── docker-compose.lowmem.yml   2 核 2G 覆盖层（<3GB 内存自动叠加）
    ├── nginx/default.conf          前端静态托管 + /api 反代
    ├── scripts/deploy.sh           部署入口（up/backup/restore/migrate）
    ├── scripts/migrate_sqlite_to_pg.py
    ├── platform-evidence.json       Phase 0 核验证据（默认空/全 false）
    └── platform-evidence.example.json
```

---

## 路由模块一览（`apps/api/app/modules/`）

| 文件                 | 路由前缀                           | 职责                                                      |
| ------------------ | ------------------------------ | ------------------------------------------------------- |
| `auth.py`          | `/api/v1/auth`                 | 邮箱密码注册/登录/改密、开发态 subject 登录、令牌吊销                        |
| `workspaces.py`    | `/api/v1/workspaces`           | 工作区 CRUD、多工作区隔离                                         |
| `topics.py`        | `/api/v1/workspaces/current`   | 当前工作区上下文（选题库已下线，仅保留此端点）                                 |
| `content.py`       | `/api/v1/content`              | 稿件版本、平台版本                                               |
| `ai_content.py`    | `/api/v1/ai`                   | SSE 流式生成、大纲/正文/润色（`GET /ai/hot-topics` 为贴图构思等数据源）  |
| `hot_topics.py`    | `/api/v1/hot-topics`           | 热点选题：多源采集、事件合并、评分、筛选、一键成稿                               |
| `ai_images.py`     | `/api/v1/ai/image-generations` | AI 生图任务                                                 |
| `assets.py`        | `/api/v1/assets`               | 素材上传、7 种图像处理、分格                                         |
| `cards.py`         | `/api/v1/cards`                | 图文卡片生成（8套模板）、PNG/ZIP 导出                                 |
| `characters.py`    | `/api/v1/characters`           | 角色库：用户级角色卡 CRUD、多参考图、正文抽取草稿（不落库）                        |
| `prompts.py`       | `/api/v1/prompts`              | 提示词库 CRUD（生图提示词模板，按用户隔离）                                |
| `handdraw.py`      | `/api/v1/handdraw`             | 手绘风格提示词库（内置模板 + 用户收藏）                                   |
| `publishing.py`    | `/api/v1/publishing`           | 发布队列、平台账号、OAuth 接入                                      |
| `wechat_mp.py`     | `/api/v1/wechat-mp`            | 公众号草稿箱、排版主题、绑定验证                                        |
| `studio.py`        | `/api/v1/studio`               | 成稿台：段落级改稿、对话编排、质检修复、撤销/版本对比、横幅规格                          |
| `studio_layout.py` | `/api/v1/studio`               | 成稿台排版：正文渲染与风格/配色/SVG 卡目录（预览直接用，不跳排版工作台）                    |
| `studio_media.py`  | `/api/v1/studio`               | 成稿台媒体：封面生成/选图、正文插图生成与回填、SVG 动画卡插入                              |
| `studio_publish.py`| `/api/v1/studio`               | 成稿台发布：存入文章管理、按平台建发布任务、排期与复盘                                     |
| `skill_center.py`  | `/api/v1/skill-center`         | 技能中心：技能卡 CRUD、蒸馏、导入导出（独立技能库；创作规则模块已下线）                     |
| `pet_chat.py`      | `/api/v1/pet`                  | 成稿台悬浮助手：会话读写，技能来自技能中心                                          |
| `platform_auth.py` | `/api/v1/platforms`            | Phase 0 核验、OAuth 连接器、维护视图                               |
| `agent.py`         | `/api/v1/agent`                | Agent 会话、Skill、工具授权、审批                                  |
| `jobs.py`          | `/api/v1/jobs`                 | 统一任务查询（keyset 分页）                                       |
| `models_module.py` | `/api/v1/models`               | 模型连接管理、真实连通性测试                                          |
| `dashboard.py`     | `/api/v1/dashboard`            | 总览、日历、周复盘                                               |
| `preferences.py`   | `/api/v1/preferences`          | 用户偏好（主题）服务端持久化                                          |
| `rbac.py`          | `/api/v1/rbac`                 | 角色/权限/菜单：我的权限、角色 CRUD、菜单 CRUD、用户授权、审计（见 `docs/rbac.md`） |

---

## 领域服务一览（`apps/api/app/services/`）

| 文件                  | 职责                                     |
| ------------------- | -------------------------------------- |
| `ai_gateway.py`     | LLM 统一出口：SSE 流、降级、429/5xx 退避           |
| `ai_article.py`     | 文章生成编排（大纲→正文→润色）                       |
| `agent_runtime.py`  | Agent 执行引擎、工具调用、暂停/恢复/取消               |
| `agent_policy.py`   | 工具风险分级、权限快照、禁止级拒绝                      |
| `rbac_service.py`   | RBAC 幂等播种（权限点/内置角色/菜单）、菜单树组装、路径重算、审计写入 |
| `agent_status.py`   | Agent 状态查询与 Provider 脱敏列表              |
| `image_service.py`  | Pillow 同步处理、派生图、条漫分格                   |
| `image_provider.py` | AI 生图适配器（OpenAI 兼容协议）                  |
| `job_runner.py`     | 统一任务执行器（线程池/Redis 两后端共用）               |
| `publish_state.py`  | 发布状态机、幂等提交、排期自动提升                      |
| `wechat_article.py` | 公众号文章三步推送（封面→正文图→draft/add）            |
| `wechat_layout.py`  | 公众号排版渲染（内联样式、预设/自定义主题）                 |
| `hot_ranking.py`    | 纯函数：标题归一、同事件合并、赛道/风险词表、100 分评分         |
| `hot_topics.py`     | 热点选题：固定格式 ingest、当天全量重算、聚簇落库、筛选读取、一键成稿；兼作「灵感」面板数据源 |
| `card_paginator.py` | 图文卡片 `/cards/paginate` 协同模式            |
| `provider_probe.py` | 模型供应商连通性探测                             |
| `article_blocks.py` | 纯函数：Markdown 分段、块类型、applyOps 白名单改稿、快照/版本 diff、修复分组 |
| `article_quality.py` | 质检：朱雀 API 检测与片段对齐、报告解析回填、无密钥时的本地规则打分 |
| `studio_pipeline.py` | 成稿流水线：写正文/出标题/对话改稿/单段与批量修复/横幅，含任务并发与停止；写正文按 `resources/studio_skills/styles/*.md` 的风格档拼 prompt |
| `studio_render.py`   | 排版渲染：选项合并补默认、封面/插图取图、横幅前置、复用 `wechat_layout_engine` 出正文 |
| `studio_media.py`    | 媒体：生图入队与 job 回填、插图插块/移除、SVG 卡 fence 插块（状态都在稿件 JSON 里） |
| `studio_publish.py`  | 发布：导出成正式稿件后建 `publish_jobs`、平台归一与幂等、按稿件聚合复盘状态 |
| `studio_repo.py`     | 成稿台仓储：文章头 + 段落 + 快照在 ORM 与 dict 之间搬运 |
| `author_skills.py`   | 技能中心：独立技能卡 CRUD、config 校验、编译 prompt、资料蒸馏、导入导出 |
| `pet_conversation_service.py` | 宠物对话：意图识别 → 技能编译 → 模型生成；只写对话，不改稿件 |

---

## 前端页面一览（`apps/web/src/views/`）

| 文件                   | 路由            | 功能                                                          |
| -------------------- | ------------- | ----------------------------------------------------------- |
| `OverviewView.vue`   | `/`           | 总览仪表盘                                                       |
| `CalendarView.vue`   | `/calendar`   | 内容日历（`/topics` 已重定向到文章管理）                                   |
| `HotTopicsView.vue`  | `/hot-topics` | 热点选题：多平台事件的合并、评分、筛选与一键成稿                                    |
| `PaperView.vue`      | `/paper`      | 成稿台：段号稿纸/排版预览双视图，点整段进编辑、逐段改稿、对话改稿、质检修复、标题、横幅、封面插图、发布复盘（一页完成） |
| `components/paper/`  | —             | 成稿台组件：右栏四个面板 + 单段编辑器 `PaperBlockEditor`（显式保存/快捷键/本机草稿）、卡片插入弹窗 `PaperCardInsert`（图片/长图/长提示词）、批注条 `PaperAnnotation`（改写建议与主操作）、逐段 diff `PaperDiffDialog` |
| `composables/useParagraphDrafts.js` | — | 成稿台段落草稿：模块级单例 + localStorage 暂存，切稿/刷新不丢未保存的修改 |
| `utils/editorFormat.js` | — | 成稿台格式化规则：强调三件套（`**`/`==`/`++`）、三组插入项、`cardMarkdown`、`formatSelection`；纯函数，由 `tests/editorFormat.test.mjs` 守着 |
| `CardsView.vue`      | `/cards`      | 图文卡片生成与导出                                                   |
| `PromptsView.vue`    | `/prompts`    | 提示词库维护（生图提示词模板）                                             |
| `HanddrawView.vue`   | `/handdraw`   | 手绘风格提示词库                                                    |
| `CharactersView.vue` | `/characters` | 角色库：跨项目复用的角色卡、多参考图与正文抽取草稿                                   |
| `AssetsView.vue`     | `/assets`     | 素材管理、AI 生图任务列表                                              |
| `PublishingView.vue` | `/publishing` | 发布中心（含公众号草稿箱入口）                                             |
| `ModelsView.vue`     | `/models`     | 系统 → 模型管理：文本 / 生图分组登记连接，拉取供应商模型列表并支持关键字筛选填充代号，配置内测试与真实连通性测试 |
| `SettingsView.vue`   | `/settings`   | 外观与主题、Agent 与 Skills、数据与安全、系统日志                             |

---

## 本地开发快速启动

### 0. 本地数据库（PostgreSQL + Redis）

不用容器（本机已装 PostgreSQL）：

```bash
cd infra
./scripts/dev-pg.sh up        # 自建私有集群 127.0.0.1:5433，数据在 infra/.pgdev/data
./scripts/dev-pg.sh bootstrap # 可选：起库 + 建表 + 创建首个账号
# 复用系统已装的 PG 服务（默认 5432，需 postgres 超级用户密码）：
# PG_MODE=service ./scripts/dev-pg.sh up
```

Windows PowerShell 用 `.ps1` 版（`.sh` 会被 WSL 的 bash.exe 接管，不起作用）：  
`.\scripts\dev-pg.ps1 up` / `status` / `bootstrap` / `down`。该文件必须保持 UTF-8 with BOM。

用容器只起库：

```bash
cd infra
./scripts/dev-db.sh up        # 独立卷/端口 5433、6380，不会碰生产数据
./scripts/dev-db.sh bootstrap # 可选：起库 + 建表 + 创建首个账号
```

两者抢同一个 5433 端口，同时只用一个。`apps/api/.env` 的 `DATABASE_URL` 已指向 `127.0.0.1:5433`。

### 1. 后端

```bash
cd apps/api
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-prod.txt  # Windows
# requirements-prod.txt 提供 PG 驱动 psycopg，本地用 PG 必须装
cp .env.example .env                                           # 按需修改
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

| 服务      | 地址                           |
| ------- | ---------------------------- |
| API     | <http://127.0.0.1:8000>      |
| Swagger | <http://127.0.0.1:8000/docs> |

### 2. 前端

```bash
cd apps/web
npm install
npm run dev   # http://localhost:5173
```

> Vite 已将 `/api` 代理到 `127.0.0.1:8000`，无需手动配置跨域。

### 3. 获取开发 Token

正式登录（邮箱 + 密码，先注册；库里没有密码账号时首个账号自动放行）：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"me@example.com","password":"Str0ng-pass-2026"}'
```

开发态免密入口（需 `ALLOW_DEV_LOGIN=true`，生产关闭）：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"subject":"local-dev"}'
```

两个接口都返回 `{token, user, workspaceId}`，后续请求带 `Authorization: Bearer <token>`。

---

## 测试与验证节奏

**默认走增量，全量只在交付前跑一次。** 改几行就全量 `pytest -q` + `npm run build` 是最常见的时间浪费：后端全量 600+ 项、前端 `vite build` 是生产打包（压缩、分包、产物校验），都是分钟级。开发中用秒级的增量检查确认改动没坏，收尾交付时再跑全量兜底。

### 开发中：按改动范围选最小检查

| 改了什么                                 | 跑什么                                                    | 不跑什么              |
| ------------------------------------ | ------------------------------------------------------ | ----------------- |
| 后端单个模块 / 服务                          | `pytest tests/test_<对应文件>.py -q`，跨模块时加 `-k "关键词"`      | 全量 pytest         |
| 后端纯逻辑、不碰路由/模型                        | 同上，只跑直接覆盖的用例                                           | compileall、前端任何命令 |
| 前端 `.vue` / `.js`                    | **不用跑命令**：`npm run dev` 开着时 Vite HMR 即改即见，报错在终端和浏览器控制台 | `npm run build`   |
| 前端构建配置（`vite.config`、`package.json`） | `npm run build`                                        | —                 |
| 只改文档 / 注释                            | 不跑                                                     | 全部                |

```bash
cd apps/api
# 增量：只跑受影响的测试文件（多数改动 10 秒内出结果）
.venv/Scripts/python.exe -m pytest tests/test_skills.py -q
# 跨文件但不想全量时按关键字收窄
.venv/Scripts/python.exe -m pytest -q -k "skill_registry or skills"
# 改了 import / 语法、想快速确认能编译：只编译改过的文件，不要 compileall 整棵 app
.venv/Scripts/python.exe -m py_compile app/modules/skill_center.py
```

选测试文件的规则：路由改动对应 `tests/test_<模块名>.py`；服务改动先找引用它的测试文件，找不到再加一个最小用例，不要靠全量碰运气。

### 交付前：全量一次

功能完成、准备提交或合并时跑完整回归，这是唯一需要全量的时机：

```bash
cd apps/api
.venv/Scripts/python.exe -m pytest -q                  # 全量
.venv/Scripts/python.exe -m compileall -q app          # 编译检查
cd ../web && npm run build                             # 前端生产构建
```

**测试注意事项**：

- 不要在测试文件顶层 `import app.services` 或 `app.core.db`，否则会在 fixture 设置临时 DB 前绑定开发库，导致测试写脏 `hanyue.db`。
- 新服务的测试用 `conftest.py` 中已有的 `client` / `db` fixture，不要重新初始化。

---

## 常用开发任务

### 新增 API 路由

1. 在 `apps/api/app/modules/` 新建路由文件（参考 `topics.py`）。
2. 在 `apps/api/app/main.py` 用 `app.include_router()` 注册，统一前缀 `/api/v1`。
3. 响应 JSON 用 camelCase（`CamelModel` + `alias_generator`），错误结构含 `code` / `message` / `retryable`。
4. 列表端点用 `core/pagination.py` 的 `paginate()`，务必带复合排序键 `id`，用 `set_cursor_header()` 回传游标令牌。

### 新增数据库字段

1. 在 `models.py` 加列（带默认值）。
2. 在 `core/db.py` 的 `_ADDED_COLUMNS` 字典登记，`_apply_additive_migrations()` 会幂等执行 `ALTER TABLE ... ADD COLUMN`。
3. 对应更新 `schemas.py`。

### 新增领域服务

1. 在 `services/` 新建文件，只注入 `DbSession` / `Settings`，不直接依赖路由层。
2. 出站 HTTP 必须走 `core/ssrf.py` 的 `safe_request()` / `pin_url()`，禁止自行创建 httpx client 直连外部域名。

### 新增前端页面

1. 在 `views/` 新建 `.vue` 文件。
2. 在 `router.js` 注册路由。
3. API 调用统一走 `src/api/` 下的封装（已处理 401 重登、token 刷新）；分页列表用 `api.getPage(path)` 拿 `{ data, nextCursor }`。

---

## 关键约束（改代码前必读）

1. **禁止占位文本冒充成功**：未配置模型/供应商时返回明确错误码，不写假数据。
2. **禁止级工具不执行**：`run_shell` / `raw_sql` / `read_local_files` 在工具层直接拒绝，不绕过。
3. **出站 HTTP 必过 SSRF 防护**：`safe_request()` 校验字面量 IP、DNS 解析、逐跳重定向，且把校验后 IP 固定连接层防 DNS rebinding。
4. **发布超时结果未知 → `needs_attention`**，绝不自动重发；远端已有内容则直接视为成功。
5. **自动发布五条件**：平台能力已核验 + 接口支持发布 + 官方 OAuth 授权 + 账号开启自动发布 + 已人工审核，缺一不可。
6. **生产安全门禁**：`ENVIRONMENT=production` 时拒绝弱 `SECRET_KEY` / `ALLOW_DEV_LOGIN=true` / SQLite；正式登录为邮箱密码（scrypt 哈希、失败锁定、改密踢旧会话）。
7. **接口权限点**：业务模块用 `dependencies=[require_permission("xxx")]` 强制校验，前端菜单只是投影。


挂之前先判断「这是不是单一页面的专属能力」——`/models/catalog`、`/ai/text-generations`、  
`/jobs`、`/assets`、`/content` 是多页面共用地基，**不能挂页面权限点**，否则普通用户创作页直接 403。  
映射与判定标准见 `docs/rbac.md §7`，回归网在 `tests/test_permission_gate.py`（挡得住 + 不误伤，成对断言）。

---

## 环境变量（关键项）

| 变量                                          | 默认                           | 说明                          |
| ------------------------------------------- | ---------------------------- | --------------------------- |
| `DATABASE_URL`                              | `sqlite:///<绝对路径>/hanyue.db` | 生产用 `postgresql+psycopg://` |
| `SECRET_KEY`                                | `dev-only-change-me`         | 生产必须 ≥32 字符随机值              |
| `REDIS_URL`                                 | 空                            | 配置后由 Worker 消费；留空用线程池       |
| `IMAGE_PROVIDER_BASE_URL/API_KEY/MODEL`     | 空                            | 三项齐全才启用 AI 生图               |
| `WECHAT_MP_APPID` / `WECHAT_MP_APPSECRET`   | 空                            | 同时存在才启用公众号草稿箱               |
| `PLATFORM_EVIDENCE_FILE`                    | 空                            | Phase 0 核验证据 JSON 路径        |
| `HOT_TOPICS_REFRESH_COOLDOWN`               | `15`                         | 热点「重算评分」冷却（秒），0 关闭；采集在各平台定时任务里 |
| `ENVIRONMENT`                               | `development`                | 设 `production` 开启安全门禁       |
| `ALLOW_DEV_LOGIN`                           | `true`（本地）                   | 生产必须为 `false`               |
| `ALLOW_REGISTRATION`                        | `false`                      | 开放注册；首个密码账号始终放行（bootstrap）  |
| `AUTH_LOCK_THRESHOLD` / `AUTH_LOCK_MINUTES` | `5` / `15`                   | 连续失败锁定策略                    |

完整变量列表见 `README.md` 末尾的环境变量表，或 `apps/api/.env.example`。

---

## 文档入口

| 文档                        | 内容                                 |
| ------------------------- | ---------------------------------- |
| `docs/design.md`          | 产品设计说明书（权威来源，改功能前先看）               |
| `docs/setup.md`           | 完整启动手册（含 Redis/Worker/Docker/故障排查） |
| `docs/plan.md`            | 实施计划与里程碑                           |
| `docs/wechat-layout.md`   | 公众号排版渲染管线（标签约束、主题参数、语法规范）          |
| `docs/studio-redesign.md` | Studio 创作页重设计说明                    |
| `docs/hot-topics.md`      | 热点选题：采集源、合并算法、评分口径、接口与降级显示         |
| `docs/paper-studio.md`    | 成稿台产品说明：段落模型、改稿流程、质检与发布           |
| `docs/paper-studio-contract.md` | 成稿台与参考项目的接口契约、实施偏差表                |
| `docs/paper-editing-contract.md` | 成稿台段落编辑契约：组件接口、快捷键、草稿生命周期、批注与 diff 规则 |
| `docs/skill-center.md` | 技能中心：独立技能库模型、三类技能 config、蒸馏与导入导出契约、宠物集成 |
| `README.md`               | 全量功能说明、部署指南、已实现/未实现能力边界            |

---

## 当前能力边界（快速参考）

**已实现**：稿件/日历/复盘、多模型连接与流式生成、AI 生图、7 种图像处理与条漫分格、图文卡片（8套模板）、**热点选题（7 路采集 + 同事件合并 + 100 分可解释评分 + 一键成稿）**、漫画分镜与**角色库**（用户级角色卡跨项目复用、按镜头景别选参*考图、生成时快照进 job payload、正文 LLM 抽取草稿需人工确认）与****场景级风格覆盖***（StoryboardScene.style_config，场级优先、空则回落项目级，styleSnapshot.source 记录生效来源），Agent 受控执行、发布队列与幂等提交、公众号草稿箱（AppSecret 绑定 + 真实三步推送）、真 keyset 分页、SSRF 防护、生产安全门禁。

**明确未实现**：各平台具体发布适配器字段映射、公众号群发、公众号指标采集、OCR/抠图、Agent SDK/MCP 独立 Worker、TanStack Query/Tiptap。

> 未实现的功能**返回明确错误**，不伪造成功。
