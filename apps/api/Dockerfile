FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Pillow 运行期依赖（libjpeg/zlib 已包含在 wheel 中，此处保留最小系统包）
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-prod.txt

COPY app ./app

# 以非 root 运行；命名卷首次挂载会继承镜像内 /data/storage 的属主
RUN useradd --system --uid 10001 app \
    && mkdir -p /data/storage \
    && chown -R app:app /data
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
