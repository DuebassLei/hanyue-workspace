"""RBAC 接口：我的权限、角色、菜单、用户授权、审计。

权限边界
--------
- `/rbac/me` 任何登录用户可读（前端靠它渲染菜单，不能要求先有权限）
- 其余全部按权限点保护，且**菜单管理与角色管理分开授权**：
  只给「菜单管理」的人能改菜单，不该顺手拿到改角色的能力。
- 超管角色（is_superadmin）不可删除、不可改名、不可被移除最后一个持有者，
  否则系统会进入无人能授权、也无人能恢复的死锁。
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import delete, func, select

from .. import models, schemas
from ..core.config import get_settings
from ..core.deps import ApiError, CurrentUser, DbSession, conflict, not_found
from ..core.rbac import Access, require_permission, resolve_access
from ..core.security import hash_password, validate_password
from ..services import rbac_service

router = APIRouter(tags=["rbac"])

CanRoles = Annotated[Access, require_permission("rbac.role.manage")]
CanMenus = Annotated[Access, require_permission("rbac.menu.manage")]
CanUsers = Annotated[Access, require_permission("rbac.user.manage")]
CanAudit = Annotated[Access, require_permission("rbac.audit.view")]


# ---------------- 我的权限 ----------------


@router.get("/rbac/me", response_model=schemas.RbacMeOut)
def my_rbac(user: CurrentUser, db: DbSession) -> schemas.RbacMeOut:
    access = resolve_access(db, user)
    menus = rbac_service.user_menu_tree(db, access)
    return schemas.RbacMeOut(
        is_superadmin=access.is_superadmin,
        role_keys=list(access.role_keys),
        permissions=sorted(access.permissions),
        menus=[schemas.NavGroupOut.model_validate(item) for item in menus],
    )


@router.get("/rbac/permissions", response_model=list[schemas.PermissionOut])
def list_permissions(
    access: Annotated[Access, require_permission("rbac.role.manage", "rbac.menu.manage", mode="any")],
    db: DbSession,
) -> list[schemas.PermissionOut]:
    # 直接读库而不是读常量：库里可能还留着代码中已删除、但角色仍绑定的权限点，
    # 后台要能把它们显示出来并清理，不能让它们凭空消失。
    rows = db.scalars(
        select(models.Permission).order_by(models.Permission.module, models.Permission.sort)
    ).all()
    return [
        schemas.PermissionOut(code=row.code, name=row.name, module=row.module, sort=row.sort)
        for row in rows
    ]


# ---------------- 角色 ----------------


def _role_out(db, role: models.Role) -> schemas.RoleOut:
    codes = db.scalars(
        select(models.RolePermission.permission_code).where(
            models.RolePermission.role_id == role.id
        )
    ).all()
    menu_ids = db.scalars(
        select(models.RoleMenu.menu_id).where(models.RoleMenu.role_id == role.id)
    ).all()
    user_count = db.scalar(
        select(func.count())
        .select_from(models.UserRole)
        .where(models.UserRole.role_id == role.id)
    )
    return schemas.RoleOut(
        id=role.id,
        role_key=role.role_key,
        name=role.name,
        description=role.description,
        is_superadmin=role.is_superadmin,
        is_system=role.is_system,
        sort=role.sort,
        permission_codes=sorted(codes),
        menu_ids=list(menu_ids),
        user_count=int(user_count or 0),
    )


@router.get("/rbac/roles", response_model=list[schemas.RoleOut])
def list_roles(
    access: Annotated[Access, require_permission("rbac.role.manage", "rbac.user.manage", mode="any")],
    db: DbSession,
) -> list[schemas.RoleOut]:
    roles = db.scalars(select(models.Role).order_by(models.Role.sort, models.Role.created_at)).all()
    return [_role_out(db, role) for role in roles]


@router.post("/rbac/roles", response_model=schemas.RoleOut, status_code=201)
def create_role(
    payload: schemas.RoleIn, access: CanRoles, user: CurrentUser, db: DbSession
) -> schemas.RoleOut:
    if db.scalar(select(models.Role.id).where(models.Role.role_key == payload.role_key)):
        raise conflict("role_key_exists", f"角色标识 {payload.role_key} 已存在")
    role = models.Role(
        role_key=payload.role_key,
        name=payload.name,
        description=payload.description,
        sort=payload.sort,
        is_system=False,
        is_superadmin=False,
    )
    db.add(role)
    db.flush()

    codes = payload.permission_codes or []
    _validate_permissions(db, codes)
    for code in codes:
        db.add(models.RolePermission(role_id=role.id, permission_code=code))

    # 新角色默认绑定全部菜单：不留「有权限但看不见任何菜单」这种反直觉状态
    for menu_id in db.scalars(select(models.Menu.id)).all():
        db.add(models.RoleMenu(role_id=role.id, menu_id=menu_id))

    rbac_service.audit(
        db,
        actor_user_id=user.id,
        action="role.create",
        target_type="role",
        target_id=role.id,
        detail={"roleKey": role.role_key, "permissions": codes},
    )
    db.commit()
    return _role_out(db, role)


@router.patch("/rbac/roles/{role_id}", response_model=schemas.RoleOut)
def update_role(
    role_id: str, payload: schemas.RolePatch, access: CanRoles, user: CurrentUser, db: DbSession
) -> schemas.RoleOut:
    role = db.get(models.Role, role_id)
    if not role:
        raise not_found("角色")
    data = payload.model_dump(exclude_unset=True)
    if role.is_superadmin and "name" in data and data["name"] != role.name:
        raise ApiError("superadmin_locked", "超管角色不可改名", 409)
    changed = {key: value for key, value in data.items() if getattr(role, key) != value}
    for key, value in changed.items():
        setattr(role, key, value)
    if changed:
        rbac_service.audit(
            db,
            actor_user_id=user.id,
            action="role.update",
            target_type="role",
            target_id=role.id,
            detail={"changes": changed},
        )
    db.commit()
    return _role_out(db, role)


@router.delete("/rbac/roles/{role_id}", status_code=204)
def delete_role(role_id: str, access: CanRoles, user: CurrentUser, db: DbSession) -> None:
    role = db.get(models.Role, role_id)
    if not role:
        raise not_found("角色")
    if role.is_system or role.is_superadmin:
        raise ApiError("role_protected", "内置角色不可删除，请改为调整其权限", 409)
    user_count = db.scalar(
        select(func.count())
        .select_from(models.UserRole)
        .where(models.UserRole.role_id == role.id)
    )
    if user_count:
        raise conflict("role_in_use", f"还有 {user_count} 个用户属于该角色，请先转移")
    rbac_service.audit(
        db,
        actor_user_id=user.id,
        action="role.delete",
        target_type="role",
        target_id=role.id,
        detail={"roleKey": role.role_key},
    )
    db.delete(role)
    db.commit()


@router.put("/rbac/roles/{role_id}/permissions", response_model=schemas.RoleOut)
def set_role_permissions(
    role_id: str,
    payload: schemas.RolePermissionIn,
    access: CanRoles,
    user: CurrentUser,
    db: DbSession,
) -> schemas.RoleOut:
    role = db.get(models.Role, role_id)
    if not role:
        raise not_found("角色")
    _validate_permissions(db, payload.codes)
    db.execute(delete(models.RolePermission).where(models.RolePermission.role_id == role.id))
    for code in dict.fromkeys(payload.codes):
        db.add(models.RolePermission(role_id=role.id, permission_code=code))
    rbac_service.audit(
        db,
        actor_user_id=user.id,
        action="role.permissions.set",
        target_type="role",
        target_id=role.id,
        detail={"permissions": payload.codes},
    )
    db.commit()
    return _role_out(db, role)


@router.put("/rbac/roles/{role_id}/menus", response_model=schemas.RoleOut)
def set_role_menus(
    role_id: str, payload: schemas.RoleMenuIn, access: CanMenus, user: CurrentUser, db: DbSession
) -> schemas.RoleOut:
    role = db.get(models.Role, role_id)
    if not role:
        raise not_found("角色")
    valid = set(db.scalars(select(models.Menu.id).where(models.Menu.id.in_(payload.menu_ids))).all())
    unknown = set(payload.menu_ids) - valid
    if unknown:
        raise ApiError("invalid_menu", f"菜单不存在：{'、'.join(sorted(unknown))}", 400)
    db.execute(delete(models.RoleMenu).where(models.RoleMenu.role_id == role.id))
    for menu_id in dict.fromkeys(payload.menu_ids):
        db.add(models.RoleMenu(role_id=role.id, menu_id=menu_id))
    rbac_service.audit(
        db,
        actor_user_id=user.id,
        action="role.menus.set",
        target_type="role",
        target_id=role.id,
        detail={"menuCount": len(payload.menu_ids)},
    )
    db.commit()
    return _role_out(db, role)


def _validate_permissions(db, codes: list[str]) -> None:
    if not codes:
        return
    known = set(db.scalars(select(models.Permission.code).where(models.Permission.code.in_(codes))).all())
    unknown = [code for code in codes if code not in known]
    if unknown:
        raise ApiError("invalid_permission", f"未知权限点：{'、'.join(unknown)}", 400)


# ---------------- 菜单 ----------------


@router.get("/rbac/menus", response_model=list[schemas.MenuNodeOut])
def list_menus(access: CanMenus, db: DbSession) -> list[schemas.MenuNodeOut]:
    return [schemas.MenuNodeOut.model_validate(item) for item in rbac_service.admin_menu_tree(db)]


@router.post("/rbac/menus", response_model=schemas.MenuNodeOut, status_code=201)
def create_menu(
    payload: schemas.MenuIn, access: CanMenus, user: CurrentUser, db: DbSession
) -> schemas.MenuNodeOut:
    if db.scalar(select(models.Menu.id).where(models.Menu.menu_key == payload.key)):
        raise conflict("menu_key_exists", f"菜单标识 {payload.key} 已存在")
    parent = _require_parent(db, payload.parent_id)
    _validate_menu_permission(db, payload.permission)
    if payload.kind == "group" and parent is not None:
        raise ApiError("invalid_menu", "分组只能建在顶级", 400)
    if payload.kind != "group" and parent is None:
        raise ApiError("invalid_menu", "页面菜单必须挂在某个分组下", 400)

    menu = models.Menu(
        parent_id=parent.id if parent else None,
        menu_key=payload.key,
        label=payload.label,
        short_label=payload.short,
        icon=payload.icon,
        kind=payload.kind,
        permission_code=payload.permission,
        sort=payload.sort,
        visible=payload.visible,
    )
    db.add(menu)
    db.flush()
    rbac_service.rebuild_paths(db)
    # 新菜单默认对全部角色可见：否则管理员建完菜单却「谁都看不见」，像个 bug
    for role_id in db.scalars(select(models.Role.id)).all():
        db.add(models.RoleMenu(role_id=role_id, menu_id=menu.id))
    rbac_service.audit(
        db,
        actor_user_id=user.id,
        action="menu.create",
        target_type="menu",
        target_id=menu.id,
        detail={"key": menu.menu_key, "permission": menu.permission_code},
    )
    db.commit()
    db.refresh(menu)
    return _menu_node(menu)


@router.patch("/rbac/menus/{menu_id}", response_model=schemas.MenuNodeOut)
def update_menu(
    menu_id: str, payload: schemas.MenuPatch, access: CanMenus, user: CurrentUser, db: DbSession
) -> schemas.MenuNodeOut:
    menu = db.get(models.Menu, menu_id)
    if not menu:
        raise not_found("菜单")
    data = payload.model_dump(exclude_unset=True)

    # 父级单独处理：要防自环、防移到自己的子树下，且父级必须是分组
    if "parent_id" in payload.model_fields_set:
        new_parent_id = payload.parent_id
        if new_parent_id == menu.id:
            raise ApiError("invalid_menu", "菜单不能把自己设为父级", 400)
        if new_parent_id and _is_descendant(db, menu.id, new_parent_id):
            raise ApiError("invalid_menu", "不能把菜单移动到自己的子级下", 400)
        parent = _require_parent(db, new_parent_id)
        menu.parent_id = parent.id if parent else None
        data.pop("parent_id", None)

    if "permission" in data:
        _validate_menu_permission(db, data["permission"])
        if menu.kind == "group" and data["permission"]:
            raise ApiError("invalid_menu", "分组不挂权限点，请给子菜单挂", 400)

    if "kind" in data and data["kind"] != menu.kind:
        has_children = db.scalar(
            select(func.count()).select_from(models.Menu).where(models.Menu.parent_id == menu.id)
        )
        if has_children:
            raise ApiError("invalid_menu", "该菜单下还有子菜单，不能改类型", 409)

    mapping = {
        "label": "label",
        "short": "short_label",
        "icon": "icon",
        "kind": "kind",
        "permission": "permission_code",
        "sort": "sort",
        "visible": "visible",
    }
    changed: dict = {}
    for field, column in mapping.items():
        if field in data:
            setattr(menu, column, data[field])
            changed[field] = data[field]

    rbac_service.rebuild_paths(db)
    rbac_service.audit(
        db,
        actor_user_id=user.id,
        action="menu.update",
        target_type="menu",
        target_id=menu.id,
        detail={"changes": changed},
    )
    db.commit()
    db.refresh(menu)
    return _menu_node(menu)


@router.delete("/rbac/menus/{menu_id}", status_code=204)
def delete_menu(menu_id: str, access: CanMenus, user: CurrentUser, db: DbSession) -> None:
    menu = db.get(models.Menu, menu_id)
    if not menu:
        raise not_found("菜单")
    children = db.scalar(
        select(func.count()).select_from(models.Menu).where(models.Menu.parent_id == menu.id)
    )
    if children:
        raise conflict("menu_has_children", f"该菜单下还有 {children} 个子菜单，请先删除或移走")
    rbac_service.audit(
        db,
        actor_user_id=user.id,
        action="menu.delete",
        target_type="menu",
        target_id=menu.id,
        detail={"key": menu.menu_key},
    )
    db.delete(menu)
    db.commit()


def _menu_node(menu: models.Menu, children: list[dict] | None = None) -> schemas.MenuNodeOut:
    return schemas.MenuNodeOut.model_validate(
        {
            "id": menu.id,
            "parentId": menu.parent_id,
            "key": menu.menu_key,
            "label": menu.label,
            "short": menu.short_label,
            "icon": menu.icon,
            "path": menu.path,
            "kind": menu.kind,
            "permission": menu.permission_code,
            "sort": menu.sort,
            "visible": menu.visible,
            "children": children or [],
        }
    )


def _require_parent(db, parent_id: str | None) -> models.Menu | None:
    if not parent_id:
        return None
    parent = db.get(models.Menu, parent_id)
    if not parent:
        raise not_found("父级菜单")
    if parent.kind != "group":
        raise ApiError("invalid_menu", "父级必须是分组", 400)
    return parent


def _validate_menu_permission(db, code: str | None) -> None:
    if not code:
        return
    if not db.scalar(select(models.Permission.code).where(models.Permission.code == code)):
        raise ApiError("invalid_permission", f"未知权限点：{code}", 400)


def _is_descendant(db, ancestor_id: str, candidate_id: str) -> bool:
    """candidate 是否在 ancestor 的子树里。菜单最多两层，循环查父级即可。"""
    current = db.get(models.Menu, candidate_id)
    guard = 0
    while current is not None and guard < 32:
        if current.parent_id == ancestor_id:
            return True
        current = db.get(models.Menu, current.parent_id) if current.parent_id else None
        guard += 1
    return False


# ---------------- 用户管理 ----------------

# 与 auth.py 同一把尺子：只挡明显不合法的地址，不做 RFC 全量校验。
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_EMAIL_MAX_LENGTH = 180


def _user_out(user: models.User, role_ids: list[str], role_keys: list[str]) -> schemas.UserRolesOut:
    return schemas.UserRolesOut(
        id=user.id,
        display_name=user.display_name,
        email=user.email,
        is_active=user.is_active,
        has_password=bool(user.password_hash),
        role_ids=role_ids,
        role_keys=role_keys,
    )


def _roles_of(db, user_ids: list[str]) -> dict[str, tuple[list[str], list[str]]]:
    if not user_ids:
        return {}
    rows = db.execute(
        select(models.UserRole.user_id, models.Role.id, models.Role.role_key)
        .join(models.Role, models.Role.id == models.UserRole.role_id)
        .where(models.UserRole.user_id.in_(user_ids))
    ).all()
    by_user: dict[str, tuple[list[str], list[str]]] = {}
    for user_id, role_id, role_key in rows:
        cols = by_user.setdefault(user_id, ([], []))
        cols[0].append(role_id)
        cols[1].append(role_key)
    return by_user


def _generate_password() -> str:
    """一次性初始密码：通过现有弱口令校验，且不含容易看错的字符。"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    min_length = max(12, get_settings().password_min_length)
    for _ in range(8):
        candidate = "".join(secrets.choice(alphabet) for _ in range(min_length))
        if validate_password(candidate, min_length=get_settings().password_min_length) is None:
            return candidate
    raise ApiError("password_generation_failed", "生成初始密码失败，请重试", 500)


def _active_superadmin_count(db, *, exclude_user_id: str | None = None) -> int:
    """启用中的超管人数。停用最后一个启用超管会把系统锁死。"""
    query = (
        select(func.count(func.distinct(models.UserRole.user_id)))
        .select_from(models.UserRole)
        .join(models.Role, models.Role.id == models.UserRole.role_id)
        .join(models.User, models.User.id == models.UserRole.user_id)
        .where(models.Role.is_superadmin.is_(True), models.User.is_active.is_(True))
    )
    if exclude_user_id:
        query = query.where(models.UserRole.user_id != exclude_user_id)
    return int(db.scalar(query) or 0)


@router.get("/rbac/users", response_model=list[schemas.UserRolesOut])
def list_users(
    access: CanUsers,
    db: DbSession,
    q: str = Query(default="", max_length=120),
    status: str = Query(default="active", pattern="^(active|disabled|all)$"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[schemas.UserRolesOut]:
    query = select(models.User).order_by(models.User.created_at.desc()).limit(limit)
    if status == "active":
        query = query.where(models.User.is_active.is_(True))
    elif status == "disabled":
        query = query.where(models.User.is_active.is_(False))
    if q.strip():
        keyword = f"%{q.strip().lower()}%"
        query = query.where(
            func.lower(models.User.email).like(keyword)
            | func.lower(models.User.display_name).like(keyword)
        )
    users = db.scalars(query).all()
    if not users:
        return []

    by_user = _roles_of(db, [user.id for user in users])
    return [
        _user_out(user, *by_user.get(user.id, ([], [])))
        for user in users
    ]


@router.post("/rbac/users", response_model=schemas.ManagedUserCreatedOut, status_code=201)
def create_user(
    payload: schemas.ManagedUserIn, access: CanUsers, current: CurrentUser, db: DbSession
) -> schemas.ManagedUserCreatedOut:
    email = payload.email.strip().lower()
    if len(email) > _EMAIL_MAX_LENGTH or not _EMAIL_PATTERN.match(email):
        raise ApiError("invalid_email", "请输入有效的邮箱地址")
    if db.scalar(select(models.User.id).where(func.lower(models.User.email) == email)):
        raise conflict("email_taken", "该邮箱已有账号")

    roles = db.scalars(select(models.Role).where(models.Role.id.in_(payload.role_ids))).all()
    unknown = set(payload.role_ids) - {role.id for role in roles}
    if unknown:
        raise ApiError("invalid_role", f"角色不存在：{'、'.join(sorted(unknown))}", 400)
    password = _generate_password()
    now = datetime.now(timezone.utc)
    name = (payload.display_name or "").strip() or email.split("@", 1)[0]
    user = models.User(
        auth_subject=f"password:{email}",
        email=email,
        display_name=name,
        password_hash=hash_password(password),
        password_changed_at=now,
        failed_login_attempts=0,
        is_active=True,
    )
    db.add(user)
    db.flush()
    for role in roles:
        db.add(models.UserRole(user_id=user.id, role_id=role.id))
    rbac_service.audit(
        db,
        actor_user_id=current.id,
        action="user.create",
        target_type="user",
        target_id=user.id,
        detail={"email": email, "roleKeys": [role.role_key for role in roles]},
    )
    db.commit()
    return schemas.ManagedUserCreatedOut(
        user=_user_out(user, [role.id for role in roles], [role.role_key for role in roles]),
        initial_password=password,
    )


@router.patch("/rbac/users/{user_id}", response_model=schemas.UserRolesOut)
def update_user(
    user_id: str,
    payload: schemas.ManagedUserPatch,
    access: CanUsers,
    current: CurrentUser,
    db: DbSession,
) -> schemas.UserRolesOut:
    target = db.get(models.User, user_id)
    if not target:
        raise not_found("用户")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise ApiError("empty_patch", "没有需要更新的字段")

    if "display_name" in data:
        name = (data["display_name"] or "").strip()
        if not name:
            raise ApiError("invalid_display_name", "昵称不能为空")
        target.display_name = name

    if "is_active" in data and data["is_active"] is not target.is_active:
        turning_off = data["is_active"] is False
        if turning_off and target.id == current.id:
            raise ApiError("cannot_disable_self", "不能停用自己的账号", 409)
        if turning_off and _active_superadmin_count(db, exclude_user_id=target.id) == 0:
            raise ApiError("last_superadmin", "这是最后一个启用中的超级管理员，不能停用", 409)
        target.is_active = bool(data["is_active"])

    rbac_service.audit(
        db,
        actor_user_id=current.id,
        action="user.update",
        target_type="user",
        target_id=target.id,
        detail={"changes": data},
    )
    db.commit()
    roles = _roles_of(db, [target.id]).get(target.id, ([], []))
    return _user_out(target, *roles)


@router.post("/rbac/users/{user_id}/reset-password", response_model=schemas.ManagedUserCreatedOut)
def reset_user_password(
    user_id: str, access: CanUsers, current: CurrentUser, db: DbSession
) -> schemas.ManagedUserCreatedOut:
    """重置为一次性随机密码。改 password_changed_at 后，对方已发出的令牌立即失效。"""
    target = db.get(models.User, user_id)
    if not target:
        raise not_found("用户")
    if target.id == current.id:
        raise ApiError("cannot_reset_self", "不能在这里重置自己的密码，请到账号设置里修改", 409)
    if not target.email:
        raise ApiError("no_email", "该账号没有邮箱，无法设置密码登录", 409)

    password = _generate_password()
    target.password_hash = hash_password(password)
    target.password_changed_at = datetime.now(timezone.utc)
    target.failed_login_attempts = 0
    target.locked_until = None
    # 开发态账号补上密码后，身份键保持不变：名下稿件不会因此换人
    rbac_service.audit(
        db,
        actor_user_id=current.id,
        action="user.password.reset",
        target_type="user",
        target_id=target.id,
        detail={"email": target.email},
    )
    db.commit()
    roles = _roles_of(db, [target.id]).get(target.id, ([], []))
    return schemas.ManagedUserCreatedOut(
        user=_user_out(target, *roles),
        initial_password=password,
    )


def _load_roles(db, role_ids: list[str]) -> list[models.Role]:
    roles = db.scalars(select(models.Role).where(models.Role.id.in_(role_ids))).all()
    unknown = set(role_ids) - {role.id for role in roles}
    if unknown:
        raise ApiError("invalid_role", f"角色不存在：{'、'.join(sorted(unknown))}", 400)
    if not roles:
        raise ApiError("role_required", "至少选择一个角色", 400)
    return list(roles)


@router.put("/rbac/users/{user_id}/roles", response_model=schemas.UserRolesOut)
def set_user_roles(
    user_id: str,
    payload: schemas.UserRolesIn,
    access: CanUsers,
    current: CurrentUser,
    db: DbSession,
) -> schemas.UserRolesOut:
    target = db.get(models.User, user_id)
    if not target:
        raise not_found("用户")

    roles = db.scalars(select(models.Role).where(models.Role.id.in_(payload.role_ids))).all()
    unknown = set(payload.role_ids) - {role.id for role in roles}
    if unknown:
        raise ApiError("invalid_role", f"角色不存在：{'、'.join(sorted(unknown))}", 400)

    # 不允许把自己变成「没有超管权限」的人，除非还有别的超管
    losing_super = target.id == current.id and not any(role.is_superadmin for role in roles)
    if losing_super and not _other_superadmin_exists(db, target.id):
        raise ApiError("last_superadmin", "你是最后一个超级管理员，不能移除自己的权限", 409)

    db.execute(delete(models.UserRole).where(models.UserRole.user_id == target.id))
    for role_id in dict.fromkeys(payload.role_ids):
        db.add(models.UserRole(user_id=target.id, role_id=role_id))

    rbac_service.audit(
        db,
        actor_user_id=current.id,
        action="user.roles.set",
        target_type="user",
        target_id=target.id,
        detail={"roleKeys": [role.role_key for role in roles]},
    )
    db.commit()
    return _user_out(
        target,
        [role.id for role in roles],
        [role.role_key for role in roles],
    )


def _other_superadmin_exists(db, exclude_user_id: str) -> bool:
    row = db.scalar(
        select(models.UserRole.user_id)
        .join(models.Role, models.Role.id == models.UserRole.role_id)
        .where(models.Role.is_superadmin.is_(True), models.UserRole.user_id != exclude_user_id)
        .limit(1)
    )
    return row is not None


# ---------------- 审计 ----------------


@router.get("/rbac/audits", response_model=list[schemas.RbacAuditOut])
def list_audits(
    access: CanAudit,
    db: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[schemas.RbacAuditOut]:
    rows = db.execute(
        select(models.RbacAuditLog, models.User.display_name)
        .outerjoin(models.User, models.User.id == models.RbacAuditLog.actor_user_id)
        .order_by(models.RbacAuditLog.created_at.desc())
        .limit(limit)
    ).all()
    return [
        schemas.RbacAuditOut(
            id=log.id,
            actor_user_id=log.actor_user_id,
            actor_name=actor_name,
            action=log.action,
            target_type=log.target_type,
            target_id=log.target_id,
            detail=log.detail or {},
            created_at=log.created_at,
        )
        for log, actor_name in rows
    ]
