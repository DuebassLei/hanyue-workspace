FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 国内构建：把 Debian 源换成国内镜像（$APT_MIRROR，如 mirrors.aliyun.com）。
# 只替换主机名，路径保留 —— bookworm 的 /etc/apt/sources.list 与 trixie 起改用
# 的 /etc/apt/sources.list.d/debian.sources 两种写法都能覆盖。
# 用 "//host" 作锚点，避免误伤注释里出现的裸域名。留空则完全不动，走官方源。
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then \
        for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do \
            [ -f "$f" ] || continue; \
            sed -i "s|//deb.debian.org|//$APT_MIRROR|g; s|//security.debian.org|//$APT_MIRROR|g" "$f"; \
        done; \
        echo "apt 源已切到 $APT_MIRROR，实际生效的源："; \
        grep -hE '^(deb |URIs:)' /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null | head -4 || true; \
    fi

# libpq5：psycopg 运行期系统库兜底
# tzdata：应用用 zoneinfo 解析用户时区（Asia/Shanghai 等），slim 镜像默认不含时区库
# ffmpeg/ffprobe：图文成片的硬依赖（video_render.probe_duration / ffmpeg_bin），
#   缺失时能力探测会返回 ffmpeg_missing，视频合成直接不可用
# fonts-wqy-microhei + fontconfig：ASS 硬字幕写死 Microsoft YaHei，需别名到开源中文字体
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 tzdata ffmpeg fonts-wqy-microhei fontconfig \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY docker/fonts-cjk-alias.conf /etc/fonts/conf.d/99-cjk-alias.conf
RUN fc-cache -f >/dev/null

# 国内构建可传 --build-arg PIP_INDEX_URL=... 加速。
# http 协议的源（如腾讯云内网 http://mirrors.tencentyun.com/pypi/simple）
# 必须再传 PIP_TRUSTED_HOST=主机名，否则 pip 以不安全为由直接拒绝。
ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir --retries 5 --timeout 60 \
        ${PIP_INDEX_URL:+--index-url $PIP_INDEX_URL} \
        ${PIP_TRUSTED_HOST:+--trusted-host $PIP_TRUSTED_HOST} \
        -r requirements.txt -r requirements-prod.txt

COPY app ./app

# 以非 root 运行；命名卷首次挂载会继承镜像内 /data/storage 的属主
RUN useradd --system --uid 10001 app \
    && mkdir -p /data/storage \
    && chown -R app:app /data
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
