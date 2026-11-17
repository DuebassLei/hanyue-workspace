"""正式登录（邮箱密码）的回归网。

覆盖的是安全属性，不是接口形状：
- 注册后可直接拿到会话，且 provider 标记为 password
- 弱密码 / 重复邮箱被拒
- 密码错误达阈值后账号被锁定（在线爆破防护）
- 改密后旧令牌立即失效（令牌带 pwdv，与 password_changed_at 比对）
- 关闭开放注册后仍允许创建「首个密码账号」，否则新部署无路可走
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

PREFIX = "/api/v1"
GOOD_PASSWORD = "Str0ng-pass-2026"


def _email() -> str:
    # 会话级库共享，用随机邮箱避免用例之间撞唯一索引
    return f"auth-{uuid.uuid4().hex[:10]}@example.com"


def _register(client, email: str | None = None, password: str = GOOD_PASSWORD):
    return client.post(
        f"{PREFIX}/auth/register",
        json={
            "email": email or _email(),
            "password": password,
            "displayName": "认证测试",
        },
    )


def test_register_returns_usable_session(client, monkeypatch):
    from app.modules import auth

    monkeypatch.setattr(auth.settings, "allow_registration", True)
    res = _register(client)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["user"]["authProvider"] == "password"
    assert body["user"]["hasPassword"] is True
    assert body["workspaceId"]

    headers = {"Authorization": f"Bearer {body['token']}"}
    me = client.get(f"{PREFIX}/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == body["user"]["email"]


def test_email_is_normalized_and_unique(client, monkeypatch):
    from app.modules import auth

    monkeypatch.setattr(auth.settings, "allow_registration", True)
    email = _email()
    assert _register(client, email.upper()).status_code == 201
    # 大小写/空格不同也算同一账号
    dup = _register(client, f"  {email}  ")
    assert dup.status_code == 409
    assert dup.json()["code"] == "email_taken"


def test_weak_password_rejected(client, monkeypatch):
    from app.modules import auth

    monkeypatch.setattr(auth.settings, "allow_registration", True)
    res = _register(client, password="12345678")
    assert res.status_code == 400
    assert res.json()["code"] == "weak_password"


def test_login_with_wrong_password_is_rejected(client, monkeypatch):
    from app.modules import auth

    monkeypatch.setattr(auth.settings, "allow_registration", True)
    email = _email()
    assert _register(client, email).status_code == 201

    ok = client.post(
        f"{PREFIX}/auth/login", json={"email": email, "password": GOOD_PASSWORD}
    )
    assert ok.status_code == 200, ok.text

    bad = client.post(
        f"{PREFIX}/auth/login", json={"identifier": email, "password": "wrong-pass-1"}
    )
    assert bad.status_code == 401
    assert bad.json()["code"] == "invalid_credentials"

    # 未注册的邮箱返回同一错误码，不泄露账号是否存在
    ghost = client.post(
        f"{PREFIX}/auth/login",
        json={"email": _email(), "password": GOOD_PASSWORD},
    )
    assert ghost.status_code == 401
    assert ghost.json()["code"] == "invalid_credentials"


def test_account_locks_after_repeated_failures(client, monkeypatch):
    from app.modules import auth

    monkeypatch.setattr(auth.settings, "allow_registration", True)
    monkeypatch.setattr(auth.settings, "auth_lock_threshold", 3)
    monkeypatch.setattr(auth.settings, "auth_lock_minutes", 10)
    email = _email()
    assert _register(client, email).status_code == 201

    for _ in range(3):
        assert (
            client.post(
                f"{PREFIX}/auth/login", json={"email": email, "password": "nope-pass-1"}
            ).status_code
            == 401
        )

    locked = client.post(
        f"{PREFIX}/auth/login", json={"email": email, "password": GOOD_PASSWORD}
    )
    assert locked.status_code == 429
    assert locked.json()["code"] == "account_locked"
    # 即便密码正确也进不去——锁定优先于密码校验
    assert "分钟" in locked.json()["message"]


def test_changing_password_invalidates_old_tokens(client, monkeypatch):
    from app.modules import auth

    monkeypatch.setattr(auth.settings, "allow_registration", True)
    email = _email()
    registered = _register(client, email)
    old_token = registered.json()["token"]
    old_headers = {"Authorization": f"Bearer {old_token}"}
    assert client.get(f"{PREFIX}/auth/me", headers=old_headers).status_code == 200

    # 服务端 second 级精度：确保新时间戳严格大于旧令牌的 iat
    import time

    time.sleep(1.2)

    changed = client.post(
        f"{PREFIX}/auth/password",
        json={"currentPassword": GOOD_PASSWORD, "newPassword": "An0ther-pass-2026"},
        headers=old_headers,
    )
    assert changed.status_code == 200, changed.text
    new_token = changed.json()["token"]
    assert new_token != old_token

    assert client.get(f"{PREFIX}/auth/me", headers=old_headers).status_code == 401
    assert (
        client.get(
            f"{PREFIX}/auth/me", headers={"Authorization": f"Bearer {new_token}"}
        ).status_code
        == 200
    )

    # 新密码可登录，旧密码不行
    assert (
        client.post(
            f"{PREFIX}/auth/login", json={"email": email, "password": "An0ther-pass-2026"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{PREFIX}/auth/login", json={"email": email, "password": GOOD_PASSWORD}
        ).status_code
        == 401
    )


def test_change_password_requires_current_password(client, monkeypatch):
    from app.modules import auth

    monkeypatch.setattr(auth.settings, "allow_registration", True)
    token = _register(client).json()["token"]
    res = client.post(
        f"{PREFIX}/auth/password",
        json={"newPassword": "An0ther-pass-2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
    assert res.json()["code"] == "current_password_required"


def test_disabled_account_cannot_use_existing_token(client, monkeypatch):
    from app.core.db import SessionLocal
    from app import models
    from app.modules import auth

    monkeypatch.setattr(auth.settings, "allow_registration", True)
    body = _register(client).json()
    email = body["user"]["email"]
    headers = {"Authorization": f"Bearer {body['token']}"}

    with SessionLocal() as db:
        user = db.scalar(
            select(models.User).where(models.User.email == email)
        )
        user.is_active = False
        db.commit()

    denied = client.get(f"{PREFIX}/auth/me", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["code"] == "account_disabled"


def test_additive_migration_keeps_existing_users_active(tmp_path, monkeypatch):
    """老库补列时必须把已有行补成「启用」。

    这是真踩过的坑：`is_active` 补列写 `DEFAULT false` 会把所有历史账号
    判成停用（登录直接 403），而新库因为走 create_all 完全看不出来。
    """
    from sqlalchemy import create_engine, text

    from app.core import db as db_module

    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                " id VARCHAR(32) PRIMARY KEY,"
                " auth_subject VARCHAR(190) UNIQUE,"
                " display_name VARCHAR(120),"
                " timezone VARCHAR(64),"
                " created_at TIMESTAMP,"
                " updated_at TIMESTAMP)"
            )
        )
        conn.execute(
            text("INSERT INTO users (id, auth_subject) VALUES ('u-old', 'local-creator')")
        )

    monkeypatch.setattr(db_module, "engine", engine)
    db_module._apply_additive_migrations()

    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT is_active, failed_login_attempts, password_hash "
                "FROM users WHERE id = 'u-old'"
            )
        ).one()
    assert row[0] == 1, "历史账号被补成停用会导致升级后所有人都登不进去"
    assert row[1] == 0
    assert row[2] is None
    engine.dispose()


def test_registration_closed_still_allows_first_password_account(client, monkeypatch):
    """关闭开放注册时，首个密码账号仍必须能创建——否则新部署无路可走。"""
    from app.modules import auth

    monkeypatch.setattr(auth.settings, "allow_registration", False)
    cfg = client.get(f"{PREFIX}/auth/config").json()
    assert cfg["passwordLogin"] is True

    if cfg["bootstrapAvailable"]:
        # 空库：注册能力放行，且确实能建成
        assert cfg["allowRegistration"] is True
        assert _register(client).status_code == 201
    else:
        assert cfg["allowRegistration"] is False
        res = _register(client)
        assert res.status_code == 403
        assert res.json()["code"] == "registration_disabled"
