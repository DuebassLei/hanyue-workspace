# 启动与配置手册

本文是 `README.md`「启动方式 / 启用 AI 生图 / 公众号草稿箱」几节的操作展开，可独立阅读。
权威需求仍是 `design.md`，实施切片见 `plan.md`。

## 1. 环境要求

| 组件 | 版本 | 备注 |
|---|---|---|
| Python | 3.12（≥3.10 可用） | 后端与 Worker 共用同一个虚拟环境 |
| Node.js | ≥ 18 | 前端 Vite |
| PostgreSQL | 16 / 17 | 本地开发两种来源任选：本机已装的服务，或脚本自建私有集群（见方式 A-1） |
| Docker / Compose | 可选 | 仅方式 A-2、方式 C 和 Docker 版 Redis 需要 |
| Redis | 可选 | 不装就用 API 内置线程池 |

仓库结构要点：`apps/api`（FastAPI）、`apps/web`（Vue 3）、`apps/worker`（队列消费者，复用 `apps/api/app` 的领域服务）、`infra`（compose 与平台证据文件）。

## 2. 三种启动方式

### 方式 A：本地开发（最简）

数据库二选一，连接串格式完全相同，换来源不用改代码：

| | A-1 本机 PostgreSQL（**不用容器**） | A-2 Docker 只起库 |
|---|---|---|
| 命令 | `cd infra && ./scripts/dev-pg.sh up` | `cd infra && ./scripts/dev-db.sh up` |
| 端口 | 5433（自建集群）／5432（复用系统服务） | 5433（PG）+ 6380（Redis） |
| 数据位置 | `infra/.pgdev/data`（可整目录删掉） | Docker 卷 `hanyue-dev_pgdata-dev` |
| 前提 | 本机装了 PostgreSQL（有 `psql` / `initdb`） | Docker Desktop 或 engine 在跑 |
| 顺带起 Redis | 不会，需要时单独跑 | 会 |

#### A-1 本机 PostgreSQL，不经过容器

`infra/scripts/dev-pg.sh` 支持两种来源，用 `PG_MODE` 选：

- **`cluster`（默认）**：用本机 `initdb` 在 `infra/.pgdev/data` 建一个**只监听 `127.0.0.1:5433`** 的私有集群。
  不注册 Windows 服务、不碰系统里已有的库，`reset` 就是删目录重来。超级用户就是 `hanyue`，密码 `hanyue_dev_pwd`，因此**不需要任何密码输入**。
- **`service`**：复用本机已安装并注册成服务的 PostgreSQL（默认 `127.0.0.1:5432`），
  只在里面建角色 `hanyue` 与库 `hanyue`，然后跑初始化 SQL。需要 `postgres` 超级用户密码
  （交互输入，或给 `PGSUPERUSER_PASSWORD`）。

```bash
cd infra

# —— 自建集群（推荐：隔离、无需密码、随项目删）——
./scripts/dev-pg.sh up            # 首次 initdb + 启动 + 建库 + 跑 01-init.sql
./scripts/dev-pg.sh status        # 进程、端口、库里的表数量
./scripts/dev-pg.sh psql          # 直接用应用账号进 psql
./scripts/dev-pg.sh down          # 停集群，数据留在 infra/.pgdev/data

# 一次到位：起库 + 建表 + 建首个账号（密码可 HANYUE_NEW_PASSWORD 传入）
BOOTSTRAP_EMAIL=me@example.com ./scripts/dev-pg.sh bootstrap

# —— 复用系统已装的 PG 服务 ——
PG_MODE=service ./scripts/dev-pg.sh up
```

**Windows PowerShell 用 `.ps1` 版**（`.sh` 在 PowerShell 里会被 `C:\Windows\system32\bash.exe`
当成 WSL 脚本执行，等于没跑）：

```powershell
cd infra
.\scripts\dev-pg.ps1 up
.\scripts\dev-pg.ps1 status
.\scripts\dev-pg.ps1 bootstrap          # 会问邮箱与密码
.\scripts\dev-pg.ps1 psql               # \q 退出
.\scripts\dev-pg.ps1 down

# 复用系统已装的 PG 服务（会问 postgres 超级用户密码）
$env:PG_MODE='service'; .\scripts\dev-pg.ps1 up

# 给 psql 传参数：要么用 --%，要么把 -c 用引号包住
.\scripts\dev-pg.ps1 psql --% -c "select count(*) from users"
.\scripts\dev-pg.ps1 psql "-c" "select current_database()"
# 内联 SQL 里带中文时写成 .sql 文件再 -f（命令行按 GBK 传参，psql 按 UTF8 解码）
.\scripts\dev-pg.ps1 psql --% -f D:\tmp\check.sql
```

> 两个版本子命令、环境变量、数据目录完全一致。`.ps1` 必须保持 **UTF-8 with BOM**，
> 否则 PowerShell 5.1 按 GBK 读会把中文提示读成语法错误。提示「禁止运行脚本」时用
> `powershell -ExecutionPolicy Bypass -File .\scripts\dev-pg.ps1 up`。

| 子命令 | 作用 |
|---|---|
| `up [--write-env]` | 初始化 + 启动 + 建库；`--write-env` 顺手把 `DATABASE_URL` 写进 `apps/api/.env` |
| `down` | 停集群（`service` 模式只提示，不动系统服务） |
| `status` / `ps` | 模式、端口、进程、就绪状态、`public` 表数量 |
| `psql` | 用应用账号进入 psql |
| `reset` | 删掉 `infra/.pgdev/data` 重建（二次确认；`service` 模式拒绝执行） |
| `url` | 只打印 `DATABASE_URL` |
| `init-sql` | 只重跑 `postgres/init/01-init.sql`（时区、`statement_timeout`、`pg_trgm`） |
| `init-schema` | 执行 `postgres/schema.sql` 建全部表/索引（纯 SQL、幂等，见下） |
| `bootstrap` | `up` + 建表（`init_db`）+ 创建首个账号 |
| `import-sqlite [file]` | 把旧 SQLite 数据导入（默认 `apps/api/hanyue.db`） |
| `write-env` | 把当前连接串写进 `apps/api/.env` 的 `DATABASE_URL` 行 |

可覆盖的变量（也可写进 `infra/.env`）：`PG_MODE`、`PG_BIN`、`DEV_PG_PORT`、
`DEV_POSTGRES_USER` / `DEV_POSTGRES_PASSWORD` / `DEV_POSTGRES_DB`、
`PGSUPERUSER` / `PGSUPERUSER_PASSWORD`、`PG_DATA_DIR`、`BOOTSTRAP_EMAIL`。

### 完整建表脚本 schema.sql

`infra/postgres/schema.sql` 是**从 SQLAlchemy 模型自动生成的完整建表脚本**（63 张表 + 全部索引），
供 DBA 审阅、无 DDL 权限环境预建、或新库一次性初始化。两条建表路径互补：

- `bootstrap`（或启动 API）走 `init_db()`：建表 + 补列补索引 + 播种数据，适合日常开发；
- `init-schema` 走纯 SQL：建表结构 + 播种**引导超管账号**，适合需要评审 DDL 的场景。

脚本幂等（`CREATE TABLE/INDEX IF NOT EXISTS` + 种子段 `ON CONFLICT DO NOTHING`），空库直接执行、
已有库重跑只补缺失对象、绝不覆盖已有数据（老库补列仍由 `init_db()` 负责）。直接用 psql 执行：

```bash
psql -U <user> -d <db> -v ON_ERROR_STOP=1 -f infra/postgres/schema.sql
```

**引导超管账号**：脚本末尾会幂等创建内置超管角色 `superadmin`、账号
`1130122701@qq.com`（显示名「钥见AI」）并绑定超管角色。初始密码是仓库里的
**公开固定值**（见 `export_pg_schema.py` 的 `ADMIN_PASSWORD`），仅作首次登录引导——
**生产环境登录后必须立即改密**：

```bash
cd apps/api && python scripts/create_user.py reset-password --email 1130122701@qq.com
```

种子段不会覆盖已有账号：管理员后来改过的密码、显示名、角色绑定在重跑时全部保留。
应用启动的 `seed()` 与该种子完全兼容（只增不减，可补齐其余 RBAC 数据）。

**模型变更后必须重新生成**，否则 CI 的 `--check` 会失败：

```bash
cd apps/api
python scripts/export_pg_schema.py -o ../../infra/postgres/schema.sql
python scripts/export_pg_schema.py -o ../../infra/postgres/schema.sql --check   # 校验一致性
```

生成器以模型为唯一真相源：表/列/约束/模型内索引来自 `Base.metadata`，`db.py` 的补充索引
直接 import 其清单复用——所以改模型或改 `db.py:_INDEXES` 后都要重新生成。

几个容易踩的点：

- 脚本自己探测 `psql`：先看 `PATH`，再扫 `C:\Program Files\PostgreSQL\*\bin`（取版本号最大的），
  然后 macOS Homebrew / Linux `apt` 的常见路径。探测错了就 `PG_BIN="/path/to/bin"` 指定。
- **端口**：自建集群刻意用 `5433`，避开系统服务占用的 `5432`；若 5433 也被占，用
  `DEV_PG_PORT=5434 ./scripts/dev-pg.sh up`（compose 版同理）。
- **`postgres.exe` 无法监听端口**（日志里 `could not create any TCP/IP sockets` / `Permission denied`）：
  多半是企业安全软件或防火墙拦了它，或端口落在 Windows 保留区间
  （`netsh int ipv4 show excludedportrange protocol=tcp` 查）。两条出路：换端口，或直接
  `PG_MODE=service ./scripts/dev-pg.sh up` 用系统那个已经在跑的服务。
- 库级设置、扩展都来自 `infra/postgres/init/01-init.sql`，与 Docker 版**同一份文件**，
  两边行为一致；改了这个文件要 `init-sql` 重跑一次才生效。
- 连接串默认 `postgresql+psycopg://hanyue:hanyue_dev_pwd@127.0.0.1:5433/hanyue`，
  与 `apps/api/.env` 里的默认值一致，所以走自建集群时 `.env` 不用改。

#### A-2 Docker 只起库

```bash
cd infra
./scripts/dev-db.sh up     # 首次 initdb 会自动执行 postgres/init/01-init.sql
./scripts/dev-db.sh status
```

它会打印 `DATABASE_URL` 与 `REDIS_URL`，与 `apps/api/.env` 里的默认值一致。
`./scripts/dev-db.sh bootstrap` 可一次完成「起库 + 建表 + 创建首个账号」。

> A-1 与 A-2 抢同一个 5433 端口，**同时只能用一个**。切回 Docker 版前先
> `./scripts/dev-pg.sh down`。

后端**必须在 `apps/api` 目录启动**：`.env` 固定为 `apps/api/.env`，
`DATABASE_URL` 里的相对 SQLite 路径（仅无 PG 时的兜底）也以该目录为基准。

**Windows PowerShell**

```powershell
cd apps/api
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-prod.txt
Copy-Item .env.example .env          # 可选；按需修改
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

**macOS / Linux**

```bash
cd apps/api
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-prod.txt
cp .env.example .env                 # 可选；按需修改
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

> `requirements-prod.txt` 提供 PostgreSQL 驱动 `psycopg`，本地用 PG 时必须安装。

前端（另开一个终端，任意平台）：

```bash
cd apps/web
npm install
npm run dev
```

| 入口 | 地址 | 说明 |
|---|---|---|
| 前端 | http://localhost:5173 | 邮箱密码登录；库里没有密码账号时可自助注册首个账号 |
| 后端 | http://127.0.0.1:8000 | `apps/web/vite.config.js` 把 `/api` 代理到这里 |
| API 文档 | http://127.0.0.1:8000/docs | Swagger，可直接试调用 |

未配置 `REDIS_URL` 时任务由 API 进程内线程池真实执行，**不要启动 Worker**；状态落 `background_jobs` 表，`GET /api/v1/jobs/{jobId}` 可查。

> 线程池模式重启 API 进程时会自动做两件事：把崩溃遗留的 `running` 任务（超过 `JOB_LEASE_SECONDS`）回收为 `queued` 或标 `worker_crashed`，并重投**启动前创建、尚未开始**的任务。运行期崩溃到重启之间提交的任务不保证恢复；需要跨进程可靠性请用方式 B/C。

### 方式 B：本地 + Redis + 独立 Worker

```bash
# 1) 起 Redis（任选其一）
docker run -d --name hanyue-redis -p 6379:6379 redis:7-alpine
# redis-server --daemonize yes   # 本机已安装 Redis 时

# 2) 装生产依赖（含 redis / psycopg[binary]）
cd apps/api
./.venv/Scripts/python.exe -m pip install -r requirements-prod.txt

# 3) 在 apps/api/.env 或 shell 中导出
#    REDIS_URL=redis://127.0.0.1:6379/0

# 4) 启动消费者（另开终端，使用同一个虚拟环境）
./.venv/Scripts/python.exe ../worker/worker.py
# 只消费生图队列：
# ./.venv/Scripts/python.exe ../worker/worker.py --queue ai-image
```

| Worker 参数 | 作用 |
|---|---|
| 无参数 | 连接 Redis，BLPOP 持续消费 `image-processing` / `ai-image` / `platform-publish` / `platform-sync` |
| `--queue <name>` | 只监听指定队列，便于按负载拆进程 |
| `--once` | 从 stdin 读 `{"jobId":"..."}` 执行一个任务后退出，用于调试或外部调度 |

`--once` 示例（Windows PowerShell）：

```powershell
'{"jobId":"替换为真实 jobId"}' | .\.venv\Scripts\python.exe ..\worker\worker.py --once
```

Worker 启动时调用 `recover_pending()` 重投「状态为 `queued` 且 `started_at` 为空」的任务，这就是断线恢复。未配置 `REDIS_URL` 时它会打印提示并返回 0，不会伪装成已在消费。

### 方式 C：Docker Compose（推荐用于部署）

完整说明见 **`docs/docker-deploy.md`**（架构、发版、备份恢复、SQLite → PG 迁移、排障）。

```bash
cd infra
./scripts/deploy.sh doctor       # 环境自检：daemon / compose v2 / 内存磁盘 / 端口 / 配置
./scripts/deploy.sh init-env     # 生成 .env（随机 SECRET_KEY / POSTGRES_PASSWORD）+ 证据文件
vi .env                          # 检查 WEB_PORT、TZ；需要生图/公众号再填凭据
./scripts/deploy.sh up           # 构建并启动 web + api + worker + redis + db
./scripts/deploy.sh ps           # 五个服务应全部 healthy
./scripts/deploy.sh logs worker  # 观察消费者
./scripts/deploy.sh down         # 停止，数据卷保留
```

Docker 装在 WSL Ubuntu 子系统里（没有 Docker Desktop）时，daemon 要手动起、compose v2 要单独装，
见 `docs/docker-deploy.md` 的附录「在 WSL2（原生 Docker Engine）里跑」。

对外只暴露 `web`（`WEB_PORT`，默认 8080，nginx 托管前端并反代 `/api`）；
`api` 仅绑定 `127.0.0.1:API_PORT` 供宿主机调试。不用脚本时等价命令：
`cp .env.example .env && docker compose up -d --build`。

2 核 2G 的机器可以正常跑（空载约 500–650 MB）：`deploy.sh` 检测到物理内存 < 3 GB 会自动叠加
`infra/docker-compose.lowmem.yml`；镜像在打包机构建后 `save-images` 导入，目标机只 `up -d --no-build`。
见 `docs/docker-deploy.md` §2.3。

`SECRET_KEY` 与 `POSTGRES_PASSWORD` 使用 `${VAR:?}` 强制注入，未填写时 `docker compose config` 会直接报错（不会再拿弱默认值启动）。镜像以非 root 运行，api 有 `/health` 健康检查，worker 每轮向 Redis 写 30 秒心跳供健康检查判定。

| 服务 | 内容 | 关键配置 |
|---|---|---|
| `web` | nginx + 前端 dist | `WEB_PORT`、反代 `api:8000`、SPA 回退、上传上限 25m |
| `api` | uvicorn，端口 8000 | `DATABASE_URL=postgresql+psycopg://...`、`REDIS_URL`、`PLATFORM_EVIDENCE_FILE` |
| `worker` | `python worker.py` | 与 api 共用数据库、Redis、存储卷和生图凭据 |
| `redis` | `redis:7-alpine`，开启 AOF | 队列持久化 |
| `db` | `postgres:16-alpine` | 带 `pg_isready` 健康检查 |

镜像同时安装 `requirements.txt` 与 `requirements-prod.txt`，因此 `psycopg` 与 `redis` 都已就绪。数据落在 `pgdata` / `storage` / `redisdata` 三个卷。`SECRET_KEY` 和 AI 生图凭据通过 `infra/.env` 注入（compose 的 `${...}` 替换只读取该文件，不读取 `apps/api/.env`）：

```dotenv
# infra/.env（必填两项）
SECRET_KEY=用 secrets.token_urlsafe(48) 生成的随机串
POSTGRES_PASSWORD=强密码
# 可选：AI 生图
IMAGE_PROVIDER_BASE_URL=https://api.example.com/v1
IMAGE_PROVIDER_API_KEY=sk-...
IMAGE_PROVIDER_MODEL=gpt-image-1
```

compose 还会注入 `ENVIRONMENT=production` / `ALLOW_DEV_LOGIN=false`；后端启动门禁会再次校验密钥强度与开发登录开关，配置不合格会拒绝启动。

改完需 `./scripts/deploy.sh up`（或 `docker compose up -d --build`）；只改了变量则 `./scripts/deploy.sh restart api worker`。

## 3. 配置优先级与 `.env`

```
shell / compose 注入的环境变量  >  apps/api/.env  >  代码默认值
```

`core/config.py` 在进程启动时把 `apps/api/.env` 完整载入 `os.environ`（只补尚不存在的键，不覆盖外部注入）。这一点很关键：Pydantic 的 `env_file` 只填充 `Settings` 对象，而 `image_provider`、`queue`、`connectors` 等模块直接用 `os.getenv` 读取 `IMAGE_PROVIDER_*`、`REDIS_URL`、`PLATFORM_*`；不做这层同步，`.env` 里这些键会被静默忽略。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://hanyue:hanyue_dev_pwd@127.0.0.1:5433/hanyue` | 本地由 `infra/scripts/dev-pg.sh up`（本机 PG）或 `dev-db.sh up`（Docker）提供；生产用 compose 的 `db`。SQLite 仅作无 PG 时的兜底 |
| `SECRET_KEY` | `dev-only-change-me` | 令牌签名、凭据加密、上传会话签名；生产必须替换 |
| `STORAGE_DIR` | `apps/api/storage` | 素材存储目录 |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 逗号分隔 |
| `REDIS_URL` | 空 | 配置后由 Worker 消费；留空用线程池 |
| `IMAGE_PROVIDER_BASE_URL` / `API_KEY` / `MODEL` | 空 | 三者齐全才启用 AI 生图 |
| `PLATFORM_EVIDENCE_FILE` | 空 | Phase 0 证据 JSON 路径，建议绝对路径 |
| `PLATFORM_REDIRECT_BASE` | `http://localhost:5173` | OAuth 回调基址 |
| `PLATFORM_<CODE>_CLIENT_ID/SECRET/AUTH_URL/TOKEN_URL/API_BASE/SCOPES` | 空 | 逐平台 OAuth 配置 |

> **改 `.env` 后必须重启后端与 Worker**：配置只在进程启动时载入，`--reload` 只监听 Python 源码，不会重载 `.env`。

完整示例见 `apps/api/.env.example`。

## 4. 启用 AI 生图

默认不启用。未配置供应商时：`POST /ai/image-generations` 的任务明确进入 `failed`，**不会生成占位图**。

启用后，`POST /ai/image-generations`、`GET /ai/image-provider/status`、素材图库页都会走真实供应商调用。

### 路径 1：环境变量（推荐给部署）

`apps/api/.env`（本地）或 `infra/.env`（Docker），三个变量必须同时填写：

```dotenv
IMAGE_PROVIDER_BASE_URL=https://api.example.com/v1
IMAGE_PROVIDER_API_KEY=sk-...
IMAGE_PROVIDER_MODEL=gpt-image-1
```

- 全局生效，优先级高于设置页登记的模型。
- 本地改完重启 uvicorn；Docker 改完重建 api 与 worker（compose 已把这三个变量同时透传给两个服务）。
- `BASE_URL` 填到 `/v1` 即可：代码会请求 `{BASE_URL}/images/generations`。

### 路径 2：设置页登记模型能力

设置 → 模型连接 → 新增或编辑：

| 字段 | 填写 |
|---|---|
| Base URL | `https://供应商域名/v1`（必须 HTTPS 公网地址） |
| API Key | 供应商密钥，服务端加密存储，接口只返回掩码 |
| 模型代号 | 生图模型名 |
| 能力 | `text.generate,text.stream,image.generate`；纯生图模型填 `image.generate` 即可 |

保存后可点「测试连接」：后端会真实请求 `{BASE_URL}/models`，只有 2xx 才标 `connected`，并显示 HTTP 码与耗时。部分生图供应商不提供 `/models`，此时返回 `endpoint_not_supported`（404），**不代表生图不可用**；能力登记是否生效以 `GET /ai/image-provider/status` 的 `available` 为准。

生效条件（见 `services/image_provider.py:resolve_image_provider`）：模型 `enabled=True`、`capabilities` 含 `image.generate`、连接属于当前用户、`base_url` 与解密后的密钥均非空。

### 供应商接口约定

| 项 | 要求 |
|---|---|
| 请求 | `POST {BASE_URL}/images/generations`，Header `Authorization: Bearer <KEY>` |
| 请求体 | `model`、`prompt`、`n`(1~4)、`size`、`response_format=b64_json` |
| 响应 | `{"data":[{"b64_json":"..."}]}` 或 `{"data":[{"url":"https://..."}]}`，OpenAI 兼容格式 |
| 尺寸映射 | `1:1`→1024×1024，`3:4`/`9:16`→1024×1536，`4:3`/`16:9`→1536×1024 |
| 图片校验 | Pillow 校验有效性，≤ 20 MB、≤ 1600 万像素 |
| 出站安全 | 公网仅 HTTPS（端口限 443/8443）；拒绝公网 `http://`、内网/保留网段、云元数据地址；3xx 逐跳重新校验。本机环回地址（`localhost` / `127.0.0.1` / `::1`）允许 HTTP 与任意端口，供本机模型服务使用 |

> 自建在 `http://localhost:xxxx` 或 `http://127.0.0.1:xxxx` 的模型服务可以直接配置为 Base URL：环回地址不可达其他机器，SSRF 防护对其放行 HTTP 与任意端口。局域网地址（如 `192.168.x.x`、`10.x.x.x`）仍会被拒绝——内网自建网关请用域名并登记到 `SSRF_TRUSTED_HOSTS`（见 `core/ssrf.py`），不要关闭校验。

### 一键验证脚本

**bash / Git Bash**

```bash
BASE=http://127.0.0.1:8000/api/v1
TOKEN=$(curl -s -X POST -H "Content-Type: application/json" \
  -d '{"subject":"user-a"}' "$BASE/auth/login" \
  | ./.venv/Scripts/python.exe -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl -s -H "Authorization: Bearer $TOKEN" "$BASE/ai/image-provider/status"
# 期望 {"available":true,"modelCode":"...","note":"已配置图像生成供应商"}

JOB=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt":"一张简洁的科技感封面","aspectRatio":"3:4","count":1}' \
  "$BASE/ai/image-generations" \
  | ./.venv/Scripts/python.exe -c "import sys,json; print(json.load(sys.stdin)['jobId'])")

curl -s -H "Authorization: Bearer $TOKEN" "$BASE/jobs/$JOB"
# 期望 status=succeeded，result.assetIds 非空
```

**Windows PowerShell**

```powershell
$base = "http://127.0.0.1:8000/api/v1"
$login = Invoke-RestMethod -Method Post -Uri "$base/auth/login" `
  -ContentType "application/json" -Body '{"subject":"user-a"}'
$headers = @{ Authorization = "Bearer $($login.token)" }

Invoke-RestMethod -Headers $headers -Uri "$base/ai/image-provider/status"
$job = Invoke-RestMethod -Method Post -Headers $headers `
  -ContentType "application/json" -Uri "$base/ai/image-generations" `
  -Body '{"prompt":"一张简洁的科技感封面","aspectRatio":"3:4","count":1}'
Invoke-RestMethod -Headers $headers -Uri "$base/jobs/$($job.jobId)"
```

界面验证：打开「素材图库」页 → 顶部显示「已配置图像生成供应商」→ 填提示词生成 → 任务列表变 `succeeded` → 素材网格出现新图片。任务列表每几秒轮询 `GET /jobs`，断线后凭 `jobId` 也能恢复状态。

### 错误码对照

| 错误码 | 含义 | 处理 |
|---|---|---|
| `image_provider_unavailable` | 三个环境变量不全，且无 `image.generate` 模型 | 按路径 1 或 2 补齐 |
| `invalid_base_url` / `UnsafeUrl` | Base URL 不是 HTTPS 或命中内网/元数据黑名单 | 换成公网 HTTPS 地址 |
| `unauthorized` | 供应商返回 401/403 | 检查 API Key 与账号权限 |
| `provider_unreachable` | 网络失败、超时或 URL 被 SSRF 拒绝 | 检查网络、域名解析、是否被重定向到内网 |
| `provider_error` | 供应商返回其他 4xx/5xx | 看任务 `errorMessage` 中的 HTTP 码 |
| `empty_provider_response` / `invalid_provider_response` | 响应无 `data`、不是 JSON，或图片不合法/超限 | 核对供应商是否兼容 OpenAI 图像格式 |
| `image_download_failed` | 返回 `url` 时下载失败 | 检查图片 URL 可访问且为公网 HTTPS |

## 5. 启用公众号草稿箱

公众号**不用 OAuth**（它没有面向第三方创作工具的授权码流程），凭据是 AppID + AppSecret，且只做**草稿箱推送**，不群发。

### 第一步：拿到凭据

公众号后台 →「设置与开发 → 基本配置」：

- `AppID`、`AppSecret`（Secret 只显示一次，重置后旧的立即失效）
- 把**本服务器的公网出口 IP** 加进「IP 白名单」。不加会返回 `40164`，这是最常见的一次性失败原因。

> 注意：草稿箱接口要求**已认证**公众号；未认证会返回 `48001`。

### 第二步：配置并重启

`apps/api/.env`：

```bash
WECHAT_MP_APPID=wx1234567890abcdef
WECHAT_MP_APPSECRET=your-app-secret
WECHAT_MP_AUTHOR=你的署名            # 可选，≤16 字
# WECHAT_MP_API_BASE=https://api.weixin.qq.com   # 一般不用改
```

改完**必须重启后端**（配置只在进程启动时载入）。

### 第三步：真实探测验证

```bash
cd apps/api
$base = "http://127.0.0.1:8000/api/v1"
$login = Invoke-RestMethod -Method Post -Uri "$base/auth/login" `
  -ContentType "application/json" -Body '{"subject":"user-a"}'
$headers = @{ Authorization = "Bearer $($login.token)" }

Invoke-RestMethod -Headers $headers -Uri "$base/wechat-mp/status"
Invoke-RestMethod -Method Post -Headers $headers -Uri "$base/wechat-mp/bind" `
  -ContentType "application/json" -Body '{"displayName":"主号"}'
```

`bind` 会真的去调 `draft/batchget`：**只有微信返回 2xx 才标记为已连接**，失败会带原始 errcode。

界面验证：「发布中心 → 公众号草稿箱」卡片 → 点「绑定并验证」→ 显示 AppID 与后台草稿数 → 点「推送草稿」→ 选封面与正文图 → 校验并预览 → 推送到草稿箱 → 点「查看后台草稿」确认真的出现了。

### 推送流程（微信官方三步）

1. 封面 → `material/add_material`（图片永久素材）拿 `thumb_media_id`，≤10 MB
2. 正文图 → `media/uploadimg` 拿微信域名 URL，≤1 MB，仅 jpg/png
3. `draft/add` → 返回 `media_id`，写入 `PublishJob.remoteContentId` 与 `resultStatus=draft_created` 的 `PublishRecord`

原图会按限制**真实转码**（超限逐级降质降分辨率），转不动就报错，不上传碰运气。正文里非微信域名的图片会被拒（45166），所以正文图必须先上传。

### 错误码对照

| 错误码 | 微信 errcode | 处理 |
|---|---|---|
| `ip_not_whitelisted` | 40164 | 把服务器公网出口 IP 加进公众号 IP 白名单 |
| `invalid_appsecret` | 40125 | AppSecret 不对；被冻结（40243）需到后台重置 |
| `api_unauthorized` | 48001 | 公众号未认证，或该接口未获授权 |
| `invalid_token` / `token_expired` | 40001/40014/42001 | 令牌被别处刷新或过期，系统会自动强刷一次；仍失败再点「绑定并验证」 |
| `thumb_required` | — | 草稿必须有封面图（本系统前置校验） |
| `draft_invalid` | — | 标题/正文/摘要超限或为空（本系统前置校验，会列出具体字段） |
| `invalid_file_type` | 40005 | 图片类型不支持；换 jpg/png |
| `invalid_media_size` | 40006 | 素材超过微信大小限制 |
| `rate_limited` | 45009/45047 | 当日调用超限，次日或稍后重试 |

### 排版主题

公众号会剥离 `<style>` 和 `class`，所以样式必须内联。渲染在服务端完成，预览与推送共用同一套函数，改主题不会影响已排队的任务（推送时把 tokens 存进 `layoutSnapshot`）。

前端入口：「发布中心 → 公众号草稿箱 → 推送草稿」里的主题下拉框，改主题后点「预览」即可看到手机端效果。

内置四套预设，也可以基于预设复制自定义：

```bash
# 列出预设与自定义主题
curl -s localhost:8000/api/v1/wechat-mp/themes -H "Authorization: Bearer $TOKEN"

# 基于 blue-business 复制一份，只改字号与主色
curl -s -X POST localhost:8000/api/v1/wechat-mp/themes \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"我的蓝","baseThemeId":"blue-business","tokens":{"bodySize":17,"primaryColor":"#1F4E79"}}'

# 只渲染不推送，看 HTML / 字数 / 兼容性告警
curl -s -X POST localhost:8000/api/v1/wechat-mp/drafts/render \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"body":"# 标题\n\n正文**加粗**\n\n- 要点","themeId":"blue-business"}'
```

可调参数：`bodySize`(14~20)、`bodyLineHeight`(1.4~2.4)、`letterSpacing`(0~2)、`paragraphGap`(0.5~3)、`indent`(0~2)、`imageRadius`(0~16)、`align`、`h1Style`(bar/block/center/number)、`h2Style`(tag/underline/plain)、`listMarker`(dot/number/none)、`fontFamily`（仅系统字体栈）以及 `textColor`/`mutedColor`/`primaryColor`/`primarySoft`/`quoteBg`/`dividerColor`。

**写错参数会直接 422 并列出可用参数**——不会静默丢弃让你以为生效了。颜色只收 `#rrggbb` 或 `rgba()`，数值超界也会拒绝。

## 6. 自检与回归

开发过程中**不要每改一次就全量跑**。全量 `pytest -q`（600+ 项）和 `npm run build`（Vite 生产打包）都是分钟级，留给交付前的一次完整回归；日常改动用下表的增量检查，秒级确认即可。

| 改了什么 | 增量检查 | 说明 |
|---|---|---|
| 后端单个模块 / 服务 | `pytest tests/test_<对应文件>.py -q` | 跨模块时加 `-q -k "关键词"` 收窄 |
| 后端 import / 语法改动 | `python -m py_compile <改过的文件>` | 不要 `compileall` 整棵 `app` |
| 前端 `.vue` / `.js` | 不跑命令 | `npm run dev` 的 HMR 即改即见，看终端与浏览器控制台报错 |
| 前端构建配置 | `npm run build` | 只有动到 `vite.config`、依赖时才需要 |
| 只改文档 | 不跑 | — |

```bash
cd apps/api
./.venv/Scripts/python.exe -m pytest tests/test_writing_rules_api.py -q   # 只跑受影响的文件
./.venv/Scripts/python.exe -m pytest -q -k "writing_rules or skill"       # 按关键字收窄
./.venv/Scripts/python.exe -m py_compile app/modules/writing_rules.py     # 单文件编译检查
```

**交付前跑一次全量**（提交 / 合并 / 发版前）：

```bash
cd apps/api
./.venv/Scripts/python.exe -m pytest -q              # 全量回归
./.venv/Scripts/python.exe -m compileall -q app ../worker/worker.py
cd ../web && npm run build                           # 前端生产构建
cd ../../infra && docker compose config             # 容器配置校验（仅改过 infra 时需要）
```

## 6.1 定时任务

页面 `/schedules`，设计见 [`scheduled-tasks.md`](scheduled-tasks.md)。首期只接一个类型：**拉取热点榜单**（`hot_topics.refresh`），默认 `0 8,12,18 * * *`、时区随用户（默认 `Asia/Shanghai`）。

启动时会做两件事，日志里可以对照：

| 日志 | 含义 |
|---|---|
| `定时调度器已启动，装载任务 N 个` | 调度器正常；N 为 enabled 的任务数 |
| `SCHEDULER_ENABLED=false，定时调度器未启动` | 总开关被关掉，接口与表仍可用但不会到点触发 |
| `定时调度器启动失败，定时任务不会触发` | 装配异常；接口照常，先看堆栈 |
| `已播种 system 定时任务 hot_topics.refresh` | 首次登录/启动时创建了系统级任务 |

system 级任务（如拉热点）**全库只有一条**，绑定到首个登录用户——`background_jobs.owner_user_id` 是必填外键，没有真实 owner 的任务跑不起来。热点表本身不按用户隔离，所以按用户各建一条只会变成 N 倍采集。

验证三步（不需要等真正到点）：

```bash
# 1. 看清单与下次触发（nextRunAt 是 UTC，前端会转本地时间）
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/schedules

# 2. 预览 cron 的未来三次（配错表达式这里就会报 400 invalid_cron）
curl -s -X POST http://127.0.0.1:8000/api/v1/schedules/preview \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"scheduleKind":"cron","cron":"0 8,12,18 * * *","timezone":"Asia/Shanghai"}'

# 3. 立即执行一次（source=manual，不消耗定时槽位；真实打采集源，约 10 秒）
curl -s -X POST http://127.0.0.1:8000/api/v1/schedules/<taskId>/run \
  -H "Authorization: Bearer $TOKEN"
```

失败行为是明确的：连续 3 次失败自动停用任务并写明原因（界面标红），不推送任何通知——项目没有通知渠道，与其假装「已提醒你」不如让人一眼看见红色。

## 7. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 改了 `.env` 但配置没变 | 配置只在进程启动时载入 | 重启 uvicorn 与 Worker；Docker 需重建容器 |
| Docker 下 `available=false`，本地正常 | `IMAGE_PROVIDER_*` 写在了 `apps/api/.env`，compose 不读它 | 改写 `infra/.env` 后 `docker compose up -d --build` |
| Worker 打印「未配置 REDIS_URL」后退出 | 正常降级，任务由 API 线程池执行 | 无需启动 Worker；确实要跨进程就配 `REDIS_URL` |
| 任务长时间停在 `queued` | Worker 没启动 / 连不上 Redis；或线程池模式下 API 被重启 | `docker compose logs -f worker`；Worker 启动会重投未开始任务 |
| `ModuleNotFoundError: psycopg` | 只装了开发依赖 | `pip install -r requirements-prod.txt` |
| 前端接口 502 / 数据空白 | 后端没起或端口不对 | 确认 8000 端口，Vite 代理目标为 `127.0.0.1:8000` |
| 「测试连接」显示非 `connected` | 真实请求供应商 `/models` 失败 | 看返回的 HTTP 码与耗时；401 查密钥；404 多为供应商不提供 `/models`（`endpoint_not_supported`），改以 `/ai/image-provider/status` 判断 |
| 素材上传失败 | MIME 不在允许列表或超过大小限制 | 仅 JPEG/PNG/WebP/GIF/BMP；全局硬上限 `MAX_UPLOAD_BYTES`（默认 20 MB），前端会先签发一次性上传会话（默认 15 MB）再带 `sessionId` 上传 |
| 生成接口返回 `provider_unavailable` | 未配置模型连接 / API Key，或供应商请求失败 | 这是**预期行为**，不会再产出本地占位文本；在「设置 → 模型连接」登记并测试通过后重试 |
| 自动发布入口仍是禁用 | `platform-evidence.json` 为空、证据过期、URL 含占位文本或 scopes 为空 | 这是设计要求的诚实降级；按 `infra/platform-evidence.example.json` 替换为真实官方证据（示例默认 `confirmed=false`）后再启用 |
| 换设备后主题没同步 | `/preferences` 写入失败或启动时拉取失败 | 页面顶部会显示可见提示；本地 `localStorage` 仍生效，恢复网络后重启页面会重新同步 |
| 生产容器起不来，日志提示「生产配置不安全」 | `SECRET_KEY` 太短/仍是默认值、`ALLOW_DEV_LOGIN=true` 或仍用 SQLite | 按提示逐项修正 `infra/.env` 与 `DATABASE_URL` |
| 前端反复提示需要重新登录 | 令牌过期 / 被吊销 / 改过密码 / 显式退出 | 令牌带 `jti`，`logout` 后旧 token 立即失效；改密会提升 `pwdv`，所有旧会话同时失效。开发态（`ALLOW_DEV_LOGIN=true`）可用「开发态免密进入」快速回到上次 subject |
| 图像处理任务 `failed` + `invalid_image` | options 非法、输出格式不是 jpeg/png/webp，或条漫没检测到空白间隔 | 看任务 `errorMessage`；分格需是带明显水平留白的条漫长图，可调整 `minGap` / `maxPanels` |
| 列表翻页返回 `400 invalid_cursor` | 游标伪造或已损坏 | 用服务端返回的 `X-Next-Cursor`（或 `/jobs` 的 `nextCursor`）原样回传，不要自行拼接 |
| OAuth 平台「验证授权」报「未配置验证端点」 | 未设置 `PLATFORM_<代码>_VERIFY_PATH` | 按平台官方文档把用户信息接口路径（如 `/userinfo`）写入该变量；系统不猜测接口路径 |
| OAuth 平台验证报 `token_verification_failed` | 令牌失效、刷新失败或验证端点路径错误 | 检查 `VERIFY_PATH` 是否为相对 `API_BASE` 的正确路径；重新走 OAuth 授权 |
| 公众号「绑定并验证」报 `ip_not_whitelisted` | 出口 IP 不在公众号 IP 白名单 | 到后台「基本配置 → IP 白名单」加入服务器公网出口 IP；用固定出口 IP 的 NAT/代理 |
| 公众号草稿推送报 `api_unauthorized`(48001) | 公众号未认证或该接口未授权 | 草稿箱要求已认证账号；认证后重试 |
| 公众号卡片显示「仅草稿，不支持群发」 | 这是设计边界 | 群发需在公众号后台人工完成；本系统不会自动群发 |
| 公众号推送任务成功但后台没看到草稿 | 看错公众号 / 看的是「发表记录」而非「草稿箱」 | 点「查看后台草稿」读的是 `draft/batchget` 真实列表；后台草稿箱在「内容管理 → 草稿箱」 |
| 排版改完推送出去没变化 | 主题没选中或改的是另一个自定义主题 | 推送弹窗里确认主题下拉框；已排队的任务用的是提交时的 `layoutSnapshot`，改主题后需重新提交 |
| 创建主题报 `invalid_theme` 并列出可用参数 | 参数名拼错（如 `bodyFontSize`，正确是 `bodySize`）或数值越界 | 按错误里列出的可用参数与区间改；系统不会静默丢弃未知参数 |
| 推送报 `draft_too_long` | 套用内联样式后 HTML 超过微信 20000 字符 | 精简正文、减少逐段强调标记，或调小 `paragraphGap`/`letterSpacing` 等不产生额外标签的参数 |
| 预览里图片显示但推送后变空 | 正文占位符编号超过图片数量 | `{{imageN}}` 的 N 对应正文图顺序（封面不算）；超范围的编号会告警并保留原位 |

## 8. 生产注意

- 替换 `SECRET_KEY`（≥32 字符随机值）、设置 `ENVIRONMENT=production` 与 `ALLOW_DEV_LOGIN=false`，并使用 `postgresql+psycopg://`；`sqlite` 仅适合开发，生产会被启动门禁拒绝。
- 正式登录是**邮箱 + 密码**：`POST /auth/register`（默认关闭注册，首个账号自动放行）、`POST /auth/login`、`POST /auth/password`（改密即让所有旧会话失效）。密码用 scrypt 加盐哈希，连续失败会锁定账号。`POST /auth/login` 只带 `subject` 的开发态入口在生产必须关闭。首个账号或重置密码用 `apps/api/scripts/create_user.py`（容器内 `./scripts/deploy.sh create-user`）。
- 未配置模型连接时 AI 生成明确报错，不会写占位稿件；进阶能力（OCR / 抠图）同样明确报错，不伪造结果。
- 平台自动发布默认禁用，需同时满足：Phase 0 能力已核验 + 官方 OAuth 授权 + 账号开启自动发布 + 人工审核；逐平台字段映射尚未实现时会明确报错。
- `STORAGE_DIR` 需挂持久卷（compose 已挂 `storage` 卷），否则容器重建后素材丢失。
- 出站请求统一走 `core/ssrf.py`，不要在业务代码里绕过它直接发 `httpx` 请求。
