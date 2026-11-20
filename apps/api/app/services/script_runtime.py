"""定时脚本统一运行时：契约校验与进程内执行。

统一口径（重要，改动前先读）：
- 一切定时任务都是脚本，契约统一为 `run(params, ctx) -> dict`；
  内置 manifest 脚本与用户自建脚本走同一条进程内执行通道，
  共享同一 ScriptContext（db / owner_user_id / task_id / run_id / job_id / task_type）。
- 脚本运行在服务端进程内，可用项目全部依赖（httpx / jieba / app.services 等），
  也可直接使用 ctx.db 读写业务数据。发布校验因此只做两件事：能编译、定义了 run。
  脚本管理权限（scheduled-script.admin）本身就是高信任角色，进程内执行不引入额外越权面。
- 超时用工作线程等待实现：超时后任务按失败处理，但线程本身无法被强杀，
  只能等脚本自然结束（记录 warning）。不要在脚本里写不可退出的死循环。
- 真正的执行、重试、进度、取消仍全在 job_runner 与 core/queue.py，这里只负责「跑一个脚本」。
"""

from __future__ import annotations

import ast
import inspect
import io
import contextlib
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .. import models
from ..core.config import get_settings
from ..core.db import SessionLocal
from ..core.deps import default_workspace
from ..core.logging import get_logger
from ..scheduled_scripts.base import ScriptContext

logger = get_logger("app.script_runtime")

# ── 测试验证专用引擎 ────────────────────────────────────────────────────
# 「脚本测试验证」要求脚本里的 commit 也只是草稿态，结束统一回滚。做法是给脚本
# 一个独立连接上的外部事务 + savepoint 会话（commit 中性）。SQLite 的 pysqlite
# 驱动默认会破坏 SAVEPOINT，必须按 SQLAlchemy 官方配方改成显式事务；但这个配方
# 不能套在主引擎上——显式 BEGIN 会让读事务持快照，与并发写者相撞时产生立即的
# SQLITE_BUSY_SNAPSHOT（等待都救不回来），曾把全量测试打出 database is locked。
# 所以专用引擎只服务测试验证的低频路径，主引擎保持隐式事务语义不变。
_test_engine = None
_test_session_factory = None


def _get_test_session_factory():
    global _test_engine, _test_session_factory
    if _test_session_factory is not None:
        return _test_session_factory
    settings = get_settings()
    is_sqlite = settings.database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    _test_engine = create_engine(
        settings.database_url, connect_args=connect_args, future=True
    )
    if is_sqlite:

        @event.listens_for(_test_engine, "connect")
        def _sqlite_connect(dbapi_connection, connection_record):
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout=15000")
                try:
                    # WAL 让测试会话的长读事务不阻塞应用写入；切不过去就退回
                    # rollback journal（仅影响并发度，不影响回滚正确性）。
                    cursor.execute("PRAGMA journal_mode=WAL")
                except Exception:
                    pass
            finally:
                cursor.close()

        @event.listens_for(_test_engine, "begin")
        def _sqlite_begin(conn):
            conn.exec_driver_sql("BEGIN")

    _test_session_factory = sessionmaker(
        bind=_test_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    return _test_session_factory


@contextmanager
def script_test_session():
    """脚本测试验证专用会话：独立连接 + 外部事务 + savepoint。

    脚本里的 commit 只释放 savepoint、不提交外层事务；退出时统一回滚，
    对数据的任何改动都不落库。SQLite 与 PG 语义一致。
    """
    factory = _get_test_session_factory()
    connection = factory.kw["bind"].connect()
    outer_tx = connection.begin()
    session = factory(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        outer_tx.rollback()
        connection.close()

MAX_LOG_CHARS = 8192


class ScriptValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScriptExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def compile_script(code: str) -> None:
    try:
        compile(code, "<scheduled_script>", "exec")
    except SyntaxError as exc:
        raise ScriptValidationError(
            "script_compile_failed", f"语法错误：{exc.msg}"
        ) from exc
    except (ValueError, TypeError) as exc:
        raise ScriptValidationError("script_compile_failed", str(exc)) from exc


def run_contract(code: str) -> dict:
    """静态检查脚本契约：必须能编译，且顶层定义了可调用的 run。

    返回 {"hasRun": bool, "argCount": int}（argCount 为 -1 表示未定义），
    供编辑器提示与测试端点复用。
    """
    try:
        tree = ast.parse(code, filename="<scheduled_script>")
    except SyntaxError as exc:
        raise ScriptValidationError("script_compile_failed", f"语法错误：{exc.msg}") from exc

    arg_count = -1
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run":
            arg_count = len(node.args.args) + len(node.args.posonlyargs)
            break
    return {"hasRun": arg_count >= 0, "argCount": arg_count}


def validate_published(code: str) -> None:
    """发布校验：编译 + run 契约。进程内全信任通道，不再做 import/名称白名单。"""
    compile_script(code)
    info = run_contract(code)
    if not info["hasRun"]:
        raise ScriptValidationError(
            "script_run_missing",
            "脚本必须定义顶层函数 run(params, ctx)，返回 dict（可省略参数：run() / run(params)）",
        )


def _truncate(text: str) -> str:
    if len(text) <= MAX_LOG_CHARS:
        return text
    return text[:MAX_LOG_CHARS] + f"\n…（截断，原始长度 {len(text)} 字符）"


def _normalize_result(value: Any) -> dict:
    """把脚本返回值统一成 dict。

    约定优先取 result['summary'] 作为界面摘要；脚本没给就兜底一句。
    """
    if value is None:
        return {"summary": "脚本执行完成"}
    if isinstance(value, dict):
        return value
    return {"summary": str(value)}


def _call_run(run, params: dict, ctx: ScriptContext) -> Any:
    """按 run 的实际签名注入参数：run() / run(params) / run(params, ctx) 三种都支持。"""
    try:
        arg_count = len(inspect.signature(run).parameters)
    except (TypeError, ValueError):
        arg_count = 2
    arg_count = min(arg_count, 2)
    args = (params, ctx)[:arg_count]
    outcome = run(*args)
    if inspect.iscoroutine(outcome):  # 异步函数直接拒绝：跑在队列线程里没有事件循环
        raise ScriptExecutionError(
            "script_async_unsupported", "run 不能是 async 函数，请改为普通函数"
        )
    return outcome


def run_script(
    *,
    code: str,
    params: dict,
    context: dict,
    timeout_seconds: int,
    db=None,
    owner_user_id: str,
    task_id: str = "",
    run_id: str = "",
    job_id: str = "",
    task_type: str = "",
) -> dict:
    """在服务端进程内执行脚本，返回 {"result": dict, "stdout": str, "stderr": str}。

    - 会话：默认为脚本单独开一个 SessionLocal（超时弃线程时不污染调用方会话）；
      测试验证等需要回滚语义的场景可显式传入受控会话。
    - 上下文：注入与内置脚本相同的 ScriptContext，外加 get_params / get_context /
      get_workspaces / create_draft / log 一组进程内帮助函数（替代旧 SDK）。
    """
    validate_published(code)

    own_session = db is None
    session = db if db is not None else SessionLocal()
    timed_out = False
    stdout_buffer = io.StringIO()

    def get_workspaces() -> list[dict]:
        rows = (
            session.query(models.Workspace)
            .filter(models.Workspace.owner_user_id == owner_user_id)
            .order_by(models.Workspace.created_at)
            .all()
        )
        return [{"id": w.id, "name": w.name} for w in rows]

    def create_draft(title: str, body: str = "") -> dict:
        user = session.get(models.User, owner_user_id)
        if user is None:
            raise ScriptExecutionError("owner_missing", "脚本所属用户不存在", retryable=False)
        workspace = default_workspace(session, user)
        item = models.ContentItem(
            workspace_id=workspace.id,
            owner_user_id=owner_user_id,
            title=title,
            type="article",
        )
        session.add(item)
        session.flush()
        version = models.ContentVersion(
            content_item_id=item.id, version_no=1, title=title, body=""
        )
        session.add(version)
        session.flush()
        item.current_version_id = version.id
        item.draft_title = title
        item.draft_body = body
        session.commit()
        return {"contentItemId": item.id}

    ctx = ScriptContext(
        db=session,
        owner_user_id=owner_user_id,
        task_id=task_id,
        run_id=run_id,
        job_id=job_id,
        task_type=task_type,
    )
    namespace: dict[str, Any] = {
        "__name__": "hanyue_scheduled_script",
        "get_params": lambda: dict(params or {}),
        "get_context": lambda: dict(context or {}),
        "get_workspaces": get_workspaces,
        "list_workspaces": get_workspaces,
        "create_draft": create_draft,
        "log": lambda *values: print(*values, flush=True),
    }

    def _execute() -> Any:
        exec(compile(code, "<scheduled_script>", "exec"), namespace)  # noqa: S102 - 受信任脚本通道
        run = namespace.get("run")
        if not callable(run):
            raise ScriptValidationError(
                "script_run_missing",
                "脚本必须定义顶层函数 run(params, ctx)，返回 dict（可省略参数：run() / run(params)）",
            )
        return _call_run(run, params if isinstance(params, dict) else {}, ctx)

    try:
        with contextlib.redirect_stdout(stdout_buffer):
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="hanyue-script") as pool:
                future = pool.submit(_execute)
                try:
                    outcome = future.result(timeout=max(1, int(timeout_seconds)))
                except FutureTimeoutError as exc:
                    # 线程无法强杀：任务先按超时失败落定，线程等脚本自然结束后自行回收。
                    # 超时路径不在这里关会话（线程可能仍在用），留给线程结束后 GC 兜底。
                    timed_out = True
                    logger.warning(
                        "脚本执行超时（%s 秒），线程将在脚本自然结束后回收", timeout_seconds
                    )
                    raise ScriptExecutionError(
                        "script_timeout",
                        f"脚本执行超时（{timeout_seconds} 秒）",
                        retryable=True,
                    ) from exc
    except ScriptExecutionError:
        raise
    except ScriptValidationError:
        raise
    except Exception as exc:  # 脚本内部异常：原样转述给 job 终态
        raise ScriptExecutionError(
            "script_failed",
            f"{type(exc).__name__}: {exc}",
            retryable=False,
        ) from exc
    finally:
        if own_session and not timed_out:
            session.close()

    stdout = stdout_buffer.getvalue()
    return {
        "result": _normalize_result(outcome),
        "stdout": _truncate(stdout),
        "stderr": "",
    }
