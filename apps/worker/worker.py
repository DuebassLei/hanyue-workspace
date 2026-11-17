"""Python 队列 Worker（设计 §5.8 / §8.2 / §11.1）。

生产形态：连接 Redis，BLPOP 消费 API 投递的 jobId，调用与本地线程池完全相同的
`job_runner.run_job`，因此两种后端的执行语义一致、任务状态都落在 background_jobs 表。

启动时调用 `recover_pending()` 重投「已入队但未开始」的任务，实现断线恢复。
未配置 REDIS_URL 时明确提示由 API 内置的线程池后端执行，不再假装启动了消费者。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from app.core import queue  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import init_db  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.services import job_runner  # noqa: E402

# hot-topics：定时任务投递的队列（docs/scheduled-tasks.md §7）。消费本身走同一个
# Redis list（queue.QUEUE_KEY），所以漏加不会漏消费；但不加这一项，
# `--queue hot-topics` 会被 argparse 直接拒绝。
QUEUES = [
    "image-processing",
    "ai-image",
    "platform-publish",
    "platform-sync",
    "hot-topics",
    "topic-digest",
]
logger = get_logger("app.worker")

# 心跳 TTL 与刷新间隔：compose healthcheck 以「心跳键是否存在」判断消费者是否活着。
# TTL 必须显著大于刷新间隔（容忍 Redis 抖动与 CPU 打满时的调度延迟）。
HEARTBEAT_KEY = "hanyue:worker:heartbeat"
HEARTBEAT_TTL = 30
HEARTBEAT_INTERVAL = 10


def _heartbeat_loop(client) -> None:
    """独立线程刷新心跳。

    不能把心跳写在消费循环里：`run_job` 是同步阻塞的，一条 1080p 成片在 2 核机器上
    要 1–3 分钟，期间主循环不回到 blpop，心跳键 30 秒就过期；compose 健康检查
    连续 10 次（约 100 秒）失败后会把 worker 标成 unhealthy —— 任务明明在正常跑。
    """
    failures = 0
    while True:
        try:
            client.set(HEARTBEAT_KEY, str(int(time.time())), ex=HEARTBEAT_TTL)
            failures = 0
        except Exception:  # Redis 短暂抖动不应终止心跳线程，下一轮自动补上
            failures += 1
            # 持续中断时每 5 分钟才打一次，避免刷满日志
            if failures == 1 or failures % 30 == 0:
                logger.warning("心跳写入失败（连续 %s 次），下一轮重试", failures, exc_info=failures == 1)
        time.sleep(HEARTBEAT_INTERVAL)


def main() -> int:
    parser = argparse.ArgumentParser(description="hanyue-workspace worker")
    parser.add_argument("--queue", default="all", choices=["all", *QUEUES])
    parser.add_argument("--once", action="store_true", help="从 stdin 读取 {jobId} 执行一个任务后退出")
    args = parser.parse_args()

    if args.once:
        # --once 的 stdout 是机器可读 JSON，不能混入日志；因此该模式不挂控制台 handler。
        init_db()
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, OSError) as exc:
            print(json.dumps({"error": "invalid_payload", "message": str(exc)}, ensure_ascii=False))
            return 1
        job_id = payload.get("jobId") if isinstance(payload, dict) else None
        if not job_id:
            print(json.dumps({"error": "missing_jobId"}, ensure_ascii=False))
            return 1
        job_runner.run_job(job_id)
        print(json.dumps({"jobId": job_id, "executed": True}, ensure_ascii=False))
        return 0

    configure_logging(get_settings(), service="worker")
    init_db()

    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        print(
            "未配置 REDIS_URL：API 已使用内置线程池后端真实执行任务，无需启动独立 worker。\n"
            "如需跨进程队列，请设置 REDIS_URL 并使用 infra/docker-compose.yml 启动 redis + worker。"
        )
        return 0

    try:
        import redis
    except ImportError:
        logger.error("缺少 redis 依赖：请 pip install -r requirements-prod.txt")
        return 1

    client = redis.Redis.from_url(redis_url, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:
        logger.error("Redis 不可达：%s", exc)
        return 1

    stale = queue.recover_stale_running()
    recovered = queue.recover_pending()
    logger.info(
        "worker 已启动，监听 %s；恢复崩溃遗留 %s 个、未开始 %s 个",
        args.queue,
        stale,
        recovered,
    )
    # 心跳由独立线程维护：run_job 阻塞期间（视频渲染）也能持续刷新
    threading.Thread(target=_heartbeat_loop, args=(client,), daemon=True, name="worker-heartbeat").start()
    try:
        while True:
            item = client.blpop(queue.QUEUE_KEY, timeout=5)
            if not item:
                continue
            _queue_name, job_id = item
            try:
                job_runner.run_job(job_id)
            except Exception as exc:  # 单个任务失败不能拖垮消费者
                logger.exception("任务 %s 执行异常", job_id)
                job_runner.mark_failed(job_id, "internal_error", str(exc))
    except KeyboardInterrupt:
        logger.info("worker 已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
