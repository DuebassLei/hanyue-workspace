#!/usr/bin/env bash
# 给 Docker daemon 配 registry-mirrors：让 docker.io 的镜像（python/node/nginx/
# postgres/redis 全套基础镜像）从国内代理拉取，而不是直连 registry-1.docker.io。
#
# 用法：
#   ./scripts/deploy.sh mirror                    # 探测 + 写入 + 重启 docker
#   ./scripts/set-docker-mirror.sh                # 同上（直接调用）
#   DRY_RUN=1 ./scripts/set-docker-mirror.sh      # 只探测并打印将要写入的内容
#   MIRRORS="https://a.example https://b.example" ./scripts/set-docker-mirror.sh
#
# 说明：
# - 只改 /etc/docker/daemon.json 里的 registry-mirrors 一个键，其余键原样保留（先备份）。
# - 这只影响「拉取」；已构建镜像与容器不受影响。重启 docker 会重启容器，但
#   restart: unless-stopped 的服务会自动回来，数据都在卷里，无损。
# - Docker Desktop（Windows/macOS）不读 /etc/docker/daemon.json，脚本会识别并给出
#   GUI 配置步骤，不会去改一个没用处的文件。

set -euo pipefail

DAEMON_JSON="${DAEMON_JSON:-/etc/docker/daemon.json}"
DRY_RUN="${DRY_RUN:-0}"

# 候选代理（按实测优先级）。判定标准：GET /v2/ 返回 401（需要 token，说明代理在
# 正常转发鉴权）或 200 即视为可用；000/超时/5xx 淘汰。
# 最多取前 3 个：registry-mirrors 是 fallback 链，塞太多会在全挂时逐个试，反而更慢。
DEFAULT_MIRRORS="https://docker.m.daocloud.io https://docker.1ms.run https://dockerproxy.net https://docker.1panel.live"
# 腾讯云 CVM 内网加速器：不占公网带宽、无限流，但 VPC 外不可达（探测会自动淘汰）。
# 通过 metadata 服务识别 CVM，只有在内网机器上才把它放进候选。
TENCENT_INTERNAL_MIRROR="https://mirror.ccs.tencentyun.com"
MAX_MIRRORS=3

# 合并 daemon.json 的 python 片段。用 -c 传而不是 heredoc：heredoc 写在 $( ) 里
# 会让 bash 报 matching `)' 语法错误；这里刻意不用临时文件，省得清理。
PYCODE='import json, os, sys
path, mirrors = sys.argv[1], sys.argv[2].split()
data = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        sys.stderr.write("daemon.json is not valid JSON: %s\n" % exc)
        sys.exit(2)
data["registry-mirrors"] = mirrors
print(json.dumps(data, indent=2, ensure_ascii=False))'

info() { printf '\033[36m[mirror]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[mirror]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[mirror]\033[0m %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"; }

# ── 1. 探测 ───────────────────────────────────────────────────
# HTTP 码放 PROBE_CODE，可用性用返回码表达，调用方不解析字符串
PROBE_CODE=000
probe() {
  PROBE_CODE="$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$1/v2/" 2>/dev/null || echo 000)"
  case "$PROBE_CODE" in
    200|401) return 0 ;;
    *) return 1 ;;
  esac
}

pick_mirrors() {
  local candidates="$1" url ok="" n=0
  info "探测可用镜像代理（每个最多 8 秒）"
  for url in $candidates; do
    if probe "$url"; then
      ok="$ok $url"
      n=$((n + 1))
      printf '  \033[32m✓\033[0m %s\n' "$url"
      [ "$n" -ge "$MAX_MIRRORS" ] && break
    else
      printf '  \033[31m✗\033[0m %s（HTTP %s 或不可达）\n' "$url" "$PROBE_CODE"
    fi
  done
  [ -n "$ok" ] || die '候选代理全部不可用：用 MIRRORS="..." 指定你信任的加速地址'
  pick_mirrors_result="$(printf '%s' "$ok" | sed -e 's/^ *//' -e 's/ *$//')"
}

# ── 2. 环境判定 ───────────────────────────────────────────────
check_docker() {
  need_cmd docker
  docker info >/dev/null 2>&1 || die "docker daemon 连不上：先启动它（sudo service docker start）"
}

is_docker_desktop() {
  local os_name
  os_name="$(docker info --format '{{.OperatingSystem}}' 2>/dev/null || true)"
  case "$os_name" in
    *"Docker Desktop"*) return 0 ;;
    *) return 1 ;;
  esac
}

# 腾讯云 CVM：metadata 服务只在 VPC 内可达。
# 必须校验返回体是 ins- 开头的实例 ID——有些网络环境（代理/劫持）会把 DNS 失败
# 也包装成 HTTP 200，只看 curl 退出码会误判。
on_tencent_cvm() {
  local id
  id="$(curl -s -m 2 "http://metadata.tencentyun.com/latest/meta-data/instance-id" 2>/dev/null || true)"
  case "$id" in
    ins-*) return 0 ;;
    *) return 1 ;;
  esac
}

# ── 3. 组装配置 ───────────────────────────────────────────────
write_daemon_json() {
  local mirrors="$1" merged list="" m
  if command -v python3 >/dev/null 2>&1; then
    merged="$(python3 -c "$PYCODE" "$DAEMON_JSON" "$mirrors")" \
      || die "合并 daemon.json 失败（原文件未改动）：请检查 $DAEMON_JSON 是否为合法 JSON"
  else
    if [ -s "$DAEMON_JSON" ]; then
      die "已有 $DAEMON_JSON 但没有 python3，无法安全合并，请手工编辑"
    fi
    # 无 python3 且文件不存在：拼一个最小 JSON（镜像地址里没有需要转义的字符）
    for m in $mirrors; do list="$list\"$m\", "; done
    list="${list%, }"
    merged="$(printf '{\n  "registry-mirrors": [%s]\n}' "$list")"
  fi

  if [ "$DRY_RUN" = "1" ]; then
    info "DRY_RUN：将写入 $DAEMON_JSON"
    printf '%s\n' "$merged"
    return 0
  fi

  if [ -f "$DAEMON_JSON" ]; then
    local backup="$DAEMON_JSON.bak-$(date +%Y%m%d-%H%M%S)"
    sudo cp -a "$DAEMON_JSON" "$backup"
    info "已备份原配置：$backup"
  fi
  sudo mkdir -p "$(dirname "$DAEMON_JSON")"
  printf '%s\n' "$merged" | sudo tee "$DAEMON_JSON" >/dev/null
  info "已写入 $DAEMON_JSON"
}

restart_docker() {
  if [ "$DRY_RUN" = "1" ]; then
    info "DRY_RUN：跳过 docker 重启"
    return 0
  fi
  info "重启 docker 使其生效（容器会自动回来，数据卷不受影响）"
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files docker.service >/dev/null 2>&1; then
    sudo systemctl restart docker
  else
    sudo service docker restart
  fi
  local i=0
  while [ "$i" -lt 30 ]; do
    if docker info >/dev/null 2>&1; then break; fi
    i=$((i + 1))
    sleep 1
  done
  docker info >/dev/null 2>&1 || die "docker 重启后连不上，查看：sudo journalctl -u docker --tail=50"
}

report() {
  local actual
  actual="$(docker info --format '{{json .RegistryConfig.Mirrors}}' 2>/dev/null || echo 未知)"
  info "生效的 registry-mirrors：$actual"
  printf '\n验证（应明显快于直连）：\n  docker pull postgres:16-alpine\n\n'
  printf '构建期仍慢则检查 infra/.env 的 PIP_INDEX_URL / APT_MIRROR / APK_MIRROR / NPM_REGISTRY，\n'
  printf '改完重跑：./scripts/deploy.sh up\n'
}

main() {
  local candidates="${MIRRORS:-$DEFAULT_MIRRORS}"
  need_cmd curl
  if on_tencent_cvm; then
    info "检测到腾讯云 CVM：优先用内网加速 $TENCENT_INTERNAL_MIRROR"
    candidates="$TENCENT_INTERNAL_MIRROR $candidates"
  fi
  if [ "$DRY_RUN" = "0" ]; then
    check_docker
    if is_docker_desktop; then
      warn "检测到 Docker Desktop：它不读 /etc/docker/daemon.json，改那里不生效。"
      cat <<'EOF'

请在 GUI 里配置（一次性）：
  Docker Desktop → Settings → Docker Engine，在 JSON 中加上：
    "registry-mirrors": ["https://docker.m.daocloud.io", "https://docker.1ms.run"]
  然后点 Apply & Restart。
EOF
      exit 3
    fi
  fi
  pick_mirrors "$candidates"
  info "选用：$pick_mirrors_result"
  write_daemon_json "$pick_mirrors_result"
  restart_docker
  if [ "$DRY_RUN" != "1" ]; then report; fi
}

main "$@"
