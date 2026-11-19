#!/usr/bin/env bash
# 本地开发数据库（PostgreSQL + Redis）管理。
#
#   ./scripts/dev-db.sh up            起库并等待就绪，打印 DATABASE_URL
#   ./scripts/dev-db.sh down          停止（保留数据）
#   ./scripts/dev-db.sh reset         删卷重建（会二次确认，数据清空）
#   ./scripts/dev-db.sh psql          进入 psql
#   ./scripts/dev-db.sh status        容器与健康状态
#   ./scripts/dev-db.sh url           只打印连接串（用于粘贴到 apps/api/.env）
#   ./scripts/dev-db.sh bootstrap     起库 + 建表 + 创建首个账号
#   ./scripts/dev-db.sh import-sqlite [文件]
#                                     把旧 SQLite 数据搬进来（默认 apps/api/hanyue.db）
#
# 与生产的隔离：独立 compose 文件（hanyue-dev 项目名）、独立卷、端口 5433/6380，
# 不会碰 infra_pgdata，也不会占用本机默认的 5432/6379。

set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$INFRA_DIR/.." && pwd)"
DEV_FILE="$INFRA_DIR/docker-compose.dev.yml"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'

info() { printf '%s[dev-db]%s %s\n' "$BLUE" "$RESET" "$*"; }
ok()   { printf '%s[dev-db]%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%s[dev-db]%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die()  { printf '%s[dev-db]%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

# shell 环境优先，其次 infra/.env，最后默认值——与 compose 的取值顺序一致
env_get() {
  local key="$1" fallback="$2" value
  # 注意：不能写成 `local key=$1 value=${!key}`，参数展开先于赋值，key 此时还是空
  value="${!key:-}"
  if [ -z "$value" ] && [ -f "$INFRA_DIR/.env" ]; then
    value="$(grep -E "^${key}=" "$INFRA_DIR/.env" | tail -1 | cut -d= -f2- || true)"
    value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  fi
  printf '%s' "${value:-$fallback}"
}

PG_USER="$(env_get DEV_POSTGRES_USER hanyue)"
PG_PASS="$(env_get DEV_POSTGRES_PASSWORD hanyue_dev_pwd)"
PG_DB="$(env_get DEV_POSTGRES_DB hanyue)"
PG_PORT="$(env_get DEV_PG_PORT 5433)"
REDIS_PORT="$(env_get DEV_REDIS_PORT 6380)"

DATABASE_URL="postgresql+psycopg://${PG_USER}:${PG_PASS}@127.0.0.1:${PG_PORT}/${PG_DB}"
REDIS_URL="redis://127.0.0.1:${REDIS_PORT}/0"

need_docker() {
  command -v docker >/dev/null 2>&1 || die "找不到 docker：请先安装 Docker Desktop 或 docker engine"
  docker info >/dev/null 2>&1 || die "docker 未运行（或当前用户无权限）：先启动 docker，或把用户加入 docker 组"
  docker compose version >/dev/null 2>&1 || die "docker compose v2 不可用：需要 'docker compose'（不是 docker-compose v1）"
  [ -f "$DEV_FILE" ] || die "找不到 $DEV_FILE"
}

compose() { docker compose -f "$DEV_FILE" "$@"; }

wait_ready() {
  info "等待 PostgreSQL 就绪（首次 initdb 约 5-10 秒）"
  local i=0
  while [ "$i" -lt 40 ]; do
    if compose exec -T db pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
      ok "PostgreSQL 已就绪：127.0.0.1:${PG_PORT}/${PG_DB}"
      return 0
    fi
    i=$((i + 1)); sleep 1
  done
  warn "40 秒内未就绪，下面是最近日志："
  compose logs --tail 30 db >&2 || true
  die "PostgreSQL 启动失败"
}

print_urls() {
  printf '\n%s把这两行写进 apps/api/.env：%s\n' "$BOLD" "$RESET"
  printf '  DATABASE_URL=%s\n' "$DATABASE_URL"
  printf '  REDIS_URL=%s\n' "$REDIS_URL"
  printf '\n%spsql：%s./scripts/dev-db.sh psql\n\n' "$DIM" "$RESET"
}

detect_python() {
  local candidate
  for candidate in \
    "$REPO_DIR/apps/api/.venv/bin/python" \
    "$REPO_DIR/apps/api/.venv/Scripts/python.exe"; do
    [ -x "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  done
  if command -v python3 >/dev/null 2>&1; then command -v python3; return 0; fi
  if command -v python >/dev/null 2>&1; then command -v python; return 0; fi
  return 1
}

need_python() {
  PY="$(detect_python)" || die "找不到 python：请在 apps/api 下创建 .venv，或安装 python3"
  if ! "$PY" -c "import psycopg" >/dev/null 2>&1; then
    die "当前 python 缺 psycopg 驱动。安装：
  $PY -m pip install 'psycopg[binary]>=3.2,<4'
（或直接 pip install -r apps/api/requirements-prod.txt）"
  fi
}

cmd_up() {
  need_docker
  # --wait 会等 db-init（一次性容器）跑完且退出码为 0：脚本有变更时它会
  # 自动重跑初始化 SQL，失败则这里直接报错，而不是静默留下半初始化的库
  compose up -d --wait
  print_urls
}

cmd_down() {
  need_docker
  compose down
  ok "已停止（数据卷保留；要清空用 reset）"
}

cmd_status() {
  need_docker
  compose ps
  printf '\n%s连接串%s\n  DATABASE_URL=%s\n  REDIS_URL=%s\n' "$DIM" "$RESET" "$DATABASE_URL" "$REDIS_URL"
}

cmd_url() {
  printf 'DATABASE_URL=%s\n' "$DATABASE_URL"
  printf 'REDIS_URL=%s\n' "$REDIS_URL"
}

cmd_psql() {
  need_docker
  compose exec db psql -U "$PG_USER" -d "$PG_DB"
}

cmd_reset() {
  need_docker
  warn "这会删除本地开发库 ${PG_DB} 的全部数据（生产卷 infra_pgdata 不受影响）。"
  printf '确认请输入 yes：'
  read -r answer
  [ "$answer" = "yes" ] || die "已取消"
  compose down --volumes --remove-orphans
  ok "已删除数据卷；下次 up 会重新执行 postgres/init/ 下的脚本（脚本变更平时由 db-init 自动重跑，无需 reset）"
}

cmd_bootstrap() {
  need_docker
  need_python
  compose up -d --wait

  info "建表（create_all + 补列迁移）"
  DATABASE_URL="$DATABASE_URL" "$PY" "$REPO_DIR/apps/api/scripts/create_user.py" list >/dev/null \
    || die "初始化失败：上面的报错来自 apps/api/scripts/create_user.py"

  local email="${BOOTSTRAP_EMAIL:-}"
  if [ -z "$email" ]; then
    printf '首个账号邮箱：'
    read -r email
  fi
  [ -n "$email" ] || die "邮箱不能为空"
  info "创建账号 $email（密码优先从 HANYUE_NEW_PASSWORD 或交互输入读取）"
  ( cd "$REPO_DIR/apps/api" && DATABASE_URL="$DATABASE_URL" "$PY" scripts/create_user.py create --email "$email" )

  print_urls
}

cmd_import_sqlite() {
  need_docker
  need_python
  local source="${1:-$REPO_DIR/apps/api/hanyue.db}"
  [ -f "$source" ] || die "找不到 SQLite 文件：$source"
  warn "把 $source 的数据导入本地开发库 ${PG_DB}（目标已有数据的表默认跳过）"
  compose up -d --wait
  DATABASE_URL="$DATABASE_URL" "$PY" "$REPO_DIR/infra/scripts/migrate_sqlite_to_pg.py" \
    --sqlite "$source"
}

usage() {
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 && !/^#/ { exit }' "${BASH_SOURCE[0]}"
}

main() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    up)            cmd_up ;;
    down)          cmd_down ;;
    status|ps)     cmd_status ;;
    url)           cmd_url ;;
    psql)          cmd_psql ;;
    reset)         cmd_reset ;;
    bootstrap)     cmd_bootstrap ;;
    import-sqlite) cmd_import_sqlite "$@" ;;
    ""|-h|--help|help) usage ;;
    *) die "未知子命令：$cmd（用 help 查看）" ;;
  esac
}

main "$@"
