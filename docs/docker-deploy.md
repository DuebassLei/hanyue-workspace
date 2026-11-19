# Docker 打包部署方案（PostgreSQL）

生产形态：`web`（nginx 托管前端 + 反代 `/api`）→ `api`（FastAPI）→ `db`（PostgreSQL 16），
`redis` + `worker` 承担跨进程任务队列。开发期的 SQLite 在生产被启动门禁拒绝，
容器内一律走 `postgresql+psycopg://`。

```
            浏览器
              │  :8080（可换成 80/443，建议前置 TLS）
        ┌─────▼─────┐
        │    web    │  nginx：dist 静态文件 + SPA 回退 + /api 反代
        └─────┬─────┘
              │ 容器内网 api:8000
        ┌─────▼─────┐        ┌──────────┐
        │    api    │◄──────►│  redis   │ 队列 / 心跳
        └─────┬─────┘        └────┬─────┘
              │                   │ 消费
        ┌─────▼─────┐        ┌────▼─────┐
        │    db     │◄───────│  worker  │
        │ PG 16     │        └──────────┘
        └───────────┘
        共享卷：pgdata / storage / redisdata
```

## 1. 交付物

| 文件 | 作用 |
|---|---|
| `infra/docker-compose.yml` | 五服务编排：web / api / worker / redis / db，含健康检查、启动顺序、日志轮转 |
| `infra/docker-compose.lowmem.yml` | 小内存档（2 核 2G）：PG/Redis 参数收紧 + 容器内存上限，< 3 GB 内存时自动叠加（见 2.3） |
| `infra/docker-compose.dev.yml` | 本地开发库：只起 PG + Redis，独立项目名/卷/端口（5433、6380），不碰生产卷 |
| `infra/postgres/init/01-init.sql` | PG 默认初始化脚本：库级时区、超时、`pg_trgm`（首次 initdb 执行；之后由 db-init 按哈希自动重跑） |
| `infra/postgres/schema.sql` | 由 SQLAlchemy 模型导出的完整建表脚本（`export_pg_schema.py` 生成）；db-init 每次启动检测变更并自动重跑 |
| `infra/postgres/create-role-and-db.sql` | 手工安装 PG（非 Docker）时的建库建用户脚本，幂等 |
| `infra/scripts/dev-db.sh` | 本地库管理（Docker）：up / down / reset / psql / status / bootstrap / import-sqlite |
| `infra/scripts/dev-pg.sh` | 本地库管理（**不用容器**）：同样的子命令，支持自建集群与复用系统已装 PG |
| `infra/nginx/default.conf` | 站点配置：SPA history 回退、`/api` 动态解析反代、上传上限 25m、静态缓存、`/healthz` |
| `infra/.env.example` | 变量模板；`init-env` 会基于它生成 `infra/.env` 并填入随机密钥 |
| `infra/scripts/deploy.sh` | 部署入口：init-env / up / down / health / logs / backup / restore / migrate / create-user / mirror |
| `infra/scripts/set-docker-mirror.sh` | 配 docker 的 `registry-mirrors`（国内拉基础镜像加速，见 2.4） |
| `infra/scripts/migrate_sqlite_to_pg.py` | 本地 SQLite → PG 数据搬迁（一次性） |
| `apps/api/scripts/create_user.py` | 账号管理 CLI：create / attach / reset-password / list / disable / enable（见 3.1） |
| `apps/api/Dockerfile` | 后端镜像：非 root、`TZ` + `tzdata`、可选 pip 镜像源 |
| `apps/worker/Dockerfile` | Worker 镜像：与 api 共用领域代码，保证两种执行后端语义一致 |
| `apps/web/Dockerfile` | 前端镜像：Node 构建 → nginx 运行，产物不含源码 |
| `apps/api/docker/fonts-cjk-alias.conf` | 把 ASS 字幕的 `Microsoft YaHei` 别名到开源中文字体（见 2.5） |
| `.dockerignore` / `apps/api/.dockerignore` / `apps/web/.dockerignore` | 把 `.env`、`*.db`、`node_modules`、`storage` 挡在构建上下文之外 |

## 2. 运行环境要求

### 2.1 硬门槛（低于此跑不起来）

| 项 | 最低 | 说明 |
|---|---|---|
| 宿主机 | Linux x86_64 / arm64，内核 ≥ 4.19 | Windows/macOS 用 Docker Desktop（WSL2 后端）可做验证，不建议生产 |
| Docker | Engine ≥ 24 + Compose v2 | `docker compose version` 能输出版本号 |
| CPU | 2 vCPU | api / worker / db / redis / nginx 五进程常驻；1 核会被渲染或建表饿死 |
| 内存 | 2 GB | 2 GB 可正常跑（空载 500–650 MB，见 2.3）；构建请放在打包机 |
| 磁盘 | 40 GB 可用 | 运行镜像合计约 2.5 GB（api/worker 各含 ffmpeg，各约 0.6 GB）；构建机另需 3–5 GB 构建缓存（`docker builder prune` 可回收）；其余随 PG 数据与 `storage` 素材增长 |
| 架构 | amd64 / arm64 均可 | psycopg[binary]、postgres、nginx、node、python 均有多架构镜像 |

推荐 4 vCPU / 8 GB；视频成片用得多的给到 8 vCPU。

### 2.2 各服务常驻内存

| 服务 | 空载 RSS | 峰值 | 峰值场景 |
|---|---|---|---|
| `web` | 10–30 MB | ~100 MB | 大量并发静态请求 |
| `api` | **110–180 MB**（实测） | ~400 MB | 批量导出、生图回调、首启动 `create_all` |
| `worker` | 150–200 MB | ~650 MB | 1080×1920 视频合成（ffmpeg 子进程 +440 MB） |
| `db` | 150–250 MB | ~600 MB | 大表排序 / 索引重建 |
| `redis` | 10–20 MB | ~100 MB | 队列堆积 |

空载合计约 **500–650 MB**（含 dockerd 自身 80–150 MB），这是实测值不是估算：`api` 加载全部路由后
110 MB，ffmpeg 渲染 1080×1920 单条成片峰值 439 MB（限 2 线程）/ 841 MB（14 核默认开线程）。
换言之「4 GB 起步」是为了给构建和并发留余量，**纯运行 2 GB 也能撑住**，见 2.3。

### 2.3 2 核 2G 能跑吗（实测结论）

**能正常跑，不需要换机器。** 前提是镜像在打包机构建、目标机只 `docker load` 后 `up -d --no-build`
（打包机要求见 2.4，2G 机器不承担构建）。本节只评估**运行期**。

| 运行期项目 | 2 核 2G 结论 |
|---|---|
| 五容器常驻 | ✅ 空载约 500–650 MB，余 1.3 GB |
| 出文章 / 排版 / 发布 | ✅ 无压力，请求耗时正常 |
| 单条 1080×1920 成片 | ⚠️ 能出片，但 ffmpeg 吃满 2 核，30 秒成片约 1–3 分钟 |
| 渲染期间同时刷页面 | ⚠️ 可用但变慢：CPU 被 ffmpeg 抢占，接口响应从几十 ms 涨到几百 ms |
| 两条视频同时渲染 | 不会发生：队列单消费者串行，一次只跑一个任务 |

内存账（实测）：空载 `api` 110 MB + `worker` 150–200 MB + `db` 150–250 MB + `redis` 20 MB
+ `web` 30 MB + dockerd 80–150 MB ≈ **500–650 MB**；渲染时 worker 侧再叠 ffmpeg 峰值 439 MB
（限 2 线程；不限线程时 14 核上会到 841 MB，2 核天然停在 440 MB 档）。总占用约 1.1 GB，**仍在 2 GB 内**。

#### 渲染期间的两个健康误判（已在代码/编排里处理）

| 症状 | 原因 | 现状 |
|---|---|---|
| `worker` 显示 `unhealthy`，但日志正在正常渲染 | 心跳原本写在消费循环里，`run_job` 阻塞期间不刷新；TTL 30s 过期 + 健康检查 10 次重试（约 100s）→ 超过约 130s 必判不健康。2 核上 30 秒成片就要 1–3 分钟 | **已修**：心跳改由独立守护线程每 10s 刷新（`worker.py` 的 `_heartbeat_loop`），阻塞期间照常心跳 |
| 渲染时 `api` 偶发 `unhealthy` | healthcheck 用 `urllib` 3 秒超时，CPU 满载时首次请求要吃解释器冷启动 | 概率很低（`/health` 是纯内存操作）；真遇到把 compose 里 api 的 `urllib` timeout 调到 5 秒、`timeout` 调到 6s |

**渲染时看到 `deploy.sh health` 返回非 0，先看 `docker logs` 有没有实际错误**，不要直接
`restart worker` —— 那会中断渲染且丢掉任务现场（`stop_grace_period` 只等 60 秒）。

#### 小内存档（自动生效）

`infra/docker-compose.lowmem.yml` 是专门给 2G 级的覆盖层，`deploy.sh` 检测到
`/proc/meminfo` < 3 GB 时会**自动叠加**（`LOWMEM=1` 强制、`LOWMEM=0` 关闭）：

| 服务 | 调整 | 原因 |
|---|---|---|
| `db` | `shared_buffers=160MB`、`work_mem=8MB`、`max_connections=40`、`jit=off`、并行查询降到 1 | 默认 `work_mem=4MB` 配合大表排序会把内存打穿，JIT 在 2 核上只增开销 |
| `redis` | `maxmemory=128MB`、`maxmemory-policy=noeviction` | **不能淘汰**：这是任务队列，LRU 会静默丢 job；写满时入队直接报错，界面可见 |
| `worker` | `mem_limit=896m`、`MALLOC_ARENA_MAX=2` | 单条渲染 ffmpeg 峰值约 440 MB + Python 侧约 200 MB |
| `api` | `mem_limit=512m`、`MALLOC_ARENA_MAX=2` | 实测 110–180 MB，上限用于防批量任务失控 |
| `web` | `mem_limit=96m` | nginx 空载 10–30 MB |

`mem_limit` 是**上限不是预留**，五者之和超过 2 GB 也无妨——它防的是「一个慢查询把整机吃光，
然后 OOM killer 随机杀掉 PG 留下半写状态」，而不是给每个服务预留内存。

#### 加 2 GB swap（2G 机器强烈建议）

云主机默认无 swap，一次尖峰就被 killer 打掉。容器内不需要改，宿主机加即可：

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -m | grep -i swap
```

注意 swap 只是防 OOM 的缓冲，视频渲染会慢到不可用——那 4 vCPU 更好用。

#### 2 核 2G 的合理定位

| 场景 | 判断 |
|---|---|
| 1–5 人内部使用、写文章/排版为主 | ✅ 推荐，够 |
| 公众号日更、偶尔出短视频 | ⚠️ 可用，视频排队慢慢跑 |
| 多人协作 + 批量生图/批量成片 | ❌ 换 4 vCPU / 8 GB |

### 2.4 构建机要求（可与运行机分开）

- `npm ci` + `vite build`：本仓库 `node_modules` 约 200 MB，Vite 打包需 ≥ 1.5 GB 可用内存，
  2 GB 内存的机器会以 `JavaScript heap out of memory` 失败 → 加 swap 或换机器构建。
- **一次只构建一个镜像**：compose v2.22+ 默认用 bake 把 web / api / worker 并行构建，
  在 2 核 / < 3 GB 的机器上等于 npm、pip、apt 三份同时抢内存与带宽——典型症状是
  `apt-get install ffmpeg` 慢到 5 分钟、`npm ci` 崩在 `Exit handler never called!`
  （这条报错不带任何原因，是 npm 在连接被掐断时退出路径自身的 bug）。
  `deploy.sh up` / `build` 默认串行（web → api → worker），大机器可用 `BUILD_PARALLEL=1` 恢复并行。
- `pip install`：轮子为主，CPU 一般不是瓶颈，但 `pillow` 等仍需要数百 MB 峰值。
- 建议 4 vCPU / 8 GB 构建；生产机只 `docker compose up -d`（镜像可先在 CI 构建后推仓库）。
- 在 Apple Silicon 上给 x86 服务器构建需显式 `--platform linux/amd64`，走 QEMU 会慢 3–5 倍。

#### 构建加速（国内网络）

境内直连 docker.io / deb.debian.org / pypi.org 常常只有几十 KB/s，四层都要换源：

| 层 | 慢在哪 | 怎么改 |
|---|---|---|
| 拉基础镜像 | `registry-1.docker.io`（python / node / nginx / postgres / redis） | `./scripts/deploy.sh mirror` |
| Debian 包 | `apt-get install ffmpeg`（api / worker） | `.env` 的 `APT_MIRROR=mirrors.aliyun.com` |
| pip | `pip install`（api / worker） | `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` |
| npm | `npm ci`（web） | `NPM_REGISTRY=https://registry.npmmirror.com` |
| Alpine 包 | `apk add tzdata`（web） | `APK_MIRROR=mirrors.aliyun.com` |

后三行是**构建参数**，写在 `infra/.env`，改完必须重建（`up` 默认带 `--build`，或 `build`）。
`APT_MIRROR` / `APK_MIRROR` 只写主机名（`mirrors.aliyun.com`），不带 `http://` 与结尾斜杠；
留空即回官方源，所以非中国网络把这几行注释掉即可。

`deploy.sh mirror` 做的事：对候选代理逐个 `GET /v2/`（返回 401 说明它在正常转发鉴权，
视为可用；000/超时/5xx 淘汰），取前 3 个写进 `/etc/docker/daemon.json` 的 `registry-mirrors`
——只改这一个键，其余键原样保留，原文件先备份——再重启 docker 生效。
两个注意点：

- **Docker Desktop 不读 `/etc/docker/daemon.json`**，脚本检测到会改为提示你去
  Settings → Docker Engine 里加同样一段，不要照着脚本路径改文件。
- 公共代理是社区公益服务，会失效。先 `DRY_RUN=1 ./scripts/set-docker-mirror.sh` 看探测结果，
  或用 `MIRRORS="https://你的地址"` 指定自建/信任的源。

重启 docker 会重启容器，`restart: unless-stopped` 的服务会自动回来，数据在卷里，不会丢。

#### 腾讯云 CVM 上构建（内网源）

`mirrors.tencentyun.com` 是 VPC **内网**源：免公网流量、不限速，但腾讯云机器之外不可达。
`infra/.env` 换成这一组（`.env.example` 里有同款注释块）：

```ini
PIP_INDEX_URL=http://mirrors.tencentyun.com/pypi/simple
PIP_TRUSTED_HOST=mirrors.tencentyun.com   # 内网 pip 源是 http，必须配，否则 pip 拒绝
APT_MIRROR=mirrors.tencentyun.com
APK_MIRROR=mirrors.tencentyun.com
```

npm 腾讯云没有镜像（`/npm/` 404），保持 `registry.npmmirror.com` 公网源。
Docker Hub 走 `./scripts/deploy.sh mirror`：脚本通过 metadata 服务识别 CVM，
自动把内网加速器 `https://mirror.ccs.tencentyun.com` 排到候选第一位——
它不占公网带宽，拉 python/node/pg/redis 都是内网速度。
在非腾讯云机器上跑同一条命令会自动跳过它（metadata 探测失败）。


### 2.5 视频成片的额外要求

- **ffmpeg + ffprobe 是硬依赖**：`video_render.py` 直接调二进制，缺失时能力探测返回
  `ffmpeg_missing`，视频合成不可用。api 与 worker 镜像都已 `apt install ffmpeg`。
- 输出 1080×1920 或 1920×1080、H.264（libx264 `-preset medium -crf 21`）+ AAC，
  纯 CPU 编码，未启用硬件加速。实测桌面 CPU 限 2 线程跑 8 秒成片用 1.1 秒（单图 zoompan，
  峰值 439 MB）；叠加多段拼接、字幕烧制（libass）与 2 核云主机的单核性能差后，
  **30 秒成片约 1–3 分钟**。2 核能出片，4 vCPU 才谈得上顺手。
- 渲染时 CPU 会被吃满：同一台机器上的接口请求会变慢（见 2.3 的两个健康误判）。
- 临时空间：单个项目渲染中间产物峰值约 80 MB（`storage/video_tmp`，24 小时后自动清理）。
- 中文字幕：ASS 样式写死 `Microsoft YaHei`，镜像用 `fonts-wqy-microhei` + fontconfig
  别名顶上（微软雅黑受授权限制不能随镜像分发）。换字体改
  `apps/api/docker/fonts-cjk-alias.conf` 与 `services/video_render.py` 的 `font=`。

### 2.6 网络与端口

- 入站：只需 `WEB_PORT`（默认 8080）可达。`API_PORT` 只绑 `127.0.0.1`，不要对外。
- 出站 HTTPS/WSS 443：
  - AI 供应商（`IMAGE_PROVIDER_BASE_URL` 等自定义 OpenAI 兼容端点）
  - 微信公众号 `api.weixin.qq.com`（草稿箱），并把服务器出口 IP 加入公众号 IP 白名单
  - edge-tts 内置语音走微软在线服务，无外网则配音阶段失败
- 容器间 DNS 由 compose 内置的 `127.0.0.11` 提供，不需要自建 DNS。
- 外层反代必须保留原始 URI 与查询串——素材直链的签名在 `?exp=&sig=` 里，剥掉前缀或重写查询会 403。

### 2.7 时区与编码

- 三个镜像都设 `TZ=Asia/Shanghai` 并装了 `tzdata`；应用内部时间戳统一存 UTC，
  `zoneinfo` 解析用户时区依赖 `tzdata`，slim 镜像默认不带。
- PostgreSQL 用官方镜像默认的 UTF8，**不要**加 `--locale`：alpine 版可能缺对应
  locale，initdb 会直接失败。

### 2.8 想要域名 + HTTPS

在 `web` 前面再挂一层 Caddy/Nginx（或云负载均衡），或把 `WEB_PORT` 改成 `80`/`443` 并挂证书。
证书与 TLS 不在本编排范围内，保持「一层反代只做静态托管 + `/api` 反代」最容易排障。


## 3. 首次部署

```bash
cd infra

./scripts/deploy.sh init-env      # 生成 .env（随机 SECRET_KEY/POSTGRES_PASSWORD）+ 证据文件
vi .env                           # 至少检查 WEB_PORT、TZ；需要生图/公众号再填凭据
./scripts/deploy.sh up            # 构建镜像并启动，等 api 健康后打印地址
./scripts/deploy.sh ps            # 五个服务应全部 healthy
```

访问 `http://<服务器IP>:8080`。API 文档在宿主机 `http://127.0.0.1:8000/docs`（只绑回环）。

`up` 的关键行为：

1. 缺 `.env` 时自动生成，`SECRET_KEY` 与 `POSTGRES_PASSWORD` 为随机值，文件权限 600；
2. 缺 `platform-evidence.json` 时从示例复制——文件不存在会让 Docker 建成同名目录，
   容器内读不到证据文件（平台能力全部待核验）；
3. 按 `db → redis → api → worker → web` 顺序等健康，api 有 40s 冷启动窗口
   （首次要 `create_all` + 补列迁移 + 内置 Skill 同步）。

启动门禁（`core/config.py::assert_safe_configuration`）会在 `ENVIRONMENT=production` 下强制校验：
`SECRET_KEY` ≥ 32 字符且不是占位值、`ALLOW_DEV_LOGIN=false`、`DATABASE_URL` 不是 SQLite。
任一条不满足，容器会退出并以非 0 状态重启——这是预期行为，按日志提示改 `.env`。

### 3.1 首个账号与登录

正式登录是**邮箱 + 密码**（`/auth/register`、`/auth/login`、`/auth/password`），
默认关闭开放注册（`ALLOW_REGISTRATION=false`）。首个账号用 CLI 创建：

```bash
./scripts/deploy.sh create-user create --email you@example.com
# 密码交互输入；非交互（CI）用 HANYUE_NEW_PASSWORD=... ./scripts/deploy.sh create-user create ...
```

`create-user` 在一次性 api 容器内执行（会按依赖先起 db，结束后自动删除容器），
所以不需要 api 处于运行中，也不会留下常驻进程。

**没有默认的初始用户或初始密码。** `up` 只起服务、不建账号，密码只在上面这条命令交互输入时存在，
不写进任何文件，仓库和镜像里都查不到。忘记了就重置（该账号全部旧会话立即失效）：

```bash
./scripts/deploy.sh create-user reset-password --email you@example.com
```

**首个账号自动成为超级管理员。** `BOOTSTRAP_FIRST_USER_SUPERADMIN=true`（默认）时，API 启动发现库里
还没有任何超管，就把最早注册的活跃账号提升为 `superadmin`；之后注册的都是 `member`。
所以 `create-user create` 建的第一个账号就是超管。公网邀请制部署把该变量设为 `false` 可关闭。

要确认当前谁是超管，进数据库查（`shell db` 进的是容器 shell，不是 psql，得再进一层）：

```bash
./scripts/deploy.sh shell db
psql -U hanyue -d hanyue          # 容器内本地连接默认 trust，不需要 POSTGRES_PASSWORD
```

```sql
SELECT u.email, u.display_name, u.created_at
FROM users u
JOIN user_roles ur ON ur.user_id = u.id
JOIN roles r ON r.id = ur.role_id
WHERE r.is_superadmin = true;
```

把某个已有账号提升为超管（幂等，重复执行不会插出重复行）：

```sql
INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id
FROM users u
JOIN roles r ON r.role_key = 'superadmin'
WHERE u.email = 'you@example.com'
  AND NOT EXISTS (
    SELECT 1 FROM user_roles ur
    WHERE ur.user_id = u.id AND ur.role_id = r.id
  );
```

权限每次请求现算，**不需要重新登录**即生效；用户看不到新菜单时刷新一次页面即可。
`\q` 退出 psql，`exit` 退出容器。本地开发库同样的 SQL，用 `./scripts/dev-pg.sh psql`（Windows：
`.\scripts\dev-pg.ps1 psql`）进入。

**从旧的开发态账号迁移**：以前用 `local-creator` 之类的 subject 登录时，
稿件、素材、发布任务都挂在那个 user.id 上。直接新建邮箱账号会「内容全不见了」。
用 `attach` 给旧账号补邮箱密码，id 不变，数据全部保留：

```bash
./scripts/deploy.sh create-user list                                    # 先看现有 subject
./scripts/deploy.sh create-user attach --subject local-creator --email you@example.com
```

其他子命令：

| 命令 | 用途 |
|---|---|
| `create-user list` | 列出邮箱 / subject / 显示名 / 登录方式 / 状态 |
| `create-user reset-password --email x` | 忘记密码时由服务端重置（会让该账号全部旧会话失效） |
| `create-user disable --email x` | 停用账号，现有令牌立即失效 |
| `create-user enable --email x` | 恢复账号 |

安全语义：

- 密码用 **scrypt**（n=16384, r=8, p=1）加盐哈希，库被拖走也无法直接还原密码；
- 同一账号连续失败 `AUTH_LOCK_THRESHOLD`（默认 5）次后锁定 `AUTH_LOCK_MINUTES`（默认 15）分钟；
- 令牌里带 `pwdv`（密码修改时间的 epoch），改密后所有旧令牌立即 401，不需要黑名单；
- 账号不存在与密码错误返回同一个 `invalid_credentials`，且都跑一次哈希，无法用响应时间枚举账号。

关掉开放注册后，登录页只在「库里还没有任何密码账号」时显示注册入口（bootstrap），
避免新部署出现「没有账号、也无法注册」的死锁。

## 4. 常用运维

```bash
./scripts/deploy.sh ps                 # 服务与健康状态
./scripts/deploy.sh health             # 逐服务判定，异常返回非 0（可挂监控）
./scripts/deploy.sh logs api           # 最近 200 行
./scripts/deploy.sh logs worker -f     # 跟随
./scripts/deploy.sh restart api worker # 改完 .env 后让新配置生效
./scripts/deploy.sh psql               # 进数据库
./scripts/deploy.sh shell api          # 进容器
./scripts/deploy.sh down               # 停服，卷保留
./scripts/deploy.sh destroy            # 停服并删卷（二次确认，数据不可恢复）
```

直接调用 compose 也等价：`cd infra && docker compose up -d --build`。
Windows 上用 Git Bash 或 WSL 运行脚本；也可只用 compose 命令。

## 5. 发版升级

```bash
cd infra
git -C .. pull                       # 或者用 CI 把代码同步到服务器
./scripts/deploy.sh up               # 重新构建变更的服务并滚动重启
```

只改了部分内容时不必全量重建：

| 改动 | 命令 |
|---|---|
| 仅前端 | `./scripts/deploy.sh build web && ./scripts/deploy.sh up --no-build` |
| 仅后端 / 依赖 | `./scripts/deploy.sh build api worker && ./scripts/deploy.sh up --no-build` |
| 仅 nginx 配置 | `docker compose exec web nginx -t && docker compose exec web nginx -s reload` |
| 仅 `.env` 变量 | `./scripts/deploy.sh restart api worker` |

**数据库表结构**：每次 `up` 时 `db-init` 服务会把 `infra/postgres/` 下的 SQL（`init/*.sql` +
`schema.sql`）做内容哈希与库内记录比对，有变更就自动按序重跑（脚本均幂等，失败则 api/worker
不启动，`docker compose logs db-init` 看报错）；应用启动时另有 `create_all` + 补列迁移
（`core/db.py::_apply_additive_migrations`），只加表/加列/加索引，不做删除与改类型。需要回滚时，
容器镜像回退即可，但已加的列会留在库里（幂等，不影响旧代码运行）。涉及破坏性变更前先 `./scripts/deploy.sh backup`。

### 5.1 镜像打包（含离线分发）

「打包」有三层含义，按需要选：

| 目的 | 做法 |
|---|---|
| 本机构建后直接跑 | `./scripts/deploy.sh up`（隐含 `docker compose build`） |
| 搬到没有外网 / 不想在目标机编译的服务器 | `./scripts/deploy.sh save-images` → 导出 tar.gz |
| 推公司私有仓库 | `docker compose build` + `docker tag` + `docker push` |

**导出**：

```bash
cd infra
./scripts/deploy.sh save-images            # 默认写到 infra/dist/
# → dist/hanyue-images-20261117-154500.tar.gz
```

脚本会先 `compose build`（有缓存则很快），再补齐 `postgres:16-alpine` / `redis:7-alpine`，
最后 `docker save | gzip`。打包内容 = `docker compose config --images` 能解析出的全部镜像，
所以目标机**完全不需要外网、也不需要 Python/Node 工具链**，只要 Docker。

**目标机导入**：

```bash
# 拷到目标机后（文件是 docker save 的 tar 再 gzip，不能直接 docker load -i）
gzip -dc hanyue-images-20261117-154500.tar.gz | docker load

cd infra
./scripts/deploy.sh init-env
docker compose up -d --no-build                      # 关键：--no-build，用导入的镜像
```

> **镜像 tag 跟目录名走**。compose 默认用目录名当项目名，镜像因此叫 `infra-api` / `infra-web`。
> 目标机仓库若落在别的目录名（如 `~/hanyue`），tag 会变成 `hanyue-api`，`--no-build` 就找不到镜像。
> 两种解法：目标目录仍叫 `infra`，或统一 `export COMPOSE_PROJECT_NAME=infra`。
> 卷名（`infra_pgdata` 等）同理，所以**别在已有数据的环境里改这个值**。

**推私有仓库**：

```bash
cd infra
docker compose build
docker tag infra-api  registry.example.com/hanyue/api:20261117
docker tag infra-worker registry.example.com/hanyue/worker:20261117
docker tag infra-web  registry.example.com/hanyue/web:20261117
docker push registry.example.com/hanyue/api:20261117   # 另两个同理
```

在 `docker-compose.yml` 里给对应服务加 `image: registry.example.com/hanyue/api:20261117`
即可让目标机走仓库而不是本地构建。

## 6. 从本地 SQLite 迁数据

本地开发现在也用 PostgreSQL（`docker-compose.dev.yml`），**正常开发不需要这一步**；
只有「历史数据还在旧 SQLite 里」时才搬。

本地库用 `dev-db.sh` 自带的封装（自动指向本地 PG、自动起库）：

```bash
cd infra
./scripts/dev-db.sh import-sqlite                 # 默认 apps/api/hanyue.db
./scripts/dev-db.sh import-sqlite /path/to/old.db # 指定文件
```

搬到**服务器上**的 PG 用 `deploy.sh`（脚本在 api 容器内执行）：

```bash
cd infra
./scripts/deploy.sh migrate --dry-run    # 先看各表行数，确认源库选对了
./scripts/deploy.sh migrate              # 目标空库：整表复制
./scripts/deploy.sh migrate --truncate   # 目标已有数据：先清空再灌
```

脚本在 `api` 容器内执行（PG 连接串与 app 模型都来自容器环境），行为要点：

- 按 `metadata.sorted_tables` 的依赖顺序复制，不临时关闭外键约束；
- SQLite 存 DATETIME 会丢时区，脚本按 UTC 补回，否则整库时间戳会偏 8 小时；
- 复制完重置 PostgreSQL 自增序列，避免新数据撞主键；
- 目标表已有数据默认**跳过**并列在输出的「说明」列里。

素材文件不在此脚本范围内。要一起搬：

```bash
cd infra
# 把本地 storage 目录灌进 storage 卷（api 服务需在运行）
docker compose cp ../apps/api/storage/. api:/data/storage/
```

## 7. 备份与恢复

```bash
./scripts/deploy.sh backup
# → backups/db-20261117-130500.dump     pg_dump 自定义格式（自带压缩，可选择性恢复）
# → backups/storage-20261117-130500.tgz storage 卷快照
```

保留份数由 `.env` 的 `BACKUP_KEEP_DAYS`（默认 14 天）控制，脚本只清理本目录下的旧备份。
建议再加一条宿主机 crontab，并把 `backups/` 同步到对象存储或另一台机器——本地同盘备份挡不住磁盘故障：

```cron
30 3 * * * cd /srv/hanyue/infra && ./scripts/deploy.sh backup >> /var/log/hanyue-backup.log 2>&1
```

恢复（会先停 api/worker，避免恢复期间继续写库）：

```bash
./scripts/deploy.sh restore backups/db-20261117-130500.dump
```

素材卷回滚：`docker compose cp backups/storage-20261117-130500.tgz` 解压后覆盖，或
`tar xzf ... -C <卷挂载路径>`。恢复后跑一次 `./scripts/deploy.sh health`，再抽查页面与素材直链。

## 8. 前端与反代约定

前端请求基址固定为同源相对路径 `/api/v1`（`apps/web/src/api/client.js`），所以：

- 不需要为跨域配置 `CORS_ORIGINS`，浏览器看到的是同源；
- `location /api/` 用 `proxy_pass http://$api_upstream$request_uri` 保留完整 URI 与查询串，
  不剥前缀；
- `resolver 127.0.0.11` + 变量上游：api 容器重建换 IP 后 nginx 会自动重新解析，
  否则会持续 502 直到 reload；
- vue-router 是 history 模式，`try_files $uri $uri/ /index.html` 负责刷新深链接不 404；
- `client_max_body_size 25m` 与后端 `MAX_UPLOAD_BYTES`（20MB）对齐；
- `/api/` 关了 `proxy_buffering`，`proxy_read_timeout 300s`，长导出/生成请求不会被网关掐断。

## 9. 生产加固清单

- [ ] 前置 TLS（Caddy 或宿主机 Nginx → `127.0.0.1:8080`），只放行 443/80
- [ ] `WEB_PORT` 不要直接暴露在公网裸奔；`API_PORT` 保持回环绑定
- [ ] `ENVIRONMENT=production` + `ALLOW_DEV_LOGIN=false`；首个账号用 `create-user` 建，不要靠开发态入口
- [ ] `ALLOW_REGISTRATION=false`（除非确实要对外开放注册）；密码策略保持默认或更严
- [ ] `.env` 权限 600，且不进版本库（`.gitignore` 已忽略）
- [ ] `backups/` 落在独立磁盘并异地同步
- [ ] 给 `api`/`worker` 配资源上限（`deploy.resources.limits`），避免视频渲染拖垮宿主机
- [ ] 公众号草稿箱：把服务器出口 IP 加入公众号 IP 白名单
- [ ] 定期 `./scripts/deploy.sh health` 接入监控；日志已有 10MB × 3 轮转

## 10. 排障

| 现象 | 原因与处理 |
|---|---|
| 页面提示「无法建立本地会话：开发态登录已关闭」或「需要重新进入」 | 代码里唯一的登录入口是 `POST /auth/login`（按 subject 换 token），`ENVIRONMENT=production` 时启动门禁会拒绝它，而正式 IdP 登录尚未实现 → 五个服务全 healthy 但进不去应用。自用部署在 `infra/.env` 设 `ENVIRONMENT=development` + `ALLOW_DEV_LOGIN=true` 后 `up --no-build` 重建 api/worker。**对公网开放前必须改回**，并在安全组里只放行自己的 IP |
| `api` 反复重启，日志 `生产配置不安全，已拒绝启动` | `.env` 的 `SECRET_KEY` 太短/是占位值、`ALLOW_DEV_LOGIN` 非 false，或 `DATABASE_URL` 还是 sqlite |
| `worker` 显示 `unhealthy`，但 `logs worker` 正在正常渲染 | 旧镜像的心跳写在消费循环里，`run_job` 阻塞期间不刷新，2 核上超过约 130 秒必判不健康。`build worker` 更新镜像即可（新版心跳在独立线程，见 2.3）。**不要直接 restart**，会中断渲染 |
| `db` 一直 unhealthy | `POSTGRES_PASSWORD` 为空或含 `${}` 等需转义字符；`docker compose logs db` 看 initdb 报错。改密码不会改已初始化卷，需进库 `ALTER USER` |
| `api` 反复重启，日志 `no pg_hba.conf entry for host "172.x.x.x", user ...` | 卷是**半初始化残留**：`initdb` 成功但官方 entrypoint 的 `pg_setup_hba_conf` 没跑完（它靠 `postgres -C password_encryption <你的 command 参数>` 求值，command 非法就中断），于是 `pg_hba.conf` 少了 `host all all all <method>` 这行；而 `PG_VERSION` 已存在，之后每次启动都 `Skipping initialization`，那行永远补不上。`./scripts/deploy.sh reset-db` 后 `up --no-build` |
| `db` 反复重启，日志 `FATAL: -c -c requires a value` | `docker-compose.lowmem.yml` 里 `db` 的 `command` 是「`-c` + 值」成对的列表，注释被插进了 `-c` 和值之间就会多出一个 `-c`。已由 `deploy.sh` 的 `lint_db_command` 在启动前拦截；修法是把注释挪到 `-c` 之前 |
| `web` 打不开但 `api` 正常 | `docker compose logs web` 看 502：通常是 nginx 配置语法错（`nginx -t`）或 `api` 未 healthy |
| 页面刷新 404 | `infra/nginx/default.conf` 的 `try_files ... /index.html` 被改动过 |
| 前端请求 502 / 一直 pending | api 未健康或端口被占；`./scripts/deploy.sh health` |
| 构建 `web` 卡在 `npm ci`（exit 1，日志含 `ETIMEDOUT`/`ECONNRESET`/`network`） | `registry.npmjs.org` 不可达。在 `infra/.env` 设 `NPM_REGISTRY=https://registry.npmmirror.com` 后重新 `build web`；镜像内会同时设 `replace-registry-host=always`，把 lock 里写死的 resolved 主机改写成该源 |
| `npm ci` 报 `Exit handler never called!`，不带任何原因 | npm 退出路径的 bug，触发条件是连接被中途掐断：并发构建互抢带宽/内存、镜像源限流、磁盘写满都会命中。已默认 `maxsockets 4` + 串行构建；仍复现就 `./scripts/deploy.sh doctor` 看 docker 数据目录剩余空间，并按日志尾部（现在会打印 npm debug log）判断是断连还是 `ENOSPC` |
| `apt-get install` 跑了几分钟（ffmpeg 约 120 MB） | 先确认没在并行构建（`BUILD_PARALLEL=1` 会并行）；再确认 `.env` 的 `APT_MIRROR` 生效——`build` 日志里该层应显示 `[ -n "mirrors.aliyun.com" ]` |
| 构建 `api`/`worker` 卡在 `pip install` | 同上思路，设 `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` |
| `up --build` 卡在 `Pulling fs layer` 不动，或基础镜像拉取只有几十 KB/s | 直连 docker.io。跑 `./scripts/deploy.sh mirror`（Docker Desktop 则 Settings → Docker Engine 加 `registry-mirrors`）后重试，见 2.4 |
| 构建 `api`/`worker` 卡在 `apt-get update` | Debian 源未换：`.env` 设 `APT_MIRROR=mirrors.aliyun.com` 后 `build api worker`；web 的 `apk add` 慢则设 `APK_MIRROR` |
| 上传大文件 413 | 改 nginx `client_max_body_size` 与后端 `MAX_UPLOAD_BYTES` 两处 |
| 视频成片报 `ffmpeg_missing`，或字幕显示为方块 | 镜像缺 ffmpeg 或缺中文字体；确认镜像内有 `ffmpeg` 与 `fonts-wqy-microhei`，见 §2.5 |
| worker 不消费、任务停在 queued | worker 未 healthy / 连不上 redis；`logs worker`。Redis 后端下 API 不再自行回收任务 |
| 任务时间戳差 8 小时 | 容器缺 `TZ`/`tzdata`，或迁移脚本未按 UTC 补时区；确认 Dockerfile 与 compose 的 `TZ` |
| `platform-evidence.json` 变成目录 | 挂载源文件缺失时 Docker 自动建目录；删掉目录后 `init-env` 重新生成 |
| 改了 `.env` 但配置没生效 | 配置只在进程启动时载入：`restart api worker` |
| 登录页提示「该邮箱已注册」但忘了密码 | `./scripts/deploy.sh create-user reset-password --email x`；重置后该账号所有旧会话立即失效 |
| 登录报 `account_locked` | 连续失败达 `AUTH_LOCK_THRESHOLD`（默认 5）次后被锁 `AUTH_LOCK_MINUTES`（默认 15）分钟；等锁过期，或 `create-user reset-password` 立即解锁 |
| 新部署登录页没有「注册」入口 | `ALLOW_REGISTRATION=false` 且库里已有密码账号。用 `./scripts/deploy.sh create-user create --email ...` 建账号 |
| 换用邮箱登录后「以前的稿件都不见了」 | 内容挂在旧开发态账号（如 `local-creator`）的 user.id 上。用 `create-user attach --subject local-creator --email ...` 转正，id 不变数据保留；不要新建账号 |
| 本地启动报 `ModuleNotFoundError: No module named 'psycopg'` | 本地切 PG 后缺驱动：`apps/api/.venv/bin/pip install -r requirements-prod.txt`（或单装 `psycopg[binary]`） |
| 本地 API 启动报 `connection refused ... 127.0.0.1:5433` | 本地库没起：`cd infra && ./scripts/dev-db.sh up`（用容器）或 `./scripts/dev-pg.sh up`（用本机 PG）；`status` 看进程/容器与表数量 |
| `dev-db.sh` 报端口 5433 被占 | 改 `DEV_PG_PORT`（compose 与 `DATABASE_URL` 两处要一致），或停掉占用进程；也可能是 `dev-pg.sh` 的集群还在跑：`./scripts/dev-pg.sh down` |
| `dev-pg.sh` 报 `could not create any TCP/IP sockets` / `Permission denied` | `postgres.exe` 被安全软件或防火墙拦了监听，或端口落在 Windows 保留区间（`netsh int ipv4 show excludedportrange protocol=tcp`）。换端口 `DEV_PG_PORT=5434`，或改用 `PG_MODE=service ./scripts/dev-pg.sh up` 复用系统已装服务 |
| `psql` 读初始化脚本报 `character with byte sequence ... has no equivalent in encoding "UTF8"` | Windows 客户端默认 GBK 解码 UTF-8 的 SQL：设 `PGCLIENTENCODING=UTF8`（脚本已内置），手工执行时也带上 |
| 某容器反复重启、`docker inspect` 显示 `OOMKilled: true` / exit 137 | 撞到 `mem_limit` 或宿主机内存耗尽。2G 机器先确认小内存档已生效（`doctor` 会打印），再查是谁涨的：`docker stats --no-stream`。`worker` 被杀的常见原因是同时跑了多条视频渲染 |
| `docker stats` 里 `db` 内存一路涨到上限 | `lowmem` 档未生效（`LOWMEM=0` 或内存 ≥3GB 未自动叠加），PG 用的是默认 `work_mem` / `max_connections`；手动 `docker compose -f docker-compose.yml -f docker-compose.lowmem.yml up -d` |

## 附：在 WSL2（原生 Docker Engine）里跑

装的是 **WSL Ubuntu 内的 docker.io / docker-ce**（没有 Docker Desktop）时，按下面走。
和 Docker Desktop 的区别只有三处：daemon 要自己起、compose v2 要单独装、仓库在 `/mnt/d` 上构建会慢。

### 1. 起 daemon 与 compose v2

```bash
# Ubuntu 默认没有 systemd：用 service 起；Windows 11 开了 systemd 的话用 systemctl
sudo service docker start          # 或：sudo systemctl start docker
docker info >/dev/null && echo "daemon OK"

# 免 sudo（重开终端或 newgrp 生效）
sudo usermod -aG docker "$USER"

# compose v2 插件（docker.io 不自带；Ubuntu 24.04 有 docker-compose-v2 包）
docker compose version || sudo apt install -y docker-compose-v2
```

`docker-compose`（v1，带连字符）不够用：本编排用到 `init: true`、`depends_on.condition`
等 v2 才支持的字段。若同时装了 v1 和 v2，务必用 `docker compose`（空格）。

### 2. 进到仓库并自检

```bash
cd /mnt/d/aicode/hanyue-workspace/infra
./scripts/deploy.sh doctor        # 只读检查：CPU/内存/磁盘/文件系统/daemon/compose/端口/配置
```

`doctor` 会明确告诉你缺什么。三个提示的含义：

| 输出 | 含义 |
|---|---|
| `文件系统 9p（Windows 盘）` | 当前在 `/mnt/d` 上构建，`npm ci` 会慢 5–10 倍，见下方「想快一点」 |
| `内存 ... 低于建议的 4GB` | WSL 默认最多用宿主内存的一半，改 `.wslconfig` 的 `memory=` |
| `端口 8080 已被占用` | 本服务已在跑，或换了别的进程；改 `.env` 的 `WEB_PORT` |

### 3. 部署

```bash
./scripts/deploy.sh init-env      # 生成 .env（WSL 内生成，天然 LF，不会带 CRLF）
./scripts/deploy.sh up            # 首次构建 10–25 分钟（/mnt/d 上更久）
./scripts/deploy.sh ps
```

然后 **在 Windows 浏览器打开 `http://localhost:8080`**：WSL2 有 localhost 转发，不用查 WSL 的 IP。
API 直连调试建议在 WSL 里 `curl http://127.0.0.1:8000/health`（`API_PORT` 只绑回环，
Windows 侧偶尔转发不到这个回环端口，不是服务坏了）。

### 4. 想快一点：把仓库放进 WSL 家目录

`/mnt/d` 走 9p 协议，`node_modules`（200 MB，几万个小文件）在其中极慢。改法：

```bash
rsync -a --exclude node_modules --exclude .venv --exclude storage \
      /mnt/d/aicode/hanyue-workspace/ ~/hanyue/
cd ~/hanyue/infra && ./scripts/deploy.sh up     # 构建通常快 5 倍以上
```

代价是「Windows 上改代码、WSL 里部署」两处不同步。只偶尔验证一次就用 `/mnt/d`，
长期在这台机器跑就 rsync 到家目录，改完再同步一次。

### 5. 这套环境的四个特有坑

1. **WSL 重启后容器不会自己回来**。WSL 的 init 进程一停，dockerd 也随之退出；
   `restart: unless-stopped` 只在 daemon 活着时生效。开机后先
   `sudo service docker start`，再 `cd ~/hanyue/infra && docker compose up -d`（无变更时秒起）。
   要真自启就把这两条写进 Windows 任务计划或 `wsl.conf` 的 boot command。
2. **vhdx 只涨不缩**。镜像、构建缓存、PG 数据、素材全在 `ext4.vhdx` 里，
   `docker system prune -af --volumes` 之后 vhdx 仍占着空间。
   需要在 Windows 侧压缩：`wsl --manage Ubuntu --set-sparse true`（较新 WSL）或 `diskpart` 压缩。
3. **行尾**。`infra/.env` 若用 Windows 记事本/VSCode 存过，会变成 CRLF；
   `doctor` 会提示，执行 `sed -i 's/\r$//' infra/.env` 即可。`.sh` 脚本同理。
4. **端口别和 Windows 侧冲突**。Windows 已经占了的 8080/8000，WSL 里也能绑上，
   但 Windows 浏览器访问到的可能是 Windows 那个进程 —— 现象是「页面不是本项目」。
   先 `netstat -ano | findstr :8080` 确认。

### 6. 和 Docker Desktop 的差异对照

| 项 | WSL 原生 docker | Docker Desktop |
|---|---|---|
| daemon | 自己 `service docker start` | 随 Desktop 启动 |
| compose v2 | 需 `apt install docker-compose-v2` | 自带 |
| 数据位置 | `ext4.vhdx`（不易备份/迁移） | `docker-desktop-data` vhdx |
| 端口转发 | WSL2 localhost 转发（回环端口偶有例外） | 由 Desktop 统一代理 |
| 生产可用性 | 可（本机/内网验证足够） | 不建议用于生产 |

数据要迁到真服务器时，用本文 §7 的 `backup`（`pg_dump`）+ `storage-*.tgz` 搬过去，
不要在 WSL 和服务器之间直接拷 vhdx。
