#!/usr/bin/env bash
# 部署入口：把 compose 的一堆命令收敛成子命令，并补上 compose 本身不管的事
# （生成密钥、准备证据文件、等待健康、备份/恢复数据库与素材卷、SQLite → PG 搬迁）。
#
# 用法：./scripts/deploy.sh <子命令> [参数]
#   init-env            生成 infra/.env（随机 SECRET_KEY / POSTGRES_PASSWORD），补证据文件
#   doctor              环境自检：CPU/内存/磁盘/文件系统/docker daemon/compose v2/端口/配置
#   up [--no-build]     构建并后台启动全部服务，等 api 健康后打印访问地址
#   down                停止容器（数据卷保留）
#   destroy             停止并删除数据卷（会二次确认，PG/素材/队列全部丢失）
#   restart [服务...]    重启指定服务（默认全部）
#   build [服务...]      重新构建镜像
#   save-images [目录]   构建并导出全部镜像为 tar.gz（离线/搬服务器）
#   ps                  查看服务与健康状态
#   logs [服务] [-f]     查看日志（默认 -f 跟随）
#   health              逐服务检查健康状态，任一异常返回非 0
#   psql                进入数据库交互终端
#   shell <服务>         进入容器 shell（api / worker / web / db / redis）
#   backup              备份 PG（自定义格式）+ storage 卷，并按 BACKUP_KEEP_DAYS 清理
#   restore <文件>       从 .dump 恢复数据库（--clean，会二次确认）
#   migrate [参数]       SQLite → PG 数据搬迁（参数透传给 migrate_sqlite_to_pg.py）
#
# 需要 Docker Engine + Compose v2；Windows 在 Git Bash 或 WSL 中执行。

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$INFRA_DIR"

# 基础文件必须显式写进 -f：compose 一旦收到任何 -f，就不再隐式加载默认的
# docker-compose.yml。小内存档只写了 db/redis/api 的增量字段（command/mem_limit），
# 丢掉基础文件后 db 既没有 image 也没有 build，compose 会直接判定编排非法。
COMPOSE=(docker compose -f "$INFRA_DIR/docker-compose.yml")
BACKUP_DIR="${BACKUP_DIR:-$INFRA_DIR/backups}"

# 小内存档：物理内存 < 3GB 时自动叠加 docker-compose.lowmem.yml
# （LOWMEM=1 强制启用，LOWMEM=0 关闭；读不到 /proc/meminfo 的平台默认不启用）
LOWMEM_ON=0
LOWMEM_FILE="$INFRA_DIR/docker-compose.lowmem.yml"
if [ -f "$LOWMEM_FILE" ]; then
  case "${LOWMEM:-auto}" in
    1|true|yes) LOWMEM_ON=1 ;;
    0|false|no) LOWMEM_ON=0 ;;
    *)
      mem_probe="$(awk '/^MemTotal:/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
      if [ "${mem_probe:-0}" -gt 0 ] && [ "${mem_probe}" -lt 3072 ]; then
        LOWMEM_ON=1
      fi
      ;;
  esac
fi
if [ "$LOWMEM_ON" -eq 1 ]; then
  COMPOSE+=(-f "$LOWMEM_FILE")
fi

# ── 小工具 ────────────────────────────────────────────────────
info() { printf '\033[36m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[deploy]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[deploy]\033[0m %s\n' "$*" >&2; exit 1; }

# doctor 用的三态输出；DOCTOR_RC 是被调用后累计的退出码
DOCTOR_RC=0
chk_ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
chk_warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
chk_fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; DOCTOR_RC=1; }

need_docker() {
  command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装 Docker Engine / Docker Desktop"
  docker compose version >/dev/null 2>&1 || die "未找到 docker compose v2（请升级 Docker 或安装 compose 插件）"
}

# 从 .env 读变量（不 source，避免值里的空格/特殊字符被执行）
env_get() {
  local key="$1" file="$INFRA_DIR/.env" line
  [ -f "$file" ] || return 1
  line="$(grep -E "^[[:space:]]*${key}=" "$file" | tail -n 1 || true)"
  [ -n "$line" ] || return 1
  printf '%s' "${line#*=}" | sed -e 's/^["'\'']//' -e 's/["'\'']$//' | tr -d '\r'
}

random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 48 | tr -d '\n=+/' | cut -c1-64
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c "import secrets; print(secrets.token_urlsafe(48))"
  elif command -v python >/dev/null 2>&1; then
    python -c "import secrets; print(secrets.token_urlsafe(48))"
  else
    die "需要 openssl 或 python 生成随机密钥"
  fi
}

ensure_evidence() {
  # compose 把 ./platform-evidence.json 以文件方式挂载；文件不存在时 Docker 会
  # 建成同名目录，导致容器内读不到证据文件（能力矩阵全部待核验）。
  if [ ! -f "$INFRA_DIR/platform-evidence.json" ]; then
    if [ -d "$INFRA_DIR/platform-evidence.json" ]; then
      die "infra/platform-evidence.json 是目录（Docker 自动创建的），请先 rmdir 它再重跑"
    fi
    cp "$INFRA_DIR/platform-evidence.example.json" "$INFRA_DIR/platform-evidence.json"
    info "已生成 platform-evidence.json（空证据，平台能力保持待核验）"
  fi
}

require_env() {
  [ -f "$INFRA_DIR/.env" ] || die "缺少 infra/.env，先执行：./scripts/deploy.sh init-env"
  local secret pass
  secret="$(env_get SECRET_KEY || true)"
  pass="$(env_get POSTGRES_PASSWORD || true)"
  [ -n "$secret" ] || die "infra/.env 缺少 SECRET_KEY"
  [ -n "$pass" ] || die "infra/.env 缺少 POSTGRES_PASSWORD"
  case "$secret" in
    *替换*|*change*|dev-only-*) die "infra/.env 的 SECRET_KEY 仍是占位值，请换成随机串" ;;
  esac
  case "$pass" in
    *替换*|*change*) die "infra/.env 的 POSTGRES_PASSWORD 仍是占位值，请换成强密码" ;;
  esac
  [ "${#secret}" -ge 32 ] || die "SECRET_KEY 至少 32 字符（当前 ${#secret}）"
}

# ── 子命令 ────────────────────────────────────────────────────
cmd_init_env() {
  if [ -f "$INFRA_DIR/.env" ]; then
    info "infra/.env 已存在，跳过生成（如需重置请先备份后删除）"
  else
    cp "$INFRA_DIR/.env.example" "$INFRA_DIR/.env"
    local secret pass
    secret="$(random_secret)"
    pass="$(random_secret)"
    # 用临时文件替换，兼容 macOS/BSD sed 与 GNU sed 的差异
    awk -v s="$secret" -v p="$pass" '
      /^SECRET_KEY=/ { print "SECRET_KEY=" s; next }
      /^POSTGRES_PASSWORD=/ { print "POSTGRES_PASSWORD=" p; next }
      { print }
    ' "$INFRA_DIR/.env" > "$INFRA_DIR/.env.tmp"
    mv "$INFRA_DIR/.env.tmp" "$INFRA_DIR/.env"
    chmod 600 "$INFRA_DIR/.env" 2>/dev/null || true
    info "已生成 infra/.env（SECRET_KEY / POSTGRES_PASSWORD 为随机值）"
  fi
  ensure_evidence
}

wait_healthy() {
  local service="$1" tries="${2:-60}" i=0 id status
  id="$("${COMPOSE[@]}" ps -q "$service" 2>/dev/null || true)"
  [ -n "$id" ] || { warn "$service 未启动，跳过健康等待"; return 0; }
  while [ "$i" -lt "$tries" ]; do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || echo unknown)"
    case "$status" in
      healthy|running) info "$service: $status"; return 0 ;;
      unhealthy|exited|dead) warn "$service 状态异常：$status"; return 1 ;;
    esac
    i=$((i + 1))
    sleep 2
  done
  warn "$service 在 $((tries * 2)) 秒内未就绪，请查看日志：./scripts/deploy.sh logs $service"
  return 1
}

cmd_up() {
  ensure_evidence
  local cfg_err
  # 先做一次静态校验：多文件合并结果非法时给出可读报错，而不是让 compose
  # 在 up 阶段抛一句与真实原因无关的 internal error
  if ! cfg_err="$("${COMPOSE[@]}" config --quiet 2>&1)"; then
    printf '%s\n' "$cfg_err" >&2
    die "编排校验失败（上面是 compose 原始输出）：检查 docker-compose.yml 与 lowmem 叠加后的服务定义"
  fi
  if [ "$LOWMEM_ON" -eq 1 ]; then
    info "已启用小内存档（docker-compose.yml + docker-compose.lowmem.yml；PG shared_buffers=160MB / Redis 128MB / 容器内存上限）"
  fi
  [ "${1:-}" = "--no-build" ] || build_services
  # 镜像已就绪；--no-build 同时避免 compose 又并行触发一次构建
  "${COMPOSE[@]}" up -d --no-build
  wait_healthy db 30 || true
  wait_healthy redis 20 || true
  wait_healthy api 60 || true
  wait_healthy worker 40 || true
  wait_healthy web 30 || true
  cmd_access_hint
}

cmd_access_hint() {
  local port
  port="$(env_get WEB_PORT || true)"
  port="${port:-8080}"
  info "前端入口：http://localhost:${port}"
  info "健康检查：http://localhost:${port}/healthz   API 文档：http://127.0.0.1:$(env_get API_PORT || echo 8000)/docs"
}

cmd_down() { "${COMPOSE[@]}" down; }

cmd_destroy() {
  warn "这会删除 pgdata / storage / redisdata 三个数据卷，数据库与素材不可恢复。"
  printf '确认请输入 yes：'
  read -r answer
  [ "$answer" = "yes" ] || die "已取消"
  "${COMPOSE[@]}" down --volumes --remove-orphans
}

cmd_restart() { "${COMPOSE[@]}" restart "$@"; }
cmd_ps()      { "${COMPOSE[@]}" ps; }

# 构建顺序：web 最吃内存（vite 需 ≥1.5GB），放在最前面独占机器；api/worker 同源同构。
BUILD_ORDER=(web api worker)

# compose v2.22+ 默认走 bake，把三个镜像并行构建 —— 在 2 核 / <3GB 的机器上就是
# npm、pip、apt 三份同时抢内存与带宽，典型症状是 apt 慢到几分钟、npm 崩在
# "Exit handler never called!"。默认串行；大机器可 BUILD_PARALLEL=1 恢复并行。
build_services() {
  local targets=("$@") svc
  local -a progress=()
  # BUILD_PROGRESS=plain 时把 RUN 的完整输出（含 npm 每个包的 fetch 日志）实时打到终端；
  # 默认 auto 只显示每层的秒数，看起来像"卡住"其实在下载。
  if [ -n "${BUILD_PROGRESS:-}" ]; then
    progress=(--progress "$BUILD_PROGRESS")
  fi
  if [ "${#targets[@]}" -eq 0 ]; then
    targets=("${BUILD_ORDER[@]}")
  fi
  if [ "${BUILD_PARALLEL:-0}" = "1" ]; then
    info "并行构建（BUILD_PARALLEL=1）：${targets[*]}"
    "${COMPOSE[@]}" build "${progress[@]}" "${targets[@]}"
    return 0
  fi
  for svc in "${targets[@]}"; do
    info "构建 ${svc}（串行，避免与其它镜像抢内存/带宽；大机器可 BUILD_PARALLEL=1）"
    "${COMPOSE[@]}" build "${progress[@]}" "$svc"
  done
}

cmd_build() { build_services "$@"; }

cmd_save_images() {
  need_docker
  local out="${1:-$INFRA_DIR/dist}" images img file ts
  mkdir -p "$out"

  images="$("${COMPOSE[@]}" config --images 2>/dev/null | sort -u)"
  [ -n "$images" ] || die "无法从 compose 解析镜像名"

  info "先确保镜像齐备"
  "${COMPOSE[@]}" build
  for img in $images; do
    docker image inspect "$img" >/dev/null 2>&1 || { info "拉取 $img"; docker pull "$img"; }
  done

  info "打包以下镜像（含 postgres/redis，目标机无需外网）："
  printf '%s\n' "$images" | sed 's/^/  - /'

  ts="$(date +%Y%m%d-%H%M%S)"
  file="$out/hanyue-images-$ts.tar.gz"
  info "导出到 $file（数 GB，需要几分钟）"
  # shellcheck disable=SC2086
  docker save $images | gzip -1 > "$file"
  [ -s "$file" ] || die "导出失败：$file 为空"

  info "完成：$file（$(du -h "$file" 2>/dev/null | cut -f1)）"
  cat <<EOF

目标服务器（无需外网、无需在目标机构建）：
  gzip -dc $(basename "$file") | docker load
  cd infra && ./scripts/deploy.sh init-env && docker compose up -d --no-build
EOF
}

cmd_logs() {
  local service="" follow=0
  for arg in "$@"; do
    case "$arg" in
      -f|--follow) follow=1 ;;
      *) service="$arg" ;;
    esac
  done
  if [ "$follow" -eq 1 ] || [ -z "$service" ]; then
    "${COMPOSE[@]}" logs -f --tail=200 ${service:+$service}
  else
    "${COMPOSE[@]}" logs --tail=200 "$service"
  fi
}

cmd_health() {
  local rc=0
  "${COMPOSE[@]}" ps
  for svc in db redis api worker web; do
    wait_healthy "$svc" 5 || rc=1
  done
  return "$rc"
}

cmd_psql() {
  local user db
  user="$(env_get POSTGRES_USER || echo hanyue)"
  db="$(env_get POSTGRES_DB || echo hanyue)"
  "${COMPOSE[@]}" exec db psql -U "$user" -d "$db"
}

cmd_shell() {
  local svc="${1:-}"
  [ -n "$svc" ] || die "用法：./scripts/deploy.sh shell <api|worker|web|db|redis>"
  if [ "$svc" = "db" ]; then
    "${COMPOSE[@]}" exec db sh
  else
    "${COMPOSE[@]}" exec "$svc" sh
  fi
}

cmd_backup() {
  require_env
  mkdir -p "$BACKUP_DIR"
  local ts user db file
  ts="$(date +%Y%m%d-%H%M%S)"
  user="$(env_get POSTGRES_USER || echo hanyue)"
  db="$(env_get POSTGRES_DB || echo hanyue)"
  file="$BACKUP_DIR/db-$ts.dump"

  info "备份数据库到 $file"
  # -Fc 自定义格式：可选择性恢复单表，且自带压缩
  "${COMPOSE[@]}" exec -T db pg_dump -U "$user" -d "$db" -Fc > "$file"
  [ -s "$file" ] || die "pg_dump 输出为空，备份失败"

  info "备份素材卷到 $BACKUP_DIR/storage-$ts.tgz"
  # 用 api 镜像做一次性容器，避免猜命名卷的名字（项目名由目录决定）
  "${COMPOSE[@]}" run --rm --no-deps -u 0 -T \
    -v "$BACKUP_DIR:/backup" api \
    tar czf "/backup/storage-$ts.tgz" -C /data storage

  local keep
  keep="$(env_get BACKUP_KEEP_DAYS || true)"
  keep="${keep:-14}"
  find "$BACKUP_DIR" -maxdepth 1 -name 'db-*.dump' -mtime "+$keep" -print -delete || true
  find "$BACKUP_DIR" -maxdepth 1 -name 'storage-*.tgz' -mtime "+$keep" -print -delete || true
  info "完成。保留最近 ${keep} 天备份（可在 .env 改 BACKUP_KEEP_DAYS）"
}

cmd_restore() {
  require_env
  local file="${1:-}"
  [ -n "$file" ] || die "用法：./scripts/deploy.sh restore backups/db-YYYYmmdd-HHMMSS.dump"
  [ -f "$file" ] || die "文件不存在：$file"
  local user db
  user="$(env_get POSTGRES_USER || echo hanyue)"
  db="$(env_get POSTGRES_DB || echo hanyue)"
  warn "恢复会 --clean 覆盖现有数据库内容（$db），当前数据不做自动备份。"
  printf '确认请输入 yes：'
  read -r answer
  [ "$answer" = "yes" ] || die "已取消"
  # 停掉写库进程，避免恢复期间 worker 继续写
  "${COMPOSE[@]}" stop worker api >/dev/null 2>&1 || true
  "${COMPOSE[@]}" exec -T db pg_restore -U "$user" -d "$db" --clean --if-exists --no-owner < "$file"
  "${COMPOSE[@]}" start api worker
  wait_healthy api 60 || true
  info "恢复完成"
}

cmd_migrate() {
  require_env
  local sqlite="${SQLITE_PATH:-../apps/api/hanyue.db}"
  [ -f "$INFRA_DIR/$sqlite" ] || [ -f "$sqlite" ] || die "找不到 SQLite 文件：$sqlite（可用 SQLITE_PATH 指定）"
  local abs
  abs="$(cd "$(dirname "$sqlite")" && pwd)/$(basename "$sqlite")"
  info "在 api 容器内执行迁移，源文件：$abs"
  "${COMPOSE[@]}" run --rm -T \
    -v "$abs:/data/source.db:ro" \
    -v "$INFRA_DIR/scripts:/scripts:ro" \
    api python /scripts/migrate_sqlite_to_pg.py --sqlite /data/source.db "$@"
}

port_busy() {
  local p="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk -v pat=":${p}\$" '$4 ~ pat {found=1} END{exit !found}'
  else
    timeout 1 bash -c "exec 3<>/dev/tcp/127.0.0.1/$p" >/dev/null 2>&1
  fi
}

cmd_doctor() {
  local kernel arch cpu mem_mb avail_gb fs_type secret pass port
  info "环境自检（只读，不修改任何东西）"

  printf '\n\033[1m[系统]\033[0m\n'
  kernel="$(uname -r)"
  arch="$(uname -m)"
  if grep -qi microsoft /proc/version 2>/dev/null; then
    chk_ok "WSL 环境（kernel ${kernel}, ${arch}）：Windows 浏览器可直接访问 http://localhost:<WEB_PORT>"
  else
    chk_ok "kernel ${kernel}, ${arch}"
  fi
  case "$arch" in
    x86_64|aarch64|arm64) ;;
    *) chk_warn "${arch} 未验证过，确认基础镜像有对应架构" ;;
  esac

  cpu="$(nproc 2>/dev/null || echo 0)"
  if [ "${cpu:-0}" -ge 4 ]; then
    chk_ok "CPU ${cpu} 核"
  elif [ "${cpu:-0}" -ge 2 ]; then
    chk_warn "CPU ${cpu} 核：满足最低要求，1080p 视频成片会很慢（建议 4 核）"
  else
    chk_fail "CPU ${cpu} 核：低于最低要求（2 核）"
  fi

  mem_mb="$(awk '/^MemTotal:/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
  if [ "${mem_mb:-0}" -ge 7500 ]; then
    chk_ok "内存 ${mem_mb}MB"
  elif [ "${mem_mb:-0}" -ge 3800 ]; then
    chk_ok "内存 ${mem_mb}MB（达到最低要求 4GB）"
  elif [ "${mem_mb:-0}" -ge 1800 ]; then
    chk_warn "内存 ${mem_mb}MB：低于建议的 4GB。可跑（单用户/小团队、非视频为主），但构建前端需在别处做或加 2GB swap"
  else
    chk_fail "内存 ${mem_mb}MB：不足以构建与渲染"
  fi
  if [ "$LOWMEM_ON" -eq 1 ]; then
    chk_ok "已叠加小内存档 docker-compose.lowmem.yml（PG/Redis 参数收紧 + 每容器内存上限）"
    # npm ci + vite build 峰值 ≥1.5GB，在这类机器上是构建期最容易 OOM 的一步
    local swap_mb
    swap_mb="$(awk '/^SwapTotal:/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
    if [ "${swap_mb:-0}" -lt 512 ]; then
      chk_warn "swap 只有 ${swap_mb}MB：构建 web（npm ci + vite）容易 OOM，建议加 2GB swap 或换机器构建（见 docs/docker-deploy.md 2.3）"
    else
      chk_ok "swap ${swap_mb}MB（构建有回旋余地）"
    fi
  fi

  avail_gb="$(df -Pk "$INFRA_DIR" 2>/dev/null | awk 'NR==2{printf "%d", $4/1024/1024}')"
  fs_type="$(df -PT "$INFRA_DIR" 2>/dev/null | awk 'NR==2{print $2}')"
  if [ "${avail_gb:-0}" -ge 40 ]; then
    chk_ok "可用磁盘 ${avail_gb}GB"
  elif [ "${avail_gb:-0}" -ge 15 ]; then
    chk_warn "可用磁盘 ${avail_gb}GB：够跑，但构建缓存 + 素材增长后容易满（建议 40GB）"
  else
    chk_fail "可用磁盘 ${avail_gb}GB：镜像 + 构建缓存就要 6GB 以上，先清理"
  fi
  case "$fs_type" in
    9p|drvfs|cifs|vboxsf)
      chk_warn "工作目录在 ${fs_type}（Windows 盘）上：npm ci / pip install 会明显变慢；想快就先 rsync 到 WSL 家目录再构建" ;;
    *) chk_ok "文件系统 ${fs_type}（构建速度正常）" ;;
  esac

  printf '\n\033[1m[Docker]\033[0m\n'
  if ! command -v docker >/dev/null 2>&1; then
    chk_fail "未找到 docker 命令：先安装 Docker Engine（WSL Ubuntu：sudo apt install docker.io）"
  elif ! docker info >/dev/null 2>&1; then
    chk_fail "docker daemon 连不上：WSL 里先启动它 —— sudo service docker start（有 systemd 则 sudo systemctl start docker）；若报 permission denied 则 sudo usermod -aG docker \$USER 后重开终端"
  else
    chk_ok "docker daemon 正常（server $(docker version --format '{{.Server.Version}}' 2>/dev/null)）"
    if docker compose version >/dev/null 2>&1; then
      chk_ok "docker compose v2（$(docker compose version --short 2>/dev/null)）"
    elif command -v docker-compose >/dev/null 2>&1; then
      chk_fail "只有 docker-compose v1：本编排用到 init/condition 等 v2 字段，请装 compose 插件（sudo apt install docker-compose-v2）"
    else
      chk_fail "没有 compose：sudo apt install docker-compose-v2（或 docker-compose-plugin）"
    fi
    # 国内直连 registry-1.docker.io 拉 python/node/pg/redis 常常几 KB/s；
    # 配了 mirror 才会快。只提示，不算阻断项。
    local mirrors docker_root docker_free
    mirrors="$(docker info --format '{{json .RegistryConfig.Mirrors}}' 2>/dev/null || echo '[]')"
    case "$mirrors" in
      ''|'[]'|'null')
        chk_warn "docker 未配 registry-mirrors：拉基础镜像会直连 docker.io（国内很慢），可跑 ./scripts/deploy.sh mirror" ;;
      *)
        chk_ok "registry-mirrors：$mirrors" ;;
    esac
    # Docker 的数据目录在 WSL 的 ext4 里，跟 /mnt/d 的剩余空间是两回事：
    # 只看工作目录会漏掉「镜像+构建缓存把 docker 盘写满」这一类构建失败。
    docker_root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
    docker_free="$(df -Pk "$docker_root" 2>/dev/null | awk 'NR==2{printf "%d", $4/1024/1024}')"
    if [ "${docker_free:-0}" -ge 10 ]; then
      chk_ok "docker 数据目录 $docker_root 可用 ${docker_free}GB"
    else
      chk_fail "docker 数据目录 $docker_root 只剩 ${docker_free}GB：镜像 + 构建缓存要 6GB 以上，先 docker builder prune"
    fi
  fi

  printf '\n\033[1m[端口]\033[0m\n'
  for port in "$(env_get WEB_PORT || echo 8080)" "$(env_get API_PORT || echo 8000)"; do
    if port_busy "$port"; then
      chk_warn "端口 ${port} 已被占用：若本服务已在运行属正常；否则改 .env 里的端口"
    else
      chk_ok "端口 ${port} 空闲"
    fi
  done

  printf '\n\033[1m[配置]\033[0m\n'
  if [ -f "$INFRA_DIR/.env" ]; then
    chk_ok "infra/.env 存在"
    if grep -q $'\r' "$INFRA_DIR/.env" 2>/dev/null; then
      chk_warn "infra/.env 含 CRLF 行尾（Windows 编辑器常见）：建议 sed -i 's/\r\$//' infra/.env"
    fi
    secret="$(env_get SECRET_KEY || true)"
    pass="$(env_get POSTGRES_PASSWORD || true)"
    case "$secret" in
      "") chk_fail "SECRET_KEY 为空" ;;
      *替换*|*change*) chk_fail "SECRET_KEY 仍是占位值" ;;
      *) if [ "${#secret}" -ge 32 ]; then chk_ok "SECRET_KEY 长度 ${#secret}"; else chk_fail "SECRET_KEY 只有 ${#secret} 字符（需 ≥32）"; fi ;;
    esac
    case "$pass" in
      "") chk_fail "POSTGRES_PASSWORD 为空" ;;
      *替换*|*change*) chk_fail "POSTGRES_PASSWORD 仍是占位值" ;;
      *) chk_ok "POSTGRES_PASSWORD 已设置（${#pass} 字符）" ;;
    esac
  else
    chk_warn "infra/.env 不存在：up 会自动生成随机密钥"
  fi
  if [ -d "$INFRA_DIR/platform-evidence.json" ]; then
    chk_fail "platform-evidence.json 是目录（Docker 自动建成）：rmdir 后重跑 init-env"
  elif [ -f "$INFRA_DIR/platform-evidence.json" ]; then
    chk_ok "platform-evidence.json 已就绪"
  else
    chk_warn "platform-evidence.json 缺失：init-env 会从示例复制"
  fi

  printf '\n'
  if [ "$DOCTOR_RC" -eq 0 ]; then
    info "自检通过：./scripts/deploy.sh up"
  else
    warn "存在阻断项（✗），按上面提示处理后重跑 doctor"
  fi
  return "$DOCTOR_RC"
}

usage() {
  cat <<'EOF'
部署入口：./scripts/deploy.sh <子命令> [参数]

  init-env            生成 infra/.env（随机 SECRET_KEY / POSTGRES_PASSWORD），补证据文件
  doctor              环境自检：CPU/内存/磁盘/文件系统/docker daemon/compose v2/端口/配置
  up [--no-build]     构建并后台启动全部服务，等 api 健康后打印访问地址
  down                停止容器（数据卷保留）
  destroy             停止并删除数据卷（二次确认，PG/素材/队列全部丢失）
  restart [服务...]    重启指定服务（默认全部）
  build [服务...]      重新构建镜像
  save-images [目录]   构建并把全部镜像导出成 tar.gz（离线部署/搬服务器用）
  ps                  查看服务与健康状态
  logs [服务] [-f]     查看日志（默认 -f 跟随）
  health              逐服务检查健康状态，任一异常返回非 0
  psql                进入数据库交互终端
  mirror              配置 docker 镜像加速（国内拉取基础镜像用，需 sudo）
  shell <服务>         进入容器 shell（api / worker / web / db / redis）
  backup              备份 PG + storage 卷，并按 BACKUP_KEEP_DAYS 清理
  restore <文件>       从 .dump 恢复数据库（--clean，二次确认）
  migrate [参数]       SQLite → PG 数据搬迁（参数透传给 migrate_sqlite_to_pg.py）

环境变量：
  LOWMEM=1|0          强制启用/关闭小内存档（默认 auto：物理内存 < 3GB 自动启用）
  BUILD_PARALLEL=1    并行构建三个镜像（默认串行：2 核 / <3GB 机器并行会互抢内存与带宽）
  BUILD_PROGRESS=plain  构建时实时输出 RUN 内部日志（npm 每个包的 fetch 都能看到）
  COMPOSE_PROJECT_NAME  覆盖 compose 项目名（影响镜像 tag 与卷名前缀）
  BACKUP_DIR          备份输出目录（默认 infra/backups）
EOF
}

main() {
  local cmd="${1:-help}"
  [ $# -gt 0 ] && shift || true
  case "$cmd" in
    init-env) cmd_init_env ;;
    doctor)   cmd_doctor ;;
    up)       need_docker; [ -f "$INFRA_DIR/.env" ] || cmd_init_env; require_env; cmd_up "$@" ;;
    down)     need_docker; cmd_down ;;
    destroy)  need_docker; cmd_destroy ;;
    restart)  need_docker; cmd_restart "$@" ;;
    build)    need_docker; cmd_build "$@" ;;
    save-images) need_docker; cmd_save_images "$@" ;;
    ps)       need_docker; cmd_ps ;;
    logs)     need_docker; cmd_logs "$@" ;;
    health)   need_docker; cmd_health ;;
    mirror)   bash "$INFRA_DIR/scripts/set-docker-mirror.sh" "$@" ;;
    psql)     need_docker; cmd_psql ;;
    shell)    need_docker; cmd_shell "$@" ;;
    backup)   need_docker; cmd_backup ;;
    restore)  need_docker; cmd_restore "$@" ;;
    migrate)  need_docker; cmd_migrate "$@" ;;
    help|-h|--help) usage ;;
    *) usage; die "未知子命令：$cmd" ;;
  esac
}

main "$@"
