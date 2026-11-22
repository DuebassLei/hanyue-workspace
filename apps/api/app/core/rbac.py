"""RBAC 核心：权限目录、有效权限解析、FastAPI 依赖。

设计要点
--------
1. **权限是字符串契约。** 后端 `require_permission("models.manage")` 与前端
   `meta.permission` 用同一套 code，新增权限点只改 `PERMISSION_CATALOG`。
2. **超管不是一条通配权限，而是角色上的布尔位。** 这样「某角色有哪些权限」永远
   可以用 SQL 回答，不需要在应用层展开 `*`。
3. **默认拒绝。** 没有任何角色的用户拿到空权限集；播种逻辑会保证存量用户至少
   拿到 `member`，所以正常不会把人锁在外面。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends
from sqlalchemy import select

from .. import models
from .deps import ApiError, CurrentUser, DbSession

SUPERADMIN_ROLE_KEY = "superadmin"
DEFAULT_ROLE_KEY = "member"

# (code, 名称, 模块, 排序)
PERMISSION_CATALOG: tuple[tuple[str, str, str, int], ...] = (
    ("overview.view", "创作总览", "创作", 10),
    ("hot_topics.view", "热点选题", "创作", 20),
    # 能看热点 ≠ 能往里灌数据：入库会改全局热点库，手工补数/外部对接才需要它。
    # 定时脚本走服务层，不依赖这个权限点。
    ("hot_topics.ingest", "热点数据入库", "创作", 25),
    ("calendar.view", "内容日历", "创作", 30),
    ("studio.use", "创作空间", "创作", 40),
    ("cards.use", "图文卡片", "创作", 50),
    ("images.use", "图片生成", "创作", 60),
    ("stickers.use", "贴图创作", "创作", 70),
    ("storyboards.use", "漫画分镜", "创作", 80),
    ("novels.use", "小说创作", "创作", 100),
    ("characters.use", "角色库", "创作", 110),
    ("prompts.use", "提示词库", "创作", 120),
    ("assets.use", "素材图库", "创作", 130),
    # 读与写分开：看「发过什么、失败了什么」是一件事，能真的推到公众号是另一件事。
    # 发布一旦发错，删不掉也撤不回，且会占用平台侧的群发额度——所以只读观察
    # （客服/内容负责人复盘）不该连带拿到推送能力。
    ("publishing.view", "发布查看", "运营", 195),
    ("publishing.use", "发布管理", "运营", 200),
    ("wechat_layout.use", "微信排版", "运营", 210),
    ("schedules.use", "定时任务", "运营", 220),
    ("scheduled-script.admin", "受信任脚本管理", "运营", 230),
    # 读与写分开：能看见「配了哪些模型、Key 掩码是什么」是一件事，
    # 能换 Key、删连接、改模型代号是另一件事。后者一旦误操作，全站创作立即停摆。
    ("models.view", "模型查看", "系统", 295),
    ("models.manage", "模型管理", "系统", 300),
    ("logs.view", "系统日志", "系统", 310),
    ("settings.view", "设置", "系统", 320),
    ("image_styles.manage", "风格库管理", "系统", 330),
    ("rbac.role.manage", "角色管理", "权限", 400),
    ("rbac.menu.manage", "菜单管理", "权限", 410),
    ("rbac.user.manage", "用户管理", "权限", 420),
    ("rbac.audit.view", "审计日志", "权限", 430),
)

ALL_PERMISSION_CODES = frozenset(item[0] for item in PERMISSION_CATALOG)

_CREATE_BASE = (
    "overview.view",
    "hot_topics.view",
    "calendar.view",
    "studio.use",
    "cards.use",
    "assets.use",
    "prompts.use",
    "settings.view",
)

_EDITOR_EXTRA = (
    "images.use",
    "stickers.use",
    "storyboards.use",
    "novels.use",
    "characters.use",
    "wechat_layout.use",
)

# 运营能看模型配置（知道现在挂的是哪个模型、通不通），但不持有密钥的写权限：
# 删错一个连接，全站创作当场停摆，这个错误代价远高于「要改时找超管」的不便。
# 想让运营也能改，在角色管理里给运营勾上 models.manage 即可，不需要改代码。
_OPERATOR_EXTRA = (
    "publishing.view",
    "publishing.use",
    "schedules.use",
    "scheduled-script.admin",
    "logs.view",
    "models.view",
)

# 内置角色的默认权限。superadmin 不在此表：它靠 is_superadmin 拿到全部权限。
DEFAULT_ROLE_MATRIX: dict[str, tuple[str, ...]] = {
    "member": _CREATE_BASE,
    "editor": _CREATE_BASE + _EDITOR_EXTRA,
    "operator": _CREATE_BASE + _EDITOR_EXTRA + _OPERATOR_EXTRA,
}

ROLE_META: dict[str, tuple[str, str, bool, int]] = {
    # role_key: (名称, 描述, 是否内置, 排序)
    "superadmin": ("超级管理员", "拥有全部权限，不受菜单与权限点限制", True, 0),
    "operator": ("运营", "创作全流程、发布与定时任务，可查看模型配置但不可改密钥", True, 10),
    "editor": ("编辑", "创作与排版，不能发布、不能改模型", True, 20),
    "member": ("普通用户", "基础创作功能", True, 30),
}


@dataclass(frozen=True)
class Access:
    """一次请求内的有效权限快照。"""

    user_id: str
    role_keys: tuple[str, ...]
    permissions: frozenset[str]
    is_superadmin: bool

    def can(self, code: str) -> bool:
        return self.is_superadmin or code in self.permissions

    def can_any(self, *codes: str) -> bool:
        if self.is_superadmin:
            return True
        return any(code in self.permissions for code in codes)


def resolve_access(db: DbSession, user: models.User) -> Access:
    """一次查询拿到「角色 + 权限」，避免 N+1。

    用 outerjoin：角色没有任何权限点时仍要出现在 role_keys 里（管理页要显示
    「这个角色目前是空的」，而不是「这个用户没有角色」）。
    """
    rows = db.execute(
        select(models.Role.role_key, models.Role.is_superadmin, models.RolePermission.permission_code)
        .select_from(models.UserRole)
        .join(models.Role, models.Role.id == models.UserRole.role_id)
        .outerjoin(models.RolePermission, models.RolePermission.role_id == models.Role.id)
        .where(models.UserRole.user_id == user.id)
    ).all()

    role_keys: list[str] = []
    permissions: set[str] = set()
    is_superadmin = False
    for role_key, role_is_super, code in rows:
        if role_key not in role_keys:
            role_keys.append(role_key)
        if role_is_super:
            is_superadmin = True
        if code:
            permissions.add(code)

    return Access(
        user_id=user.id,
        role_keys=tuple(role_keys),
        permissions=frozenset(permissions),
        is_superadmin=is_superadmin,
    )


def get_access(db: DbSession, user: CurrentUser) -> Access:
    return resolve_access(db, user)


AccessDep = Annotated[Access, Depends(get_access)]


def require_permission(
    *codes: str, mode: Literal["all", "any"] = "all"
) -> Depends:
    """接口级权限依赖。

        @router.post("/models", dependencies=[Depends(require_permission("models.manage"))])

    或需要拿到 Access 时：

        def handler(access: Annotated[Access, Depends(require_permission("logs.view"))]): ...
    """

    def dependency(access: AccessDep) -> Access:
        ok = access.can(codes[0]) if len(codes) == 1 else (
            all(access.can(code) for code in codes)
            if mode == "all"
            else any(access.can(code) for code in codes)
        )
        if not ok:
            missing = [code for code in codes if not access.can(code)]
            raise ApiError(
                # 专用码而不是泛化的 forbidden：前端要据此判断「权限被收回了，
                # 重新拉一次 /rbac/me 并刷新菜单」，业务拒绝不该触发同样的动作。
                "permission_denied",
                f"缺少权限：{'、'.join(missing)}",
                status_code=403,
            )
        return access

    return Depends(dependency)
