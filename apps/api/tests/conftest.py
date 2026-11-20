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
# 播种语义与本地 .env 解耦：多个用例依赖「登录即播种 system 任务」，
# 测试永远按默认（播种）跑，开发库想关 seeding 改自己的 .env 即可。
os.environ["SCHEDULER_SEED_SYSTEM_TASKS"] = "true"
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


@pytest.fixture(autouse=True)
def business_role(monkeypatch, request):
    """业务用例里登录的账号默认拿到 `superadmin`（最大权限）。

    绝大多数用例检验的是功能本身（发布、排版、模型、定时任务……），不是权限边界。
    这些模块接上 `require_permission` 之后，如果测试账号仍是低权限角色，
    几百个用例会集体 403——那检验的是夹具，不是业务代码。

    为什么是超管而不是运营：运营已经收窄成「可看模型配置、不可改密钥」，
    而模型类用例需要建连接、换 Key、跑探针。业务用例以最大权限运行，
    权限边界交给 `tests/test_permission_gate.py` 用显式角色去验，两边不互相绑架。

    `tests/test_rbac.py` 不参与这个替换：那里的新用户照旧是 `member`，
    才能验出「没权限就是 403」。需要精确控制角色的用例，用
    `conftest.login(client, role="member")`。
    """
    if request.node.fspath.basename == "test_rbac.py":
        return
    from app.services import rbac_service

    original = rbac_service.assign_default_role

    def assign_default(db, user_id, role_key="superadmin"):
        # 显式传了角色（比如测试自己要 member）就尊重调用方
        return original(db, user_id, role_key)

    monkeypatch.setattr(rbac_service, "assign_default_role", assign_default)


def login(client, subject="user-a", role="superadmin"):
    """登录并赋予角色，返回鉴权头。

    业务用例检验的是功能本身，不是权限边界，所以默认给 `superadmin`（最大权限）。
    **权限边界的用例必须显式传角色**（`role="member"` / `"operator"` / `"editor"`），
    或像 `tests/test_rbac.py` 那样自己建用户，否则「没权限要 403」的断言会被夹具架空。
    """
    res = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    body = res.json()
    _assign_role(body["user"]["id"], role)
    return {"Authorization": f"Bearer {body['token']}"}


def _assign_role(user_id: str, role_key: str) -> None:
    """把用户重置为单一角色。登录时播种已给过 member，这里先清再绑。"""
    # 延迟导入：conftest 顶层不能碰 app.core.db，否则 engine 会在收集阶段被绑到开发库
    from sqlalchemy import delete, select

    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        role_id = db.scalar(select(models.Role.id).where(models.Role.role_key == role_key))
        assert role_id, f"角色 {role_key} 未播种"
        current = db.scalar(
            select(models.UserRole.role_id).where(models.UserRole.user_id == user_id)
        )
        if current == role_id:
            return
        db.execute(delete(models.UserRole).where(models.UserRole.user_id == user_id))
        db.add(models.UserRole(user_id=user_id, role_id=role_id))
        db.commit()
