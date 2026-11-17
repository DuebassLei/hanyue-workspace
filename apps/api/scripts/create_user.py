#!/usr/bin/env python
"""账号管理 CLI：创建账号、重置密码、停用/启用、列出现有账号。

为什么需要它：
- 生产环境通常关掉开放注册（ALLOW_REGISTRATION=false），首个账号得有人手工建；
- 忘记密码时，改密接口需要旧密码，只能从服务端重置；
- 容器内没有交互式终端时，靠 HTTP 接口也无从下手。

用法（在 apps/api 目录下，或用绝对路径调用）：

    python scripts/create_user.py create --email me@example.com
    python scripts/create_user.py attach --subject local-creator --email me@example.com
    python scripts/create_user.py reset-password --email me@example.com
    python scripts/create_user.py list
    python scripts/create_user.py disable --email old@example.com

`attach` 是「把已有的开发态账号转正」：只补邮箱与密码，**user.id 不变**，
所以该账号名下的稿件、素材、发布任务全部保留。从 ALLOW_DEV_LOGIN=true
切到邮箱密码登录时，用它迁移老数据，避免建新账号后「内容全不见了」。

密码优先从交互式提示读取（不进 shell history）；非交互场景用
`--password` 或环境变量 `HANYUE_NEW_PASSWORD`。

数据库由 DATABASE_URL 决定（apps/api/.env 或同名环境变量），与 API 完全一致。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
if (API_DIR / "app" / "models.py").is_file():
    sys.path.insert(0, str(API_DIR))
elif not (Path("/app") / "app" / "models.py").is_file():
    sys.exit("找不到 app 包：请在 apps/api 目录下运行")

from sqlalchemy import func, select  # noqa: E402

from app import models  # noqa: E402,F401  注册全部 ORM 模型
from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal, init_db  # noqa: E402
from app.core.security import hash_password, validate_password  # noqa: E402

_EMAIL_MAX_LENGTH = 180


def _normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def _read_password(given: str | None, *, confirm: bool) -> str:
    if given:
        return given
    env = os.environ.get("HANYUE_NEW_PASSWORD")
    if env:
        return env
    if not sys.stdin.isatty():
        sys.exit("非交互环境请用 --password 或 HANYUE_NEW_PASSWORD 提供密码")
    first = getpass.getpass("密码：")
    if confirm and getpass.getpass("再输一次：") != first:
        sys.exit("两次输入不一致")
    return first


def _find(db, email: str) -> models.User | None:
    return db.scalar(select(models.User).where(func.lower(models.User.email) == email))


def cmd_create(args) -> int:
    email = _normalize_email(args.email)
    if len(email) > _EMAIL_MAX_LENGTH or "@" not in email:
        sys.exit("邮箱格式不合法")
    password = _read_password(args.password, confirm=True)
    problem = validate_password(password)
    if problem:
        sys.exit(problem)

    with SessionLocal() as db:
        if _find(db, email):
            sys.exit(f"{email} 已存在；改密请用 reset-password")
        now = datetime.now(timezone.utc)
        user = models.User(
            auth_subject=f"password:{email}",
            email=email,
            display_name=args.display_name or email.split("@", 1)[0],
            password_hash=hash_password(password),
            password_changed_at=now,
            failed_login_attempts=0,
            is_active=True,
        )
        db.add(user)
        db.commit()
        print(f"已创建账号：{email}（id={user.id}）")
    return 0


def cmd_attach(args) -> int:
    """给已有账号（通常是开发态 subject）补邮箱与密码，id 不变 → 数据不丢。"""
    email = _normalize_email(args.email)
    if len(email) > _EMAIL_MAX_LENGTH or "@" not in email:
        sys.exit("邮箱格式不合法")
    password = _read_password(args.password, confirm=True)
    problem = validate_password(password)
    if problem:
        sys.exit(problem)

    with SessionLocal() as db:
        user = db.scalar(
            select(models.User).where(models.User.auth_subject == args.subject)
        )
        if not user:
            sys.exit(f"找不到 subject：{args.subject}（先 list 看现有账号）")
        if user.email and user.email != email:
            sys.exit(f"该账号已绑定 {user.email}，不能直接换绑")
        other = _find(db, email)
        if other and other.id != user.id:
            sys.exit(f"{email} 已被另一个账号占用")

        user.email = email
        user.password_hash = hash_password(password)
        # 设为当前时间：该账号之前签发的令牌（pwdv=0）会全部失效，强制用新密码登录
        user.password_changed_at = datetime.now(timezone.utc)
        user.failed_login_attempts = 0
        user.locked_until = None
        user.is_active = True
        db.commit()
        print(
            f"已转正：{email} ← subject={args.subject}（id={user.id}，历史内容保留）"
        )
    return 0


def cmd_reset_password(args) -> int:
    email = _normalize_email(args.email)
    password = _read_password(args.password, confirm=True)
    problem = validate_password(password)
    if problem:
        sys.exit(problem)

    with SessionLocal() as db:
        user = _find(db, email)
        if not user:
            sys.exit(f"找不到账号：{email}")
        user.password_hash = hash_password(password)
        # 改 password_changed_at 会让该账号所有旧令牌立即失效
        user.password_changed_at = datetime.now(timezone.utc)
        user.failed_login_attempts = 0
        user.locked_until = None
        db.commit()
        print(f"已重置密码：{email}（旧会话已全部失效）")
    return 0


def cmd_set_active(args, active: bool) -> int:
    email = _normalize_email(args.email)
    with SessionLocal() as db:
        user = _find(db, email)
        if not user:
            sys.exit(f"找不到账号：{email}")
        user.is_active = active
        db.commit()
        print(f"已{'启用' if active else '停用'}：{email}")
    return 0


def cmd_list(_args) -> int:
    with SessionLocal() as db:
        users = db.scalars(select(models.User).order_by(models.User.created_at)).all()
        if not users:
            print("（没有任何账号）")
            return 0
        print(
            f"{'邮箱':<30}{'subject':<28}{'显示名':<12}{'登录':<6}{'状态':<6}最近登录"
        )
        for user in users:
            method = "密码" if user.password_hash else "开发态"
            state = "正常" if user.is_active else "停用"
            last = (
                user.last_login_at.isoformat(sep=" ", timespec="minutes")
                if user.last_login_at
                else "-"
            )
            print(
                f"{(user.email or '-'):<30}{user.auth_subject:<28}{user.display_name:<12}"
                f"{method:<6}{state:<6}{last}"
            )
    return 0


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="账号管理")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="创建邮箱密码账号")
    create.add_argument("--email", required=True)
    create.add_argument("--password", help="不推荐：会留在 shell history")
    create.add_argument("--display-name")
    create.set_defaults(func=cmd_create)

    attach = sub.add_parser(
        "attach", help="给已有账号（如 local-creator）补邮箱密码，保留全部数据"
    )
    attach.add_argument("--subject", required=True)
    attach.add_argument("--email", required=True)
    attach.add_argument("--password", help="不推荐：会留在 shell history")
    attach.set_defaults(func=cmd_attach)

    reset = sub.add_parser("reset-password", help="重置密码（会踢掉全部旧会话）")
    reset.add_argument("--email", required=True)
    reset.add_argument("--password")
    reset.set_defaults(func=cmd_reset_password)

    disable = sub.add_parser("disable", help="停用账号")
    disable.add_argument("--email", required=True)
    disable.set_defaults(func=lambda a: cmd_set_active(a, False))

    enable = sub.add_parser("enable", help="启用账号")
    enable.add_argument("--email", required=True)
    enable.set_defaults(func=lambda a: cmd_set_active(a, True))

    listing = sub.add_parser("list", help="列出账号")
    listing.set_defaults(func=cmd_list)

    args = parser.parse_args()
    print(f"目标数据库：{settings.database_url.split('@')[-1]}")
    init_db()  # 首次在空库上跑时自动建表，省得先启动一次 API
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
