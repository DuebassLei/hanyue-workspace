"""RBAC 播种与菜单树组装。

播种是**幂等且只增不减**的：
- 新的权限点 / 内置角色 / 菜单会被补上
- 管理员在后台删掉的权限或菜单绑定**不会被重启复活**（只对「本次新建」的对象套默认值）

这条规则很重要。否则每次发版重启，运营在后台调整的权限都会被悄悄改回去，
而现象是「昨天还好好的，今天权限又变了」——最难查的那类 bug。

唯一的例外是 `_run_migrations`：有些变更（比如把某个权限从内置角色上**收窄**）
只能改一次，否则会和上一条规则冲突。这类变更写成「执行过就不再执行」的迁移，
标志位落在 `app_settings`，管理员之后手工改回去不会被覆盖。
"""

from __future__ import annotations

from sqlalchemy import delete, select

from .. import models
from ..core.config import get_settings
from ..core.rbac import (
    DEFAULT_ROLE_KEY,
    DEFAULT_ROLE_MATRIX,
    PERMISSION_CATALOG,
    ROLE_META,
    SUPERADMIN_ROLE_KEY,
    Access,
)

# 菜单种子：(key, 名称, 简称, 图标键, 权限点, 子菜单)
# page 类型的 key 必须与前端 router.js 的路由 name 一致，否则点击无反应。
MENU_SEED: tuple[tuple[str, str, str, str, str | None, tuple], ...] = (
    (
        "create",
        "创作",
        "",
        "create",
        None,
        (
            ("overview", "创作总览", "总览", "overview", "overview.view"),
            ("hot-topics", "热点选题", "热点", "hot-topics", "hot_topics.view"),
            ("calendar", "内容日历", "日历", "calendar", "calendar.view"),
        ),
    ),
    (
        "workspace",
        "工作区",
        "",
        "workspace",
        None,
        (
            ("studio", "创作空间", "创作", "studio", "studio.use"),
            ("articles", "文章管理", "文章", "articles", "studio.use"),
            ("cards", "图文卡片", "卡片", "cards", "cards.use"),
            ("images", "图片生成", "图片", "images", "images.use"),
            ("stickers", "贴图创作", "贴图", "stickers", "stickers.use"),
            ("storyboards", "漫画分镜", "分镜", "storyboards", "storyboards.use"),
            ("videos", "视频创作", "视频", "videos", "videos.use"),
            ("characters", "角色库", "角色", "characters", "characters.use"),
            ("novels", "小说创作", "小说", "novels", "novels.use"),
            ("prompts", "提示词库", "提示词", "prompts", "prompts.use"),
            ("assets", "素材图库", "图库", "assets", "assets.use"),
        ),
    ),
    (
        "ops",
        "运营",
        "",
        "ops",
        None,
        (
            ("publishing", "发布管理", "发布", "publishing", "publishing.view"),
            ("wechat-layout", "微信排版", "排版", "wechat-layout", "wechat_layout.use"),
            ("schedules", "定时任务", "定时", "schedules", "schedules.use"),
        ),
    ),
    (
        "rbac",
        "权限",
        "",
        "system",
        None,
        (
            ("users", "用户管理", "用户", "characters", "rbac.user.manage"),
            ("roles", "角色管理", "角色", "settings", "rbac.role.manage"),
            ("menus", "菜单管理", "菜单", "logs", "rbac.menu.manage"),
        ),
    ),
    (
        "system",
        "系统",
        "",
        "system",
        None,
        (
            ("models", "模型管理", "模型", "models", "models.view"),
            ("logs", "系统日志", "日志", "logs", "logs.view"),
            ("settings", "设置", "我的", "settings", "settings.view"),
        ),
    ),
)


# 菜单权限码的历史改名：老库里已经落成旧值的，启动时校正为新值。
# 只在「当前值恰好等于旧值」时改写，管理员手工指定过的权限码不会被覆盖。
MENU_PERMISSION_RENAMES: dict[str, tuple[str, str]] = {
    # 模型页从「管理」降为「查看」：页面本身只要求能看见，写操作在页内另行判定
    "models": ("models.manage", "models.view"),
    # 发布页同理：进页只要能看到记录，推送动作在页内判 publishing.use
    "publishing": ("publishing.use", "publishing.view"),
}


def _seed_permissions(db) -> list[str]:
    """补权限点，返回本次新增的 code。

    返回新增列表是为了给**存量内置角色**补默认授权：新权限点如果只进目录不进角色，
    老库里的运营会「有菜单但点就 403」，只能靠人去后台逐个补勾。
    """
    existing = set(db.scalars(select(models.Permission.code)).all())
    created: list[str] = []
    for code, name, module, sort in PERMISSION_CATALOG:
        if code in existing:
            if code == "rbac.user.manage":
                row = db.get(models.Permission, code)
                if row is not None and row.name == "用户授权":
                    row.name = name
            continue
        db.add(models.Permission(code=code, name=name, module=module, sort=sort))
        created.append(code)
    db.flush()
    return created


def _seed_roles(db) -> list[str]:
    existing = set(db.scalars(select(models.Role.role_key)).all())
    created: list[str] = []
    for role_key, (name, description, is_system, sort) in ROLE_META.items():
        if role_key in existing:
            continue
        db.add(
            models.Role(
                role_key=role_key,
                name=name,
                description=description,
                is_system=is_system,
                is_superadmin=role_key == SUPERADMIN_ROLE_KEY,
                sort=sort,
            )
        )
        created.append(role_key)
    db.flush()
    return created


def _seed_menus(db) -> list[str]:
    """按种子建菜单，返回本次新建的 menu.id（用于给角色补默认绑定）。"""
    created: list[str] = []
    for group_key, group_label, group_short, icon, permission, children in MENU_SEED:
        group = db.scalar(select(models.Menu).where(models.Menu.menu_key == group_key))
        if group is None:
            group = models.Menu(
                menu_key=group_key,
                label=group_label,
                short_label=group_short,
                icon=icon,
                path=f"/{group_key}",
                kind="group",
                permission_code=permission,
                sort=_next_sort(db, None),
            )
            db.add(group)
            db.flush()
            created.append(group.id)
        for index, (key, label, short, item_icon, item_permission) in enumerate(children):
            existing = db.scalar(select(models.Menu).where(models.Menu.menu_key == key))
            if existing is not None:
                _rename_menu_permission(existing, item_permission)
                if key == "users" and existing.label == "用户授权":
                    existing.label = label
                    existing.short_label = short
                continue
            menu = models.Menu(
                parent_id=group.id,
                menu_key=key,
                label=label,
                short_label=short,
                icon=item_icon,
                path=f"{group.path}/{key}",
                kind="page",
                permission_code=item_permission,
                sort=(index + 1) * 10,
            )
            db.add(menu)
            db.flush()
            created.append(menu.id)
    return created


def _rename_menu_permission(menu: models.Menu, seeded_permission: str | None) -> None:
    rule = MENU_PERMISSION_RENAMES.get(menu.menu_key)
    if not rule or seeded_permission is None:
        return
    old, new = rule
    if menu.permission_code == old and seeded_permission == new:
        menu.permission_code = new


def _next_sort(db, parent_id: str | None) -> int:
    query = select(models.Menu.sort).where(models.Menu.parent_id.is_(None))
    if parent_id:
        query = select(models.Menu.sort).where(models.Menu.parent_id == parent_id)
    values = db.scalars(query).all()
    return (max(values) + 10) if values else 10


def _seed_role_permissions(db, new_role_keys: list[str]) -> None:
    """只给**本次新建**的内置角色套默认权限矩阵。

    管理员之后在后台取消的权限不会在重启时被补回来——这是有意为之。
    """
    if not new_role_keys:
        return
    for role_key in new_role_keys:
        codes = DEFAULT_ROLE_MATRIX.get(role_key)
        if not codes:
            continue
        role_id = db.scalar(select(models.Role.id).where(models.Role.role_key == role_key))
        for code in codes:
            db.add(models.RolePermission(role_id=role_id, permission_code=code))
    db.flush()


def _grant_new_permissions_to_builtin_roles(db, new_codes: list[str]) -> None:
    """把**本次新增**的权限点按默认矩阵补给存量内置角色。

    没有这一步，新增权限点只进目录不进角色：老库里的运营会看到模型菜单，
    点进去却被 403——这种「有入口没权限」最难自查。

    只补本次新增的 code：管理员此前在某个内置角色上取消过的老权限不会被复活
    （那些 code 早就存在，不在 new_codes 里）。自定义角色一律不动。
    """
    if not new_codes:
        return
    fresh = set(new_codes)
    for role_key in ROLE_META:
        codes = DEFAULT_ROLE_MATRIX.get(role_key)
        if not codes:
            continue
        grant = [code for code in codes if code in fresh]
        if not grant:
            continue
        role_id = db.scalar(select(models.Role.id).where(models.Role.role_key == role_key))
        if not role_id:
            continue
        held = set(
            db.scalars(
                select(models.RolePermission.permission_code).where(
                    models.RolePermission.role_id == role_id
                )
            ).all()
        )
        for code in grant:
            if code not in held:
                db.add(models.RolePermission(role_id=role_id, permission_code=code))
    db.flush()


def _bind_new_menus_to_all_roles(db, new_menu_ids: list[str]) -> None:
    """新菜单默认对所有已有角色可见；已存在的菜单不受影响（保留后台的隐藏决定）。"""
    if not new_menu_ids:
        return
    role_ids = db.scalars(select(models.Role.id)).all()
    for role_id in role_ids:
        for menu_id in new_menu_ids:
            db.add(models.RoleMenu(role_id=role_id, menu_id=menu_id))
    db.flush()


def assign_default_role(db, user_id: str, role_key: str = DEFAULT_ROLE_KEY) -> None:
    """新注册用户立刻绑定默认角色。

    播种只在启动时跑，注册发生在运行期；少了这一步，新用户会拿到空权限集，
    登录后看到的是一个空侧栏——比报错更难排查。
    """
    if db.scalar(select(models.UserRole.user_id).where(models.UserRole.user_id == user_id)):
        return
    role_id = db.scalar(select(models.Role.id).where(models.Role.role_key == role_key))
    if not role_id:
        return
    db.add(models.UserRole(user_id=user_id, role_id=role_id))
    db.flush()


def _ensure_user_default_role(db) -> None:
    """没有任何角色的用户补默认角色，避免播种后把人锁在门外。"""
    unassigned = db.scalars(
        select(models.User.id)
        .outerjoin(models.UserRole, models.UserRole.user_id == models.User.id)
        .where(models.UserRole.user_id.is_(None))
    ).all()
    if not unassigned:
        return
    role_id = db.scalar(
        select(models.Role.id).where(models.Role.role_key == DEFAULT_ROLE_KEY)
    )
    if not role_id:
        return
    for user_id in unassigned:
        db.add(models.UserRole(user_id=user_id, role_id=role_id))
    db.flush()


def _ensure_first_superadmin(db) -> None:
    """库里还没有超管时，把最早注册的账号提升为超管。

    没有这一步，全新部署会陷入「没人能进后台授权 → 所以永远没人能进后台」。
    """
    if not get_settings().bootstrap_first_user_superadmin:
        return
    has_super = db.scalar(
        select(models.UserRole.user_id)
        .join(models.Role, models.Role.id == models.UserRole.role_id)
        .where(models.Role.is_superadmin.is_(True))
        .limit(1)
    )
    if has_super:
        return
    first_user = db.scalar(
        select(models.User).where(models.User.is_active.is_(True)).order_by(models.User.created_at)
    )
    role_id = db.scalar(
        select(models.Role.id).where(models.Role.role_key == SUPERADMIN_ROLE_KEY)
    )
    if not (first_user and role_id):
        return
    db.add(models.UserRole(user_id=first_user.id, role_id=role_id))
    db.flush()


# 一次性数据迁移：同一个 key 下记录已执行过的迁移 id。
# 只改内置角色，自定义角色一律不碰——它们完全归管理员所有。
_MIGRATION_SETTING_KEY = "rbac.migrations"

# 2026-11：模型权限拆成 view / manage 后，运营从「可改密钥」收窄为「只能看」。
# 老库里的运营是旧矩阵发下来的，光靠补新权限点收不回来。
MIGRATION_MODEL_READ_SPLIT = "2026-11.models-read-split"


def _run_migrations(db) -> None:
    setting = db.get(models.AppSetting, _MIGRATION_SETTING_KEY)
    done = set((setting.value or {}).get("done", [])) if setting else set()
    if MIGRATION_MODEL_READ_SPLIT in done:
        return

    operator_id = db.scalar(select(models.Role.id).where(models.Role.role_key == "operator"))
    if operator_id:
        db.execute(
            delete(models.RolePermission).where(
                models.RolePermission.role_id == operator_id,
                models.RolePermission.permission_code == "models.manage",
            )
        )

    done.add(MIGRATION_MODEL_READ_SPLIT)
    if setting is None:
        setting = models.AppSetting(key=_MIGRATION_SETTING_KEY, value={})
        db.add(setting)
    setting.value = {"done": sorted(done)}
    db.flush()


def seed(db) -> None:
    """启动时调用。幂等：重复执行不会产生重复数据。"""
    new_codes = _seed_permissions(db)
    new_roles = _seed_roles(db)
    new_menus = _seed_menus(db)
    _seed_role_permissions(db, new_roles)
    _grant_new_permissions_to_builtin_roles(db, new_codes)
    _run_migrations(db)
    _bind_new_menus_to_all_roles(db, new_menus)
    _ensure_user_default_role(db)
    _ensure_first_superadmin(db)
    db.commit()


# ---------------- 读取 ----------------


def menu_visibility(db, access: Access) -> tuple[set[str], set[str]]:
    """返回 (可见菜单 id 集合, 用户拥有的角色 id 集合)。"""
    role_ids = set(
        db.scalars(
            select(models.UserRole.role_id).where(models.UserRole.user_id == access.user_id)
        ).all()
    )
    if not role_ids:
        return set(), role_ids
    bound = set(
        db.scalars(
            select(models.RoleMenu.menu_id).where(models.RoleMenu.role_id.in_(role_ids))
        ).all()
    )
    return bound, role_ids


def _menu_allowed(menu: models.Menu, access: Access, bound: set[str]) -> bool:
    if access.is_superadmin:
        return True
    if menu.id not in bound:
        return False
    if menu.permission_code and not access.can(menu.permission_code):
        return False
    return True


def user_menu_tree(db, access: Access) -> list[dict]:
    """前端侧栏用的菜单树：只含可见项，空分组自动剔除。"""
    bound, _ = menu_visibility(db, access)
    menus = db.scalars(
        select(models.Menu).where(models.Menu.visible.is_(True)).order_by(models.Menu.sort)
    ).all()
    allowed = [menu for menu in menus if _menu_allowed(menu, access, bound)]

    by_parent: dict[str | None, list[models.Menu]] = {}
    for menu in allowed:
        by_parent.setdefault(menu.parent_id, []).append(menu)

    tree: list[dict] = []
    for group in by_parent.get(None, []):
        children = by_parent.get(group.id, [])
        if group.kind == "group" and not children:
            continue
        items = [
            {
                "name": child.menu_key,
                "label": child.label,
                "short": child.short_label,
                "icon": child.icon,
                "path": child.path,
                "permission": child.permission_code,
            }
            for child in children
        ]
        tree.append(
            {
                "key": group.menu_key,
                "label": group.label,
                "icon": group.icon,
                "path": group.path,
                "items": items,
            }
        )
    return tree


def admin_menu_tree(db) -> list[dict]:
    """后台菜单管理用：包含隐藏项、id、权限点与绑定统计。"""
    menus = db.scalars(
        select(models.Menu).order_by(models.Menu.sort, models.Menu.created_at)
    ).all()
    by_parent: dict[str | None, list[models.Menu]] = {}
    for menu in menus:
        by_parent.setdefault(menu.parent_id, []).append(menu)

    def serialize(menu: models.Menu) -> dict:
        return {
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
            "children": [serialize(child) for child in by_parent.get(menu.id, [])],
        }

    return [serialize(menu) for menu in by_parent.get(None, [])]


def rebuild_paths(db) -> None:
    """父级改名/移动后重算子树 path（path 由服务端维护，不接受前端传入）。"""
    menus = db.scalars(select(models.Menu)).all()
    by_id = {menu.id: menu for menu in menus}
    by_parent: dict[str | None, list[models.Menu]] = {}
    for menu in menus:
        by_parent.setdefault(menu.parent_id, []).append(menu)

    def walk(menu: models.Menu, prefix: str) -> None:
        segment = menu.menu_key if menu.parent_id else menu.menu_key
        menu.path = f"{prefix}/{segment}" if prefix else f"/{segment}"
        for child in by_parent.get(menu.id, []):
            walk(child, menu.path)

    for root in by_parent.get(None, []):
        walk(root, "")
    db.flush()


def audit(
    db,
    *,
    actor_user_id: str | None,
    action: str,
    target_type: str,
    target_id: str,
    detail: dict | None = None,
) -> None:
    """所有角色/菜单/授权变更都必须走这里。缺了它，「谁改了权限」就查不出来。"""
    db.add(
        models.RbacAuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail or {},
        )
    )
