# 多 Agent 并行实现方案

> 状态：**执行规划稿，尚未启动 Agent**。本文回答"如何把四项功能拆给多个 Agent 并行做且不打起来"。
> 前置文档：[00-overview.md](./00-overview.md) 及 01–04 子规划（决策已定稿）。

---

## 1. 为什么不能"直接开四个 Agent 一起改"

排查当前仓库后发现两个硬约束，直接并行必然冲突：

### 1.1 硬约束 A：工作区是脏的

`git status` 显示有未提交改动，且**正好是需求 02 的半成品**：

| 状态 | 文件 |
|---|---|
| 未跟踪 `??` | `app/modules/image_styles.py`、`tests/test_image_styles.py`、`web/src/views/ImageStylesView.vue`、`docs/planning/` |
| 已修改 `M` | `models.py`(+30)、`schemas.py`、`core/db.py`、`content.py`、`StudioView.vue`(+313)、`router.js`、`ui/nav.js`、`rbac.py`、`main.py`、`rbac_service.py`、`builtin_skills.yaml`、`test_api.py` |

- 若用 **worktree** 隔离：未跟踪/未提交的文件**不会出现在新 worktree 里**，Agent C 会从零重写、丢掉已有成果。
- 若用 **单工作区**：多个 Agent 在这批文件上叠加改动，无法归因、无法回滚。

> 结论：**启动 Agent 前必须先固化基线**（见 §3 Wave 0）。

### 1.2 硬约束 B：共享热点文件

四项功能争抢同一批文件，"按功能切给 4 个 Agent"会直接冲突：

| 共享文件 | 谁要改 |
|---|---|
| `models.py` / `schemas.py` / `core/db.py` | 02（提示词列）、03（分镜 style_config） |
| `services/image_provider.py` / `job_runner.py` | 02（负向词）、03（seed）、01（handler 收敛） |
| `services/style_presets.py`（新建） | 02 建、03 用 |
| `router.js` / `ui/nav.js` | 02（风格库重定向）、04（新页面） |
| `content.py` | 02（基础风格源迁移）、04（批量接口） |
| `rbac.py` / `rbac_service.py` / `main.py` | 01/02/04 各自沾一点 |

> 结论：**必须先跑一个"地基 Agent"把所有共享改动一次性做完**，之后各 Agent 只碰自己独占的文件。

---

## 2. 总体架构：地基 + 4 路并行 + 集成

```
Wave 0  Lead（同步）      固化基线、建分支、写死契约
   │
Wave 1  Agent F 地基（同步，阻塞后续）  ← 独占所有共享文件
   │  models / schemas / db / image_provider / job_runner / style_presets / rbac / main
   ▼
Wave 2  四个 Agent 全并行（文件完全不相交）
   ├─ Agent A  01 热榜采集
   ├─ Agent B  04 文章管理 + 前端路由/菜单归并
   ├─ Agent C  02 提示词合并
   └─ Agent D  03 分镜统一风格
   │
Wave 3  Lead（同步）      逐个合并、全量测试、构建、冒烟
```

**为什么 02 和 03 可以并行**：它们原本是依赖关系（03 依赖 02 的风格注册表），但地基 Agent 会把 `style_presets` 注册表和所有列**一次性建好**，03 只需 import、不改这些文件，依赖被消除。

---

## 3. Wave 0 · Lead 固化基线（启动 Agent 前必做）

```bash
cd D:/aicode/hanyue-workspace

# 1) 把当前脏工作区整体提交到基线分支（含需求02半成品与规划文档）
git checkout -b integration/multi-agent-base
git add -A
git commit -m "chore: 固化多 Agent 实现基线（含 image_styles 半成品与规划文档）"

# 2) 为四个 Agent 建独立 worktree（Windows/Git Bash 路径用正斜杠）
git worktree add -b agent/a-hot-source   ../hanyue-wt-a   integration/multi-agent-base
git worktree add -b agent/b-articles     ../hanyue-wt-b   integration/multi-agent-base
git worktree add -b agent/c-prompts      ../hanyue-wt-c   integration/multi-agent-base
git worktree add -b agent/d-storyboard   ../hanyue-wt-d   integration/multi-agent-base
git worktree add -b agent/f-foundation   ../hanyue-wt-f   integration/multi-agent-base
```

> **依赖处理**：worktree 里没有装依赖。用主仓库的 venv 跑测试（cwd 指向 worktree 的 `apps/api`，`app` 包从当前目录解析）：
> ```bash
> D:/aicode/hanyue-workspace/apps/api/.venv/Scripts/python.exe -m pytest -q
> ```
> 前端 node_modules：在 worktree 建目录联接，否则 Vite 找不到依赖：
> ```bash
> cmd //c mklink /J D:/aicode/hanyue-wt-b/apps/web/node_modules D:/aicode/hanyue-workspace/apps/web/node_modules
> ```
> （B/C/D 三个 worktree 都要做；A 只改一个 Vue 文件，同样需要。）

**若不想用 worktree**：可退化为"单工作区 + 严格文件所有权 + Agent 不提交、只改文件"，靠 §4 的 ownership 表约束。优点是零依赖配置；缺点是隔离弱、误改难归因。推荐 worktree。

---

## 4. 文件所有权矩阵（核心，Agent 不得越界）

| 文件 / 目录 | 所有者 | 其他 Agent 的限制 |
|---|---|---|
| `app/services/style_presets.py`（新） | **F** | 仅 import，禁止编辑 |
| `app/models.py`、`app/schemas.py`、`app/core/db.py` | **F** | 禁止编辑 |
| `app/services/image_provider.py`、`app/services/job_runner.py` | **F** | 禁止编辑 |
| `app/core/rbac.py`、`app/main.py` | **F** | 禁止编辑 |
| `app/hot_sources/**`（新）、`app/services/hot_sources.py`、`app/services/hot_topics.py`、`app/modules/hot_topics.py`、`app/modules/dashboard.py`、`app/resources/scripts/manifest.yaml`、`web/src/views/OverviewView.vue`、`docs/hot-topics.md`、env 示例 | **A** | — |
| `web/src/views/ArticlesView.vue`（新）、`web/src/router.js`、`web/src/ui/nav.js`、`app/modules/content.py`、`app/services/rbac_service.py` | **B** | B 同时执行 02 的路由/菜单归并（§6） |
| `app/modules/prompts.py`、`app/modules/image_styles.py`、`app/modules/ai_images.py`、`app/services/ai_article.py`、`app/services/article_workflow.py`、`app/resources/skills/builtin_skills.yaml`、`web/src/views/PromptsView.vue`、`web/src/views/ImageStylesView.vue`、`web/src/views/ImageGenView.vue`、`web/src/views/StudioView.vue`、`app/scripts/migrate_prompts.py`（新） | **C** | — |
| `app/modules/storyboards.py`、`web/src/views/StoryboardsView.vue`、`web/src/features/storyboard/**`、`web/src/composables/useStoryboard.js` | **D** | — |
| 各自测试文件 | 对应 Agent | 只改自己功能的测试 |
| `docs/planning/**` | Lead | 全员只读 |

**铁律**：Agent 若发现需要改不属于自己的文件，**不许改**，写进 `HANDOFF.md`（每个 worktree 根目录）交给 Lead 在集成阶段处理。

---

## 5. Wave 1 · Agent F（地基）任务简报

> 目标：把所有跨功能共享改动一次性做完，且**不破坏任何现有测试**。

1. **新建 `app/services/style_presets.py`**：统一风格注册表，契约见 §7。
2. **`models.py`**：给 `prompt_templates` 加 8 列；给 `storyboard_projects` 加 `style_config` JSON 列；`owner_user_id` 改为可空。
3. **`schemas.py` + `core/db.py`**：补对应字段与 `_ADDED_COLUMNS`（老库补列），保证幂等。
4. **`image_provider.py`**：`generate_images` 增加 `negative_prompt`、`seed` 可选参数；`_request_batch` 按能力探测决定放请求体还是拼进 prompt；providers options 增加 `supportsNegativePrompt` / `supportsSeed`。
5. **`job_runner.py`**：payload 透传 `negativePrompt` / `seed` / `styleSnapshot`；**不做**业务拼装。
6. **`rbac.py`**：确认 `image_styles.manage` 作为"管理全局提示词/风格"权限码；`main.py` 注册 `style-presets` 只读路由。
7. **验证门禁（必须通过才合并）**：
   ```bash
   .venv/Scripts/python.exe -m pytest -q          # 现有全量测试不回归
   .venv/Scripts/python.exe -m compileall -q app
   ```
8. 产出 `HANDOFF.md`：记录新增列名、字段类型、style_presets API 签名，供 A/B/C/D 对照。

---

## 6. Wave 2 · 四个 Agent 任务简报

### Agent A — 01 热榜采集规范
- 按 [01-hot-source-adapter-spec.md](./01-hot-source-adapter-spec.md) 实现 `HotRawItem`/`CollectResult`/`CollectContext` 契约、`catalog.yaml`、3 类 Adapter、`ScriptAdapter`。
- 收敛 refresh 双入口：因 `job_runner.py` 归 F，A 不改它；**把"删除哪条入口"的决定写进 HANDOFF.md**，由 Lead 集成时执行。
- 首页热榜：`modules/dashboard.py` 加 `hotHighlights` + `OverviewView.vue` 卡片。
- 验证：`pytest -q tests/test_hot_topics.py` + 新增 adapter 测试 + `npm run build`。

### Agent B — 04 文章管理 + 路由菜单归并
- 按 [04-article-management.md](./04-article-management.md) 新建 `ArticlesView.vue`，复用 `DraftLibraryModal` 表格与 `api.getPage`。
- `content.py` 补 `batch-archive` / `batch-restore` 接口（不做硬删）。
- **附带执行需求 02 的路由归并**（因 router/nav 归 B）：把 `/image-styles` 重定向到 `/prompts?type=cover`，调整菜单 label/权限；具体见 02 文档 §7。
- **`content.py` 附带执行需求 02 的基础风格源迁移**：`PATCH /content/{id}/image-style` 从查 `ImageStyle` 改为查 `prompt_templates` 中 `prompt_type=base` 的记录。
- 验证：`pytest -q tests/test_content_library.py tests/test_rbac.py tests/test_permission_gate.py` + `npm run build`。

### Agent C — 02 提示词合并
- 按 [02-prompt-unification.md](./02-prompt-unification.md)：CRUD 改查 `prompt_templates`（全全局、管理员增删改、全员只读）；迁移脚本把 `image_styles` + 历史私有提示词并入全局。
- 正反向 UI 与生图提交；**底层 negative 管道由 F 建好，C 只调用**。
- `PromptsView.vue` 加类型 Tab、正反向表单、示例图按钮；`ImageStylesView.vue` 保留重定向壳；`StudioView.vue` 换数据源并加"存入提示词库"。
- 验证：`pytest -q tests/test_prompts.py tests/test_image_styles.py` + `npm run build`。

### Agent D — 03 分镜统一风格
- 按 [03-storyboard-style.md](./03-storyboard-style.md)：`storyboards.py` 的 `_queue_generation` 拼统一风格后缀 + `styleSnapshot` 快照；改 `_storyboard_references` 让风格参考图优先保留。
- 风格选项**从 F 的 `style_presets` 注册表读取，不改该文件**；seed 通过 F 建好的管道透传。
- 前端加风格选择器；项目级配置保存到 `style_config`。
- 验证：`pytest -q tests/test_storyboards.py` + `npm run build`。

---

## 7. 地基契约（F 与 C/D 的共同约定，写死在 Wave 0）

**style_presets API**
```python
@dataclass(frozen=True)
class StylePreset:
    id: str
    name: str
    prompt_suffix: str
    negative_prompt: str = ""
    reference_asset_id: str | None = None
    applicable_to: tuple[str, ...] = ()   # cover / illustration / comic_panel / base

STYLE_PRESETS: dict[str, StylePreset]
def list_presets(applicable_to: str | None = None) -> list[dict]: ...
def get_preset(style_id: str) -> StylePreset | None: ...
# GET /style-presets?applicableTo=comic_panel
```
首期内置 id：`manga` / `realistic` / `guoman` / `watercolor` + 贴图 6 档（`humanist_photo` 等）。

**prompt_templates 新增列**
`prompt_type`(str16, CK: normal/cover/illustration/base/comic_panel/reference, default normal)、`negative_content`(Text)、`summary`(str120)、`sort_order`(int)、`enabled`(bool)、`preview_asset_ids`(JSON)、`source`(str16: manual/ai/factory)；`owner_user_id` 可空。

**storyboard_projects.style_config（JSON）**
`{style_id, prompt_suffix, reference_asset_id, seed, negative_prompt}`。

**生图 payload / 能力键**
`ImageGenerationIn` 加 `negative_prompt`、`seed`；payload 键 `negativePrompt` / `seed` / `styleSnapshot`；provider options 键 `supportsNegativePrompt` / `supportsSeed`。

> 契约一旦写入 Wave 0 的 `HANDOFF.md`，C/D 按此编码，即使 F 尚未合并也能写出可编译的代码。

---

## 8. Wave 3 · 集成（Lead）

```bash
# 逐个合并（文件不相交，冲突应极少；按 B→C→D→A 顺序）
git checkout integration/multi-agent-base
git merge --no-ff agent/b-articles
git merge --no-ff agent/c-prompts
git merge --no-ff agent/d-storyboard
git merge --no-ff agent/a-hot-source

# 处理各 HANDOFF.md 里的越界请求（如 A 的 job_runner handler 收敛）

# 全量门禁
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m compileall -q app
cd apps/web && npm run build && npm run check:tokens
```

**冒烟清单**：`/articles` 打开与筛选 → `/prompts` 类型 Tab 与示例图 → 分镜生成带统一风格 → 热榜手动刷新与首页卡片。

**回退**：每个 Agent 分支保留不删；任一合并出问题，`git merge --abort` 或回退到 `integration/multi-agent-base` 重放。

---

## 9. 排期与并行度

| Wave | 参与方 | 并行度 | 阻塞关系 |
|---|---|---|---|
| 0 | Lead | 1 | 必须最先 |
| 1 | Agent F | 1 | 阻塞 Wave 2 的编码基线（契约先行可缓解） |
| 2 | A / B / C / D | **4** | 互不依赖、文件不相交 |
| 3 | Lead | 1 | 等 Wave 2 全部完成 |

> 若想让 Wave 2 真正同时开工：Wave 0 结束时就把 §7 契约写进 `HANDOFF.md`，F 与 A/B/C/D 可同时启动（F 在 `agent/f-foundation` 分支，其余按契约编码，集成时 F 先合并）。

---

## 10. 启动前需你确认的三件事

1. **脏工作区如何处理**：推荐"整体提交到 `integration/multi-agent-base`"（§3）；若你希望保留现状另作处理，请说明。
2. **隔离方式**：推荐 worktree 全隔离（§3）；若嫌依赖配置麻烦，可退化单工作区 + 严格所有权。
3. **是否现在就启动**：本文是规划；确认后我可按 Wave 0 → F → A/B/C/D 实际拉起 Agent 并跟踪进度。
