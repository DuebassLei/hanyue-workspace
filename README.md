# AI工作台

自媒体智能创作工作台。依据 `docs/design.md` 实现的纵向切片：灵感 → AI 创作 → 配图 → 发布审核 → 数据复盘，并包含受控的 AI Agent / Skill 执行层。

核心原则：**能做的真做，做不了的明确报错或跳过，不用占位文本冒充成功。**

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Vue 3 + Vite + Pinia + Vue Router |
| 后端 | FastAPI + SQLAlchemy 2 + Pydantic v2（27 个路由模块 / 33 个领域服务） |
| 数据库 | 本地与生产统一用 PostgreSQL 16；`DATABASE_URL` 可指向任意实例，SQLite 仅作无 Docker 时的兜底（生产门禁会拒绝） |
| 队列 | Redis + `apps/worker/worker.py`；未配置 Redis 时回退 API 内置线程池，两种后端共用 `job_runner`，任务状态都落 `background_jobs` 表 |
| 图像 | Pillow 基础处理（同步小图 / 队列大图）+ 配置驱动的 AI 生图适配器 |
| 平台 | OAuth2 + PKCE 连接器注册表 + Phase 0 能力核验证据文件 |

## 目录

```text
hanyue-workspace/
├── apps/
│   ├── web/        Vue 3 前端（20 个页面：总览/热点选题/定时任务/日历/创作/图文卡片/素材/发布/设置…）
│   ├── api/        FastAPI 后端（modules / services / connectors / core + 测试）
│   └── worker/     Redis 队列消费者（复用 API 的领域服务）
├── docs/           design.md（设计说明书）、plan.md（实施计划）、setup.md（启动与配置手册）
└── infra/          docker-compose.yml、nginx/、scripts/（部署·备份·迁移）、platform-evidence.json
```

## 启动方式

> 完整操作手册（Windows / macOS 命令、Redis 与 Worker、验证脚本、故障排查）见 [`docs/setup.md`](docs/setup.md)。

### A. 本地开发（最简，无需 Redis）

**1. 起本地数据库**（PostgreSQL 16 + Redis，独立卷与端口 5433/6380，不影响生产数据）：

```bash
cd infra
./scripts/dev-db.sh up        # 首次会 initdb 并执行 postgres/init/01-init.sql
# 输出：
#   DATABASE_URL=postgresql+psycopg://hanyue:hanyue_dev_pwd@127.0.0.1:5433/hanyue
#   REDIS_URL=redis://127.0.0.1:6380/0
```

`apps/api/.env` 的 `DATABASE_URL` 已按上面的地址配好。常用命令：`status` / `psql` /
`reset`（删卷重建）/ `bootstrap`（建表并创建首个账号）/ `import-sqlite`（搬旧 SQLite 数据）。
没有 Docker 时可把 `.env` 里被注释的 `sqlite:///./hanyue.db` 打开，但生产必须用 PG。

**2. 后端**必须在 `apps/api` 目录下启动：`.env` 固定读取 `apps/api/.env`，相对路径也以该目录为基准。

```bash
cd apps/api
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-prod.txt  # Windows
# python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-prod.txt  # macOS/Linux
cp .env.example .env                                                    # 可选，按需修改
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000 # Windows
# .venv/bin/python -m uvicorn app.main:app --reload --port 8000         # macOS/Linux
```

> `requirements-prod.txt` 里有 PostgreSQL 驱动 `psycopg`；本地用 PG 时必须装，
> 否则启动会报 `ModuleNotFoundError: No module named 'psycopg'`。

**3. 前端**（另开一个终端）：

```bash
cd apps/web
npm install
npm run dev
```

首次打开会停在登录页：点「立即注册」创建账号（库里还没有密码账号时自动放行），
或用 `./scripts/dev-db.sh bootstrap` 一步建表 + 建账号。开发态免密入口
（`local-creator`）仍保留，但只在 `ALLOW_DEV_LOGIN=true` 时显示。

| 入口 | 地址 | 说明 |
|---|---|---|
| 前端 | http://localhost:5173 | 邮箱密码登录；首次可自助注册 |
| 后端 | http://127.0.0.1:8000 | Vite 把 `/api` 代理到这里 |
| API 文档 | http://127.0.0.1:8000/docs | Swagger，可直接试调用 |

未配置 `REDIS_URL` 时使用 API 内置线程池执行任务，**不需要**启动 Worker；任务状态同样落 `background_jobs` 表、可用 `GET /api/v1/jobs/{jobId}` 查询。

### B. 本地 + 独立 Worker（可选）

需要跨进程队列时，先启动 Redis，再在 `apps/api` 的同一个虚拟环境里启动消费者：

```bash
# 设置 REDIS_URL（写进 apps/api/.env 或在 shell 导出）
cd apps/api
./.venv/Scripts/python.exe -m pip install -r requirements-prod.txt
./.venv/Scripts/python.exe ../worker/worker.py            # 持续消费全部队列
# ./.venv/Scripts/python.exe ../worker/worker.py --queue ai-image   # 只消费生图
```

Worker 启动时会重投「已入队但未开始」的任务实现断线恢复；未配 `REDIS_URL` 时它会明确提示无需启动，不会假装消费。

### C. Docker Compose（web + api + worker + redis + postgres）

```bash
cd infra
./scripts/deploy.sh doctor      # 环境自检：daemon / compose v2 / 内存磁盘 / 端口 / 配置
./scripts/deploy.sh init-env    # 生成 .env（随机 SECRET_KEY / POSTGRES_PASSWORD）+ 证据文件
./scripts/deploy.sh up          # 构建并启动，等 api 健康后打印地址
./scripts/deploy.sh ps          # 五个服务应全部 healthy
./scripts/deploy.sh logs worker # 观察消费者
./scripts/deploy.sh down        # 停止（数据卷保留）
```

Docker 装在 WSL Ubuntu 里（无 Docker Desktop）时，daemon 需手动启动、compose v2 需单独安装，
见 `docs/docker-deploy.md` 附录「在 WSL2（原生 Docker Engine）里跑」。

**2 核 2G 的机器能正常跑**：空载约 500–650 MB，`deploy.sh` 检测到物理内存 < 3 GB 会自动叠加
`infra/docker-compose.lowmem.yml`（PG/Redis 参数收紧 + 容器内存上限）。镜像在打包机构建后
`./scripts/deploy.sh save-images` → 目标机 `docker load` + `up -d --no-build` 即可，目标机不承担构建。
细节见 `docs/docker-deploy.md` §2.3。

对外只暴露 `web`（`WEB_PORT`，默认 8080）：nginx 托管 Vue 构建产物并把 `/api` 反代到 `api:8000`；`api` 仅绑定 `127.0.0.1:API_PORT` 供宿主机调试。不用脚本时等价于 `cp .env.example .env && docker compose up -d --build`。

`SECRET_KEY`（≥32 字符随机值）与 `POSTGRES_PASSWORD` 用 `${VAR:?}` 强制注入，未填写时 compose 直接报错而非用弱默认值启动；compose 同时设 `ENVIRONMENT=production` / `ALLOW_DEV_LOGIN=false`，后端启动门禁会再次校验。镜像以非 root 用户运行，api 检查 `/health`、worker 检查 Redis 心跳、web 检查 `/healthz`，五个服务均为 `restart: unless-stopped`。

镜像同时安装 `requirements.txt` 与 `requirements-prod.txt`（含 `psycopg[binary]` 与 `redis`），`postgresql+psycopg://` 连接串可直接工作；`worker` 服务运行真正的 `worker.py`。compose 变量替换读取 `infra/.env`，AI 生图凭据请写在这里（见下节）。

发版、备份恢复、SQLite → PostgreSQL 数据迁移与排障见 **`docs/docker-deploy.md`**：

```bash
./scripts/deploy.sh backup                   # pg_dump + storage 卷快照
./scripts/deploy.sh restore backups/db-xxx.dump
./scripts/deploy.sh migrate --dry-run        # 本地 hanyue.db → 容器内 PG
```

### 验证与测试

```bash
cd apps/api
./.venv/Scripts/python.exe -m pytest -q                  # 当前 484 passed
./.venv/Scripts/python.exe -m compileall -q app
cd ../web && npm run build                               # 前端构建
```

首次启动自检：打开 `/docs` → 用 `POST /api/v1/auth/login`（body `{"subject":"user-a"}`，**仅开发态可用**）取 token → 打开前端任一页面确认数据加载。未配置模型连接时生成接口会明确返回 `provider_unavailable`，这是预期行为。

## 启用 AI 生图

默认**未启用**：不配供应商时 `POST /ai/image-generations` 的任务明确进入 `failed` 并给出 `image_provider_unavailable`，不会生成假图。启用后「素材图库」页与 `POST /ai/image-generations` 都会真实调用供应商。

### 两种启用方式

| 方式 | 操作 | 特点 |
|---|---|---|
| **1. 环境变量**（推荐给部署） | 三个变量必须同时填写 | 全局生效、重启进程后载入；适合容器与 CI |
| **2. 模型管理登记模型能力** | 系统 → 模型管理 → 「生图模型」栏 → 添加生图模型，填好连接与模型代号 | 按用户隔离、密钥服务端加密、界面即时生效；能力随「生图模型」类型自动登记 `image.generate`，无需重启 |

**方式 1 — 本地**：写入 `apps/api/.env` 后**重启后端**（配置在进程启动时载入，改文件不会热更新）。

```dotenv
IMAGE_PROVIDER_BASE_URL=https://api.example.com/v1
IMAGE_PROVIDER_API_KEY=sk-...
IMAGE_PROVIDER_MODEL=gpt-image-1
```

**方式 1 — Docker**：写入 `infra/.env`（compose 从这里做 `${...}` 替换），然后重建容器；api 与 worker 都会拿到同一份配置。

```dotenv
IMAGE_PROVIDER_BASE_URL=https://api.example.com/v1
IMAGE_PROVIDER_API_KEY=sk-...
IMAGE_PROVIDER_MODEL=gpt-image-1
```

```bash
cd infra && docker compose up -d --build
```

**方式 2**：设置 → 模型连接 → 填 Base URL（`https://供应商域名/v1`）、API Key、模型代号，能力填 `text.generate,text.stream,image.generate`（纯生图模型也可只填 `image.generate`），保存后点「测试连接」确认凭据可用。生效条件：`capabilities` 含 `image.generate`、模型 `enabled=True`、连接属于当前用户、`base_url` 与密钥均非空。

> 「测试连接」探测的是 `{BASE_URL}/models`。部分生图供应商不提供该端点（返回 `endpoint_not_supported`），这**不影响**能力登记生效；是否真正可用请以 `/ai/image-provider/status` 与一次实际生成结果为准。

> 优先级：环境变量三个值齐全时**优先使用环境变量**；否则才回退到设置页登记的模型。

### 供应商接口约定

代码会向 `{BASE_URL}/images/generations` 发起 `POST`（Bearer 鉴权），所以 **BASE_URL 填到 `/v1` 即可，不要带 `/images/generations`**。

| 项 | 要求 |
|---|---|
| 请求体 | `model` / `prompt` / `n`(1~4) / `size` / `response_format=b64_json` |
| 响应 | `{"data":[{"b64_json":"..."}]}` 或 `{"data":[{"url":"https://..."}]}`（OpenAI 兼容格式） |
| 图片限制 | Pillow 校验为有效图片，≤ 20 MB、≤ 1600 万像素 |
| 网络安全 | 公网必须 HTTPS；SSRF 防护拒绝公网 `http://`、内网 IP、云元数据地址，并对 3xx 每一跳重新校验。本机环回地址（`localhost` / `127.0.0.1` / `::1`）允许 HTTP 与任意端口 |

### 验证是否生效

```bash
# 1) 状态检查：available 应为 true，modelCode 为生效的模型
curl -H "Authorization: Bearer <TOKEN>" \
  http://127.0.0.1:8000/api/v1/ai/image-provider/status

# 2) 提交一次生成（返回 202 + jobId）
curl -X POST -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" \
  -d '{"prompt":"一张简洁的科技感封面","aspectRatio":"3:4","count":1}' \
  http://127.0.0.1:8000/api/v1/ai/image-generations

# 3) 查任务终态：succeeded 时 result 带 assetIds
curl -H "Authorization: Bearer <TOKEN>" \
  http://127.0.0.1:8000/api/v1/jobs/<JOB_ID>
```

界面验证：打开「素材图库」页，顶部显示「已配置图像生成供应商」→ 填写提示词生成 → 任务列表变为 `succeeded` → 素材网格出现新图片。

| 常见错误码 | 含义 / 处理 |
|---|---|
| `image_provider_unavailable` | 三个环境变量不全，且没有登记 `image.generate` 模型；按上面任一方式补齐 |
| `unauthorized` | 供应商返回 401/403，检查 API Key |
| `provider_unreachable` | 网络失败或 URL 被 SSRF 拒绝（常见于填了 `http://localhost` 自建服务） |
| `provider_error` | 供应商返回 4xx/5xx，看任务 `errorMessage` 中的 HTTP 码 |
| `invalid_provider_response` / `image_download_failed` | 响应不是预期 JSON、不是有效图片，或 `url` 图片下载失败 |

生成失败时任务会明确进入 `failed` 并记录原因，**不会**写入占位图。

## Agent 运行时

| 接口 | 作用 |
|---|---|
| `GET /agent/status` | 运行模式、进行中/待批准数量、数据库与素材等真实绝对路径 |
| `GET /agent/policy` | 服务端生效的工具风险分级，设置页直接渲染 |
| `GET /agent/providers` | 可用 Provider（脱敏） |

权限由服务端强制执行：禁止级工具（`run_shell` / `raw_sql` / `read_local_files`）在工具层直接拒绝；外部副作用逐次人工批准且绑定参数摘要。审批通过后若适配器未配置，会明确记录 `actionExecuted: false`，不伪装成已执行。

## 任务队列与状态恢复

所有耗时任务统一落到 `background_jobs`，通过 `GET /api/v1/jobs/{jobId}` 查询（也兼容查询旧的 image / sync / generation / publish 任务）。

| 队列 | 入口 | 说明 |
|---|---|---|
| `ai-image` | `POST /ai/image-generations` | 无供应商时任务明确 `failed` + `image_provider_unavailable` |
| `image-processing` | `POST /image-jobs/async` | Pillow 派生图，原图不覆盖 |
| `platform-sync` | `POST /platform-accounts/{id}/sync` | 不再永远停在 `queued`；无凭据/能力未核验会进入终态并附原因 |
| `platform-publish` | 自动发布任务（需官方 OAuth + 已核验能力） | 适配器未实现时明确失败，绝不伪造 remote id |

`GET /platform-accounts/{id}/sync-status` 返回真实的最近同步状态。独立 Worker 启动时会重投「已入队但未开始」的任务；**线程池模式下 API 进程启动时也会重投**（`main.py` 的 lifespan 只在 `backend_name() == "threadpool"` 时执行，Redis 模式交给 Worker，避免重复投递）。

## 运行日志

- 统一入口 `app/core/logging.py`：控制台与文件同一格式，API 写 `storage/logs/api.log`，独立 Worker 写 `worker.log`；按 `LOG_MAX_BYTES` 轮转并保留 `LOG_BACKUP_COUNT` 份。
- 每个请求生成或透传 `X-Request-ID`（响应头返回，CORS 已暴露），该请求内所有日志行都带同一个 requestId，便于串起一次调用链。
- 访问日志记录方法、路径、状态码、耗时与客户端 IP；未捕获异常记录完整堆栈后原样抛出。
- 为免重复，uvicorn 自带 access log 已关闭；请求日志不含 query string，避免把 OAuth code / token 写进日志。
- 日志级别与路径通过 `LOG_LEVEL` / `LOG_DIR` / `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT` 配置；目录不可写时降级为仅控制台并告警，不影响服务启动。

## 列表分页协议

主要列表端点统一支持 `?limit=<1..200>&cursor=<token>`，使用**真 keyset 分页**：按 `(排序字段, id)` 复合游标在 SQL 里加 seek 谓词，不再「先取前 N 条再在内存里找 cursor」。

| 项 | 约定 |
|---|---|
| 下一页令牌 | 响应头 `X-Next-Cursor`（CORS 已 `expose_headers`）；`/jobs` 额外保留响应体 `nextCursor` 字段 |
| 响应形状 | 保持向后兼容：业务列表仍是裸数组，`/jobs` 仍是 `{items, nextCursor, backend}` |
| 无效游标 | 明确返回 `400 invalid_cursor`，不静默回退到第一页 |
| 已覆盖 | `/jobs`、`/assets`、`/content`、`/platform-accounts`、`/publishing/jobs`、`/skills`、`/models/connections` |
| 前端 | `api.getPage(path)` 返回 `{ data, nextCursor }`；稿件/素材/任务列表已接「加载更多」 |
| 未覆盖 | 日历、周复盘、模型目录、生成记录、Agent 会话/事件/审批等固定窗口或按 `seq` 增量的端点 |

## 用户偏好与图像处理

- `GET /preferences` / `PATCH /preferences`：主题三选（`system` / `light` / `dark`），落在 `user_preferences` 表并按用户隔离；无记录时返回 `source=default`。前端 `boot()` 以服务端值为准，写入失败仍保留 `localStorage` 离线回退。
- 图像处理现为 7 种：`thumbnail` / `resize` / `crop` / `compress` / `convert` / `watermark` / `split_panels`。输出格式只允许 `jpeg` / `png` / `webp`，非法 options 统一返回 `invalid_image` 而不是 500。
- `split_panels` 用投影算法检测条漫长图的水平空白带，一次产出多张派生图（任务 `result.panelCount` 与 `assetIds` 对应）；检测不到间隔或超出 `maxPanels` 会明确失败，不会把一张原图伪装成分格成功。OCR / 抠图仍需外部视觉服务。

## 平台接入与 Phase 0 核验

代码不会自行宣称任何平台「已核验」：

- `infra/platform-evidence.json` 为空 → 五个平台全部 `confirmed=false`，自动发布入口保持禁用。
- 只有证据文件同时提供 `verifiedAt`、`evidenceDate`、`evidenceUrl`、`scopes` 才会把某平台标记为已核验；证据不全会被拒绝并给出 warning。格式见 `infra/platform-evidence.example.json`。
- 应用凭据通过 `PLATFORM_<平台>_CLIENT_ID/CLIENT_SECRET/AUTH_URL/TOKEN_URL/API_BASE/SCOPES` 配置；`GET /platforms/{platform}/connect` 生成带 state/nonce/PKCE 的授权链接，回调校验 state 一次性与过期后把令牌加密落库，`authSource=oauth`。
- 本地演示账号（`simulate-authorized`）的 `authSource=demo`、`scopes=[]`、无令牌，`/platform-accounts/{id}/test` 会明确拒绝，不会被当成真实授权。

### 微信公众号草稿箱（真的能推）

公众号与其他四个平台**不同**：它没有面向第三方创作工具的 OAuth 授权码流程，凭据是 AppID + AppSecret。因此它走独立实现，并且只做**草稿箱**，不做群发。

配置两个变量后重启服务即可：

```bash
WECHAT_MP_APPID=wx...            # 设置与开发 → 基本配置
WECHAT_MP_APPSECRET=...
WECHAT_MP_AUTHOR=                # 可选，草稿默认署名
```

还要在公众号后台把**本服务器公网出口 IP** 加入「设置与开发 → 基本配置 → IP 白名单」，否则微信会返回 `40164`（本系统会原样提示该错误码与处理办法）。

| 端点 | 作用 |
|---|---|
| `GET /wechat-mp/status` | 是否已配置、令牌缓存状态、内容限制、绑定情况 |
| `POST /wechat-mp/bind` | **真实探测**：调用 `draft/batchget` 验证凭据 + 白名单 + 草稿权限，通过才落库为已连接账号 |
| `POST /wechat-mp/drafts/preview` | 推送前预检（标题 ≤64、摘要 ≤120、正文 ≤20000 字、必须有封面图） |
| `POST /wechat-mp/drafts` | 创建推送任务（异步队列，幂等键去重） |
| `GET /wechat-mp/drafts` | 读公众号后台**真实**草稿列表 |
| `GET /wechat-mp/drafts/{media_id}` | 读单篇草稿 |

推送流程按微信官方三步执行，任一步失败都带真实 errcode，不写假 `media_id`：

1. 封面 → `material/add_material`（图片永久素材）拿 `thumb_media_id`；原始图先按 10 MB 限制真实转码。
2. 正文图 → `media/uploadimg`（≤1 MB，仅 jpg/png）拿微信域名 URL，同样先转码；正文里非微信域名图片会被 45166 拒绝。
3. `draft/add` 创建草稿 → 返回 `media_id`，同时写 `PublishJob.remoteContentId` 与一条 `resultStatus=draft_created` 的 `PublishRecord`（`publishedAt` 保持为空，因为草稿不是发布）。

注意点：

- 令牌用 `stable_token` 换取并**加密落库缓存**（`platform_access_tokens`，`(platform, app_id)` 唯一），多进程共享同一枚，避免互相顶掉；令牌失效会自动强刷重试一次。
- 正文支持 `# 标题` / `## 小标题` / `- 列表` / 空行分段，插图用 `{{image1}}` 占位；所有文本先转义，不会把用户内容当 HTML 注入。
- 微信公众号需要**已认证**账号才有草稿箱权限，未认证会返回 `48001`。
- 前端入口在「发布中心 → 公众号草稿箱」卡片：绑定并验证、推送草稿、查看后台草稿。

### 公众号排版（手机端真实样式）

公众号正文只接受**内联样式**：`<style>`、`class`、`h1`、`div` 会被编辑器剥离，所以排版必须在生成 HTML 时就写进 `style` 属性。渲染管线在 `apps/api/app/services/wechat_layout.py`，设计说明见 [`docs/wechat-layout.md`](docs/wechat-layout.md)。

| 能力 | 说明 |
|---|---|
| 预设主题 | `ink-green`（墨绿简约）、`warm-orange`（暖橙生活）、`mono`（极简黑白）、`blue-business`（商务蓝）、`notebook`（手帐）、`dispatch`（公文）、`poster`（海报）、`margin-note`（旁注）、`seal`（印章）、`lead-in`（拉引）、`eyebrow`（眉题）、`kicker`（题签）、`framed`（题框）、`rail`（侧栏）等 18 套，`GET /wechat-mp/themes` 一次返回 |
| 自定义主题 | `POST/PATCH/DELETE /wechat-mp/themes`；可调字号、行高、字距、段距、对齐、首行缩进、圆角、标题样式、列表符号与配色 |
| 参数校验 | 数值有上下界、颜色只收 `#rrggbb`/`rgba()`、枚举值固定；**拼错或不存在的参数直接 422 并列出可用参数**，不会静默忽略让你以为生效了 |
| 正文语法 | `#` / `##` / `###` 标题、`> ` 引用、`- ` 与 `1. ` 列表、`---` 分割线、代码块、`:::card` 强调卡、`:::follow` 关注引导、`:::sign` 署名、`{{image1\|图注}}` 插图 |
| 行内标记 | `**加粗**`、`*斜体*`、`==高亮==`、`~~删除线~~`、行内代码、`[文本](链接)`（外链在公众号不可点，自动降级为文本并给出告警） |
| 预览 | `POST /wechat-mp/drafts/render` 返回 `html` + `tokens` + `stats`（字数/图数/预计阅读）+ `warnings`/`issues`（兼容性 lint） |
| 所见即所得 | 推送任务用 `PublishJob.layoutSnapshot` 保存的 tokens 渲染，与预览同一套函数；主题后续被改也不影响已排队的任务 |

排版层的硬约束（都会明确报错或给出可执行告警，不静默降级）：

- 只输出 `section` / `p` / `span` / `img` / `ul` / `li` / `blockquote` 等微信允许的标签，禁止 `<style>`、`class`、`h1`~`h6`、`div`；
- 禁止外链字体（`fontFamily` 只允许系统字体栈），微信不支持 web font；
- 内联样式会显著增加 HTML 长度，而微信按 `content` 字符数计上限，超 20000 时推送前就拦下并提示精简；
- 未知或未闭合的区块（写了 `:::card` 却没有 `:::`）不会吞掉内容，而是按原样渲染并给出告警。

### 全平台维护与验证（统一能力）

每个平台都有独立的维护视图与真实验证入口，不依赖散落的猜测逻辑：

| 端点 | 作用 |
|---|---|
| `GET /platforms/{platform}/maintenance` | 单平台维护视图：凭据状态（`app_secret` / `oauth` / 未配置）、能力核验、验证能力、绑定账号与可验证性一次返回 |
| `POST /platform-accounts/{id}/test` | 统一真实验证：OAuth 平台用落库令牌调用显式配置的验证端点；公众号用 `draft/batchget` 探针一次确认凭据 + IP 白名单 + 草稿权限 |
| `GET /platforms/connectors/status` | 全平台接入方式总览（每平台 `mode` / `verifyCapable` / 原因） |

OAuth 平台的令牌验证端点按官方文档**显式配置**（`PLATFORM_<大写平台代码>_VERIFY_PATH`，相对 `API_BASE` 的路径）；不配置时「验证授权」明确报错——绝不猜测平台私有接口冒充验证成功。发布中心的能力表格会显示每个平台的接入方式标签与原因。

## 热点选题

「每日热榜」解决今天大家搜什么；`/hot-topics` 解决**我该写哪一个**。一次刷新完成 7 路采集（今日头条 / 百度 / 抖音直取，微博 / 知乎走 60s 聚合，AIHOT 热点与资讯），把同一事件的多平台标题合并成一个事件，再按 100 分制打分：

**共振 30 + 势能 25 + 时效 20 + 赛道 15 + 可写性 10 − 风险 0~30**

分数、档位与每一项的证据都由服务端算好原样下发，前端只排序、筛选、展示，不重算。时间基准是本轮 `captured_at`，同一批数据任何时候复算结果一致。只有一轮采样时，「名次升降」「日内新鲜度」明确标为数据不足（界面显示斜纹条与原因），不显示 0 冒充已计算。

- 单源失败只记录提示，其余源照常返回；7 路全失败返回 `all_sources_failed`（可重试），不返回「0 个事件」的假成功。所有出站请求走 `safe_request`。
- 采集与落库幂等：条目按 `平台 + 归一标题` 去重，名次采样按 `条目 + 采集时刻` 唯一，事件按归一标题指纹复用；人工「忽略/在观察」与已出稿关联在刷新后保留。
- 点「写这篇」直接创建稿件并跳转创作空间；重复点击返回同一篇。**不恢复选题库，不生成占位正文。**
- 创作空间的「灵感」面板继续使用 `GET /api/v1/hot-topics`：优先读已评分事件，库中无数据时才回落到旧的单源接口。

详细算法、接口与降级口径见 [`docs/hot-topics.md`](docs/hot-topics.md)。

## 已实现的能力边界

**可用**
- 工作区、稿件版本、平台版本、日历、周复盘（选题库已下线，`/topics` 前端重定向到创作空间）
- 多模型连接：新增 / 编辑 / **真实连通性测试**（只有供应商返回 2xx 才标 `connected`）/ 删除；密钥服务端加密、只返回掩码
- 模型目录可按能力登记（含 `image.generate`）；SSE 流式生成、备用模型降级与 429/5xx 退避
- 素材上传（支持一次性签名上传会话）、7 种图像处理（含条漫分格多图产出）、AI 生图、素材页与任务状态
- 图文卡片：Markdown → 3:4 / 9:16 / 1:1 / 4:3 固定画幅卡片，8 套模板 + 底纹/边框/背景图层、三种分卡策略、3x PNG 与 ZIP 图集导出；自定义模板服务端持久化；可切后端 `/cards/paginate` 协同模式
- Agent 会话、Skill 注册与启停、工具授权、运行事件、暂停/恢复/取消、审批；**步骤规划优先走真实 LLM**，失败明确降级
- **热点选题**：7 路热点采集、同事件合并（硬否决 + 并查集）、100 分可解释评分、多维筛选、一键成稿；数据不足的维度显式降级
- **视频创作（图文成片）**：文案/AI 拆解说稿+分镜 → 逐镜头 AI 生图（Ken Burns 推/拉/pan 运镜）→ edge-tts 旁白配音 → FFmpeg 硬字幕烧录合成 MP4；默认 9:16 竖屏可切 16:9，BGM 可选混音；渲染走单一后台任务（五阶段进度、断点续跑缓存配图/配音），成品入素材库；依赖 edge-tts（`pip install edge-tts`）与本机 ffmpeg（含 libass/zoompan），缺失时明确报错不伪造
- 发布队列、幂等键、取消与受控重试；数据分析（缺失指标显示「不可获取」）
- **微信公众号草稿箱**：AppID/AppSecret 绑定并真实探测、封面永久素材 + 正文图片上传 + `draft/add` 三步真实推送、后台草稿列表回读、令牌落库共享缓存；**只推草稿不群发**
- 主题偏好**服务端持久化**（`GET/PATCH /preferences`），跨设备同步，离线回退 `localStorage`
- 主要列表端点**真 keyset 分页**（`X-Next-Cursor` 响应头）；线程池模式启动时重投滞留任务，并回收崩溃遗留的 `running` 任务
- SSRF 防护：字面量校验（含十进制/八进制/十六进制 IP、端口限制）+ 请求前 DNS 解析 + **把校验过的 IP 固定连接层**（`sni_hostname` 保持原域名证书校验），消除 DNS rebinding；逐跳重定向重新校验，不跟随 3xx
- 队列**原子领取（CAS）**、取消协作式检查、终态写回不覆盖 `cancelled`；Redis 模式用 SETNX 去重键避免重复投递
- 发布任务幂等提交用唯一约束兜底（并发不 500）、排期到点自动提升为待提交；手动指标只接受已知键与有限数值，汇总按来源拆分
- Phase 0 证据文件强制校验日期时效、官方 HTTPS 链接、非空真实 scope、占位文本检测；示例模板默认 `confirmed=false`
- 生产鉴权门禁：`ENVIRONMENT=production` 时拒绝弱 `SECRET_KEY` / `ALLOW_DEV_LOGIN=true` / SQLite；登录为邮箱 + 密码（scrypt 哈希、失败锁定、改密踢旧会话），令牌带 `jti` 可吊销；凭据加密使用随机 nonce

**明确未实现（避免伪造）**
- 五个平台的**逐平台适配器字段映射**：OAuth 框架、令牌存储、能力核验注册表已就绪，但 `publish` / `sync*` 的具体请求体需按各平台官方文档与获批资质实现；未实现时明确报错，不猜测接口（公众号走独立的 AppSecret 实现，已按官方文档完成草稿箱三步，但**群发未实现**）
- 公众号正式群发 / 发布：需要已认证账号的群发权限，本系统只做到草稿箱，`publish` 调用会明确报错
- 公众号指标采集（阅读/在看/涨粉）：适配器未接入，调用报错
- 视频平台发布（视频号/抖音等推视频）：本期不做，成片只入素材库供手动下载分发；`TTS_PROVIDER_*` HTTP 供应商通道已预留但后端未实现，配置齐全会明确报 `tts_backend_not_implemented`
- Phase 0 官方能力核验本身（需要应用资质与官方文档证据），因此自动发布仍默认禁用
- Claude Code CLI 或任意本机命令集成：禁止级工具永不执行
- Agent 未接入 Agent SDK / MCP 与独立隔离 Worker：LLM 规划只用于「选工具与排顺序」，执行仍走现有受控运行时；开启条件是模型连接已通过真实「测试连接」（`status=connected`），否则明确降级为确定性规则并在 `plan.created` 事件里记录 `planner` 与原因
- 设计 §8.3 的 **OCR / 抠图**：需要 OCR 引擎或视觉模型服务，当前不提供、也不会用占位文本冒充识别结果；**条漫分格**已用真实投影算法实现
- 设计 §5.5 的 TanStack Query / Tiptap：当前用轻量服务端查询组合式函数 + `textarea` 编辑器，未引入这两个依赖
- 部分辅助列表端点仍只用 `limit`（日历、复盘、模型目录、生成记录、Agent 事件/审批、运行列表等）：这些是固定窗口或按 `seq` 增量，未接 keyset 游标
- 线程池后端只在**进程启动瞬间**重投滞留/超时任务；运行期间进程崩溃到重启之间提交的任务不保证恢复，需要跨进程可靠性请配 `REDIS_URL` + Worker
- 未配置模型凭据时**不生成任何文本**：SSE 明确返回 `generation.error(provider_unavailable)`，`generate_text` 返回空文本与 errorCode，生成任务据此标为失败，绝不写入占位稿件版本
- 标准 AEAD/KMS 凭据加密、令牌 refresh 轮换、OAuth 回调的 SameSite cookie 会话绑定：当前分别使用自研 HMAC 流加密（随机 nonce）、可吊销 jti 令牌与 state 原子占用，生产接入正式密钥管理与 IdP 时替换

## 关键设计约束（代码中强制执行）

1. 发布超时或结果未知 → `needs_attention`，绝不自动重发；远端已产生内容则直接视为成功。
2. 自动发布需同时满足：平台能力已核验 + 接口支持发布 + **官方 OAuth 授权** + 账号开启自动发布 + 已人工审核。
3. Agent 工具权限 = Skill 声明 ∩ 工作区授权，且不能超过风险上限；禁止级工具在工具层直接拒绝。
4. Agent 每次运行保存权限快照，恢复/审批时重新鉴权；外部副作用必须人工批准，参数摘要绑定审批。
5. 主题三种模式（跟随系统/浅色/暗色）以服务端偏好为准、跨设备同步，`localStorage` 仅作离线回退；移动端 320px 无整页横向溢出，触控目标 ≥ 44px。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://hanyue:hanyue_dev_pwd@127.0.0.1:5433/hanyue` | 本地由 `infra/scripts/dev-db.sh up` 提供；生产指向 compose 的 `db`。用 SQLite 会被生产门禁拒绝 |
| `ENVIRONMENT` | `development` | 设为 `production` 时启用启动安全门禁 |
| `ALLOW_DEV_LOGIN` | `true`（本地） | 任意 subject 换 token 的开发入口；生产必须为 `false`，正式登录用邮箱密码 |
| `SECRET_KEY` | `dev-only-change-me` | 令牌签名、凭据加密、上传会话签名；生产必须 ≥32 字符随机值 |
| `TOKEN_TTL_SECONDS` | `604800` | 令牌有效期；吊销走 `POST /auth/logout` |
| `JOB_LEASE_SECONDS` | `300` | `running` 超过该时长视为崩溃并回收 |
| `MAX_UPLOAD_BYTES` | `20971520` | 单次上传硬上限，签名会话可进一步收紧 |
| `STORAGE_DIR` | `apps/api/storage` | 素材存储目录 |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 允许的前端来源，逗号分隔 |
| `LOG_LEVEL` | `INFO` | 运行日志级别（DEBUG / INFO / WARNING / ERROR） |
| `LOG_DIR` | 空 | 日志目录；留空使用 `<STORAGE_DIR>/logs` |
| `LOG_MAX_BYTES` | `10485760` | 单个日志文件轮转上限 |
| `LOG_BACKUP_COUNT` | `5` | 轮转日志保留份数 |
| `REDIS_URL` | 空 | 配置后由 Worker 消费；留空用 API 内置线程池 |
| `IMAGE_PROVIDER_BASE_URL` | 空 | 生图供应商基址，填到 `/v1`，须公网 HTTPS |
| `IMAGE_PROVIDER_API_KEY` | 空 | 生图供应商密钥 |
| `IMAGE_PROVIDER_MODEL` | 空 | 生图模型代号；三个 `IMAGE_PROVIDER_*` 齐全才启用 |
| `TTS_PROVIDER_BASE_URL` | 空 | TTS 供应商基址（预留通道，须公网 HTTPS） |
| `TTS_PROVIDER_API_KEY` | 空 | TTS 供应商密钥 |
| `TTS_PROVIDER_MODEL` | 空 | TTS 模型代号；三个 `TTS_PROVIDER_*` 齐全才启用（当前启用会明确报 `tts_backend_not_implemented`），留空用内置 edge-tts |
| `PLATFORM_EVIDENCE_FILE` | 空 | Phase 0 核验证据 JSON 路径（建议绝对路径） |
| `HOT_TOPICS_PLATFORMS` | 空（全部 7 路） | 热点采集源，逗号分隔（如 `微博,知乎`）；用于降噪或调试 |
| `HOT_TOPICS_TIMEOUT` | `12` | 热点单源请求超时（秒，最小 3） |
| `HOT_TOPICS_REFRESH_COOLDOWN` | `15` | 热点刷新冷却（秒），0 表示关闭 |
| `HOT_TOPICS_API_URL` | 空 | 旧版单源热点接口；仅当库中没有任何评分事件时回落使用 |
| `SCHEDULER_ENABLED` | `true` | 定时调度器总开关；`false` 时不装载（接口与表照常可用，用于排障） |
| `SCHEDULER_MISFIRE_GRACE` | `300` | 错过触发点的宽限秒数（默认值，单个任务可覆盖），超过即记 `missed` 不补跑 |
| `SCHEDULER_MAX_CONSECUTIVE_FAILURES` | `3` | 连续失败多少次自动暂停任务，`0` 表示不自动暂停 |
| `SCHEDULER_SYSTEM_WRITE` | `dev` | system 级任务写权限：`dev`（仅开发态可改）/ `any` / `never`（只读，由 seed 维护） |
| `PLATFORM_REDIRECT_BASE` | `http://localhost:5173` | 官方 OAuth 回调基址 |
| `PLATFORM_<CODE>_CLIENT_ID/SECRET/AUTH_URL/TOKEN_URL/API_BASE/SCOPES` | 空 | 逐平台 OAuth 应用配置 |
| `WECHAT_MP_APPID` | 空 | 公众号 AppID；与 Secret 同时存在才启用草稿箱 |
| `WECHAT_MP_APPSECRET` | 空 | 公众号 AppSecret（需同时把出口 IP 加白名单） |
| `WECHAT_MP_AUTHOR` | 空 | 草稿默认作者署名（≤16 字） |
| `WECHAT_MP_API_BASE` | `https://api.weixin.qq.com` | 可指向私有代理；非法地址按未配置处理 |
| `WECHAT_MP_NEED_OPEN_COMMENT` | `0` | 草稿默认是否开放留言 |
| `WECHAT_MP_ONLY_FANS_CAN_COMMENT` | `0` | 草稿默认是否仅粉丝可留言 |

**配置优先级**：shell / compose 注入的环境变量 > `apps/api/.env` > 代码默认值。`.env` 会被完整载入 `os.environ`，所以 `REDIS_URL`、`IMAGE_PROVIDER_*`、`PLATFORM_*` 这些非 Settings 字段同样能从 `.env` 读取；文件位置固定为 `apps/api/.env`，与启动目录无关。Docker 部署改用 `infra/.env` 供 compose 做变量替换。完整示例见 `apps/api/.env.example`。

## 开发说明

- **加列迁移**：`core/db.py` 的 `_apply_additive_migrations()` 用 `ALTER TABLE ... ADD COLUMN` 补列，幂等；新增字段记得登记到 `_ADDED_COLUMNS`。
- **生产安全**：`ENVIRONMENT=production` 时 `get_settings()` 会校验密钥强度、关闭开发登录、拒绝 SQLite；正式登录为邮箱 + 密码（`/auth/register`、`/auth/login`、`/auth/password`），密码用 scrypt 加盐哈希、连续失败锁定、改密后旧令牌立即失效。所有出站 HTTP 必须走 `core/ssrf.py` 的 `safe_request` / `pin_url`，不要自己 new 一个 httpx client 直连外部域名。
- **前端会话**：401 由 `api/client.js` 统一清 token 并触发 `hanyue:unauthorized`，`session` 尝试按上次 subject 重登；显式退出后不自动重登，需点「重新进入」。`setTheme()` 同步失败会在顶部显示可见提示，不再静默假装已保存。
- **前端主题**：`data-theme` + CSS 变量；`session.boot()` 从 `/preferences` 拉取并以服务端为准，`localStorage` 只作离线回退。
- **新增列表端点**：统一用 `core/pagination.py` 的 `paginate()`，务必带复合排序键 `id` 并用 `set_cursor_header()` 回传令牌；不要在测试文件顶层 `import app.services/app.core.db`，否则 `core.db` 会在 fixture 设置临时数据库前绑定默认开发库（测试会写脏 `hanyue.db`）。
- **API 约定**：统一前缀 `/api/v1`，JSON 用 camelCase（`CamelModel` + `alias_generator`），错误结构含 `code` / `message` / `retryable`。
- **登录**：开发态 `POST /api/v1/auth/login`，body `{"subject": "<任意标识>"}`。
