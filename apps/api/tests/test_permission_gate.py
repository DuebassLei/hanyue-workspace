"""业务模块的权限闸门：该挡的挡住，共用的创作通道不被误伤。

这个文件存在的理由很具体——给模块加 `require_permission` 时，最容易犯两种错：

1. **漏挡**：管理接口仍然对普通用户开放（比如 `/models/connections` 能看别人的 Key）
2. **误伤**：把跨页面共用的接口也挂上页面权限，导致普通用户在创作页点一下就 403
   （`/models/catalog` 被模型下拉调用、`/ai/image-generations` 被创作空间调用）

所以每个断言都在验「某个具体的人能不能做某件具体的事」，而不是接口形状。
"""

from __future__ import annotations

import uuid

import pytest

from .conftest import login

PREFIX = "/api/v1"


def _member(client, subject="gate-member"):
    return login(client, subject=subject, role="member")


def _operator(client, subject="gate-operator"):
    return login(client, subject=subject, role="operator")


# ---------------- 管理接口：普通用户必须被挡住 ----------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/logs"),
        ("get", "/logs/summary"),
        ("get", "/models/connections"),
        ("get", "/publishing/jobs"),
        ("get", "/schedules"),
        ("get", "/platform-accounts"),
    ],
)
def test_member_is_denied_admin_endpoints(client, method, path):
    headers = _member(client)
    res = getattr(client, method)(f"{PREFIX}{path}", headers=headers)
    assert res.status_code == 403, res.text
    # 专用错误码：前端据此刷新权限，而不是当成普通业务失败
    assert res.json()["code"] == "permission_denied"
    assert "缺少权限" in res.json()["message"]


def test_member_cannot_list_model_connections(client):
    """模型连接里是 API Key 的密文与厂商信息，是这套体系里最该挡住的数据。"""
    headers = _member(client)
    res = client.get(f"{PREFIX}/models/connections", headers=headers)
    assert res.status_code == 403


# ---------------- 共用通道：普通用户必须仍然可用 ----------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        # 模型下拉：每个创作页都要读
        ("get", "/models/catalog", None),
        # 全局状态栏
        ("get", "/models/status", None),
        # 创作空间内嵌配图，与「图片生成」页共用
        ("post", "/ai/image-generations", {"prompt": "一只猫", "count": 1}),
    ],
)
def test_member_keeps_shared_channels(client, method, path, body):
    """这些接口被低权限页面直接调用，挂错权限会让创作流程整体失效。

    只断言「不是 403」：业务上可能因为没配供应商、参数校验等原因返回别的状态，
    那属于功能测试的范畴，这里只守权限边界。
    """
    headers = _member(client)
    res = getattr(client, method)(f"{PREFIX}{path}", headers=headers, **({"json": body} if body else {}))
    assert res.status_code != 403, res.text


def test_member_keeps_text_generation_channel(client):
    """`/ai/text-generations` 和模型管理写在同一模块里，是最容易被误伤的一个。"""
    headers = _member(client)
    res = client.post(
        f"{PREFIX}/ai/text-generations",
        headers=headers,
        json={"taskType": "outline", "prompt": "写个大纲"},
    )
    assert res.status_code != 403, res.text


def test_sticker_endpoint_open_to_hot_topic_users(client):
    """热点页里有「一键生成贴图」，所以贴图接口按 any-of 放行给 hot_topics.view。"""
    headers = _member(client)
    res = client.get(f"{PREFIX}/sticker-styles", headers=headers)
    assert res.status_code == 200, res.text


def test_hot_ingest_is_not_implied_by_viewing_hot_topics(client):
    """能看热点 ≠ 能往热点库灌数据：ingest 单独一个权限点，默认只给超管。"""
    member = _member(client)
    body = {
        "platform": "越权测试",
        "sourceUrl": "https://example.com/hot",
        "items": [{"title": "不该被普通用户写进来的标题"}],
    }
    denied = client.post(f"{PREFIX}/hot-topics/ingest", json=body, headers=member)
    assert denied.status_code == 403, denied.text

    # 同一个账号看列表是允许的，挡的是写
    assert client.get(f"{PREFIX}/hot-topics/clusters", headers=member).status_code == 200


def test_member_sees_only_permitted_menus(client):
    """菜单是权限的投影：没权限的分组连壳都不出现。"""
    headers = _member(client)
    me = client.get(f"{PREFIX}/rbac/me", headers=headers).json()
    groups = {group["key"] for group in me["menus"]}
    assert "rbac" not in groups
    assert "ops" not in groups
    # 普通用户仍要有地方可去，否则登录即空白页
    assert groups


# ---------------- 运营角色：拿得到运营能力，拿不到权限管理 ----------------


def test_operator_can_publish_but_not_manage_roles(client):
    headers = _operator(client)
    assert client.get(f"{PREFIX}/schedules", headers=headers).status_code == 200
    assert client.get(f"{PREFIX}/logs", headers=headers).status_code == 200
    # 运营不是管理员：角色/菜单管理仍然是 403
    assert client.get(f"{PREFIX}/rbac/roles", headers=headers).status_code == 403


def test_superadmin_bypasses_every_gate(client):
    """超管靠 is_superadmin 免检，不依赖权限点表里的通配。"""
    headers = _operator(client, subject="gate-super")

    from app import models
    from app.core.db import SessionLocal
    from sqlalchemy import delete, select

    me = client.get(f"{PREFIX}/auth/me", headers=headers).json()
    with SessionLocal() as db:
        role_id = db.scalar(select(models.Role.id).where(models.Role.role_key == "superadmin"))
        db.execute(delete(models.UserRole).where(models.UserRole.user_id == me["id"]))
        db.add(models.UserRole(user_id=me["id"], role_id=role_id))
        db.commit()

    assert client.get(f"{PREFIX}/rbac/roles", headers=headers).status_code == 200
    assert client.get(f"{PREFIX}/models/connections", headers=headers).status_code == 200
    assert client.get(f"{PREFIX}/logs", headers=headers).status_code == 200


# ---------------- 模型的读 / 写分离 ----------------
#
# 模型连接里存的是 API Key 密文与厂商地址。「看得见配置」和「能换 Key」是两件事：
# 换错 Key 或删错连接，全站创作立即停摆，而看错一眼不会造成任何损失。


def test_operator_can_view_model_config_but_not_change_keys(client):
    """运营持有 models.view：能确认在用什么模型、连不连得上；没有 models.manage。"""
    headers = _operator(client, subject="gate-operator-models")
    assert client.get(f"{PREFIX}/models/connections", headers=headers).status_code == 200

    created = client.post(
        f"{PREFIX}/models/connections",
        headers=headers,
        json={
            "displayName": "运营不该能建",
            "providerType": "openai_compatible",
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-should-not-work",
        },
    )
    assert created.status_code == 403, created.text
    assert created.json()["code"] == "permission_denied"


def test_editor_cannot_even_view_model_config(client):
    """编辑连看都不该看到：连接列表暴露厂商与 Base URL，与创作职能无关。"""
    headers = login(client, subject="gate-editor-models", role="editor")
    res = client.get(f"{PREFIX}/models/connections", headers=headers)
    assert res.status_code == 403
    assert res.json()["code"] == "permission_denied"


# ---------------- 发布的读 / 写分离 ----------------
#
# 「能翻发布记录、看失败原因」和「能把稿子推到公众号」是两件事：
# 推错一次删不掉、也撤不回，还会占掉平台侧的群发额度；而读错一眼没有代价。
# 客服/内容负责人复盘只需要 publishing.view。


def _custom_role(codes: list[str], tag: str) -> str:
    """建一个只含指定权限点的自定义角色，返回 role_key。

    直接落库而不是走 `/rbac/roles`：这里要验的是「某个权限点够不够用」，
    不该顺带依赖角色管理接口的可用性。
    """
    from app import models
    from app.core.db import SessionLocal

    role_key = f"qa_{tag}_{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        role = models.Role(
            role_key=role_key,
            name=f"QA {tag}",
            description="权限边界用例专用",
            is_system=False,
            is_superadmin=False,
            sort=999,
        )
        db.add(role)
        db.flush()
        for code in codes:
            db.add(models.RolePermission(role_id=role.id, permission_code=code))
        db.commit()
    return role_key


def test_publishing_view_reads_everything_but_cannot_push(client):
    role_key = _custom_role(["publishing.view"], "pubread")
    headers = login(client, subject="gate-pub-read", role=role_key)

    # 读：任务列表、汇总、账号、平台能力、连接器状态全部放行
    for path in (
        "/publishing/jobs",
        "/publishing/jobs/summary",
        "/platform-accounts",
        "/platforms/capabilities",
        "/platforms/connectors/status",
    ):
        res = client.get(f"{PREFIX}{path}", headers=headers)
        assert res.status_code == 200, (path, res.text)

    # 写：创建任务、预览校验、绑定账号、动别人的任务一律 403
    for method, path, body in (
        ("post", "/publishing/jobs", {}),
        ("post", "/publishing/preview", {}),
        ("post", "/platform-accounts", {"platform": "wechat_official"}),
        ("delete", "/publishing/jobs/not-exist", None),
        ("post", "/publishing/jobs/not-exist/retry", None),
    ):
        res = getattr(client, method)(
            f"{PREFIX}{path}", headers=headers, **({"json": body} if body is not None else {})
        )
        assert res.status_code == 403, (path, res.text)
        assert res.json()["code"] == "permission_denied"


def test_publishing_use_without_view_still_reads(client):
    """存量角色只授过 publishing.use 时，读接口不能反过来把它挡在门外。

    读接口用 any-of 就是为这一条：升级不该让「昨天能看的发布队列」今天变 403。
    """
    role_key = _custom_role(["publishing.use"], "pubuse")
    headers = login(client, subject="gate-pub-use", role=role_key)
    assert client.get(f"{PREFIX}/publishing/jobs", headers=headers).status_code == 200
    assert client.get(f"{PREFIX}/platforms/connectors/status", headers=headers).status_code == 200
    # 有 use 自然也能写：这里只守「不是 403」，业务失败另有功能用例
    assert client.post(f"{PREFIX}/publishing/jobs", headers=headers, json={}).status_code != 403


def test_operator_holds_both_publish_permissions(client):
    headers = _operator(client, subject="gate-operator-pub")
    assert client.get(f"{PREFIX}/publishing/jobs", headers=headers).status_code == 200
    assert client.post(f"{PREFIX}/publishing/jobs", headers=headers, json={}).status_code != 403


# ---------------- 排版 / 公众号通道 ----------------
#
# wechat_mp 曾经整模块 any-of：有排版权限就能绑公众号、推草稿。
# 排版只改自己的工作室配置；绑号和推草稿落到公众号上。


def test_layout_role_can_render_but_cannot_bind_or_push(client):
    headers = login(client, subject="gate-layout", role="editor")
    assert client.get(f"{PREFIX}/wechat-mp/layout-studio/catalog", headers=headers).status_code == 200
    rendered = client.post(
        f"{PREFIX}/wechat-mp/layout-studio/render", headers=headers, json={"body": "你好"}
    )
    assert rendered.status_code == 200, rendered.text

    for method, path, body in (
        ("post", "/wechat-mp/bind", {"displayName": "不该能绑"}),
        ("post", "/wechat-mp/drafts", {"accountId": "none", "idempotencyKey": "layout-nope"}),
        ("post", "/wechat-mp/drafts/preview", {"title": "x", "body": "y"}),
        ("put", "/wechat-mp/credentials", {"appId": "wx", "appSecret": "secret"}),
        ("get", "/wechat-mp/egress-ip", None),
        ("get", "/wechat-mp/status", None),
    ):
        kwargs = {"json": body} if body is not None else {}
        res = getattr(client, method)(f"{PREFIX}{path}", headers=headers, **kwargs)
        assert res.status_code == 403, (path, res.text)
        assert res.json()["code"] == "permission_denied"


def test_publish_role_can_push_path_but_not_edit_studio_settings(client):
    """只有 publishing.use 的角色要能渲染、能走到绑定；不能修改工作室配置。"""
    role_key = _custom_role(["publishing.use"], "wxpub")
    headers = login(client, subject="gate-wx-pub", role=role_key)
    assert client.get(f"{PREFIX}/wechat-mp/layout-studio/catalog", headers=headers).status_code == 200
    assert client.get(f"{PREFIX}/wechat-mp/status", headers=headers).status_code == 200
    rendered = client.post(
        f"{PREFIX}/wechat-mp/layout-studio/render", headers=headers, json={"body": "你好"}
    )
    assert rendered.status_code == 200, rendered.text
    # 没配公众号时业务失败，但不能是权限拒绝
    bound = client.post(
        f"{PREFIX}/wechat-mp/bind", headers=headers, json={"displayName": "试用"}
    )
    assert bound.status_code != 403, bound.text

    created = client.put(
        f"{PREFIX}/wechat-mp/layout-studio/articles/not-exist", headers=headers, json={"options": {}}
    )
    assert created.status_code == 403, created.text
    assert created.json()["code"] == "permission_denied"


def test_publish_viewer_can_see_channel_status_only(client):
    role_key = _custom_role(["publishing.view"], "wxview")
    headers = login(client, subject="gate-wx-view", role=role_key)
    assert client.get(f"{PREFIX}/wechat-mp/status", headers=headers).status_code == 200
    assert client.get(f"{PREFIX}/wechat-mp/credentials", headers=headers).status_code == 200
    denied = client.post(f"{PREFIX}/wechat-mp/bind", headers=headers, json={})
    assert denied.status_code == 403
    # 工作室目录不是发布复盘的一部分
    assert client.get(f"{PREFIX}/wechat-mp/layout-studio/catalog", headers=headers).status_code == 403


def test_member_studio_catalog_stays_closed(client):
    """工作室目录保持排版权限边界，不对全员放开。"""
    headers = _member(client, subject="gate-wx-member")
    res = client.get(f"{PREFIX}/wechat-mp/layout-studio/catalog", headers=headers)
    assert res.status_code == 403
    assert res.json()["code"] == "permission_denied"


def test_publishing_menu_gated_by_view(client):
    """发布菜单挂在 publishing.view 上，否则只读角色进不去自己的复盘页。"""
    headers = login(client, subject="gate-menu-pub", role="superadmin")
    tree = client.get(f"{PREFIX}/rbac/menus", headers=headers).json()
    ops = next(node for node in tree if node["key"] == "ops")
    publishing = next(child for child in ops["children"] if child["key"] == "publishing")
    assert publishing["permission"] == "publishing.view"
