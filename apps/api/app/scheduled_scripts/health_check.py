"""通用脚本示例：验证「定时任务 → 脚本 → 结果摘要」整条链路。

它刻意不依赖任何业务服务，方便在空库上直接「立即执行」看到效果：
新建时填 label / verbose，到点后结果摘要里就能看到参数确实传进来了。
"""

from __future__ import annotations

from datetime import datetime, timezone

from .base import ScriptContext


def run(params: dict, ctx: ScriptContext) -> dict:
    label = str(params.get("label") or "未命名检查")
    verbose = bool(params.get("verbose", False))

    result = {
        "summary": f"{label} · 正常",
        "label": label,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "taskType": ctx.task_type,
    }
    if verbose:
        result["context"] = {
            "taskId": ctx.task_id,
            "runId": ctx.run_id,
            "ownerUserId": ctx.owner_user_id,
        }
    return result
