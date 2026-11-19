#!/usr/bin/env bash
# 本机原生 PostgreSQL 管理（**不依赖 Docker**）。
#
# 两种用法，按需选一种：
#
#   1) 项目自带集群（默认，推荐）：initdb 一个只用 127.0.0.1:5433 的私有集群，
#      数据落在 infra/.pgdev/data，删掉即清空，不碰系统里别的库。
#   2) 复用系统已装的 PostgreSQL 服务：连本机 5432，只建角色/库，不 initdb。
#
#   ./scripts/dev-pg.sh up            初始化（首次）+ 启动 + 建库，打印 DATABASE_URL
#   ./scripts/dev-pg.sh down          停止集群（数据保留；service 模式只提示不动系统服务）
#   ./scripts/dev-pg.sh status        集群状态、端口、库表数量
#   ./scripts/dev-pg.sh psql          用应用账号进入 psql
#   ./scripts/dev-pg.sh reset         删掉集群目录重建（二次确认，数据清空）
#   ./scripts/dev-pg.sh url           只打印连接串
#   ./scripts/dev-pg.sh bootstrap     up + 建表 + 创建首个账号
#   ./scripts/dev-pg.sh init-sql      只重跑 postgres/init/01-init.sql（时区/扩展等）
#   ./scripts/dev-pg.sh init-schema   用 postgres/schema.sql 建全部表/索引（纯 SQL，幂等）
#   ./scripts/dev-pg.sh import-sqlite [文件]
#                                     把旧 SQLite 数据搬进来（默认 apps/api/hanyue.db）
#   ./scripts/dev-pg.sh write-env     把连接串写进 apps/api/.env 的 DATABASE_URL
#
# 模式与参数（都可用环境变量覆盖，也可写在 infra/.env）：
#   PG_MODE=cluster|service   默认 cluster
#   PG_BIN=/path/to/pg/bin    不指定则自动探测（PATH → Program Files → homebrew/apt）
#   DEV_PG_PORT               默认 cluster 5433 / service 5432
#   DEV_POSTGRES_USER/DEV_POSTGRES_PASSWORD/DEV_POSTGRES_DB
#                             默认 hanyue / hanyue_dev_pwd / hanyue（与 docker dev 一致）
#   PGSUPERUSER / PGSUPERUSER_PASSWORD
#                             service 模式下建角色用的超级用户；密码留空则交互输入
#   BOOTSTRAP_EMAIL           bootstrap 时免交互指定邮箱
#
# 为什么端口用 5433：本机可能已经跑着 5432（系统服务），开发集群避开它，
# 与 infra/docker-compose.dev.yml 的约定也一致，apps/api/.env 无需改。

set -euo pipefail

# psql 默认按 Windows 本地代码页（GBK）解码输入，读 UTF-8 的 .sql 会报
# "character with byte sequence ... has no equivalent in encoding UTF8"。
# 库是 UTF8（initdb --encoding=UTF8），客户端也固定成 UTF8。
export PGCLIENTENCODING=UTF8

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$INFRA_DIR/.." && pwd)"
API_DIR="$REPO_DIR/apps/api"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'

info() { printf '%s[dev-pg]%s %s\n' "$BLUE" "$RESET" "$*"; }
ok()   { printf '%s[dev-pg]%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%s[dev-pg]%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die()  { printf '%s[dev-pg]%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

# shell 环境优先，其次 infra/.env，最后默认值——与 compose / dev-db.sh 一致
env_get() {
  local key="$1" fallback="$2" value
  value="${!key:-}"
  if [ -z "$value" ] && [ -f "$INFRA_DIR/.env" ]; then
    value="$(grep -E "^${key}=" "$INFRA_DIR/.env" | tail -1 | cut -d= -f2- || true)"
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  fi
  printf '%s' "${value:-$fallback}"
}

MODE="$(env_get PG_MODE cluster)"
PG_USER="$(env_get DEV_POSTGRES_USER hanyue)"
PG_PASS="$(env_get DEV_POSTGRES_PASSWORD hanyue_dev_pwd)"
PG_DB="$(env_get DEV_POSTGRES_DB hanyue)"
HOST=127.0.0.1

case "$MODE" in
  cluster)
    PORT="$(env_get DEV_PG_PORT 5433)"
    DATA_DIR="$(env_get PG_DATA_DIR "$INFRA_DIR/.pgdev/data")"
    ;;
  service)
    PORT="$(env_get DEV_PG_PORT 5432)"
    DATA_DIR=""
    ;;
  *) die "PG_MODE 只能是 cluster 或 service（当前：$MODE）" ;;
esac
LOG_FILE="$(dirname "${DATA_DIR:-$INFRA_DIR/.pgdev/x}")/server.log"

DATABASE_URL="postgresql+psycopg://${PG_USER}:${PG_PASS}@${HOST}:${PORT}/${PG_DB}"

# ── 定位 PostgreSQL 可执行文件 ──────────────────────────────────
find_pg_bin() {
  local candidate base
  if [ -n "${PG_BIN:-}" ]; then
    candidate="${PG_BIN%/bin}"
    [ -x "$candidate/bin/psql" ] || [ -x "$candidate/bin/psql.exe" ] || die "PG_BIN 里找不到 psql：$PG_BIN"
    printf '%s' "$candidate/bin"; return 0
  fi
  candidate="$(command -v psql 2>/dev/null || true)"
  if [ -n "$candidate" ]; then dirname "$candidate"; return 0; fi
  for base in "/c/Program Files/PostgreSQL" "/c/Program Files (x86)/PostgreSQL"; do
    [ -d "$base" ] || continue
    candidate="$(ls -1d "$base"/*/bin 2>/dev/null | sort -V | tail -1 || true)"
    [ -n "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  done
  for base in /opt/homebrew/opt/postgresql*/bin /usr/local/opt/postgresql*/bin /usr/lib/postgresql/*/bin; do
    [ -d "$base" ] && { printf '%s' "$base"; return 0; }
  done
  return 1
}

exe() {
  local name="$1" cand
  for cand in "$PGBIN/$name" "$PGBIN/$name.exe"; do
    [ -x "$cand" ] && { printf '%s' "$cand"; return 0; }
  done
  die "找不到 $PGBIN/$name —— 请安装 PostgreSQL 客户端/服务端，或用 PG_BIN 指定 bin 目录"
}

need_bin() {
  PGBIN="$(find_pg_bin)" || die "找不到 PostgreSQL（psql）。安装方式见 docs/setup.md「方式 A0」，
macOS: brew install postgresql@16 / Ubuntu: sudo apt install postgresql
Windows: 官方安装包或 winget install PostgreSQL.PostgreSQL.17"
  PSQL="$(exe psql)"
  PG_ISREADY="$(exe pg_isready)"
  if [ "$MODE" = cluster ]; then
    PG_CTL="$(exe pg_ctl)"
    INITDB="$(exe initdb)"
    CREATEDB="$(exe createdb)"
  fi
  info "PostgreSQL 客户端：$PGBIN"
}

# ── 集群模式（项目自带实例）────────────────────────────────────
cluster_initialized() { [ -n "$DATA_DIR" ] && [ -f "$DATA_DIR/PG_VERSION" ]; }
cluster_running() { "$PG_CTL" -D "$DATA_DIR" status >/dev/null 2>&1; }
server_ready() { PGPASSWORD="$PG_PASS" "$PG_ISREADY" -h "$HOST" -p "$PORT" -U "$PG_USER" -d postgres >/dev/null 2>&1; }

ensure_conf() {
  # 幂等：initdb 只写默认配置，端口/监听地址由这里补；用注释当标记，重复执行不追加。
  # 标记与 dev-pg.ps1 共用（纯 ASCII），两个脚本轮流用也不会重复追加。
  grep -q 'hanyue dev cluster\|hanyue 本地开发实例' "$DATA_DIR/postgresql.conf" 2>/dev/null && return 0
  cat >> "$DATA_DIR/postgresql.conf" <<EOF

# ---- hanyue dev cluster (added by infra/scripts/dev-pg.sh; run reset to regenerate) ----
port = $PORT
listen_addresses = '127.0.0.1'
timezone = 'Asia/Shanghai'
log_timezone = 'Asia/Shanghai'
log_min_duration_statement = 500
EOF
}

init_cluster() {
  if cluster_initialized; then
    # 上次 initdb 成功但中途被打断时，配置块可能没写进去——这里补齐
    ensure_conf
    return 0
  fi
  info "首次初始化集群：$DATA_DIR（端口 $PORT，仅监听 127.0.0.1）"
  mkdir -p "$DATA_DIR"
  # 密码文件放在集群旁边而不是 mktemp：部分环境会拦系统临时目录的写/删
  local pwfile; pwfile="$(dirname "$DATA_DIR")/.initdb-pw"
  ( umask 077; printf '%s' "$PG_PASS" > "$pwfile" )
  # 与容器版一致：UTF8 + C 排序（--no-locale），避免本地/线上排序规则不同
  "$INITDB" -D "$DATA_DIR" -U "$PG_USER" --pwfile="$pwfile" \
    --encoding=UTF8 --no-locale --auth-host=scram-sha-256 --auth-local=trust >/dev/null \
    || die "initdb 失败（目录不可写或权限不足）：$DATA_DIR"
  rm -f "$pwfile" || true
  ensure_conf
  ok "集群已创建"
}

start_cluster() {
  init_cluster
  if cluster_running; then
    if server_ready; then
      ok "集群已在运行（端口 $PORT）"
      return 0
    fi
    # 上次被强杀会留下 postmaster.pid，pg_ctl status 仍报「在运行」，但端口没响应
    warn "发现残留的 postmaster.pid（上次异常退出），继续尝试启动"
  fi
  if server_ready; then
    warn "端口 $PORT 已被别的 PostgreSQL 占用（不是本脚本的集群）。"
    die "换端口：DEV_PG_PORT=5434 ./scripts/dev-pg.sh up；或先停掉占用者"
  fi
  # 端口没响应 = 确认是残留 pid 文件，清掉再起，免得 pg_ctl 反复提示
  if [ -f "$DATA_DIR/postmaster.pid" ]; then
    rm -f "$DATA_DIR/postmaster.pid"
  fi
  info "启动集群"
  # </dev/null：不接管调用方 stdin；>/dev/null：pg_ctl 的提示不落到管道上，
  # 否则它派生的 postgres 会一直占着管道，`脚本 | head` 这类用法会卡住。
  "$PG_CTL" -D "$DATA_DIR" -l "$LOG_FILE" -w -t 60 start </dev/null >/dev/null 2>&1 || {
    warn "启动失败，最近日志（$LOG_FILE）："
    tail -30 "$LOG_FILE" >&2 || true
    if grep -qE "could not create any TCP/IP sockets|could not bind" "$LOG_FILE" 2>/dev/null; then
      warn "postgres 无法监听端口（Permission denied）。常见原因：企业安全软件/防火墙禁止 postgres.exe 监听，
或该端口落在 Windows 保留区间（netsh int ipv4 show excludedportrange protocol=tcp）。
两条出路：
  a) 换端口：DEV_PG_PORT=5434 ./scripts/dev-pg.sh up
  b) 直接用系统已装的 PG 服务（无需监听新端口）：
     PG_MODE=service ./scripts/dev-pg.sh up
     （会问 postgres 超级用户密码，或在环境里给 PGSUPERUSER_PASSWORD）"
    fi
    die "pg_ctl start 失败"
  }
  ok "已启动（数据目录 $DATA_DIR）"
}

# ── 库/角色/初始化 SQL ────────────────────────────────────────
psql_as() {  # psql_as <user> <password> <db> [args...]
  local user="$1" pass="$2" db="$3"; shift 3
  PGPASSWORD="$pass" "$PSQL" -h "$HOST" -p "$PORT" -U "$user" -d "$db" -v ON_ERROR_STOP=1 "$@"
}

ensure_database() {
  local exists
  exists="$(psql_as "$PG_USER" "$PG_PASS" postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$PG_DB'" 2>/dev/null || true)"
  if [ "$exists" = "1" ]; then
    ok "库 $PG_DB 已存在"
  else
    info "创建库 $PG_DB（owner=$PG_USER）"
    psql_as "$PG_USER" "$PG_PASS" postgres -c "CREATE DATABASE \"$PG_DB\" OWNER \"$PG_USER\"" >/dev/null
    ok "库 $PG_DB 已创建"
    run_init_sql
  fi
}

run_init_sql() {
  local sql="$INFRA_DIR/postgres/init/01-init.sql"
  [ -f "$sql" ] || die "找不到 $sql"
  info "执行 postgres/init/01-init.sql（时区 / 语句超时 / pg_trgm）"
  psql_as "$PG_USER" "$PG_PASS" "$PG_DB" -q -f "$sql" >/dev/null
  ok "库级设置已就绪"
}

# ── service 模式（复用系统服务）────────────────────────────────
super_password() {
  local pass="${PGSUPERUSER_PASSWORD:-}"
  if [ -z "$pass" ]; then
    # 提示必须写 stderr：函数结果会被 $(...) 捕获，写 stdout 会把提示混进密码
    printf 'PostgreSQL 超级用户 %s 的密码（也可用 PGSUPERUSER_PASSWORD 传入）：' "$PG_SUPERUSER" >&2
    read -rs pass; printf '\n' >&2
  fi
  [ -n "$pass" ] || die "超级用户密码不能为空"
  printf '%s' "$pass"
}

ensure_role_and_db_service() {
  PG_SUPERUSER="$(env_get PGSUPERUSER postgres)"
  local pass; pass="$(super_password)"
  local sql="$INFRA_DIR/postgres/create-role-and-db.sql"
  [ -f "$sql" ] || die "找不到 $sql"
  info "在系统实例（$HOST:$PORT）上确保角色 $PG_USER / 库 $PG_DB 存在"
  PGPASSWORD="$pass" "$PSQL" -h "$HOST" -p "$PORT" -U "$PG_SUPERUSER" -d postgres \
    -v ON_ERROR_STOP=1 \
    -v "app_user=$PG_USER" -v "app_db=$PG_DB" -v "app_password=$PG_PASS" \
    -q -f "$sql" >/dev/null || die "建角色/库失败：检查超级用户密码与端口 $PORT"
  run_init_sql   # 幂等，重复执行只更新库级设置
}

# ── 子命令 ───────────────────────────────────────────────────
need_python() {
  local candidate
  for candidate in "$API_DIR/.venv/bin/python" "$API_DIR/.venv/Scripts/python.exe"; do
    [ -x "$candidate" ] && { PY="$candidate"; break; }
  done
  [ -n "${PY:-}" ] || die "找不到 $API_DIR/.venv —— 先建虚拟环境并装依赖（见 docs/setup.md 方式 A）"
  "$PY" -c "import psycopg" >/dev/null 2>&1 || die "当前 venv 缺 PG 驱动：$PY -m pip install -r requirements-prod.txt"
}

print_urls() {
  printf '\n%s写进 apps/api/.env（或直接用 ./scripts/dev-pg.sh write-env）：%s\n' "$BOLD" "$RESET"
  printf '  DATABASE_URL=%s\n' "$DATABASE_URL"
  printf '%sRedis 可选：不配 REDIS_URL 时任务由 API 进程内线程池执行，无需启动 Worker。%s\n' "$DIM" "$RESET"
  printf '%spsql：%s./scripts/dev-pg.sh psql\n\n' "$DIM" "$RESET"
}

cmd_up() {
  need_bin
  if [ "$MODE" = cluster ]; then
    start_cluster
  else
    server_ready || die "系统 PostgreSQL 没在 $HOST:$PORT 就绪。
Windows：服务管理器启动 postgresql-x64-17（或管理员 net start postgresql-x64-17）
macOS：brew services start postgresql@16    Linux：sudo systemctl start postgresql"
    ok "系统实例已就绪（$HOST:$PORT）"
    ensure_role_and_db_service
  fi
  ensure_database
  if [ "${1:-}" = "--write-env" ]; then cmd_write_env; fi
  print_urls
}

cmd_down() {
  need_bin
  if [ "$MODE" = cluster ]; then
    cluster_running || { ok "本来就没在运行"; return 0; }
    "$PG_CTL" -D "$DATA_DIR" -m fast -w stop >/dev/null
    ok "已停止（数据保留在 $DATA_DIR；下次 up 直接接着用）"
  else
    warn "service 模式不动系统服务。要停请用系统方式：
Windows：服务管理器 → postgresql-x64-17 → 停止    macOS：brew services stop postgresql@16"
  fi
}

cmd_status() {
  need_bin
  printf '%s模式%s %s    %s端口%s %s\n' "$BOLD" "$RESET" "$MODE" "$BOLD" "$RESET" "$PORT"
  if [ "$MODE" = cluster ]; then
    printf '%s数据目录%s %s\n' "$BOLD" "$RESET" "${DATA_DIR:-<未初始化>}"
    if cluster_running; then printf '%s进程%s 运行中\n' "$BOLD" "$RESET"; else printf '%s进程%s 未运行\n' "$BOLD" "$RESET"; fi
  fi
  # pg_isready 只证明「有服务在听」，不代表应用账号能连：这里真连一次
  if psql_as "$PG_USER" "$PG_PASS" "$PG_DB" -tAc "SELECT 1" >/dev/null 2>&1; then
    local tables
    tables="$(psql_as "$PG_USER" "$PG_PASS" "$PG_DB" -tAc \
      "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null || echo '?')"
    printf '%s连接%s 正常（%s/%s，public 下 %s 张表）\n' "$BOLD" "$RESET" "$PG_USER" "$PG_DB" "$tables"
    if [ "$tables" = "0" ]; then
      warn "库里还没有表：./scripts/dev-pg.sh bootstrap 建表并创建首个账号"
    fi
  elif "$PG_ISREADY" -h "$HOST" -p "$PORT" >/dev/null 2>&1; then
    printf '%s连接%s 服务在 %s:%s 上，但应用账号 %s 连不上\n' "$BOLD" "$RESET" "$HOST" "$PORT" "$PG_USER"
    warn "多半是角色/库还没建，或密码不对：./scripts/dev-pg.sh up
service 模式下若端口不是默认值，加 DEV_PG_PORT=<实际端口>"
  else
    printf '%s连接%s 端口 %s 上没有 PostgreSQL\n' "$BOLD" "$RESET" "$PORT"
    warn "cluster 模式先 ./scripts/dev-pg.sh up；service 模式先启动系统服务（postgresql-x64-17）"
  fi
  printf '\nDATABASE_URL=%s\n' "$DATABASE_URL"
}

cmd_psql() {
  need_bin
  server_ready || die "连接不上 $HOST:$PORT，先 ./scripts/dev-pg.sh up"
  info "psql $HOST:$PORT/$PG_DB（\\q 退出）"
  PGPASSWORD="$PG_PASS" "$PSQL" -h "$HOST" -p "$PORT" -U "$PG_USER" -d "$PG_DB" "$@"
}

cmd_reset() {
  [ "$MODE" = cluster ] || die "service 模式的库不属于本脚本，拒绝删除。要清空请手工 dropdb/drop database"
  need_bin
  cluster_initialized || { ok "集群还没创建，无需删除"; return 0; }
  warn "这会永久删除 $DATA_DIR （库 $PG_DB 的全部数据）。其他 PG 实例不受影响。"
  printf '确认请输入 yes：'
  read -r answer
  [ "$answer" = "yes" ] || die "已取消"
  cluster_running && "$PG_CTL" -D "$DATA_DIR" -m fast -w stop >/dev/null
  # 只删确认过是 PG 数据目录的路径
  [ -f "$DATA_DIR/PG_VERSION" ] || die "$DATA_DIR 不像 PG 数据目录，拒绝删除"
  rm -rf "$DATA_DIR"
  ok "已删除；下次 up 会重新 initdb 并重跑 01-init.sql"
}

cmd_url() { printf 'DATABASE_URL=%s\n' "$DATABASE_URL"; }

cmd_init_sql() {
  need_bin
  server_ready || die "连接不上 $HOST:$PORT，先 ./scripts/dev-pg.sh up"
  run_init_sql
}

cmd_init_schema() {
  need_bin
  server_ready || die "连接不上 $HOST:$PORT，先 ./scripts/dev-pg.sh up"
  local sql="$INFRA_DIR/postgres/schema.sql"
  [ -f "$sql" ] || die "找不到 $sql —— 先在 apps/api 下运行：
  .venv/Scripts/python.exe scripts/export_pg_schema.py -o ../../infra/postgres/schema.sql"
  info "执行 postgres/schema.sql（从 SQLAlchemy 模型生成的完整建表脚本，幂等）"
  psql_as "$PG_USER" "$PG_PASS" "$PG_DB" -q -f "$sql"
  ok "建表完成（63 张表 + 索引；可用 ./scripts/dev-pg.sh status 核对）"
  warn "schema.sql 只建表结构，不播种数据（角色/菜单/权限）。创建首个账号请用 bootstrap，或启动 API 由 lifespan 播种。"
}

cmd_bootstrap() {
  cmd_up
  need_python
  info "建表 + 补列（等价 API 启动时的 init_db）"
  ( cd "$API_DIR" && DATABASE_URL="$DATABASE_URL" "$PY" scripts/create_user.py list >/dev/null ) \
    || die "建表失败：上面是 apps/api/scripts/create_user.py 的报错"
  local email="${BOOTSTRAP_EMAIL:-}"
  if [ -z "$email" ]; then
    printf '首个账号邮箱：'
    read -r email
  fi
  [ -n "$email" ] || die "邮箱不能为空"
  info "创建账号 $email（密码优先读 HANYUE_NEW_PASSWORD，否则交互输入）"
  ( cd "$API_DIR" && DATABASE_URL="$DATABASE_URL" "$PY" scripts/create_user.py create --email "$email" )
  ok "完成：cd apps/api && .venv 启动 uvicorn 即可登录"
  print_urls
}

cmd_import_sqlite() {
  local source="${1:-$API_DIR/hanyue.db}"
  [ -f "$source" ] || die "找不到 SQLite 文件：$source"
  cmd_up >/dev/null
  need_python
  warn "把 $source 导入 $PG_DB（目标表已有数据时默认跳过，详见迁移脚本 --help）"
  DATABASE_URL="$DATABASE_URL" "$PY" "$INFRA_DIR/scripts/migrate_sqlite_to_pg.py" --sqlite "$source"
}

cmd_write_env() {
  need_python
  local file="$API_DIR/.env"
  [ -f "$file" ] || cp "$API_DIR/.env.example" "$file"
  "$PY" - "$file" "$DATABASE_URL" <<'PY'
import pathlib, sys

path, url = pathlib.Path(sys.argv[1]), sys.argv[2]
# 保留原文件的换行风格：Windows 上 .env 常是 CRLF，按文本读写会整片改成 LF
raw = path.read_bytes().decode("utf-8")
nl = "\r\n" if "\r\n" in raw else "\n"
lines = raw.split(nl)
out, found = [], False
for line in lines:
    if line.startswith("DATABASE_URL="):
        out.append(f"DATABASE_URL={url}")
        found = True
    else:
        out.append(line)
if not found:
    out.insert(0, f"DATABASE_URL={url}")
path.write_bytes(nl.join(out).encode("utf-8"))
print(f"已更新 {path}: DATABASE_URL={url}")
PY
  ok "改完记得重启 uvicorn（配置只在进程启动时载入）"
}

usage() {
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 && !/^#/ { exit }' "${BASH_SOURCE[0]}"
}

main() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    up)            cmd_up "$@" ;;
    down)          cmd_down ;;
    status|ps)     cmd_status ;;
    psql)          cmd_psql "$@" ;;
    reset)         cmd_reset ;;
    url)           cmd_url ;;
    init-sql)      cmd_init_sql ;;
    init-schema)   cmd_init_schema ;;
    bootstrap)     cmd_bootstrap ;;
    import-sqlite) cmd_import_sqlite "$@" ;;
    write-env)     cmd_write_env ;;
    ""|-h|--help|help) usage ;;
    *) die "未知子命令：$cmd（用 help 查看）" ;;
  esac
}

main "$@"
