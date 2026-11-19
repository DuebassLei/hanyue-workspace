FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 国内构建：把 Debian 源换成国内镜像（$APT_MIRROR，如 mirrors.aliyun.com）。
# 只替换主机名、保留路径，sources.list 与 debian.sources 两种写法都覆盖；
# 用 "//host" 作锚点避免误伤注释。留空则完全不动。
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then \
        for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do \
            [ -f "$f" ] || continue; \
            sed -i "s|//deb.debian.org|//$APT_MIRROR|g; s|//security.debian.org|//$APT_MIRROR|g" "$f"; \
        done; \
        echo "apt 源已切到 $APT_MIRROR，实际生效的源："; \
        grep -hE '^(deb |URIs:)' /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null | head -4 || true; \
    fi

# tzdata：Worker 侧同样要解析任务时区（scheduled_tasks 用 zoneinfo）
# libpq5：psycopg 运行期系统库兜底
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 国内构建可传 --build-arg PIP_INDEX_URL=... 加速。
# http 协议的源（如腾讯云内网 http://mirrors.tencentyun.com/pypi/simple）
# 必须再传 PIP_TRUSTED_HOST=主机名，否则 pip 以不安全为由直接拒绝。
ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

# 构建上下文为仓库根目录（infra/docker-compose.yml 中的 context: ..）
COPY apps/api/requirements.txt apps/api/requirements-prod.txt ./
RUN pip install --no-cache-dir --retries 5 --timeout 60 \
        ${PIP_INDEX_URL:+--index-url $PIP_INDEX_URL} \
        ${PIP_TRUSTED_HOST:+--trusted-host $PIP_TRUSTED_HOST} \
        -r requirements.txt -r requirements-prod.txt

# Worker 与 API 共用同一套领域模型与服务，保证两种执行后端语义一致
COPY apps/api/app ./app
COPY apps/worker/worker.py ./worker.py

RUN useradd --system --uid 10001 app \
    && mkdir -p /data/storage \
    && chown -R app:app /data
USER app

CMD ["python", "worker.py"]
