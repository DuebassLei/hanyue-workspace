"""RBAC 回归网：权限隔离、菜单投影、角色改动的即时生效与不可锁死。

这些用例验证的是**边界**而不是接口形状：
- 普通用户拿不到管理接口（403），而不是「前端不显示」
- 改角色权限后，已登录用户的 `/rbac/me` 立刻反映新权限（不需要重新登录）
- 菜单是权限的投影：没权限的页面即使被绑定也不出现
- 超管角色不可删、最后一个超管不能自废，避免系统被锁死
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

PREFIX = "/api/v1"


def _login(client, tag: str = "rbac"):
    subject = f"{tag}-{uuid.uuid4().hex[:8]}"
    res = client.post(
        f"{PREFIX}/auth/login", json={"subject": subject, "displayName": subject}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["user"]["id"]


def _set_roles(user_id: str, role_keys: list[str]) -> None:
    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        db.execute(delete(models.UserRole).where(models.UserRole.user_id == user_id))
        for role_key in role_keys:
            role_id = db.scalar(
                select(models.Role.id).where(models.Role.role_key == role_key)
            )
            assert role_id, f"角色 {role_key} 未播种"
            db.add(models.UserRole(user_id=user_id, role_id=role_id))
        db.commit()


def _superadmin(client):
    headers, user_id = _login(client, "super")
    _set_roles(user_id, ["superadmin"])
    return headers, user_id


def _role_id(client, headers, role_key: str) -> str:
    res = client.get(f"{PREFIX}/rbac/roles", headers=headers)
    assert res.status_code == 200, res.text
    return next(item["id"] for item in res.json() if item["roleKey"] == role_key)


# ---------------- 基础投影 ----------------


def test_new_user_gets_default_role_and_menus(client):
    headers, _ = _login(client, "member")
    me = client.get(f"{PREFIX}/rbac/me", headers=headers)
    assert me.status_code == 200, me.text
    body = me.json()

    assert "member" in body["roleKeys"]
    assert body["isSuperadmin"] is False
    assert "studio.use" in body["permissions"]
    # 普通用户不该有任何后台管理能力
    assert "rbac.role.manage" not in body["permissions"]
    assert body["menus"], "普通用户至少应看到创作分组"
    assert "模型管理" not in str(body["menus"])


def test_member_cannot_reach_admin_endpoints(client):
    headers, _ = _login(client, "member")
    assert client.get(f"{PREFIX}/rbac/roles", headers=headers).status_code == 403
    assert client.get(f"{PREFIX}/rbac/menus", headers=headers).status_code == 403
    assert client.get(f"{PREFIX}/rbac/users", headers=headers).status_code == 403
    # 菜单管理不能顺手改角色（权限点分离）
    assert client.get(f"{PREFIX}/rbac/permissions", headers=headers).status_code == 403


def test_superadmin_sees_every_menu(client):
    headers, _ = _superadmin(client)
    body = client.get(f"{PREFIX}/rbac/me", headers=headers).json()
    assert body["isSuperadmin"] is True
    groups = {group["key"] for group in body["menus"]}
    assert {"create", "workspace", "ops", "rbac", "system"} <= groups

    names = {item["name"] for group in body["menus"] for item in group["items"]}
    assert {"roles", "menus", "users", "models", "logs"} <= names


# ---------------- 改动即时生效 ----------------


def test_permission_change_takes_effect_without_relogin(client):
    admin, _ = _superadmin(client)
    member_headers, member_id = _login(client, "member")

    role_key = f"qa_{uuid.uuid4().hex[:6]}"
    created = client.post(
        f"{PREFIX}/rbac/roles",
        headers=admin,
        json={"roleKey": role_key, "name": "QA 角色", "permissionCodes": ["studio.use"]},
    )
    assert created.status_code == 201, created.text
    role = created.json()
    _set_roles(member_id, [role_key])

    before = client.get(f"{PREFIX}/rbac/me", headers=member_headers).json()
    assert "studio.use" in before["permissions"]
    assert "calendar.view" not in before["permissions"]

    # 同一个令牌，不重新登录：权限必须立刻变成新的那一套
    patched = client.put(
        f"{PREFIX}/rbac/roles/{role['id']}/permissions",
        headers=admin,
        json={"codes": ["calendar.view"]},
    )
    assert patched.status_code == 200, patched.text

    after = client.get(f"{PREFIX}/rbac/me", headers=member_headers).json()
    assert "calendar.view" in after["permissions"]
    assert "studio.use" not in after["permissions"]
    # 菜单跟着权限走：没有 studio.use 就不该出现创作空间
    names = {item["name"] for group in after["menus"] for item in group["items"]}
    assert "studio" not in names
    assert "calendar" in names

    client.delete(f"{PREFIX}/rbac/roles/{role['id']}", headers=admin)


def test_menu_binding_hides_even_with_permission(client):
    """绑定是第二道闸：有权限但角色没绑这个菜单，也不显示。"""
    admin, _ = _superadmin(client)
    member_headers, member_id = _login(client, "member")

    tree = client.get(f"{PREFIX}/rbac/menus", headers=admin).json()
    workspace = next(node for node in tree if node["key"] == "workspace")
    create_group = next(node for node in tree if node["key"] == "create")
    # 只绑「创作总览」和它的分组，其余一律不绑
    keep = [
        create_group["id"],
        next(child["id"] for child in create_group["children"] if child["key"] == "overview"),
    ]

    role_key = f"qa_{uuid.uuid4().hex[:6]}"
    role = client.post(
        f"{PREFIX}/rbac/roles",
        headers=admin,
        json={"roleKey": role_key, "name": "受限角色", "permissionCodes": ["studio.use", "overview.view"]},
    ).json()
    assert client.put(
        f"{PREFIX}/rbac/roles/{role['id']}/menus", headers=admin, json={"menuIds": keep}
    ).status_code == 200
    _set_roles(member_id, [role_key])

    body = client.get(f"{PREFIX}/rbac/me", headers=member_headers).json()
    names = {item["name"] for group in body["menus"] for item in group["items"]}
    groups = {group["key"] for group in body["menus"]}
    assert "overview" in names
    assert "studio" not in names  # 有权限但未绑定
    assert workspace["key"] not in groups  # 空分组自动剔除

    client.delete(f"{PREFIX}/rbac/roles/{role['id']}", headers=admin)


# ---------------- 不可锁死 ----------------


def test_system_role_cannot_be_deleted(client):
    admin, _ = _superadmin(client)
    member_role_id = _role_id(client, admin, "member")
    res = client.delete(f"{PREFIX}/rbac/roles/{member_role_id}", headers=admin)
    assert res.status_code == 409
    assert res.json()["code"] == "role_protected"


def test_last_superadmin_cannot_remove_self(client):
    admin, admin_id = _superadmin(client)
    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        # 只保留当前这个超管，构造「最后一个」的真实场景
        super_role_id = db.scalar(
            select(models.Role.id).where(models.Role.is_superadmin.is_(True))
        )
        db.execute(
            delete(models.UserRole).where(
                models.UserRole.role_id == super_role_id,
                models.UserRole.user_id != admin_id,
            )
        )
        db.commit()

    res = client.put(
        f"{PREFIX}/rbac/users/{admin_id}/roles", headers=admin, json={"roleIds": []}
    )
    assert res.status_code == 409
    assert res.json()["code"] == "last_superadmin"


# ---------------- 用户生命周期 ----------------


def test_admin_creates_user_with_one_time_password(client):
    admin, _ = _superadmin(client)
    member_id = _role_id(client, admin, "member")
    email = f"new-{uuid.uuid4().hex[:8]}@example.com"

    created = client.post(
        f"{PREFIX}/rbac/users",
        headers=admin,
        json={"email": email, "displayName": "新人", "roleIds": [member_id]},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["user"]["email"] == email
    assert body["user"]["hasPassword"] is True
    assert member_id in body["user"]["roleIds"]
    password = body["initialPassword"]
    assert password and "initialPassword" not in str(
        client.get(f"{PREFIX}/rbac/users", headers=admin).json()
    )

    # 用一次性密码能登录，且只有被分配的角色
    logged = client.post(f"{PREFIX}/auth/login", json={"email": email, "password": password})
    assert logged.status_code == 200, logged.text
    me = client.get(
        f"{PREFIX}/rbac/me", headers={"Authorization": f"Bearer {logged.json()['token']}"}
    )
    assert me.json()["roleKeys"] == ["member"]

    # 零角色、重复邮箱都要被拒
    assert (
        client.post(
            f"{PREFIX}/rbac/users", headers=admin, json={"email": f"x-{email}", "roleIds": []}
        ).status_code
        == 422
    )
    taken = client.post(
        f"{PREFIX}/rbac/users",
        headers=admin,
        json={"email": email.upper(), "roleIds": [member_id]},
    )
    assert taken.status_code == 409
    assert taken.json()["code"] == "email_taken"


def test_disable_blocks_next_request_and_protects_last_superadmin(client):
    admin, admin_id = _superadmin(client)
    member_headers, member_id = _login(client, "lifecycle")

    disabled = client.patch(
        f"{PREFIX}/rbac/users/{member_id}", headers=admin, json={"isActive": False}
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["isActive"] is False
    # 停用在下一次请求生效：已签发的令牌不再被接受
    assert client.get(f"{PREFIX}/rbac/me", headers=member_headers).status_code == 403

    enabled = client.patch(
        f"{PREFIX}/rbac/users/{member_id}", headers=admin, json={"isActive": True}
    )
    assert enabled.status_code == 200

    cannot_self = client.patch(
        f"{PREFIX}/rbac/users/{admin_id}", headers=admin, json={"isActive": False}
    )
    assert cannot_self.status_code == 409
    assert cannot_self.json()["code"] == "cannot_disable_self"

    # 停用的人默认不出现在列表里，但搜索跨状态仍能找到
    listed = client.get(f"{PREFIX}/rbac/users?status=active", headers=admin).json()
    assert all(item["isActive"] for item in listed)
    client.patch(f"{PREFIX}/rbac/users/{member_id}", headers=admin, json={"isActive": False})
    hidden = client.get(f"{PREFIX}/rbac/users", headers=admin).json()
    assert member_id not in {item["id"] for item in hidden}
    disabled_list = client.get(f"{PREFIX}/rbac/users?status=disabled", headers=admin).json()
    assert member_id in {item["id"] for item in disabled_list}


def test_reset_password_kicks_existing_session_and_not_self(client, monkeypatch):
    # 基线已知竞态：令牌 pwdv 是秒级 epoch。若"用户登录"与"管理员重置密码"
    # 落在同一秒，重置写入的 password_changed_at 与令牌 pwdv 相等，鉴权处
    # `pwdv < epoch(password_changed_at)` 不成立，旧会话不会被判失效。
    # 这里把重置时刻的时间戳稳定地推到登录后 3 秒，让本用例确定性地验证
    # "改密即全端下线"的业务语义，而不依赖墙钟是否恰好跨秒。
    # 注：生产侧同一秒改密仍可能漏踢旧会话，属独立的鉴权精度问题，另行处理。
    import datetime as _dt

    import app.modules.rbac as _rbac

    _offset = {"seconds": 0}

    class _ShiftedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime.now(tz) + _dt.timedelta(seconds=_offset["seconds"])

    monkeypatch.setattr(_rbac, "datetime", _ShiftedDatetime)
    admin, admin_id = _superadmin(client)
    member_id = _role_id(client, admin, "member")
    email = f"reset-{uuid.uuid4().hex[:8]}@example.com"
    created = client.post(
        f"{PREFIX}/rbac/users",
        headers=admin,
        json={"email": email, "roleIds": [member_id]},
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["user"]["id"]
    first = created.json()["initialPassword"]

    session = client.post(f"{PREFIX}/auth/login", json={"email": email, "password": first})
    old_headers = {"Authorization": f"Bearer {session.json()['token']}"}
    assert client.get(f"{PREFIX}/auth/me", headers=old_headers).status_code == 200

    # 仅把重置时刻推进 3 秒，确保 password_changed_at 严格晚于旧令牌 pwdv。
    _offset["seconds"] = 3
    reset = client.post(f"{PREFIX}/rbac/users/{user_id}/reset-password", headers=admin)
    _offset["seconds"] = 0
    assert reset.status_code == 200, reset.text
    new_password = reset.json()["initialPassword"]
    assert new_password != first
    # 旧会话立即失效；新密码可以登录
    assert client.get(f"{PREFIX}/auth/me", headers=old_headers).status_code == 401
    again = client.post(
        f"{PREFIX}/auth/login", json={"email": email, "password": new_password}
    )
    assert again.status_code == 200, again.text

    self_reset = client.post(f"{PREFIX}/rbac/users/{admin_id}/reset-password", headers=admin)
    assert self_reset.status_code == 409
    assert self_reset.json()["code"] == "cannot_reset_self"


def test_member_cannot_create_users(client):
    headers, _ = _login(client, "member")
    res = client.post(
        f"{PREFIX}/rbac/users",
        headers=headers,
        json={"email": "nope@example.com", "roleIds": ["x"]},
    )
    assert res.status_code == 403


# ---------------- 菜单 CRUD ----------------


def test_menu_crud_rebuilds_path_and_guards_children(client):
    admin, _ = _superadmin(client)
    suffix = uuid.uuid4().hex[:6]

    group = client.post(
        f"{PREFIX}/rbac/menus",
        headers=admin,
        json={"key": f"qa{suffix}", "label": "QA 分组", "kind": "group", "sort": 990},
    )
    assert group.status_code == 201, group.text
    group_id = group.json()["id"]
    assert group.json()["path"] == f"/qa{suffix}"

    child = client.post(
        f"{PREFIX}/rbac/menus",
        headers=admin,
        json={
            "parentId": group_id,
            "key": f"qa-page-{suffix}",
            "label": "QA 页面",
            "kind": "page",
            "permission": "settings.view",
        },
    )
    assert child.status_code == 201, child.text
    child_id = child.json()["id"]
    assert child.json()["path"] == f"/qa{suffix}/qa-page-{suffix}"

    patched = client.patch(
        f"{PREFIX}/rbac/menus/{child_id}", headers=admin, json={"label": "QA 页面改名"}
    )
    assert patched.status_code == 200
    assert patched.json()["label"] == "QA 页面改名"

    # 有子菜单时不能删父级
    assert client.delete(f"{PREFIX}/rbac/menus/{group_id}", headers=admin).status_code == 409

    # 未知权限点被拒
    bad = client.post(
        f"{PREFIX}/rbac/menus",
        headers=admin,
        json={"parentId": group_id, "key": f"qa-bad-{suffix}", "label": "坏节点", "kind": "page", "permission": "nope.nope"},
    )
    assert bad.status_code == 400

    assert client.delete(f"{PREFIX}/rbac/menus/{child_id}", headers=admin).status_code == 204
    assert client.delete(f"{PREFIX}/rbac/menus/{group_id}", headers=admin).status_code == 204

    audits = client.get(f"{PREFIX}/rbac/audits", headers=admin)
    assert audits.status_code == 200
    actions = {row["action"] for row in audits.json()}
    assert {"menu.create", "menu.update", "menu.delete"} <= actions


# ---------------- 菜单权限码升级 ----------------
#
# 页面的「进门权限」会随着读写拆分往下调（models.manage → models.view、
# publishing.use → publishing.view）。老库里存的是旧值，重启时必须自动校正，
# 否则现象是「菜单在，点进去只读用户 403」——最难自查的那类故障。


def test_menu_permission_renamed_on_upgrade(client):
    from app import models
    from app.core.db import SessionLocal
    from app.services import rbac_service

    with SessionLocal() as db:
        menu = db.scalar(select(models.Menu).where(models.Menu.menu_key == "publishing"))
        assert menu is not None

        # 伪造老库：发布菜单还挂在 publishing.use 上
        menu.permission_code = "publishing.use"
        db.commit()

        rbac_service.seed(db)
        db.commit()
        db.refresh(menu)
        assert menu.permission_code == "publishing.view"

        # 管理员手工指定过的权限码不该被覆盖（比如把发布页改成要 settings.view）
        menu.permission_code = "settings.view"
        db.commit()
        rbac_service.seed(db)
        db.commit()
        db.refresh(menu)
        assert menu.permission_code == "settings.view"

        # 还原，不把这次实验留给后面的用例
        menu.permission_code = "publishing.view"
        db.commit()


def test_operator_gets_publish_view_without_losing_publish_use(client):
    """新增 publishing.view 要补给存量运营，但不能顺手摘掉 publishing.use。

    前者少了 → 运营打不开发布页；后者少了 → 运营突然不能发布，是更严重的回归。
    """
    from app import models
    from app.core.db import SessionLocal
    from app.services import rbac_service

    with SessionLocal() as db:
        operator_id = db.scalar(select(models.Role.id).where(models.Role.role_key == "operator"))

        def held() -> set[str]:
            return set(
                db.scalars(
                    select(models.RolePermission.permission_code).where(
                        models.RolePermission.role_id == operator_id
                    )
                ).all()
            )

        # 伪造升级前：只有 publishing.use，没有 publishing.view
        db.execute(
            delete(models.RolePermission).where(
                models.RolePermission.role_id == operator_id,
                models.RolePermission.permission_code == "publishing.view",
            )
        )
        db.execute(delete(models.Permission).where(models.Permission.code == "publishing.view"))
        db.commit()

        rbac_service.seed(db)
        db.commit()

        assert {"publishing.view", "publishing.use"} <= held()

        # 只读权限不该外溢给编辑 / 普通用户
        for role_key in ("editor", "member"):
            role_id = db.scalar(select(models.Role.id).where(models.Role.role_key == role_key))
            codes = set(
                db.scalars(
                    select(models.RolePermission.permission_code).where(
                        models.RolePermission.role_id == role_id
                    )
                ).all()
            )
            assert "publishing.view" not in codes
            assert "publishing.use" not in codes


def test_role_key_uniqueness(client):
    admin, _ = _superadmin(client)
    res = client.post(
        f"{PREFIX}/rbac/roles",
        headers=admin,
        json={"roleKey": "member", "name": "重复角色"},
    )
    assert res.status_code == 409
    assert res.json()["code"] == "role_key_exists"


def test_new_permission_code_backfills_builtin_roles(client):
    """新增权限点要自动补给存量内置角色。

    否则老库升级后会出现最难自查的那种故障：菜单在、点进去 403——
    权限点进了目录，角色绑定没跟上，而管理员不知道该去补哪个勾。
    """
    from app import models
    from app.core.db import SessionLocal
    from app.services import rbac_service

    with SessionLocal() as db:
        # 模拟升级前的老库：models.view 既不在目录里，也没绑给任何角色
        db.execute(
            delete(models.RolePermission).where(
                models.RolePermission.permission_code == "models.view"
            )
        )
        db.execute(delete(models.Permission).where(models.Permission.code == "models.view"))
        db.commit()

        rbac_service.seed(db)
        db.commit()

        operator_id = db.scalar(select(models.Role.id).where(models.Role.role_key == "operator"))
        held = set(
            db.scalars(
                select(models.RolePermission.permission_code).where(
                    models.RolePermission.role_id == operator_id
                )
            ).all()
        )
        assert "models.view" in held
        # 运营被收窄：能看配置，不能碰密钥
        assert "models.manage" not in held

        # 补权限不等于复活老绑定：member 没被顺手塞上模型权限
        member_id = db.scalar(select(models.Role.id).where(models.Role.role_key == "member"))
        member_held = set(
            db.scalars(
                select(models.RolePermission.permission_code).where(
                    models.RolePermission.role_id == member_id
                )
            ).all()
        )
        assert "models.view" not in member_held


def test_models_read_split_migration_runs_only_once(client):
    """老库的运营被收回 models.manage，但只在迁移那一次。

    第二次启动时管理员如果又把 manage 勾回去，重启不该再撤销它——
    这正是「一次性迁移」和「每次同步」的分界：前者修数据，后者会跟人对着干。
    """
    from app import models
    from app.core.db import SessionLocal
    from app.services import rbac_service

    with SessionLocal() as db:
        operator_id = db.scalar(select(models.Role.id).where(models.Role.role_key == "operator"))

        def held() -> set[str]:
            return set(
                db.scalars(
                    select(models.RolePermission.permission_code).where(
                        models.RolePermission.role_id == operator_id
                    )
                ).all()
            )

        # 伪造旧库：运营持有 manage，且迁移标记尚未写下
        db.execute(
            delete(models.RolePermission).where(
                models.RolePermission.role_id == operator_id,
                models.RolePermission.permission_code == "models.manage",
            )
        )
        db.execute(
            delete(models.AppSetting).where(
                models.AppSetting.key == rbac_service._MIGRATION_SETTING_KEY
            )
        )
        db.add(models.RolePermission(role_id=operator_id, permission_code="models.manage"))
        db.commit()

        rbac_service.seed(db)
        db.commit()
        assert "models.manage" not in held()

        # 管理员手工勾回来，再重启一次：不该被撤掉
        db.add(models.RolePermission(role_id=operator_id, permission_code="models.manage"))
        db.commit()
        rbac_service.seed(db)
        db.commit()
        assert "models.manage" in held()

        # 还原成收窄后的状态，不把这次实验留给后面的用例
        db.execute(
            delete(models.RolePermission).where(
                models.RolePermission.role_id == operator_id,
                models.RolePermission.permission_code == "models.manage",
            )
        )
        db.commit()


def test_legacy_menu_prune_and_articles_bootstrap(client):
    """老库升级：删废弃的 image-styles 菜单 + 补 articles 菜单，且迁移只跑一次。

    模拟已有库的状态（有 image-styles、无 articles、迁移标记未写），跑一次 seed：
    - image-styles 被删、articles 被补上并绑定角色
    - 管理员之后手工重建的 image-styles 不会被再次误删（只增不减）
    """
    from app import models
    from app.core.db import SessionLocal
    from app.services import rbac_service

    with SessionLocal() as db:
        workspace = db.scalar(select(models.Menu).where(models.Menu.menu_key == "workspace"))
        assert workspace is not None

        # --- 伪造老库 ---
        # 1) 移除 articles（若已被其它用例种下）
        db.execute(delete(models.RoleMenu).where(
            models.RoleMenu.menu_id.in_(
                select(models.Menu.id).where(models.Menu.menu_key == "articles")
            )
        ))
        db.execute(delete(models.Menu).where(models.Menu.menu_key == "articles"))
        # 2) 重建废弃菜单 image-styles
        legacy = db.scalar(select(models.Menu).where(models.Menu.menu_key == "image-styles"))
        if legacy is None:
            legacy = models.Menu(
                parent_id=workspace.id,
                menu_key="image-styles",
                label="风格库",
                short_label="风格",
                icon="image-styles",
                path=f"{workspace.path}/image-styles",
                kind="page",
                permission_code="image_styles.manage",
                sort=999,
            )
            db.add(legacy)
            db.flush()
        # 给它绑一个角色，验证级联清理绑定
        admin_role = db.scalar(select(models.Role).where(models.Role.role_key == "operator"))
        db.execute(delete(models.RoleMenu).where(
            models.RoleMenu.role_id == admin_role.id,
            models.RoleMenu.menu_id == legacy.id,
        ))
        db.add(models.RoleMenu(role_id=admin_role.id, menu_id=legacy.id))
        # 3) 抹掉菜单迁移标记，让本次 seed 认为这是一次老库升级
        setting = db.get(models.AppSetting, rbac_service._MIGRATION_SETTING_KEY)
        done = set((setting.value or {}).get("done", [])) if setting else set()
        done.discard(rbac_service.MIGRATION_PRUNE_LEGACY_MENUS)
        if setting is None:
            setting = models.AppSetting(key=rbac_service._MIGRATION_SETTING_KEY, value={})
            db.add(setting)
        setting.value = {"done": sorted(done)}
        db.commit()

        # --- 第一次 seed：应删 image-styles、补 articles ---
        rbac_service.seed(db)
        db.commit()
        assert db.scalar(select(models.Menu).where(models.Menu.menu_key == "image-styles")) is None
        articles = db.scalar(select(models.Menu).where(models.Menu.menu_key == "articles"))
        assert articles is not None
        assert articles.parent_id == workspace.id
        assert articles.path == f"{workspace.path}/articles"

        # --- 管理员手工重建废弃菜单后再次 seed：不该被再次删除 ---
        reborn = models.Menu(
            parent_id=workspace.id,
            menu_key="image-styles",
            label="我的风格",
            short_label="风格",
            icon="image-styles",
            path=f"{workspace.path}/image-styles",
            kind="page",
            permission_code="image_styles.manage",
            sort=998,
        )
        db.add(reborn)
        db.commit()
        rbac_service.seed(db)
        db.commit()
        assert db.scalar(select(models.Menu).where(models.Menu.menu_key == "image-styles")) is not None

        # 还原：删掉重建的菜单，不污染后续用例
        db.delete(reborn)
        db.commit()
