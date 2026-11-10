"""统一错误结构与依赖注入。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from .db import get_db
from .security import decode_token

DbSession = Annotated[Session, Depends(get_db)]


class ApiError(HTTPException):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={
                "code": code,
                "message": message,
                "requestId": uuid.uuid4().hex,
                "retryable": retryable,
            },
        )


def not_found(what: str = "资源") -> ApiError:
    return ApiError("not_found", f"{what}不存在或无权访问", status.HTTP_404_NOT_FOUND)


def forbidden(message: str = "无权执行该操作") -> ApiError:
    return ApiError("forbidden", message, status.HTTP_403_FORBIDDEN)


def conflict(code: str, message: str) -> ApiError:
    return ApiError(code, message, status.HTTP_409_CONFLICT)


def get_current_user(
    db: DbSession, authorization: Annotated[str | None, Header()] = None
) -> models.User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError("unauthorized", "缺少登录会话", status.HTTP_401_UNAUTHORIZED)
    body = decode_token(authorization.split(" ", 1)[1].strip())
    if not body:
        raise ApiError("unauthorized", "登录已过期，请重新登录", status.HTTP_401_UNAUTHORIZED)
    if body.get("jti") and db.get(models.RevokedToken, body["jti"]):
        raise ApiError("unauthorized", "该会话已退出登录，请重新登录", status.HTTP_401_UNAUTHORIZED)
    user = db.get(models.User, body["sub"])
    if not user:
        raise ApiError("unauthorized", "用户不存在", status.HTTP_401_UNAUTHORIZED)
    return user


CurrentUser = Annotated[models.User, Depends(get_current_user)]


def get_optional_user(
    db: DbSession, authorization: Annotated[str | None, Header()] = None
) -> models.User | None:
    """允许匿名访问的接口用：有合法会话就返回用户，否则返回 None。

    注意与 get_current_user 的区别——这里对「令牌过期」不做 401，
    因为签名直链本身就是另一种合法凭据，不能因浏览器残留的过期 token 而拒绝。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    body = decode_token(authorization.split(" ", 1)[1].strip())
    if not body:
        return None
    if body.get("jti") and db.get(models.RevokedToken, body["jti"]):
        return None
    return db.get(models.User, body["sub"])


OptionalUser = Annotated[models.User | None, Depends(get_optional_user)]


def require_workspace(db: Session, user: models.User, workspace_id: str) -> models.Workspace:
    workspace = db.get(models.Workspace, workspace_id)
    if not workspace or workspace.owner_user_id != user.id:
        raise not_found("工作区")
    return workspace


def default_workspace(db: Session, user: models.User) -> models.Workspace:
    workspace = db.scalar(
        select(models.Workspace)
        .where(models.Workspace.owner_user_id == user.id)
        .order_by(models.Workspace.created_at)
    )
    if workspace:
        return workspace
    workspace = models.Workspace(owner_user_id=user.id, name="我的工作区", settings={})
    db.add(workspace)
    db.commit()
    return workspace
