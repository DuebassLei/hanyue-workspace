"""脚本运行时契约：上下文与可预期失败。

脚本只依赖这一层，不直接 import job_runner / scheduled_tasks，避免循环依赖，
也让脚本的单元测试不必构造完整的后台任务对象。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ScriptFailure(RuntimeError):
    """脚本可预期的业务失败，会被映射成 job 的 errorCode/errorMessage。

    与 job_runner.JobFailure 刻意分开：脚本层不需要认识队列执行器。
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ScriptContext:
    """注入给脚本的执行上下文，全部是只读视图。

    - db：当前请求的数据库会话，脚本可用它读写业务数据；
    - owner_user_id：任务所属用户（system 任务也有真实 owner，见 ScheduledTask 注释）；
    - task_id / run_id：触发账本主键，便于脚本写审计或幂等；
    - job_id：后台任务主键，脚本想查自身状态时用；
    - task_type：声明里的 key，脚本可按类型分支（一般不需要）。
    """

    db: Any
    owner_user_id: str
    task_id: str = ""
    run_id: str = ""
    job_id: str = ""
    task_type: str = ""
