"""受信任脚本静态校验与子进程执行。"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


MAX_STDOUT = 8192
MAX_STDERR = 8192
ALLOWED_MODULES = frozenset(
    {
        "json",
        "time",
        "math",
        "re",
        "datetime",
        "decimal",
        "statistics",
        "itertools",
        "functools",
        "collections",
        "textwrap",
        "typing",
        "zoneinfo",
    }
)
FORBIDDEN_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "system",
        "popen",
    }
)
INHERITED_ENV_KEYS = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "LOCALAPPDATA",
        "NO_PROXY",
        "PATH",
        "PATHEXT",
        "PROGRAMFILES",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


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


def validate_script(code: str) -> None:
    try:
        tree = ast.parse(code, filename="<scheduled_script>")
    except SyntaxError as exc:
        raise ScriptValidationError("script_compile_failed", f"语法错误：{exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            raise ScriptValidationError(
                "script_import_forbidden",
                "不允许 import；请直接使用 SDK 与内置函数",
            )
        if isinstance(node, ast.ImportFrom):
            if node.level != 0:
                raise ScriptValidationError(
                    "script_import_forbidden", "不允许相对 import"
                )
            module = node.module or ""
            if module not in ALLOWED_MODULES or len(module) > 32:
                raise ScriptValidationError(
                    "script_import_forbidden", f"不允许的模块：{module or '<空>'}"
                )
            continue
        if isinstance(node, ast.Global):
            raise ScriptValidationError("script_global_forbidden", "不允许 global")
        if isinstance(node, ast.Nonlocal):
            raise ScriptValidationError("script_nonlocal_forbidden", "不允许 nonlocal")
        if isinstance(node, ast.Name) and node.id == "__import__":
            raise ScriptValidationError("script_import_forbidden", "不允许 __import__")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise ScriptValidationError(
                "script_function_forbidden", f"不允许使用名称：{node.id}"
            )


def compile_script(code: str) -> None:
    try:
        compile(code, "<scheduled_script>", "exec")
    except SyntaxError as exc:
        raise ScriptValidationError(
            "script_compile_failed", f"语法错误：{exc.msg}"
        ) from exc
    except (ValueError, TypeError) as exc:
        raise ScriptValidationError("script_compile_failed", str(exc)) from exc


def validate_published(code: str) -> None:
    compile_script(code)
    validate_script(code)


def run_script(
    *,
    code: str,
    params: dict,
    context: dict,
    timeout_seconds: int,
    base_url: str,
    token: str,
) -> dict:
    """在全新 Python 子进程里执行发布代码，父进程不传递任何 DB 会话。"""
    # 父进程以二进制收 stdout/stderr，再统一 UTF-8 + replace 解码，
    # 避免脚本输出混合编码时让后台任务崩溃。
    with tempfile.TemporaryDirectory(prefix="hanyue-script-") as directory:
        sdk_dir = Path(__file__).resolve().parent
        runner_path = Path(directory) / "_runner.py"
        script_path = Path(directory) / "script.py"
        script_path.write_text(code, encoding="utf-8")
        runner_source = f"""
import sys
sys.path.insert(0, {str(sdk_dir)!r})
import script_runtime_sdk
with open({str(script_path)!r}, encoding='utf-8') as script_file:
    source = script_file.read()
namespace = {{
    'get_params': script_runtime_sdk.get_params,
    'get_context': script_runtime_sdk.get_context,
    'get_workspaces': script_runtime_sdk.get_workspaces,
    'list_workspaces': script_runtime_sdk.list_workspaces,
    'create_draft': script_runtime_sdk.create_draft,
    'log': script_runtime_sdk.log,
}}
try:
    exec(compile(source, {str(script_path)!r}, 'exec'), namespace)
except BaseException as exc:
    import json
    import sys
    print(json.dumps({{'hanyueScriptError': str(exc), 'errorType': type(exc).__name__}}, ensure_ascii=False), file=sys.stderr)
    raise
"""
        runner_path.write_text(runner_source, encoding="utf-8")
        env = {key: os.environ[key] for key in INHERITED_ENV_KEYS if key in os.environ}
        env.update(
            {
                "HANYUE_SCRIPT_SDK_PARAMS": json.dumps(
                    params, ensure_ascii=False, separators=(",", ":")
                ),
                "HANYUE_SCRIPT_SDK_CONTEXT": json.dumps(
                    context, ensure_ascii=False, separators=(",", ":")
                ),
                "HANYUE_SCRIPT_SDK_TOKEN": token,
                "HANYUE_SCRIPT_SDK_BASE_URL": base_url,
                "PYTHONIOENCODING": "utf-8",
            }
        )
        try:
            completed = subprocess.run(
                [sys.executable, str(runner_path)],
                input=b"",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=max(1, int(timeout_seconds)),
                cwd=directory,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise subprocess.TimeoutExpired(
                cmd=[sys.executable, str(runner_path)],
                timeout=timeout_seconds,
                output=stdout.encode("utf-8", errors="replace"),
                stderr=stderr.encode("utf-8", errors="replace"),
            )
        except OSError as exc:
            raise ScriptExecutionError(
                "script_failed",
                f"脚本进程启动失败：{exc}",
                retryable=True,
            ) from exc

    stdout = io.TextIOWrapper(
        io.BytesIO(completed.stdout), encoding="utf-8", errors="replace"
    ).read()
    stderr = io.TextIOWrapper(
        io.BytesIO(completed.stderr), encoding="utf-8", errors="replace"
    ).read()
    if completed.returncode != 0:
        raise ScriptExecutionError(
            "script_failed",
            (stderr or f"脚本退出码 {completed.returncode}")[:1000],
            retryable=True,
        )
    return {
        "stdout": stdout[:MAX_STDOUT],
        "stderr": stderr[:MAX_STDERR],
        "exitCode": completed.returncode,
    }
