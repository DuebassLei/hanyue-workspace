# Agent F 地基交接说明（agent/f-foundation）

> 基线：564 passed。本分支门禁：**570 passed**（564 基线 + 6 新增），`compileall -q app` 通过。
> 所有改动仅限 F 独占文件；未碰任何前端或其他 Agent 的模块文件。
>
> **已知基线 flaky（非本分支回归）**：`tests/test_rbac.py::test_reset_password_kicks_existing_session_and_not_self`
> 依赖秒级时间戳，登录与重置密码落在同一秒时旧会话不会被判失效，偶发 401 断言失败。
> 已在未改动的基线 worktree 上复现同一失败；全量重跑即可通过（本次 570 passed）。
> 根因在 `app/core/deps.py:67`（`pwdv < epoch_seconds(password_changed_at)`）与该测试之间，
> 属 B/集成范围，我未改。

## 1. 新增列与类型

### `prompt_templates`（`app/models.py` + `app/core/db.py:_ADDED_COLUMNS`）

| 列名 | ORM 类型 | 默认 | 老库补列 DDL | 说明 |
|---|---|---|---|---|
| `prompt_type` | `String(16)` | `normal` | `VARCHAR(16) DEFAULT 'normal'` | CK `ck_prompt_templates_type IN ('normal','cover','illustration','base','comic_panel','reference')` |
| `negative_content` | `Text` | `""` | `TEXT DEFAULT ''` | 负向提示词 |
| `summary` | `String(120)` | `""` | `VARCHAR(120) DEFAULT ''` | 卡片摘要 |
| `sort_order` | `Integer` | `0` | `INTEGER DEFAULT 0` | 排序 |
| `enabled` | `Boolean` | `True` | `BOOLEAN DEFAULT true` | 启停 |
| `preview_asset_ids` | `JSON` | `list` | `JSON DEFAULT '[]'` | 示例图 asset id 列表 |
| `source` | `String(16)` | `manual` | `VARCHAR(16) DEFAULT 'manual'` | CK `ck_prompt_templates_source IN ('manual','ai','factory')` |
| `owner_user_id` | `ForeignKey→users.id` | `None` | 原列改 nullable | 新记录恒为 null 表示全局；历史值保留不删 |

### `storyboard_projects`

| 列名 | ORM 类型 | 默认 | 老库补列 DDL |
|---|---|---|---|
| `style_config` | `JSON` | `dict` | `JSON DEFAULT '{}'` |

结构约定：`{style_id, prompt_suffix, reference_asset_id, seed, negative_prompt}`（见 03 文档 §3.1）。

**注意**：CheckConstraint 只对新库 `create_all` 生效；`_ADDED_COLUMNS` 只补列，老库不会追加 CK。如集成时要求老库也有约束，需 Lead 另写迁移。`JSON DEFAULT '[]'/'{}'` 在 SQLite 与 PG 均可隐式转换，保证补列后已有行不是 NULL（否则 `PromptTemplateOut.preview_asset_ids: list[str]` 会校验失败）。`init_db()` 仍幂等（按 information_schema / inspector 判存）。

## 2. style_presets API

**服务层** `app/services/style_presets.py`：

```python
@dataclass(frozen=True)
class StylePreset:
    id: str
    name: str
    prompt_suffix: str
    negative_prompt: str = ""
    reference_asset_id: str | None = None
    applicable_to: tuple[str, ...] = ()

STYLE_PRESETS: dict[str, StylePreset]
def list_presets(applicable_to: str | None = None) -> list[dict]: ...  # 返回 snake_case 键
def get_preset(style_id: str) -> StylePreset | None: ...
```

- `list_presets()` 返回字典的键为 **snake_case**（`prompt_suffix` / `applicable_to` 等），保留注册顺序；未知 `applicable_to` 返回 `[]` 不抛错。HTTP 层经 `CamelModel` 自动转成 camelCase。
- 内置 10 项 id（顺序即注册顺序）：`manga`、`realistic`、`guoman`、`watercolor`、`humanist_photo`、`minimal_tech`、`flat_illustration`、`handdraw_note`、`info_card`、`retro_nostalgia`。
- 前 4 项 `prompt_suffix` 取自 `services/character_images.py:11-16` 的中文风格词，`applicable_to=("cover","illustration","comic_panel")`；后 6 项取自 `services/sticker_styles.py` 的 `prompt_suffix`，`applicable_to=("cover","illustration")`。
- 全部 `negative_prompt=""`、`reference_asset_id=None`（契约默认值；风格词本身已含 `no illustration` 等约束，不重复塞负向）。注册表是代码常量、只读，**没有写接口**。

**路由** `app/modules/style_presets.py`（已在 `app/main.py` 两处注册）：

```
GET /api/v1/style-presets?applicableTo=comic_panel
```

- 任何登录用户可读（仅挂 `CurrentUser` 鉴权，不挂 `require_permission`）；未登录 401；无 POST/PATCH/DELETE（405）。
- 响应字段：`id, name, promptSuffix, negativePrompt, referenceAssetId, applicableTo`。

## 3. 负向词 / seed 能力键与兜底

- 能力键（`ModelCatalogEntry.options`）：`supportsNegativePrompt`、`supportsSeed`（常量 `CAPABILITY_NEGATIVE_PROMPT` / `CAPABILITY_SEED`）。
- **保守探测**：只有 `options[key] is True` 才算支持；缺失/None/False 一律不支持。环境变量供应商恒为 False。
- `generate_images(..., negative_prompt=None, seed=None, merge_meta=None)`：
  - 支持负向词 → 请求体加独立 `negative_prompt`；不支持 → 拼进正向尾部 `, avoid: ...`，并写 `merge_meta["negative_prompt_merged"]=True`。
  - 支持 seed → 请求体加 `seed`，写 `merge_meta["seed_applied"]=True`；不支持 → 忽略并写 `merge_meta["seed_ignored"]=True`。
  - `merge_meta` 是可选 out 参数；不传时行为与改动前完全一致，不影响现有调用点。
- `job_runner._handle_ai_image` 从 payload 读 `negativePrompt` / `seed`（首调用与 edits 降级重试都传），把 `merge_meta` 写进 job.result：
  `negativePromptMerged`、`seedApplied`、`seedIgnored`、以及原样透传的 `styleSnapshot`（无则 None）。seed 非法字符串会忽略并标注 `seedIgnored`，不会让任务崩掉。
- 没有改 `_execute` 的 handlers 分派结构（留给 Agent A）。

## 4. 已确认无需改动

- `app/core/rbac.py`：`image_styles.manage` 权限码已存在（PERMISSION_CATALOG 第 55 行），未新增无关权限码。

## 5. 我没有改、但集成时需要处理的文件

| 文件 | 所有者 | 需要做的事 |
|---|---|---|
| `app/modules/ai_images.py` | C | `create_image_generation` 入队 payload 目前**没有** `negativePrompt`/`seed`；需从 `ImageGenerationIn` 透传，否则负向/seed 管道从 HTTP 入口进来是空的。`styleSnapshot` 由分镜侧产生，不由这里发。 |
| `app/modules/prompts.py` | C | CRUD 目前只处理旧 5 个字段，新字段（尤其 `prompt_type`/`negative_content`/`source`）需要接上；列表按 §02 D1 改为全局可读（去掉 `owner_user_id == user.id` 过滤）、增删改加 `image_styles.manage`。 |
| `app/modules/storyboards.py` | D | `_project_out` 目前不构造 `style_config`（我给 Out 加了 `= None` 默认值所以不会报错，但字段恒为 null，需下发真实值）；PATCH 项目接口接 `style_config`；`_queue_generation` payload 带 `negativePrompt`/`seed`/`styleSnapshot`；风格选项从 `GET /style-presets?applicableTo=comic_panel` 取。 |
| `app/modules/content.py` | B | §02 §6：`PATCH /content/{id}/image-style` 改查 `prompt_templates` 中 `prompt_type=base`；批量归档接口。 |
| `app/scripts/migrate_prompts.py`（新） | C | `image_styles` + 历史私有提示词迁移脚本（含出厂模板负向拆分）。 |
| `app/services/image_provider.py:SIZE_MAP` | F（本文件） | 02 文档 Q4 要求补 `2.35:1`（封面规格）。属示例图阶段 M4，不在地基契约 §7 范围，我未加；请 Lead 决定由 C 调用时明示还是直接补进 SIZE_MAP。 |
| 前端 `router.js` / `ui/nav.js` / 各 View | B/C/D | 风格库重定向、类型 Tab、风格选择器等（按各自规划）。 |

## 6. 偏差说明

1. **测试文件路径**：任务书写 `app/tests/test_style_presets.py`，但项目实际测试目录是 `apps/api/tests/`（`conftest.py` 的 `client`/`login` 夹具只在该目录生效），故放在 `tests/test_style_presets.py`。按"代码现状为准"处理。
2. **PromptTemplate CK**：任务书只要求 `prompt_type` 的 CK，我额外加了 `source` 的 CK（同属枚举契约），写法与 `ImageStyle` 一致；若 Lead 认为多余可删 `ck_prompt_templates_source`。
3. **Schema 默认值**：Out 的新标量字段给了与 ORM 一致的默认值（如 `prompt_type="normal"`），`style_config` 用 `dict | None = None`，确保 D 未改 `_project_out` 时现有测试不回归。
4. `_ADDED_COLUMNS` 的 JSON 补列带 `DEFAULT '[]'/'{}'`，与项目现有纯 `"JSON"` 写法略有差异，目的是避免老库已有行读到 NULL 导致 Out 校验失败。

---

## 7. Agent B 交接（agent/b-articles）

> 门禁：**574 passed**（570 基线 + 4 新增），`compileall -q app` 通过，`npm run build` 通过。
> 改动仅限 B 独占文件 + 允许附带的 `tests/test_image_styles.py`。

### 7.1 ArticlesView.vue 页面结构（`apps/web/src/views/ArticlesView.vue`）

- 页头：标题 + 「+ 新建稿件」（`POST /content` 后跳 `/studio?id=<新id>`）。
- 工具栏：已选计数 / 批量归档 / 批量恢复（均二次确认）/ 排序下拉。
- 状态 chips：全部 / 草稿 / 待处理 / 排期中 / 进行中 / 需处理 / 成功 / 已取消。
- 筛选条：搜索框（300ms 防抖）+ 平台 + 更新时间 + 含已归档开关。
- 表格：勾选框 / 标题 / 状态 / 版本 `vN` / 平台数 / 更新时间 / 操作（打开、归档/恢复、删除）。
- 分页：`api.getPage()` 真 keyset「加载更多」；空态、错误态齐备。
- 所有筛选条件同步 URL query（`q/status/platform/range/sort/archived`），支持刷新/分享/浏览器前进后退。
- 状态列：`GET /publishing/jobs?contentItemId=<id>&limit=1` 取最新 `status`；无 `publishing.view` 时用 `disabledReason` 置灰为「—」，不发请求、不报错。

### 7.2 路由 / 菜单改动

- `router.js`：新增 `/articles`（name=`articles`，`meta.permission='studio.use'`）；`/image-styles` 改为 `redirect: { path:'/prompts', query:{ type:'cover' } }`，原 name 与组件引用已移除。
- `ui/nav.js`：workspace 组 studio 之后加 `articles`；system 组移除 `image-styles`（风格库并入提示词库）。
- `rbac_service.py:MENU_SEED`：workspace 组加 `("articles", "文章管理", "文章", "articles", "studio.use")`，system 组删 `image-styles` 条目。

**需要 Lead 在菜单管理页补/删（已初始化 DB 不会自动删旧菜单）**：
1. 在「工作区」分组下新增子菜单：key=`articles`、label=`文章管理`、short=`文章`、path=`/articles`、icon=`articles`、permission=`studio.use`、kind=`page`、visible=`true`、sort 紧跟 studio。
2. 删除旧「风格库」菜单（key=`image-styles`）。新库 `create_all` + `_seed_menus` 已自动处理这两项；已有库需手工操作。

### 7.3 后端接口

1. `POST /content/batch-archive`
   - body：`{ ids: string[], archived: boolean }`（CamelModel；`ids` 会去重）
   - 响应：`{ updated: number, skipped: number }`
   - 语义：软归档/恢复，**不做硬删**；每条校验 `owner_user_id == user.id`，非本人/不存在 id 计入 `skipped` 而不让整批 404；单稿硬删仍走 `DELETE /content/{id}`（前端二次确认）。
2. `GET /content` 新增 `publishStatus` 查询参数（逗号分隔多值；特殊值 `none` = 无关联发布任务），走 `PublishJob` EXISTS 联表。
   - 起因：稿件自身 `status` 恒为 `draft`，文章页状态 chips 若只按 `status` 过滤就是前端假过滤；加此参数让状态筛选真正走服务端，符合验收标准 2。
   - 不要求 `publishing.view`（只是一次联表过滤）；状态**展示**仍受该权限约束。

### 7.4 基础风格源迁移与测试调整

- `PATCH /content/{id}/image-style` 已改为查 `prompt_templates`：`prompt_type='base' AND enabled=true`；快照 name 取 `title`、prompt 取 `content`。错误码 `style_not_base`/`style_disabled`/`style_not_found` 语义不变。
- `tests/test_image_styles.py`：
  - 新增 `_prompt_tpl()` 直接落 `prompt_templates`（不走 `/prompts` 写接口，C 的类型字段尚未落地，避免测试依赖未合入代码）。
  - `test_delete_marks_snapshot_deleted_but_keeps_prompt` / `test_lock_clears_and_rejects_bad_styles` 改用 prompt_templates；删除标记改用直接 DB 更新模拟（真实删除侧行为归 C）。
  - 新增 `test_lock_base_style_reads_unified_prompt_template`：断言 title/content 映射，并断言旧 `image_styles` id 已不再可锁定（`style_not_found`）。
- `tests/test_content_library.py` 新增 3 例：批量归档/恢复跳过他人稿件、空 ids no-op、`publishStatus` 服务端筛选。

### 7.5 需要 Lead / 其他 Agent 跟进

1. **【重要】`base_style_deleted` 标记缺口**：当前只有 `image_styles.py:197-198` 在删除风格时把引用它的 `ContentItem.base_style_deleted` 置 true。基础风格源已迁到 `prompt_templates`，请让 C 在 `prompts.py` 的 DELETE 里补同样的更新（`update(ContentItem).where(base_style_id==id).values(base_style_deleted=True)`），否则从提示词库删掉基础风格后，稿件侧不会标记「快照已失效」。我的测试用直接 DB 更新模拟了该行为。
2. **StatusTag.vue 无需改**：已核对 `MAP` 覆盖全部 8 个 `publish_state` 状态（`pending_review/approved/queued/publishing/succeeded/failed/needs_attention/cancelled`）及 `draft`。若 C/D 后续新增状态，再由其所有者补映射。
3. C：`StudioView.vue:246` 仍在请求 `/image-styles?purpose=`，需按 02 §7 改为 `/prompts?promptType=cover|illustration`；我未改 StudioView（非我独占）。
4. M5「创作空间侧栏加『查看全部稿件 → /articles』跳转」未做：StudioView 属 C，且规划明确放在管理页稳定后。

### 7.6 越界项（未改）

未碰 `models.py`/`schemas.py`/`core/*`/`image_provider.py`/`job_runner.py`/`main.py`/`rbac.py`/`modules/style_presets.py`/`services/style_presets.py`/`modules/prompts.py`/`modules/image_styles.py`/`modules/ai_images.py`/`modules/storyboards.py`/`modules/hot_topics.py`/`modules/dashboard.py`/`components/StatusTag.vue`/C/D/A 的所有 View。
