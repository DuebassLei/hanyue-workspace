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

## 7. Agent D 交付说明（agent/d-storyboard）

需求 03：漫画分镜组级统一风格。只改独占文件：`modules/storyboards.py`、`tests/test_storyboards.py`、`web/src/composables/useStoryboard.js`、`web/src/features/storyboard/{StoryboardWorkspace,ShotInspector}.vue`。未碰 models/schemas/db/job_runner/image_provider/style_presets 及 A/B/C 任何文件。

### 7.1 style_config 读写路径
- 新建：`POST /storyboards` 的 `StoryboardProjectCreate.style_config` → `create_project` 写入 `row.style_config`（`payload.style_config or {}`）。
- 修改：`PATCH /storyboards/{id}` 走原有 `model_dump(exclude_unset=True)` 通用循环，`styleConfig` 直接 `setattr`；传 `{}` 清空配置（传 null 会被循环跳过，故用空对象清空）。
- 下发：`_project_out` 现在下发 `project.style_config`（非 dict 时兜底 `{}`），GET/list/trash/board 导出都带真实值。
- 结构仍为 `{style_id, prompt_suffix, reference_asset_id, seed, negative_prompt, style_name?}`；前端额外存 `style_name` 用于展示，`prompt_suffix/negative_prompt/reference_asset_id` 在选预设时冗余写入快照。

### 7.2 prompt 拼装顺序（_queue_generation，约 915 行）
```
prompt = "\n".join(part for part in (style_suffix, subject, *labels, consistency) if part)
```
- 统一风格后缀**置于最前**作为画风锚点；其后依次是画面主体（手写 prompt 或 frame/action/dialogue/景别/视角/运镜）、参考图 labels、角色一致性句 `consistency`。
- 后缀来源 `_style_context`（约 392 行）：优先用 `style_config.prompt_suffix`（选预设时已冗余存储）；缺失则按 `style_id` 调 `services.style_presets.get_preset()` 取 `prompt_suffix`，`negative_prompt`/`reference_asset_id` 同理回退。未配置任何风格时后缀为空，旧行为完全不变。
- 改项目风格后，新入队镜头用新风格；旧镜头不改（不批量重生成），历史靠快照追溯。

### 7.3 payload 快照结构（styleSnapshot）
`_queue_generation` 在 payload 里新增三键（地基 job_runner 已声明透传）：
- `negativePrompt`: `style_config.negative_prompt`（或注册表回退值），无则 `None`。
- `seed`: `style_config.seed`，无则 `None`；是否真正生效由 `image_provider` 按 `supportsSeed` 能力探测决定（前端 UI 已明示）。
- `styleSnapshot`: 有任一风格配置时为 camelCase 快照 `{styleId, styleName, promptSuffix, negativePrompt, referenceAssetId, seed}`；完全未配置风格时为 `None`。仿 `characterSnapshot` 写法，原样透传进 `job.result` 供历史追溯。

### 7.4 参考图槽位新规则（_storyboard_references，约 425 行）
- 丢弃优先级由「先丢风格」改为 **「先丢地点 → 多余角色 → 镜头风格资产 →（兜底）项目风格图」**，即"风格最后丢"。草图 cap 4、正式图 cap 3+1 草图保持不变。
- `style_config.reference_asset_id` 作为**常驻槽**：不在镜头 `asset_refs` 里也进参考图，内部以 `_pinned` 标记避免 trim 时优先牺牲；草图/正式图都带。清理 `_pinned` 后才返回槽位。
- 仍校验每个参考图 `asset.owner_user_id == user.id`，不属于本人会 404。
- 注意：这些槽位来自 `project.assets.*` 的 legacy 风格资产（AssetBoard「风格」tab），与 `style_config` 指向的统一预设是两套；前端 Workspace 的风格参考图下拉已从项目视觉资产里取值，可引用 AssetBoard 登记的资产。

### 7.5 更新的测试断言（tests/test_storyboards.py）
- `test_reference_cap_drops_style_before_location` → 更名 `...drops_location_before_style`：草图断言风格保留、地点被挤掉（`referenceAssetIds == [hero, second, third, look]`、`"style 水墨" in prompt`、`"location 夜站台" not in`）；正式图因风格"最后丢"会挤掉一个角色，断言 `referenceAssetIds == [hero, second, look, hero]`、`"style 水墨" in final_prompt`。
- `test_handwritten_prompt_still_appends_consistency`（315 行 `assert prompt.startswith(custom)`）**未改**：该项目未配 style_config，后缀为空，手写 prompt 仍在开头，新语义不冲突。
- `test_generation_prompt_is_exposed_for_view_and_copy`（590 行导出=入队 prompt）**未改**：导出仍取 payload 原文，风格后缀自动包含其中。
- 新增 5 个测试：style_config 新建/GET/PATCH/清空往返；风格后缀前置 + styleSnapshot + negativePrompt/seed 落 payload；只存 style_id 时从注册表展开后缀；风格参考图常驻且草图/正式图都在；改风格只影响新镜头（旧 job prompt 快照保留旧风格）。
- 门禁：`pytest` 全量 574 passed + 1 deselected（已知 `test_rbac::test_reset_password_kicks_existing_session_and_not_self` flaky，隔离重跑通过）+ `compileall -q app` 通过；`npm run build` 通过。

### 7.6 前端
- `useStoryboard.js`：新增 `stylePresets`/`stylePresetsLoading`、`loadStylePresets()`（GET `/style-presets?applicableTo=comic_panel`，只拉一次）、`saveStyleConfig(config)`（走 `patchProject({ styleConfig })`）。
- `StoryboardWorkspace.vue`：项目栏下加「统一画风」选择器（选预设即保存 style_config），展示后缀说明；「风格细节」可展开配风格参考图（选项来自项目视觉资产）、固定 seed、负向词。seed 依 `/ai/image-provider/status` 的 `supportsSeed` 明示：支持=生效提示、不支持=禁用并说明、拿不到能力=「取决于图像模型」。并提示「改风格只影响新镜头，已有镜头需重新生成」。
- `ShotInspector.vue`：generate(stage) 仍只传 `modelId`（风格由后端从项目读，无需前端透传）；新增当前组级风格提示行，说明新镜头套用、旧镜头需重生成。
- `AssetBoard.vue`：「风格」tab 保持不动（legacy 视觉资产登记，与统一预设两回事）；其资产可通过 Workspace 的参考图下拉被 `style_config.reference_asset_id` 引用，形成联动。

### 7.7 越界项 / 待 Lead 决策
- `image_provider` 的 seed 真正落地依赖 C 在 `ai_images.py:create_image_generation` 入队时透传 `negativePrompt`/`seed`（HANDOFF §5 已列）：分镜侧 payload 已带这两键，`job_runner` 已能读；普通「图片生成」入口当前仍不带，属 C 范围，我未改。
- seed 能力探测走保守策略（只有 `options.supportsSeed is True` 才算支持）；环境变量供应商恒 False，UI 会禁用 seed 输入。
- 未做旧镜头批量重生成（决策 Q5）；风格仅项目级，无场景/镜头覆盖（决策 Q1）；`StoryboardGenerateIn.reference_asset_ids` 仍忽略（决策 Q4）。
