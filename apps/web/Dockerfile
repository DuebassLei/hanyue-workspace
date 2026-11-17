# 前端镜像：多阶段构建（Node 编译 → nginx 托管静态文件）
# 构建上下文固定为 apps/web（infra/docker-compose.yml 中的 context: ../apps/web）
# 运行期只保留 dist 产物，镜像内不含 node_modules 与源码。

# ── 构建阶段 ──────────────────────────────────────────────
FROM node:22-alpine AS build

# 国内构建可传 --build-arg NPM_REGISTRY=https://registry.npmmirror.com 加速
ARG NPM_REGISTRY=

WORKDIR /app

# 依赖层单独缓存：只有 package/lock 变化时才重新 npm ci
COPY package.json package-lock.json ./
# replace-registry-host=always：lock 里 240 个 resolved 主机是生成时用的源，
# 不重写的话换机器/换网络构建会一直打旧地址（官方源在国内直接超时）。
# maxsockets 压到 4：默认 15 个并发连接在镜像源/弱网下容易被限流或半路断连，而
#   连接异常时 npm 会崩在 "Exit handler never called!"（退出路径自身的 bug，
#   错误信息里不带任何原因）。
# npm 缓存挂 BuildKit cache 卷：即使 RUN 失败，已下载的包也留在卷里，
# 重跑不用从零拉 200MB（弱网下这是最大的时间黑洞；普通层缓存只在成功后才有）。
# loglevel=http：让每次 fetch 打一行日志，配 --progress=plain 能看清卡在哪个包。
RUN --mount=type=cache,target=/root/.npm \
    if [ -n "$NPM_REGISTRY" ]; then \
        npm config set registry "$NPM_REGISTRY" \
        && npm config set replace-registry-host always; \
    fi \
    && npm config set loglevel http \
    && npm config set maxsockets 4 \
    && npm config set fetch-retries 5 \
    && npm config set fetch-timeout 300000 \
    && npm config set fetch-retry-maxtimeout 180000 \
    && ( npm ci --no-audit --no-fund \
         || { echo "---- npm ci 第 1 次失败，清理缓存后重试 ----"; \
              npm cache clean --force || true; \
              npm ci --no-audit --no-fund; } \
         || { echo "==== npm ci 两次均失败，以下为 debug 日志尾部 ===="; \
              tail -n 60 /root/.npm/_logs/*-debug-0.log 2>/dev/null; \
              echo "==== 日志结束 ===="; exit 1; } )

COPY . .
RUN npm run build

# ── 运行阶段 ──────────────────────────────────────────────
FROM nginx:1.27-alpine

# 时区：容器默认 UTC 会让日志与「今天」的判定和宿主机差 8 小时
ENV TZ=Asia/Shanghai
# 国内构建：把 Alpine 源换成国内镜像（$APK_MIRROR，如 mirrors.aliyun.com）。
# 只换主机名，v3.x/main|community 路径保留；留空则完全不动。
ARG APK_MIRROR=
RUN if [ -n "$APK_MIRROR" ]; then \
        sed -i "s|dl-cdn.alpinelinux.org|$APK_MIRROR|g" /etc/apk/repositories; \
    fi \
    && apk add --no-cache tzdata \
    && cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone

# 站点配置由 compose 挂载 infra/nginx/default.conf（改反代不用重建镜像）
RUN rm -f /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
