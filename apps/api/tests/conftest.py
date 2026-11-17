"""共享测试夹具：整个测试会话只建一次库，避免多次 reload 造成 engine 与配置漂移。"""

import atexit
import importlib
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 关键：env 必须在**收集阶段**就写好。
# 某些测试文件在模块顶层 `from app...`，pytest 会先导入它们再执行 fixture；
# 若此时 DATABASE_URL 还没设，app.core.db 的 engine 会绑到开发库 hanyue.db，
# 测试数据就写进了真实数据库（且素材文件在临时目录，会话结束即消失）。
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="hanyue-test-"))
(_TMP_ROOT / "storage").mkdir(parents=True, exist_ok=True)
TEST_DB_PATH = _TMP_ROOT / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["STORAGE_DIR"] = (_TMP_ROOT / "storage").as_posix()
os.environ["SECRET_KEY"] = "test-secret"
# 定时调度器在测试里不启动：TestClient 的 lifespan 跑在 anyio 的门程里，
# AsyncIOScheduler 绑定的事件循环并不是真正在跑的那个，装进去只会制造噪音。
# 调度语义（CAS / 并发 / 对账）在 test_scheduled_tasks.py 里直接调服务层函数测。
os.environ["SCHEDULER_ENABLED"] = "false"
atexit.register(lambda: shutil.rmtree(_TMP_ROOT, ignore_errors=True))

# 这些用例验证的是 Agent 工具链，不是 AI 网关本身。
# 无凭据时网关按设计明确报 provider_unavailable，因此这里注入测试替身提供文本；
# 生产代码里不存在任何本地占位生成器。
_ORCHESTRATION_TESTS = {
    "test_agent_run_with_enabled_skill_creates_draft",
}


@pytest.fixture(scope="session")
def client():
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app import main as main_module

    # 兜底：若 app.core.db 在收集阶段已被导入并绑了别的库，这里直接报错，
    # 避免再悄悄把测试数据写进开发库。
    from app.core import db as db_module

    db_url = str(db_module.engine.url)
    assert db_url.endswith("test.db"), f"测试库配置未生效，实际指向：{db_url}"

    importlib.reload(main_module)
    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def ai_test_double(request, monkeypatch):
    """仅对编排类用例注入 AI 测试替身；在 fixture 运行时导入，避免收集阶段污染 engine。"""
    if request.node.name not in _ORCHESTRATION_TESTS:
        return
    from app.services import ai_gateway

    async def fake_generate_text(db, **kwargs):
        return {
            "text": f"[测试替身] {kwargs.get('task_type')}：{kwargs.get('prompt')}",
            "modelCode": "test-double",
            "provider": "test-double",
            "errorCode": None,
            "fallbackUsed": False,
            "primaryErrorCode": None,
            "attempts": 1,
        }

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text)


def login(client, subject="user-a"):
    res = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}
