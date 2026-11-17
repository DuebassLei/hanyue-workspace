# 角色权限与菜单管理（RBAC）

**状态：** 已实现（后端 + 前端）
**日期：** 2026-11-17
**关联：** `apps/api/app/core/rbac.py`（权限目录与依赖）、`apps/api/app/services/rbac_service.py`（播种与菜单树）、`apps/api/app/modules/rbac.py`（接口）、`apps/web/src/stores/rbac.js`（前端状态）

---

## 1. 设计原则

| 原则 | 具体做法 | 为什么 |
|---|---|---|
| **权限是契约，不是界面开关** | 后端 `require_permission("models.manage")` 与前端 `meta.permission` 用同一套 code | 绕过前端直接调接口也会 403；前端只负责「不显示」 |
| **菜单是权限的投影** | 菜单项挂 `permission_code`，角色绑定 `role_menus` | 两道闸（绑定 + 权限）让运营能按角色微调，而不是只能全有或全无 |
| **超管是布尔位，不是通配符权限** | `roles.is_superadmin` | 把 `*` 塞进权限表后，「这个角色有哪些权限」就无法用 SQL 回答，审计说不清 |
| **默认拒绝** | 无角色 = 空权限集 | 漏配时结果是「看不见」，不是「全开」 |
| **改权限即时生效** | 每次请求现算有效权限，不缓存进令牌 | 降权不需要等用户重新登录，也不需要等令牌过期 |
| **不可锁死** | 内置角色不可删；最后一个超管不能自废 | 否则系统会进入「没人能授权 → 也永远没人能恢复」的死局 |

---

## 2. 数据模型

```
users ──< user_roles >── roles ──< role_permissions >── permissions
                          └────< role_menus >────────── menus ──(parent_id)──┐
                                                          └─────────────────────┘
rbac_audit_logs  ← 记录每一次角色/菜单/授权变更
```

| 表 | 关键字段 | 说明 |
|---|---|---|
| `roles` | `role_key`（唯一）、`is_superadmin`、`is_system`、`sort` | 超管与内置角色受保护 |
| `permissions` | `code`（主键）、`name`、`module`、`sort` | 权限点目录，启动时按代码补插 |
| `role_permissions` | `(role_id, permission_code)` | 复合主键，`ON DELETE CASCADE` |
| `menus` | `menu_key`（唯一）、`parent_id`、`path`、`kind`、`permission_code`、`sort`、`visible` | `path` 由服务端按父子关系重算，不接受前端传入 |
| `role_menus` | `(role_id, menu_id)` | 绑定关系 |
| `user_roles` | `(user_id, role_id)` | 一人可多角色，权限取并集 |
| `rbac_audit_logs` | `actor_user_id`、`action`、`target_type/id`、`detail`（JSONB） | 操作人删除后置空，记录本身保留 |

**PG 相关取舍**

- `menus.parent_id` 用 `ON DELETE RESTRICT`：有子菜单时删父级直接失败，服务层再翻译成 409 带可读文案
- 菜单树用 `parent_id + 冗余 path`，不做递归 CTE：菜单是几十行量级，一次全查在内存组树更快，`path` 已建索引可做排序与面包屑
- `rbac_audit_logs.detail` 用 `JSON().with_variant(JSONB, "postgresql")`：生产吃 JSONB 的索引与算子，测试跑 SQLite 时自动退化
- 关键约束（`kind` 枚举、`sort >= 0`）写进 `CheckConstraint`，不只靠应用层

---

## 3. 权限目录

`apps/api/app/core/rbac.py::PERMISSION_CATALOG` 是唯一真相。新增权限点只改这一处，启动时补插库。

| 模块 | 权限点 |
|---|---|
| 创作 | `overview.view`、`hot_topics.view`、`calendar.view`、`studio.use`、`cards.use`、`images.use`、`stickers.use`、`storyboards.use`、`videos.use`、`novels.use`、`characters.use`、`prompts.use`、`assets.use` |
| 运营 | `publishing.view`、`publishing.use`、`wechat_layout.use`、`schedules.use` |
| 系统 | `models.view`、`models.manage`、`logs.view`、`settings.view` |
| 权限 | `rbac.role.manage`、`rbac.menu.manage`、`rbac.user.manage`、`rbac.audit.view` |

命名约定：`<资源>.<动作>`，动作只有 `view / use / manage` 三种。**不按页面命名**（不要 `page.roles`），否则菜单一改权限就得跟着改。

---

## 4. 默认角色矩阵

| 角色 | 权限范围 | 说明 |
|---|---|---|
| `superadmin` | 全部（靠 `is_superadmin`） | 不可删、不可改名 |
| `operator` | 创作 + 排版 + 发布（含只读复盘）+ 定时 + **模型查看**（不可改密钥）+ 日志 | 运营岗 |
| `editor` | 创作 + 排版，无发布/定时/模型 | 内容岗 |
| `member` | 总览、热点、日历、创作空间、卡片、素材、提示词、设置 | 默认角色 |

矩阵只对**首次创建**的内置角色生效。管理员在后台取消的权限**不会在重启时被补回来**——否则每次发版都会把运营的调整悄悄改回去。

两个例外，都在 `services/rbac_service.py`：

| 机制 | 做什么 | 为什么安全 |
|---|---|---|
| `_grant_new_permissions_to_builtin_roles` | 把**本次新增**的权限点按矩阵补给存量内置角色 | 只补新 code；管理员取消过的老权限不会复活；自定义角色不碰 |
| `_run_migrations` | 一次性数据迁移（如把 `models.manage` 从运营身上摘掉），已完成标记写在 `app_settings.rbac.migrations` | 只执行一次；管理员之后再勾回去，重启不会撤销 |

---

## 5. 接口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/rbac/me` | 登录即可 | 角色、权限点、可见菜单树（前端渲染侧栏用） |
| GET | `/rbac/permissions` | role 或 menu 管理 | 权限点目录 |
| GET/POST | `/rbac/roles` | `rbac.role.manage` | 列表 / 新建 |
| PATCH/DELETE | `/rbac/roles/{id}` | `rbac.role.manage` | 改名、描述、排序 / 删除（内置角色 409） |
| PUT | `/rbac/roles/{id}/permissions` | `rbac.role.manage` | **全量覆盖**权限 |
| PUT | `/rbac/roles/{id}/menus` | `rbac.menu.manage` | **全量覆盖**菜单绑定 |
| GET | `/rbac/menus` | `rbac.menu.manage` | 完整菜单树（含隐藏项与 id） |
| POST/PATCH/DELETE | `/rbac/menus[/{id}]` | `rbac.menu.manage` | 菜单 CRUD |
| GET | `/rbac/users?q=&status=` | `rbac.user.manage` | 用户 + 角色。`status` 为 `active`（默认）/ `disabled` / `all`；搜索跨所选状态 |
| POST | `/rbac/users` | `rbac.user.manage` | 建号。服务端生成一次性密码，只在本次响应的 `initialPassword` 里出现；至少选一个角色 |
| PATCH | `/rbac/users/{id}` | `rbac.user.manage` | 改昵称、停用/启用。不能停用自己；不能停用最后一个**启用中**的超管 |
| PUT | `/rbac/users/{id}/roles` | `rbac.user.manage` | 分配角色（最后一个超管 409） |
| POST | `/rbac/users/{id}/reset-password` | `rbac.user.manage` | 重置为一次性随机密码，并令对方已登录会话立即失效。不能重置自己的；没有邮箱的开发态账号 409 |
| GET | `/rbac/audits` | `rbac.audit.view` | 变更审计 |

错误码：`403 forbidden`（缺权限）、`409 role_protected / role_in_use / last_superadmin / cannot_disable_self / cannot_reset_self / no_email / email_taken / menu_has_children`、`400 invalid_menu / invalid_permission / invalid_email`。

停用只阻止后续请求（`is_active` 在每次鉴权时检查）。重置密码额外改 `password_changed_at`，令牌里的 `pwdv` 对不上就被拒，所以是立即踢下线。密码本身不进审计日志。

---

## 6. 新增一个页面要改什么

1. `core/rbac.py::PERMISSION_CATALOG` 加一行 `("<资源>.<动作>", "名称", "模块", 排序)`
2. `services/rbac_service.py::MENU_SEED` 在对应分组下加一项，`key` **必须等于** `router.js` 里的路由 `name`
3. `router.js` 加路由并在 `meta.permission` 写同一个 code；`ui/nav.js` 同步补一份（仅作后端不可达时的兜底）
4. 页面内需要按钮级控制时用 `composables/usePermission.js`（见 6.1）

重启后菜单会自动出现在侧栏，并默认绑定到所有角色；要限制可见性就在「角色管理 → 菜单可见性」里取消。

### 6.1 按钮级权限：只在「跨权限入口」上做

**先判断要不要做。** 如果一个页面要求的权限点已经覆盖了页内所有动作，那在这一页加 `v-if="can(...)"` 就是**死代码**——条件永远为 true，永远不会被验证。

真正需要按钮级判断的是「页面 A 的权限，够不着页面 B 的动作」：

| 位置 | 页面权限 | 动作所需权限 |
|---|---|---|
| 创作空间「转为图文卡片」 | `studio.use` | `cards.use` |
| 创作空间「转为视频」 | `studio.use` | `videos.use` |
| 创作空间「发布」 | `studio.use` | `publishing.use` |
| 热点选题「写这篇」 | `hot_topics.view` | `studio.use` |
| 热点选题「做贴图」 | `hot_topics.view` | `stickers.use` |
| 总览「配置模型」 | `overview.view` | `models.view` |

这些位置没有判断时，用户点下去会跳到 `/forbidden`，看起来像 bug。

**两种表现，按动作性质选：**

```js
const { can, disabledReason } = usePermission()

// 动作：置灰 + 说明原因。让用户知道有这功能、也知道该找管理员要什么权限
:disabled="Boolean(disabledReason('videos.use', '视频创作'))"
:title="disabledReason('videos.use', '视频创作') || '将当前正文带入视频创作'"

// 纯导航/整块内容：直接不渲染。报表页留一个点进去就 403 的卡片没有意义
<section v-if="can('publishing.use')">
```

**不要把它当安全边界。** 后端 `require_permission` 才是拦截点，前端只负责不让人点到一个注定失败的动作。

**已有页面中无需按钮级判断的**：`SchedulesView`（整页 `schedules.use`）、`MenusView`、`UsersView`——页内动作全部落在同一个权限点内，也确认过没有指向其他页面的入口。

### 6.2 读 / 写权限要分开

`ModelsView` 曾经是「整页 `models.manage`」，这意味着**能看见配置的人一定能删连接**。这不是一回事：

| 权限 | 能做什么 | 误操作后果 |
|---|---|---|
| `models.view` | 看连接列表、Base URL、密钥掩码、连通状态 | 无 |
| `models.manage` | 新增/删除连接、换 Key、跑探针、登记模型代号 | 删掉正在用的连接 → 全站创作立即停摆 |

所以页面路由只要求 `models.view`，页内的写操作按钮再判 `models.manage`。只读用户看到的是完整配置 + 一行说明 + 三个灰按钮，而不是一个空页面。

判定「什么时候该拆」的经验：**看这个动作做错之后，别人还能不能继续用。** 只影响操作者自己的，`use` 一个点即可；会影响其他人的（密钥、全局配置、发布通道），读和写分开。

后端对应写法（`GET` 用 any-of，避免历史上只授过 `manage` 的自定义角色升级后突然进不去）：

```python
_ADMIN_ONLY = [require_permission("models.manage")]
_MODEL_READ = [require_permission("models.view", "models.manage", mode="any")]
```

同一把尺子用在了发布上（`publishing.view` / `publishing.use`）：

| 权限 | 能做什么 | 误操作后果 |
|---|---|---|
| `publishing.view` | 看发布队列、失败原因、状态时间线、账号状态、平台能力表 | 无 |
| `publishing.use` | 建排期任务、审核通过、重试、取消、回填结果、绑定/解绑账号、跑凭据探针 | 推错一篇删不掉也撤不回，还占掉平台侧群发额度 |

客服、内容负责人做复盘只需要前者。只读时页面不是空的：完整队列 + 一条说明条 +「新建排期任务」「绑定账号」置灰并写明缺哪个权限。

菜单种子跟着改成 `publishing.view`（否则只读角色连自己的复盘页都进不去），改名规则与 `models` 共用 `MENU_PERMISSION_RENAMES`。

---

## 7. 业务接口的权限闸门

`/rbac/*` 之外，业务模块已按「页面 → 权限点」接入。改法是路由级一行：

```python
router = APIRouter(tags=["logs"], dependencies=[require_permission("logs.view")])
```

整页单一权限点的模块用上面这一行就够；`publishing` / `platform_auth` / `models_module` 是读写混合模块，模块级不挂依赖，改成按端点挂 `_XXX_READ` / `_XXX_WRITE` 两组常量（见 §6.2）。

### 7.1 已接入的映射

| 模块 | 覆盖路径 | 权限点 |
|---|---|---|
| `logs` | `/logs*` | `logs.view` |
| `publishing` | `GET /platforms/capabilities`、`GET /platform-accounts`、`GET /publishing/jobs*` | `publishing.view` \| `publishing.use` |
| | 建任务、预览校验、审核/重试/取消/改期/删除、回填结果、绑定/解绑账号、自动发布开关 | `publishing.use` |
| `platform_auth` | `GET /platforms/connectors/status`、`GET /platforms/{p}/maintenance`、`GET /platform-accounts/{id}/sync-status` | `publishing.view` \| `publishing.use` |
| | `POST /platform-accounts/{id}/test`（真实探针，会改账号状态） | `publishing.use` |
| `schedules` | `/schedules/*` | `schedules.use` |
| `dashboard` | `/dashboard` | `overview.view` |
| `hot_topics` | `/hot-topics/*`（除 `/hot-topics/status`、`/hot-topics`） | `hot_topics.view` |
| `cards` | `/cards/*` | `cards.use` |
| `prompts` | `/prompts*` | `prompts.use` |
| `novels` | `/novels/*` | `novels.use` |
| `videos` | `/video-provider/status`、`/videos*` | `videos.use` |
| `storyboards` | `/storyboards/*` | `storyboards.use` |

### 7.2 跨页面共用 → `mode="any"`

有几个模块被**低权限页面**直接调用，硬挂自己的权限点会把别人的页面打废。这些用 any-of：

| 模块 | 依赖 | 为什么 |
|---|---|---|
| `stickers` | `stickers.use` \| `hot_topics.view` | 热点页的「一键生成贴图」直接调 `/stickers` |
| `characters` | `characters.use` \| `storyboards.use` \| `novels.use` | 分镜的素材板、小说的人物圣经都要读角色库 |
| `wechat_mp` | 按端点拆，见下表 | 整模块 any-of 会让只会排版的人绑公众号 |

`models_module` 最特殊，一个文件混了三类东西，**不能整模块挂**：

| 路径 | 依赖 | 理由 |
|---|---|---|
| `GET /models/connections` | `models.view` \| `models.manage` | 只读查看配置：能看到掩码与状态，改不了 |
| 连接写操作、`/models/probe/*` | `models.manage` | 换密钥、删连接、厂商探针 |
| `POST/PATCH/DELETE /models/catalog*` | `models.manage` | 改价、改倍率 |
| `GET /models/catalog`、`GET /models/status` | 仅登录 | 每个创作页的模型下拉、全局状态栏 |
| `/ai/text-generations`、`/ai/generations*` | 仅登录 | 创作主通道，被所有页面调用 |

`wechat_mp` 同样不能整模块挂。排版是本地计算，绑定和推草稿会写到公众号：

| 路径 | 依赖 | 理由 |
|---|---|---|
| `GET /wechat-mp/themes`、`/themes/schema`、`/components`，`POST /drafts/render` | `wechat_layout.use` \| `publishing.use` | 排版页和发布抽屉都要主题与本地渲染 |
| 主题的创建 / 修改 / 删除 / 偏好 | `wechat_layout.use` | 只改自己的主题库，发布角色用预设即可 |
| `GET /wechat-mp/status`、`/credentials`、`/drafts` | `publishing.view` \| `publishing.use` | 看配置掩码和公众号侧草稿，不产生写入 |
| `POST /bind`、凭据写入、`POST /drafts`、`/drafts/preview`、`GET /egress-ip` | `publishing.use` | 绑号、换 Secret、推草稿；出口 IP 只服务于填白名单 |

设置页的写作风格会拉 `GET /wechat-mp/themes`。普通用户没有排版权限，接口返回 403，前端退回内置主题快照，不因此给全员放开这个接口。

同理 `ai_images` 只闸生成端点（`images.use` \| `studio.use`），`/ai/image-provider/status` 与 `/assets/upload-sessions` 保持开放。

### 7.3 有意不加权限的接口

| 模块 | 理由 |
|---|---|
| `auth` / `preferences` | 登录、主题偏好：任何角色都必须能用 |
| `jobs` | 异步任务轮询：发布、生图、渲染都轮询同一组接口 |
| `content` / `workspaces` / `topics` | 稿件与工作区是**所有创作页的公共地基**，权限由页面控制 |
| `ai_content` | `/hot-topics/status`、`/hot-topics` 被创作空间调用（与 `hot_topics` 模块不是同一组路由） |
| `assets` | 素材库被 Studio、发布、排版、分镜、视频、角色六处引用 |
| `agent` / `skills` / `handdraw` | 创作空间内的工具，入口页面已受 `studio.use` 保护 |

判定标准：**这个接口是不是单一页面的专属能力？** 是 → 挂页面权限点；不是（多页面共用的地基）→ 只校验登录，权限交给入口页面。

### 7.4 测试约定

`tests/conftest.py` 的 `login()` 与自动生效的 `business_role` 夹具，会把业务用例的账号默认设为 `superadmin`——它们验的是功能，不是边界。

为什么是超管而不是运营：运营已被收窄成「可看模型配置、不可改密钥」，而模型类用例需要建连接、换 Key、跑探针。**业务用例以最大权限跑，权限边界交给专门的文件**，两边不互相绑架。权限边界集中在两处：

- `tests/test_rbac.py`：角色/菜单/授权语义（该文件不参与角色替换，新用户仍是 `member`）
- `tests/test_permission_gate.py`：**该挡的挡住 + 共用通道不被误伤**，两类断言成对出现

新增模块时，请同时在这两个方向各补一条断言，否则「漏挡」和「误伤」都不会被发现。

---

## 8. 运维

- **首个超管**：`BOOTSTRAP_FIRST_USER_SUPERADMIN=true`（默认）时，库里没有超管就把最早注册的活跃账号提为超管；之后注册的都是 `member`。Docker 部署里即 `create-user create` 建的第一个账号。公网邀请制部署可设为 `false`。
- **查当前超管 / 手工提权**：`./scripts/deploy.sh shell db` 进的是容器 shell，要再 `psql -U hanyue -d hanyue`（本地开发库用 `dev-pg.sh psql` / `dev-pg.ps1 psql`）。完整 SQL 与注意事项见 `docs/docker-deploy.md` §3.1。提权本身就这一条，按邮箱匹配，幂等：

  ```sql
  INSERT INTO user_roles (user_id, role_id)
  SELECT u.id, r.id
  FROM users u
  JOIN roles r ON r.role_key = 'superadmin'
  WHERE u.email = 'you@example.com'
    AND NOT EXISTS (
      SELECT 1 FROM user_roles ur
      WHERE ur.user_id = u.id AND ur.role_id = r.id
    );
  ```

- **审计**：`GET /rbac/audits` 能看到谁在什么时候改了哪个角色的权限、删了哪个菜单
- **迁移**：新表由 `create_all` 自动创建；`users` 表**不需要**加 `role` 列，角色关系全在 `user_roles`
