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
