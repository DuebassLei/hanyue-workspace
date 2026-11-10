FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 构建上下文为仓库根目录（infra/docker-compose.yml 中的 context: ..）
COPY apps/api/requirements.txt apps/api/requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-prod.txt

# Worker 与 API 共用同一套领域模型与服务，保证两种执行后端语义一致
COPY apps/api/app ./app
COPY apps/worker/worker.py ./worker.py

RUN useradd --system --uid 10001 app \
    && mkdir -p /data/storage \
    && chown -R app:app /data
USER app

CMD ["python", "worker.py"]
