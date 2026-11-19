# 独立文章管理界面 · 规划

> 状态：**规划稿，未实现**。需求 04：新增独立的文章管理界面，解决"在创作空间不好管理"的问题。

---

## 1. 现状 RCA：为什么"不好管理"

| 痛点 | 证据 |
|---|---|
| 没有独立页面 | `views/` 无 ArticlesView/ContentView；router.js、ui/nav.js 无 articles/content 路由 |
| 管理塞在编辑器侧栏 | `StudioView.vue`（约 115KB）侧栏 1471-1653：只有标题+相对日期+删除，不显示状态/版本数/平台数 |
| 200 篇硬上限 | `loadList()`（610-623）硬编码 `GET /content?fields=list&limit=200`，无游标翻页 |
| 假搜索 | `filteredItems`（420-471）是已加载 200 条的前端 `Array.filter`，非服务端检索 |
| 完整筛选藏在弹窗 | `DraftLibraryModal.vue` 才有 q/platform/时间/排序/归档筛选，且以弹窗形式依附编辑器 |
| 无批量 | 批量归档/删除/改状态都不存在 |
| 无版本 diff | `restore()`（763-767）直接覆盖编辑器，`docs/studio-redesign.md:169` 也承认无 diff |
| 状态字段失真 | `ContentItem.status` 恒为 `draft`；真实发布状态在 `publish_jobs.status` |

**后端其实已经很完整**：`GET /content`（content.py:75-178）支持 keyset 分页、`q/title` 搜索、`status/type/platform/updatedFrom/updatedTo/includeArchived/sort`，还有 `fields=list` 瘦身模式。**瓶颈几乎全在前端展现层**。

---

## 2. 目标 / 非目标

**目标**
1. 独立路由 `/articles` + 菜单入口，专注"找得到、看得清、管得了"稿件。
2. 服务端分页 + 搜索 + 多条件筛选 + 排序，取消 200 篇上限。
3. 稿件列表展示：标题、状态（真实发布态）、版本数、平台数、更新时间、归档态。
4. 批量归档/恢复/删除；单稿归档、删除、新建。
5. 版本概览与 diff（可放二期），发布状态联查。
6. 编辑动作仍回跳创作空间，不复制编辑器逻辑。

**非目标**
- 不在本页重写编辑器（115KB 的 StudioView 逻辑保持单一来源）。
- 首期不引入稿件独立状态机（总览 D4：复用 publish_jobs 状态）。
- 首期不做文件夹/标签（后端无此模型，见 §6）。

---

## 3. 页面设计

### 3.1 结构

```
ArticlesView.vue（独立页，/articles）
├─ 工具栏：新建稿件 | 批量操作（归档/删除） | 排序 | 列密度
├─ 筛选条：搜索框(300ms 防抖) | 状态chips | 平台 | 更新时间 | 归档开关
├─ 表格/卡片：标题 | 状态 | 版本数vN | 平台数 | 更新时间 | 操作(打开/归档/删除)
├─ 分页：keyset 游标 + "加载更多"
└─ 行操作：打开 → router.push({name:'studio', query:{id}})  （沿用现有约定）
```

### 3.2 数据来源与复用件

| 能力 | 复用 | 说明 |
|---|---|---|
| 列表/分页 | `GET /content?fields=list` + `api.getPage()`（client.js:74-75） | 已支持 keyset，`X-Next-Cursor` 响应头 |
| 筛选条 | `DraftLibraryModal.vue:25-45,135-143` | 几乎可直接抽成组件 |
| 状态标签 | `StatusTag.vue` | MAP 覆盖发布全状态；稿件新状态需补映射 |
| 状态 chips（带计数） | `QueueFilterChips.vue` / `QueueToolbar.vue` | 发布队列现成范式 |
| 发布状态联查 | `GET /publishing/jobs?contentItemId=<id>`（publishing.py:484） | 需 `publishing.view`，无权限时置灰降级（仿 `disabledReason`） |
| 分组/相对时间 | StudioView.vue:428-471 | 今天/近7天/更早分组逻辑可复用 |

### 3.3 状态展示（D4 推荐）

- 列表"状态"列 = 该稿件关联 `publish_jobs` 的最新状态（SUCCEEDED/FAILED/PENDING_REVIEW…），无发布任务显示"草稿"。
- 不改 `content_items.status`，避免牵动全部创作流程。
- 若二期要稿件独立状态机，另立设计。

---

## 4. 后端改动（少量增强）

现有 `/content` 已满足列表主需求，建议补充（按优先级）：

| 接口 | 用途 | 优先级 |
|---|---|---|
| `POST /content/batch-archive` | 批量归档/恢复 | 高（批量需求） |
| `POST /content/batch-delete` | 批量删除 | 高 |
| `GET /content/{id}/versions` | 独立版本列表（docs/design.md:694 标注未实现） | 中 |
| `GET /content/{id}/versions/diff?from=&to=` | 版本 diff | 低（二期） |
| `status` 筛选语义澄清 | 当前 status 恒 draft，按 publish 状态筛选需后端联表 | 中 |

**所有权边界（已定）**：详情/编辑/删除校验 `owner_user_id == user.id`（content.py:187/201/250/293…），列表按 `workspace_id` 过滤。本页定位 **"我的稿件"**（Q1），保持现有所有权校验不放宽；文章属用户创作数据，非管理员维护的配置类数据。

---

## 5. 路由 / 菜单 / 权限

沿用现有机制，`docs/rbac.md:247-258` 明确稿件是"公共地基"，**刻意无 content.* 权限点**：

**方案 A（推荐，零后端权限改动）**
- `router.js` 加：`{ path:'/articles', name:'articles', component:()=>import('./views/ArticlesView.vue'), meta:{title:'文章管理', icon:'▤', permission:'studio.use'} }`
- `ui/nav.js` workspace 组（紧邻 studio）加一条
- 服务端 `menus` 表通过"菜单管理"页补一条（真相源，nav.js 只是兜底——`ui/nav.js:1-10` 明确要求两边都加）

**方案 B（精细授权）**：新增 `content.view`/`content.manage`，改 `ALL_PERMISSION_CODES`、`DEFAULT_ROLE_MATRIX`、router meta、menus 表，并按 `docs/rbac.md:260-269` 补 `test_rbac.py` + `test_permission_gate.py` 断言。首期不建议。

---

## 6. 明确的数据模型边界

| 想要的能力 | 现状 | 若要做的代价 |
|---|---|---|
| 正文搜索 | `/content` 只按 title ilike | 后端加 body 检索（可 FTS），代价中 |
| 标签筛选 | 仅 `platform_variants.tags`，无稿件级标签表 | 新建 tag 表 + 关联 + 接口参数，代价高 |
| 文件夹/分类 | 完全无模型 | 新建模型+迁移+UI，代价高 |
| 真实稿件状态流转 | 无，status 恒 draft | 引入状态机 + 联动 publish，代价高（D4） |

> 建议首期用"搜索 + 状态chips + 平台 + 时间 + 归档 + 排序"覆盖绝大多数管理场景，标签/文件夹/状态机作为二期可选项。

---

## 7. 分阶段实施

| 阶段 | 内容 | 依赖 |
|---|---|---|
| M1 骨架 | ArticlesView + 路由 + 菜单 + 复用 DraftLibraryModal 的表格/筛选 | 无 |
| M2 数据 | 服务端分页/搜索/筛选/排序全接到 URL query（仿 publish-redesign.md:319 的 URL 同步）；真实发布状态联查 | 无 |
| M3 批量 | batch-archive / batch-delete 接口 + 前端勾选与二次确认 | 后端小改 |
| M4 版本 | 版本概览 + diff（可选） | design.md:694 未实现接口 |
| M5 清理 | StudioView 侧栏精简（移除重复筛选入口，保留"打开全部稿件→/articles"跳转），减小编辑器耦合 | M1-M2 稳定后 |

---

## 8. 验收标准

1. `/articles` 独立页可打开，菜单可见；200 篇以上稿件能分页浏览，无硬上限。
2. 搜索/状态/平台/时间/归档/排序全部走服务端，300ms 防抖，筛选条件同步到 URL 可分享/刷新保持。
3. 状态列显示真实发布状态；无 publishing 权限时降级不报错。
4. 可批量归档/删除（带二次确认）；单稿打开回跳 `/studio?id=<id>`。
5. 从创作空间"查看全部稿件"跳转到本页；编辑器逻辑仍只有一份。

---

## 9. 决策结论（✅ 已确认）

- D4 状态：**复用 `publish_jobs.status` 展示真实发布状态**（用户拍板，总览 D4）；不引入稿件独立状态机。
- 租户边界：文章属用户创作数据，**不在"管理员维护的配置类数据"范畴**（总览 D1 已明确）。文章管理页按现有所有权/工作区规则运作，不改后端所有权校验。
- Q1 页面定位：**"我的稿件"**（保持 `owner_user_id == user.id` 校验不放宽）；列表沿用工作区过滤。若后续要团队稿件管理，另立需求。
- Q3 批量删除：**只做批量归档/恢复，不做批量硬删**；单稿硬删保留在详情页二次确认，降低误删风险。
- Q4 版本 diff：本期**不做**（接口与组件都不存在）；M4 阶段可选项，待管理页稳定后再评估。
- Q5 StudioView 侧栏精简：文章管理页稳定后（M5）再做，避免一次性改动过大。
- 权限：按方案 A 复用 `studio.use`，零后端权限改动；路由 / `ui/nav.js` / 服务端 `menus` 表三处同步。
