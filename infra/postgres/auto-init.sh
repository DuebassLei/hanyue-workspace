#!/bin/sh
# 数据库初始化执行器：每次 docker compose up 时，按「内容哈希」自动重跑有变更的初始化 SQL。
#
# 背景：官方 postgres 镜像的 /docker-entrypoint-initdb.d 只在数据卷为空时执行一次，
#       之后改脚本再 up 不会生效。这里把待执行脚本拼起来算 sha256，存进库内
#       _hanyue_init_state 表；每次启动对比，哈希变了就按序重跑，没变直接退出。
#
# 前提：所有脚本必须幂等（IF NOT EXISTS / DO $$ 探测），重复执行不改变结果——
#       infra/postgres 下的 01-init.sql 与 schema.sql 均满足。
# 失败：任一脚本报错（ON_ERROR_STOP）立即退出非 0，哈希不落库，下次 up 自动重试。
#       api/worker 依赖本服务 completed_successfully，初始化失败时应用不会带病启动。
# 查看：docker compose logs db-init
#
# 执行顺序固定：/scripts/init/*.sql（字典序）→ /scripts/schema.sql。
# 顺序影响哈希拼接结果，也保证库级设置（时区/超时）先于建表生效。

set -eu

export PGHOST="${PGHOST:-db}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${POSTGRES_USER:?缺少 POSTGRES_USER}"
export PGPASSWORD="${POSTGRES_PASSWORD:?缺少 POSTGRES_PASSWORD}"
export PGDATABASE="${POSTGRES_DB:?缺少 POSTGRES_DB}"

psql_run() { psql -v ON_ERROR_STOP=1 "$@"; }

# ── 1. 等数据库可连接（正常由 depends_on: service_healthy 保证，这里兜底）──
i=0
while ! psql_run -tAc 'SELECT 1' >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    echo "[db-init] 60 秒内连不上 ${PGHOST}:${PGPORT}/${PGDATABASE}，退出" >&2
    exit 1
  fi
  sleep 1
done
echo "[db-init] 数据库已就绪：${PGHOST}:${PGPORT}/${PGDATABASE}"

# ── 2. 收集待执行脚本（glob 展开即字典序，顺序稳定哈希才稳定）──
files=""
for f in /scripts/init/*.sql /scripts/schema.sql; do
  [ -f "$f" ] || continue
  files="$files $f"
done
if [ -z "$files" ]; then
  echo "[db-init] /scripts 下没有找到任何 .sql，无事可做"
  exit 0
fi

# ── 3. 计算当前哈希，与库内记录比对 ──
# shellcheck disable=SC2086
current="$(cat $files | sha256sum | cut -d' ' -f1)"
stored="$(psql_run -tAc "SELECT hash FROM _hanyue_init_state WHERE id = 'combined'" 2>/dev/null || true)"
# psql 输出可能带空白，统一裁掉再比
stored="$(printf '%s' "$stored" | tr -d '[:space:]')"

if [ "$current" = "$stored" ]; then
  echo "[db-init] 脚本未变更（hash=$current），跳过初始化"
  exit 0
fi

if [ -n "$stored" ]; then
  echo "[db-init] 检测到脚本变更：${stored:-（无记录）} → ${current}，开始重跑"
else
  echo "[db-init] 首次执行，开始初始化（hash=$current）"
fi

# ── 4. 按序执行；任一失败即退出（哈希不落库，下次 up 自动重试）──
# shellcheck disable=SC2086
for f in $files; do
  echo "[db-init] 执行 $f"
  if ! psql_run -f "$f"; then
    echo "[db-init] $f 执行失败（上行是 psql 报错）。修复脚本后重新 up 即可重试；" \
         "注意已执行的部分不会回滚，脚本必须保持幂等" >&2
    exit 1
  fi
done

# ── 5. 记录新哈希 ──
psql_run -v h="$current" <<'SQL'
CREATE TABLE IF NOT EXISTS _hanyue_init_state (
  id         TEXT PRIMARY KEY,
  hash       TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO _hanyue_init_state (id, hash) VALUES ('combined', :'h')
ON CONFLICT (id) DO UPDATE SET hash = EXCLUDED.hash, applied_at = now();
SQL

echo "[db-init] 初始化完成，哈希已记录（下次启动内容未变则跳过）"
