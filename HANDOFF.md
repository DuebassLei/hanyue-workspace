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

# Agent C 交接：提示词体系统一（agent/c-prompts）

门禁：`pytest -q` → **575 passed, 1 failed**（失败为本文 §7 记录的已知 flaky）；
`compileall -q app` 通过；`npm run build` 通过。新增 6 个测试（基线 570 → 576 收集）。

## 1. API 变更（`/prompts`）

| 接口 | 权限 | 变化 |
|---|---|---|
| `GET /api/v1/prompts` | `prompts.use`（全员可读） | **去掉 `owner_user_id == user.id` 过滤**，全局返回；新增查询参数 `promptType`、`enabled`（FastAPI bool）；排序改为 `sort_order ASC, created_at ASC, id ASC`。 |
| `POST /prompts` | `image_styles.manage` | 接入全部新字段；`owner_user_id` 恒为 `null`（全局资源）；校验 `prompt_type`/`source` 枚举，非法 → 400 `invalid_prompt_type`/`invalid_source`。 |
| `PATCH /prompts/{id}` | `image_styles.manage` | 支持 `promptType`/`negativeContent`/`summary`/`sortOrder`/`enabled`/`previewAssetIds`/`source`；不存在仍返回 404。 |
| `DELETE /prompts/{id}` | `image_styles.manage` | 不再校验 owner；不存在仍返回 404。 |
| `POST /prompts/from-draft` | `image_styles.manage`（新增） | AI 成文提示词沉淀。入参 `{title, coverPrompt, inlinePrompts[]}`；返回 `PromptTemplateOut[]`；`source=ai`，封面→`cover`、插图→`illustration`，标题 `<title>·封面` / `<title>·配图N`。按标题幂等，重复调用返回 `[]`。 |

字段清单（Out/In/Patch 均已在地基就位，路由层已接）：`id,title,category,content,tags,promptType,negativeContent,summary,sortOrder,enabled,previewAssetIds,source,createdAt,updatedAt`。
合法 `promptType`：`normal/cover/illustration/base/comic_panel/reference`；合法 `source`：`manual/ai/factory`。

**注意**：`DELETE /prompts/{id}` 目前不会像旧 `/image-styles` 那样把引用它的 `ContentItem.base_style_deleted` 置位（该逻辑仅在兼容壳 DELETE 中保留）。若 B 的 `content.py` 快照机制要求统一 DELETE 也标记，请在集成时决定并补。

## 2. 负向词 / seed 最后一环

`modules/ai_images.py:create_image_generation` 入队 payload 已加 `negativePrompt` / `seed`（来自 `ImageGenerationIn`）。地基的 provider / job_runner 已能据此做能力探测、兜底拼接与结果标注。新增 `tests/test_negative_prompt_pipeline.py` 直接断言任务行 payload。

## 3. `/image-styles` 兼容壳（DEPRECATED）

`modules/image_styles.py` 保留全部旧路由（OpenAPI 已标 `deprecated`），**以 `prompt_templates` 为准**读写：
`purpose→prompt_type`：base→base、cover→cover、inline→illustration；`name→title`、`prompt→content`、负向句→`negativeContent`。
Out 新增 `negativeContent` 字段；其余字段（含 `includeDisabled=1` 的 403 语义）不变。

**过渡双写（重要，集成后需清理）**：本 worktree 尚无 B 的 `content.py` 迁移（仍查 `image_styles` 表）。为保证基础风格锁定不 404，兼容壳在 POST/PATCH/DELETE/seed 时**同时写回 `image_styles` 旧表，两表共用同一 id**，旧表保留完整 prompt（含负向标记）。等 B 的 `content.py` 改为查 `prompt_templates(prompt_type=base)` 并验证通过后，应删除 image_styles.py 中所有 `models.ImageStyle` 双写代码。

出厂封面模板在 seed / 迁移时按标记 `画面避免出现：` 拆为 `content` + `negativeContent`；`tests/test_image_styles.py:248` 的断言已相应更新（正向不再含标记，负向断言 `negativeContent`）。

## 4. 迁移脚本

```bash
cd apps/api
.venv/Scripts/python.exe -m app.scripts.migrate_prompts
```

- 文件：`app/scripts/migrate_prompts.py`（新建，可调用 `migrate(db) -> dict` + `__main__`；**不在应用启动时自动执行**）。
- 行为：`image_styles` 行迁入 `prompt_templates`（purpose 映射见 §3；prompt 按 `画面避免出现：` 拆正负向）；按 `(title, owner_user_id)` 判重，重复跑不新增；历史私有提示词 `owner_user_id` 原值保留仅作追溯、不参与可见性；新记录恒 null。
- 统计输出：`legacy_prompts_kept / styles_inserted / styles_skipped`。产出 `source`：出厂封面 `factory`，其余 `manual`。
- 测试：`tests/test_migrate_prompts.py`（含拆分、映射、幂等、出厂负向断言）。
- 建议集成顺序：先部署地基新列 → 启动播种（此时 prompt_templates 已有出厂行）→ 跑迁移脚本（会自动判重跳过出厂行）→ 再启用新前端。

## 5. AI 成文提示词沉淀

- `services/ai_article.py` 新增 `deposit_article_prompts(db, cover_prompt, inline_prompts, title_prefix)` 与 `deposit_draft_prompts(db, ArticleDraft, ...)` 便捷重载；`source=ai`、按标题幂等。
- `article_workflow.py` 未改动（该文件设计原则是纯函数/只读查询，不写库）。
- HTTP 入口为 `POST /prompts/from-draft`（§1），由 StudioView「存入提示词库」按钮手动触发；**默认不自动沉淀**，符合 Q5。
- 前端：`StudioView.vue` 在「画面描述」区加按钮，非管理员 `disabledReason('image_styles.manage')` 置灰。

## 6. 示例图流程（D5 落库、跨会话）

1. 管理员在 `PromptsView.vue` 编辑弹窗里填正向 `content` + 负向 `negativeContent`、选比例（3:4/1:1/16:9/9:16），点「生成示例图」。新提示词需先保存（按钮在保存前禁用）。
2. 前端 `POST /ai/image-generations`（带 `negativePrompt`），轮询 `GET /jobs/{jobId}`。
3. 成功后从 `job.result.assetIds` 取 asset id，追加进 `previewAssetIds` 并 `PATCH /prompts/{id}` 回写。
4. 卡片与编辑弹窗用 `fetchBlobUrl('/assets/{id}/raw')` 显示缩略图，点击放大；素材删除时缩略图静默忽略。
5. 结果标注 `negativePromptMerged` 时提示「负向词已并入正向文本」（D2 明示）。

前端其余变更：`ImageGenView.vue` 提示词选择器支持类型过滤、套用时同时填入正/负向、提交带 `negativePrompt`/`seed` 与对应输入框；`StudioView.vue` 封面/基础/配图风格源改为 `/prompts?promptType=cover|base|illustration&enabled=true`（前端做字段归一化），生图请求带 `negativePrompt`；`ImageStylesView.vue` 改为重定向壳（跳 `/prompts?type=cover`），与 B 的 router 重定向双保险。`PromptsView.vue` Tab 改为类型（含手绘风格入口），全局只读 + 管理员编辑。

## 7. 需要 Lead / 其他 Agent 处理（越界项）

1. **SIZE_MAP 补 2.35:1（Q4）**：`services/image_provider.py:SIZE_MAP`（F 所有）未加该比例，当前 `SIZE_MAP.get(ratio, "1024x1536")` 会静默回落 3:4。C 未改该文件，仅在 `PromptsView.vue` 明示「2.35:1 暂不支持，回落 3:4」。若需真正支持，请 Lead 决定是否在 SIZE_MAP 加 `"2.35:1": "<供应商接受的尺寸>"`（需确认供应商支持自定义尺寸）；否则保留前端明示 + 回落。
2. **已知 flaky 测试（本环境稳定失败，非本分支回归）**：`tests/test_rbac.py::test_reset_password_kicks_existing_session_and_not_self`。根因在 `core/deps.py:67` 用秒级 `pwdv < epoch_seconds(password_changed_at)`，登录与重置落同一秒时旧会话不失效。本次全量重跑两次均失败（200 != 401），隔离运行亦失败；未在地基分支验证到通过，属 A/B 范围。请 Lead 安排把比较改为 `<=` 或提高时间戳精度（password_changed_at 带微秒且 token 内记录同精度）。其余 575 项全通过。
3. **双写清理（§3）**：B 的 `content.py` 迁移合入并验证后，删除 `image_styles.py` 的 `models.ImageStyle` 双写（POST/PATCH/DELETE/seed 四处）。
4. **统一 DELETE 与稿件快照（§1 末）**：决定 `/prompts` 删除时是否也要标记 `ContentItem.base_style_deleted`。
5. **PromptTemplate CK 仅对新库生效**（地基 HANDOFF §1 已说明）：老库补列无 CheckConstraint，如需老库强约束请 Lead 另写迁移。
