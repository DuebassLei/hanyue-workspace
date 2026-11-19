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

from dataclasses import dataclass, field

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

@dataclass(frozen=True)
class MenuSeed:
    """菜单种子节点。group 可以再套一层 group（如「工作区 → 文章 → 文章管理」）。"""

    key: str
    label: str
    short: str = ""
    icon: str = ""
    permission: str | None = None
    kind: str = "page"
    children: tuple["MenuSeed", ...] = field(default_factory=tuple)


def _page(key: str, label: str, short: str, icon: str, permission: str | None) -> MenuSeed:
    return MenuSeed(key, label, short, icon, permission, "page")


def _group(key: str, label: str, icon: str, children: tuple[MenuSeed, ...]) -> MenuSeed:
    # 分组是纯容器：不挂权限点，可见性由子项推导
    return MenuSeed(key, label, label, icon, None, "group", children)


# 菜单种子。page 的 key 必须与前端 router.js 的路由 name 一致，否则点击无反应。
# 层级最多三层：顶级分组 → 分类分组 → 页面。
MENU_SEED: tuple[MenuSeed, ...] = (
    _group(
        "create",
        "创作",
        "create",
        (
            _page("overview", "创作总览", "总览", "overview", "overview.view"),
            _page("hot-topics", "热点选题", "热点", "hot-topics", "hot_topics.view"),
            _page("calendar", "内容日历", "日历", "calendar", "calendar.view"),
        ),
    ),
    _group(
        "workspace",
        "工作区",
        "workspace",
        (
            # 跨类型的入口留在顶级：创作空间是编辑枢纽，提示词库被文章/图片共用
            _page("studio", "创作空间", "创作", "studio", "studio.use"),
            _page("prompts", "提示词库", "提示词", "prompts", "prompts.use"),
            _group(
                "group-articles",
                "文章",
                "articles",
                (
                    _page("articles", "文章管理", "文章", "articles", "studio.use"),
                    _page("cards", "图文卡片", "卡片", "cards", "cards.use"),
                ),
            ),
            _group(
                "group-images",
                "图片",
                "images",
                (
                    _page("images", "图片生成", "图片", "images", "images.use"),
                    _page("stickers", "贴图创作", "贴图", "stickers", "stickers.use"),
                    _page("storyboards", "漫画分镜", "分镜", "storyboards", "storyboards.use"),
                    _page("assets", "素材图库", "图库", "assets", "assets.use"),
                ),
            ),
            _group(
                "group-videos",
                "视频",
                "videos",
                (
                    _page("videos", "视频创作", "视频", "videos", "videos.use"),
                ),
            ),
            _group(
                "group-novels",
                "小说",
                "novels",
                (
                    _page("novels", "小说创作", "小说", "novels", "novels.use"),
                    _page("characters", "角色库", "角色", "characters", "characters.use"),
                ),
            ),
        ),
    ),
    _group(
        "ops",
        "运营",
        "ops",
        (
            _page("publishing", "发布管理", "发布", "publishing", "publishing.view"),
            _page("wechat-layout", "微信排版", "排版", "wechat-layout", "wechat_layout.use"),
            _page("schedules", "定时任务", "定时", "schedules", "schedules.use"),
        ),
    ),
    _group(
        "rbac",
        "权限",
        "system",
        (
            _page("users", "用户管理", "用户", "characters", "rbac.user.manage"),
            _page("roles", "角色管理", "角色", "settings", "rbac.role.manage"),
            _page("menus", "菜单管理", "菜单", "logs", "rbac.menu.manage"),
        ),
    ),
    _group(
        "system",
        "系统",
        "system",
        (
            _page("models", "模型管理", "模型", "models", "models.view"),
            _page("logs", "系统日志", "日志", "logs", "logs.view"),
            _page("settings", "设置", "我的", "settings", "settings.view"),
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
    """按种子建菜单（最多三层），返回本次新建的 menu.id（用于给角色补默认绑定）。"""
    created: list[str] = []
    for node in MENU_SEED:
        _seed_menu_node(db, node, None, _next_sort(db, None), created)
    return created


def _seed_menu_node(db, node: MenuSeed, parent: models.Menu | None, sort: int, created: list[str]) -> None:
    menu = db.scalar(select(models.Menu).where(models.Menu.menu_key == node.key))
    if menu is None:
        menu = models.Menu(
            parent_id=parent.id if parent else None,
            menu_key=node.key,
            label=node.label,
            short_label=node.short,
            icon=node.icon,
            path=f"{parent.path}/{node.key}" if parent else f"/{node.key}",
            kind=node.kind,
            permission_code=node.permission,
            sort=sort,
        )
        db.add(menu)
        db.flush()
        created.append(menu.id)
    elif node.permission is not None:
        _rename_menu_permission(menu, node.permission)
        if node.key == "users" and menu.label == "用户授权":
            menu.label = node.label
            menu.short_label = node.short
    for index, child in enumerate(node.children):
        _seed_menu_node(db, child, menu, (index + 1) * 10, created)


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

# 2026-11：风格库并入提示词库后，旧的 image-styles 菜单已废弃（路由已重定向到 /prompts）。
# 菜单播种是「只增不减」，无法自动删除，故用一次性迁移清理老库的废弃菜单。
# 只删这一批明确废弃的 key，不碰管理员自建菜单。
MIGRATION_PRUNE_LEGACY_MENUS = "2026-11.prune-legacy-menus"
_DEPRECATED_MENU_KEYS = ("image-styles",)

# 2026-11：工作区菜单太挤（11 项平铺），拆成「文章 / 图片 / 视频 / 小说」四个分类。
# 老库里页面直接挂在 workspace 下，播种只增不减、不会搬动它们，故用一次性迁移重挂载。
# 只调整归属和分类顺序：页面自身的排序、隐藏状态和管理员的角色绑定一律保留。
MIGRATION_WORKSPACE_GROUPS = "2026-11.workspace-groups"
_WORKSPACE_SUBGROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("group-articles", ("articles", "cards")),
    ("group-images", ("images", "stickers", "storyboards", "assets")),
    ("group-videos", ("videos",)),
    ("group-novels", ("novels", "characters")),
)


def _migrate_workspace_groups(db) -> None:
    workspace = db.scalar(select(models.Menu).where(models.Menu.menu_key == "workspace"))
    if workspace is None:
        return

    children = list(
        db.scalars(select(models.Menu).where(models.Menu.parent_id == workspace.id)).all()
    )
    by_key = {menu.menu_key: menu for menu in children}
    # 分类排在仍留在顶级的页面（创作空间、提示词库）之后，避免把它们挤到分组下面。
    base = max((menu.sort for menu in children if menu.kind != "group"), default=0)

    for index, (group_key, member_keys) in enumerate(_WORKSPACE_SUBGROUPS, start=1):
        group = by_key.get(group_key)
        if group is None:
            # 正常不会发生（播种先跑）；兜底建一个，避免迁移后页面无处可挂。
            group = models.Menu(
                parent_id=workspace.id,
                menu_key=group_key,
                label=group_key,
                kind="group",
                path=f"{workspace.path}/{group_key}",
                sort=base + index * 10,
            )
            db.add(group)
            db.flush()
        group.parent_id = workspace.id
        group.kind = "group"
        group.permission_code = None
        group.sort = base + index * 10
        for key in member_keys:
            page = db.scalar(select(models.Menu).where(models.Menu.menu_key == key))
            if page is None:
                continue
            # 只搬还挂在 workspace 下的：管理员已经手工挪过的不覆盖
            if page.parent_id == workspace.id:
                page.parent_id = group.id
    db.flush()
    rebuild_paths(db)


# 2026-11：漫画分镜按产出归到「图片」分类（最初误放在「视频」下）。
# 已经跑过上一轮迁移的库需要单独的一次性迁移把它挪回来。
MIGRATION_STORYBOARDS_TO_IMAGES = "2026-11.storyboards-to-images"


def _migrate_storyboards_to_images(db) -> None:
    storyboards = db.scalar(select(models.Menu).where(models.Menu.menu_key == "storyboards"))
    images_group = db.scalar(select(models.Menu).where(models.Menu.menu_key == "group-images"))
    if storyboards is None or images_group is None:
        return
    if storyboards.parent_id == images_group.id:
        return
    videos_group = db.scalar(select(models.Menu).where(models.Menu.menu_key == "group-videos"))
    workspace = db.scalar(select(models.Menu).where(models.Menu.menu_key == "workspace"))
    # 只搬仍挂在旧分类（视频）或工作区下的；管理员手工挪过的不覆盖
    old_parents = {
        menu.id for menu in (videos_group, workspace) if menu is not None
    }
    if storyboards.parent_id not in old_parents:
        return
    siblings = db.scalars(
        select(models.Menu).where(models.Menu.parent_id == images_group.id)
    ).all()
    stickers = next((menu for menu in siblings if menu.menu_key == "stickers"), None)
    if stickers is not None:
        # 排在贴图创作之后、素材图库之前
        storyboards.sort = stickers.sort + 1
    else:
        storyboards.sort = max((menu.sort for menu in siblings), default=0) + 10
    storyboards.parent_id = images_group.id
    db.flush()
    rebuild_paths(db)


def _run_migrations(db) -> None:
    setting = db.get(models.AppSetting, _MIGRATION_SETTING_KEY)
    done = set((setting.value or {}).get("done", [])) if setting else set()
    changed = False

    # 迁移 1：运营权限收窄（保留原逻辑，改为独立判断以支持多个迁移并存）
    if MIGRATION_MODEL_READ_SPLIT not in done:
        operator_id = db.scalar(select(models.Role.id).where(models.Role.role_key == "operator"))
        if operator_id:
            db.execute(
                delete(models.RolePermission).where(
                    models.RolePermission.role_id == operator_id,
                    models.RolePermission.permission_code == "models.manage",
                )
            )
        done.add(MIGRATION_MODEL_READ_SPLIT)
        changed = True

    # 迁移 2：清理已废弃的菜单（image-styles 等）。RoleMenu 为 ON DELETE CASCADE，
    # 显式删除可跨 SQLite（测试库）与 PostgreSQL（运行库）兼容。
    if MIGRATION_PRUNE_LEGACY_MENUS not in done:
        legacy = db.scalars(
            select(models.Menu).where(models.Menu.menu_key.in_(_DEPRECATED_MENU_KEYS))
        ).all()
        for menu in legacy:
            db.execute(delete(models.RoleMenu).where(models.RoleMenu.menu_id == menu.id))
            db.delete(menu)
        done.add(MIGRATION_PRUNE_LEGACY_MENUS)
        changed = True

    # 迁移 3：工作区二级分类（文章 / 图片 / 视频 / 小说）
    if MIGRATION_WORKSPACE_GROUPS not in done:
        _migrate_workspace_groups(db)
        done.add(MIGRATION_WORKSPACE_GROUPS)
        changed = True

    # 迁移 4：漫画分镜从「视频」挪到「图片」
    if MIGRATION_STORYBOARDS_TO_IMAGES not in done:
        _migrate_storyboards_to_images(db)
        done.add(MIGRATION_STORYBOARDS_TO_IMAGES)
        changed = True

    if changed:
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
    """前端侧栏用的菜单树（最多三层）：只含可见项，空分组自动剔除。

    分类分组是纯容器：不要求角色绑定、也没有权限点，只要下面还有可见页面就显示。
    这样新增角色或调整子项权限时，不会出现「叶子可见、父级没绑 → 整块消失」。
    """
    bound, _ = menu_visibility(db, access)
    menus = db.scalars(
        select(models.Menu).where(models.Menu.visible.is_(True)).order_by(models.Menu.sort)
    ).all()

    by_parent: dict[str | None, list[models.Menu]] = {}
    for menu in menus:
        by_parent.setdefault(menu.parent_id, []).append(menu)

    def build_items(parent_id: str | None) -> list[dict]:
        items: list[dict] = []
        for child in by_parent.get(parent_id, []):
            if child.kind == "group":
                nested = build_items(child.id)
                if not nested:
                    continue
                items.append(
                    {
                        "name": child.menu_key,
                        "label": child.label,
                        "short": child.short_label,
                        "icon": child.icon,
                        "path": child.path,
                        "permission": None,
                        "children": nested,
                    }
                )
            elif _menu_allowed(child, access, bound):
                items.append(
                    {
                        "name": child.menu_key,
                        "label": child.label,
                        "short": child.short_label,
                        "icon": child.icon,
                        "path": child.path,
                        "permission": child.permission_code,
                        "children": [],
                    }
                )
        return items

    tree: list[dict] = []
    for group in by_parent.get(None, []):
        if not _menu_allowed(group, access, bound):
            continue
        items = build_items(group.id)
        if group.kind == "group" and not items:
            continue
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
