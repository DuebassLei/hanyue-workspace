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

## Agent A 交接（agent/a-hot-source，需求 01）

> 门禁：API 全量 **586 passed**（570 基线 + 16 新增）；`compileall -q app` 通过；`npm run build` ✓ built in 16.52s。
> 已知基线 flaky `tests/test_rbac.py::test_reset_password_kicks_existing_session_and_not_self` 在第一轮全量中出现一次，原样重跑即 586 全过（与 F 记录一致，非本分支回归）。

### 1. 实现说明

新建 `apps/api/app/hot_sources/` 包，`services/hot_sources.py` 收缩为兼容层（re-export + `_rows_*` 解析入口 + `collect` 同名转发），不改 `collect(platforms) -> (rows, notes)` 签名，`hot_topics.refresh()` 与 `scheduled_scripts/hot_topics.py:refresh` 都走这一套。

| 文件 | 职责 |
|---|---|
| `contracts.py` | `HotRawItem` / `CollectResult` / `CollectContext` / `HotSourcesError`；`safe_get` 统一走 ssrf，统一超时/重试/错误码 |
| `catalog.yaml` + `catalog.py` | 单一真相源；`enabled_platforms` / `enabled_sources` / `source_catalog` 全部读它，消除散落 `*_SOURCES` 常量 |
| `parsers.py` | 原 `_rows_*` 解析函数原样迁入，行为等价（21 项旧测试全过） |
| `adapters.py` | `JsonAdapter` / `TextPatternAdapter` / `ScriptAdapter`；单源失败返回 `ok=false` 不抛 |
| `__init__.py` | 编排器：按 catalog 遍历、统一超时重试、汇总 rows/notes、全源失败抛 `HotSourcesError` |

### 2. catalog 平台清单（22 个，顺序即采集顺序）

| id | name | kind | parser |
|---|---|---|---|
| toutiao | 今日头条 | json | direct |
| baidu | 百度 | json | direct |
| douyin | 抖音 | json | direct |
| weibo | 微博 | json | agg |
| zhihu | 知乎 | json | agg |
| aihot_hot | AI·热点 | json | aihot |
| aihot_feed | AI·资讯 | json | aihot |
| aihot_agent_hot | AIHOT 热点与早报 | markdown | aihot_agent |
| aihot_agent_daily | AIHOT 日报 | markdown | aihot_agent |
| wechat_10w | 微信 10w+ 爆文池 | json | wechat_10w |
| wechat_search | 微信文章搜索 | html | wechat_search |
| openrank_today | 开源榜·每日飙升 | html | openrank |
| openrank_weekly | 开源榜·每周飙升 | html | openrank |
| openrank_stars | 开源榜·Star 总榜 | html | openrank |
| uapi_bilibili | B站 | json | uapi |
| uapi_tieba | 贴吧 | json | uapi |
| uapi_36kr | 36氪 | json | uapi |
| uapi_huxiu | 虎嗅 | json | uapi |
| uapi_ithome | IT之家 | json | uapi |
| uapi_coolapk | 酷安 | json | uapi |
| uapi_juejin | 掘金 | json | uapi |
| uapi_sspai | 少数派 | json | uapi |

### 3. Adapter 用法：新增一个平台

1. 在 `catalog.yaml` 加一条：`id`（稳定，落库后不改）/ `name`（展示名，也是 `HOT_TOPICS_PLATFORMS` 过滤值）/ `kind` / `adapter`（模板入口）/ `parser` / `url` / `enabled` / `timeout_seconds` / `retry` / `tags`。
2. 结构常见的 JSON 源：`parser: direct` + `options.items_path` / `title_keys` / `hot_keys` / `url_keys`，**0 行代码**即可产出数据。
3. Markdown/HTML：选 `parser: aihot_agent` / `wechat_search` / `openrank`。
4. 完全自定义：写 `app/hot_sources/scripts/<name>.py` 的 `collect(ctx: CollectContext) -> CollectResult`，catalog 设 `kind: script` + `adapter: app.hot_sources.scripts.<name>:collect`（受控进程内扩展，随代码评审部署，非在线任意执行）。
5. 解析到 0 行会自动标 `selector_may_changed`；若该源「本来就可能没数据」，加 `allow_empty: true` 关闭该标注。
6. 跑 `pytest tests/test_hot_sources.py`；`docs/hot-topics.md` 的表格只是镜像，同步一下即可。

### 4. notes / error_code 清单

error_code（`CollectResult.error_code` / `HotSourcesError.code`）：

| code | 触发 |
|---|---|
| `fetch_failed` | httpx 网络异常，重试后仍失败 |
| `http_status` | 3xx/4xx/5xx（`safe_request` 不把重定向页当成功） |
| `ssrf_blocked` | `ssrf.UnsafeUrl`：协议/端口/内网/云元数据/重定向越界 |
| `invalid_response` | JsonAdapter 拿到非 JSON |
| `adapter_error` | 解析函数抛异常 / 编排器兜底 / 脚本 import 或调用失败 |
| `unknown_parser` | catalog 的 parser 未注册 |
| `config_missing` | ScriptAdapter 的 adapter 未配成 `模块:函数` |
| `invalid_result` | 脚本没返回 `CollectResult` |
| `all_sources_failed` | 编排器判定全源失败，`refresh` 转 502（retryable=true） |

notes（`GET /hot-topics/degraded` 以 `{"dim": "source", "reason": ...}` 输出）：
- `{平台}：N 条`（成功条数）
- `{平台}：selector_may_changed（解析到 0 条，页面结构可能改版）`
- `{平台}：未配置关键词（HOT_TOPICS_WECHAT_KEYWORD），已跳过`（不算失败）
- `{平台}：失败（{error_code}）...`

### 5. 未改 / 越界项（交 Lead 集成期决策）

1. **`job_runner._execute` 的 handlers 分派未动**：F 在 §3 写「留给 Agent A」，但本任务书明确不要收敛 refresh/digest 双入口，故未删任何 handler。两个入口当前都调 `hot_topics.refresh()` → 新 `collect()`，行为一致；是否二选一由 Lead 定。
2. **`infra/docker-compose.yml` 的 `x-app-env` 锚点未加 HOT_TOPICS_***（compose 不在独占清单）：`infra/.env.example` 已补齐 6 个变量，但 compose 采用显式白名单透传（`environment: *app-env`），Docker 部署要生效需在该锚点同样加 `HOT_TOPICS_PLATFORMS` / `HOT_TOPICS_TIMEOUT` / `HOT_TOPICS_REFRESH_COOLDOWN` / `HOT_TOPICS_WECHAT_10W_URL` / `HOT_TOPICS_WECHAT_KEYWORD` / `HOT_TOPICS_API_URL`。
3. **并发与响应快照回落未做**：并发属规范 M2 可选项（SSRF 逐跳校验下需额外保证结果顺序稳定），快照回落按 Q2 明确首期不做；当前串行 + catalog 统一超时/重试，三条不变量（可复现/幂等/不伪造）全部保留。
4. **`schemas.py` 不可改**：`/dashboard` 的 `hotHighlights` 用 `modules/dashboard.py` 里的 `DashboardWithHotOut(schemas.DashboardOut)` 本地子类输出，CamelModel 自动转 camelCase。
5. **notes 不落库**（`models.py` 不可改）：`services/hot_topics.py` 进程内缓存最近一轮 notes，`/degraded` 可读；服务重启清空，多 worker 下只反映处理该次刷新的 worker。把 notes 写进 `job.result` 需改 `job_runner`，留集成期。
6. **`fetch_hot_topics()` 兼容层原样保留**（创作空间灵感面板在用）；`scheduled_scripts/hot_topics.py` 代码未改（本来就调同一 refresh），只在 manifest 描述里指向 catalog。
