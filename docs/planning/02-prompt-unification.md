# 提示词体系合并 · 规划

> 状态：**规划稿，未实现**。需求 02：把"文章配图提示词 / 封面图提示词"与现有提示词合并为一套，
> 用类型区分（普通 / 封面图 / 文章配图），支持正反向提示词，并在提示词管理页可生成示例图预览效果。

---

## 1. 现状 RCA：三套并存 + 一条缺失链路

| 体系 | 存储 | 归属 | 分类 | 前端页 | 问题 |
|---|---|---|---|---|---|
| ① 提示词库 | `prompt_templates` 表 | 用户私有 `owner_user_id` | 自由文本 `category` | `/prompts` PromptsView.vue | category 非枚举，无类型概念 |
| ② 风格库 | `image_styles` 表 | 全局，无归属 | 枚举 `purpose`: base/cover/inline | `/image-styles` ImageStylesView.vue（**未提交**） | 与①模型/权限/UI 完全不同，是第二套 |
| ③ AI 成文配图提示词 | `ArticleDraft` 瞬时字段 `coverPrompt`/`inlinePrompts` | 随生成任务 | cover/inline | 创作空间 StudioView.vue | 不落库、不可管理 |

**两个关键缺口：**
- **无正反向字段**：全代码库运行时没有 negative prompt 字段。`image_styles.py:22` 注释写明"负向词内嵌（生图接口没有独立 negative 字段）"——出厂封面模板把"画面避免出现：…"硬拼进单条 prompt。`ImageGenerationIn`（schemas.py:355-364）、provider 请求体（`image_provider.py:207-254`）、job payload 全链路都没有 negative。
- **无示例图预览**：提示词管理页没有任何出图能力；`ImageGenView.vue` 的"预览"是看已生成的图（预览结果，非"用这条提示词试出一张"）。

**现成依据**：`docs/design.md:483-492` 早已定义 `purpose: 'cover'|'article_illustration'|'comic_panel'|'reference'` 与 `negativePrompt?: string`，并要求"供应商不支持的字段需 UI 明示，不得静默声称生效"——**实现应向这份设计对齐，而非另起炉灶**。

---

## 2. 目标 / 非目标

**目标**
1. 一套提示词实体，用**类型字段**区分：普通 / 封面图 / 文章配图（+ 预留 base/comic_panel/reference）。
2. 每条提示词支持**正向 + 负向**两段；生图时正确传递。
3. 提示词管理页内可**生成示例图**并预览效果。
4. AI 成文产出的 cover/inline 提示词能**沉淀进库**被管理（而不是每次丢弃）。
5. 合并后的提示词库**全部为全局资源、由管理员统一维护**；普通用户只读使用。数据迁移不丢内容（含历史用户私有提示词的处理策略，见 §3.3）。

**非目标**
- 不动手绘风格库 `handdraw_styles`（其设计文档明确刻意与 `prompt_templates` 隔离，保持独立 Tab）。
- 首期不做供应商 unsupported 字段的"自动改写提示词"，只做明示 + 兜底拼接。

---

## 3. 统一数据模型设计

### 3.1 方案：合并到 `prompt_templates`（推荐）

在现有 `prompt_templates`（models.py:618-628）上扩展，`image_styles` 数据迁入后废弃：

| 字段 | 变化 | 说明 |
|---|---|---|
| `id` / `title` / `content` / `tags` | 保留 | `content` 语义变为"正向提示词" |
| `prompt_type` (新) | 枚举 | `normal` / `cover` / `illustration` / `base` / `comic_panel` / `reference`；DB CheckConstraint |
| `negative_content` (新, Text) | 默认空 | 负向提示词 |
| `summary` (新, 120) | 来自 image_styles | 卡片摘要 |
| `sort_order` (新, int) | 来自 image_styles | 排序 |
| `enabled` (新, bool) | 默认 true | 启停 |
| `owner_user_id` | **不再作为归属/可见性依据** | 全部记录全局共享；该列保留仅为兼容旧数据（历史值不删），新记录恒为 null；列表/可见性不再按 owner 过滤 |
| `preview_asset_ids` (新, JSON) | 默认 [] | 示例图 asset id 列表（关联 §5） |
| `source` (新, 16) | `manual`/`ai`/`factory` | 来源标记，便于识别 AI 成文沉淀的提示词 |

> **归属与权限（总览 D1 已定：全部管理员维护）**：合并后提示词库是全局资源。所有登录用户 `GET /prompts` 可读全部记录、可在创作/生图时选用；**增删改（POST/PATCH/DELETE）统一要求管理员权限 `image_styles.manage`**（沿用现有系统级权限码，不必新增）。`prompts.use` 仅保留为"可使用提示词"的读取权限。前端对非管理员隐藏/禁用编辑入口（用 `usePermission().disabledReason` 置灰模式）。

### 3.2 类型映射（迁移用）

| 旧来源 | 旧分类 | 新 `prompt_type` |
|---|---|---|
| 提示词库 `category`=通用/其它 | 自由文本 | `normal`（`category` 原文可并入 tags 保留） |
| image_styles `purpose=base` | base | `base` |
| image_styles `purpose=cover` | cover | `cover` |
| image_styles `purpose=inline` | inline | `illustration` |
| AI 成文 `coverPrompt` | — | 落库为 `cover`（source=ai） |
| AI 成文 `inlinePrompts` | — | 落库为 `illustration`（source=ai） |
| 出厂封面模板 | cover | `cover`（source=factory，负向句拆入 `negative_content`） |

### 3.3 迁移步骤（幂等）

1. 加列：改 `models.py` + `core/db.py` 的 `_ADDED_COLUMNS`（老表补列机制），`create_all` 对新库生效。
2. 数据迁移脚本（可重复跑）：按 `(owner_user_id, title)` 判重，把 `image_styles` 行插/更新进 `prompt_templates`；`image_styles.prompt` 拆正向/负向（用"画面避免出现："等标记切分，或保留整串 + 标注待人工拆分）。
3. **历史用户私有提示词处理**：因库改为全局管理员维护，迁移时把原 `prompt_templates` 中 `owner_user_id` 非空的记录一并纳入全局（保留 `owner_user_id` 原值仅作来源追溯，不影响可见性）；标题冲突时按 `title + owner_user_id` 保序去重，避免同名覆盖。
4. 出厂封面模板的"画面避免出现：…"拆入 `negative_content`（测试 `test_image_styles.py:248-262` 需相应更新）。
5. 保留 `image_styles` 表一个版本做回滚兜底，不立即删；`ContentItem.base_style_*` 快照机制保留（快照仍存文本，不回写）。

---

## 4. 正反向提示词贯通（生成链路改动）

| 层 | 文件 | 改动 |
|---|---|---|
| Schema | `schemas.py:355-364` `ImageGenerationIn` | 加 `negative_prompt: str \| None` |
| 路由 | `modules/ai_images.py:30-64` | payload 透传 `negativePrompt` |
| 任务队列 | `job_runner.py:485-618` `_handle_ai_image` | 读 `negativePrompt`，风格后缀/负向逻辑合一 |
| Provider | `image_provider.py:207-329` `_request_batch` / `generate_images` | 请求体加 `negative_prompt`（若网关支持）；签名加参数 |
| 能力探测 | `image_provider.py:54-83` `list_providers`/`options` | 仿 `supportsEdits` 增加 `supportsNegativePrompt` |
| 前端 | `ImageGenView.vue`、`PromptsView.vue` | 表单加负向输入框；不支持的供应商置灰并明示 |

**D2 决策落地**：
- 供应商支持 → 走独立 `negative_prompt` 字段。
- 不支持 → 服务端把负向内容按约定拼进正向 prompt 尾部（如 `, avoid: ...`），并在 job.result / UI 标注 `negative_prompt_merged=true`，**明确告知用户负向词是以文本方式生效**。
- 绝不在用户看不到的情况下声称"负向提示词已生效"。

---

## 5. 示例图 / 效果图预览

**D5 已定方案（落库、可跨会话）**：复用生图链路 + `assets` 关联。

- 提示词管理页编辑弹窗加按钮"生成示例图"：用当前 `content`（正向）+ `negative_content`（负向）+ 选定比例（封面建议补 `2.35:1` 等比例到 `SIZE_MAP`，见下）提交 `POST /ai/image-generations`。
- 生成结果 asset id 回写 `prompt_templates.preview_asset_ids`；卡片上显示缩略图，点击放大。
- 前端可直接复用 `ImageGenView.vue:880-969`（结果网格）与 `:997-1062`（预览弹窗）的 UI 片段。
- 比例现状：`SIZE_MAP`（image_provider.py:22-28）仅 3:4/9:16/4:3/16:9/1:1；`docs/design.md` 封面要 2.35:1。**已定：补该比例**（Q4），部分生图模型仅支持固定尺寸时按 D2 明示并回落。

---

## 6. AI 成文提示词沉淀

- 在 `ai_article.py` / `article_workflow.py` 落库环节（`StudioView.vue:833-897` 保存新版本时），把 `ArticleDraft.cover_prompt` / `inline_prompts` 以 `source=ai` 写入 `prompt_templates`。
- 提供开关（用户偏好或写作策略 `imagePolicy`）控制是否自动沉淀，避免污染提示词库；默认关闭、用户可"存入提示词库"。
- 这样"AI 生成配图提示词"与"手工管理的提示词"归一， StudioView 里三处生图调用统一从提示词库取。

---

## 7. 前端整合

| 页面 | 变化 |
|---|---|
| `PromptsView.vue` | 加类型 Tab（全部/普通/封面图/文章配图…）；表单加正向/负向两栏、示例图区、类型选择；"生成示例图"按钮 |
| `ImageStylesView.vue` | **废弃**：能力并入 PromptsView；路由 `/image-styles` 可保留重定向到 `/prompts?type=cover`，权限 `image_styles.manage` 映射为"管理全局提示词" |
| `StudioView.vue` | 封面/配图模板数据源从 `/image-styles` 改为 `/prompts?promptType=cover|illustration`；`composeImagePrompt` 拼正向 + 负向 |
| 权限 | 统一为"管理员维护、全员只读"：增删改要求 `image_styles.manage`，读取用 `prompts.use`；非管理员隐藏编辑入口。`ImageStylesView.vue:60-62` 普通用户请求 `includeDisabled=1` 会 403 的 bug 在合并时修掉 |

---

## 8. 分阶段实施

| 阶段 | 内容 | 风险点 |
|---|---|---|
| M1 数据模型与迁移 | 扩展 `prompt_templates`、幂等迁移 `image_styles` + 历史私有提示词、出厂模板负向拆分 | 老库补列；历史 owner 去重 |
| M2 正反向贯通 | schema→路由→payload→provider 全链路加 negative；能力探测 | 供应商是否支持未知（D2 已定策略：明示+兜底）；测试快照 |
| M3 合并 UI | PromptsView 类型 Tab + 正反向表单；ImageStylesView 废弃/重定向；StudioView 换数据源；非管理员只读 | 权限 403 bug；用户习惯变更 |
| M4 示例图 | 生成示例图按钮 + preview_asset_ids + 卡片缩略图；补 2.35:1 比例 | 生图成本；部分模型固定尺寸 |
| M5 AI 沉淀 | cover/inline 提示词以 source=ai 落库，StudioView 归一数据源；默认关闭+手动入库按钮 | 库污染（已用默认关闭规避） |

---

## 9. 验收标准

1. 提示词管理页能按"普通/封面图/文章配图"筛选，一条记录可同时编辑正向与负向。
2. 用一条含负向词的提示词出图：供应商支持时请求体带 `negative_prompt`；不支持时 UI 明示"负向词已并入正向文本"。
3. 旧 `image_styles` 的全局风格与旧 `prompt_templates` 的私有提示词迁移后都在，归属/权限正确。
4. 在提示词管理页点"生成示例图"能出图并回显在该提示词卡片上。
5. 出厂封面模板的"画面避免出现：…"已进入负向字段，不再混杂在正向 prompt。

---

## 10. 决策结论（✅ 已确认）

- D1 归属：**全部全局、管理员维护**（用户拍板）。`owner_user_id` 不再作可见性依据；增删改限 `image_styles.manage`，全员只读可用；历史私有提示词并入全局（§3.3）。
- D2 正反向：按推荐——能力探测 + 不支持时 UI 明示并兜底拼接（总览 D2）。
- D3 风格来源：与分镜共用统一 `style_presets` Registry，管理员维护（总览 D3）。
- D5 示例图：**落库、可跨会话**（总览 D5），`preview_asset_ids` 关联 `assets`。
- Q1 类型枚举：**包含 `base`**，用于稿件基础风格（现 `image_styles.purpose=base` 迁移而来），保留稿件快照机制。
- Q2 `image_styles` 表：迁移后**保留一个版本**做回滚兜底，不立即删。
- Q4 封面比例：**补 2.35:1**（`docs/design.md` 封面规格），若供应商不支持则按 D2 明示并回落。
- Q5 AI 成文沉淀：默认**关闭**，在 StudioView 提供"存入提示词库"按钮由用户/管理员主动沉淀，避免污染统一管理库。
