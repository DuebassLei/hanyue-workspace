# 成稿台 · 排版与剩余能力：接口契约（并行开发用）

本文件是并行开发的唯一契约。**先读它，再读代码。契约字段就是前后端对齐的凭据，改契约必须同步改本文件并说明原因。**

## 0. 硬约束（违反会直接冲突）

本轮由多个 agent 并行改同一个仓库，各自只许碰自己名下的**新文件**。以下文件**任何人不得修改**：

| 文件 | 原因 |
|---|---|
| `apps/api/app/main.py` | 主控已注册三个新 router |
| `apps/api/app/models.py` | 媒体/发布复用 `studio_articles` 已有的 `cover` / `inline_diagrams` / `scheduled_for`，不需要新表 |
| `apps/api/app/schemas.py` | 各模块在自己的文件里定义局部入参模型（可 `from ..schemas import CamelModel` 继承，不修改它） |
| `apps/api/app/modules/studio.py` | 主控统一改 |
| `apps/api/app/services/studio_repo.py` | 主控统一改 |
| `apps/api/app/services/article_blocks.py` | 段落模型已定稿，只可调用 |
| `apps/web/src/views/PaperView.vue` | 主控统一集成 |
| `apps/web/src/api/studio.js` | 主控统一改 |
| `apps/web/src/router.js` / `AGENT.md` / `infra/postgres/schema.sql` | 主控统一改 |

允许修改：自己名下的新文件；`apps/api/requirements.txt`（仅当真的缺依赖，且要写明理由）。

**只跑自己的测试文件**，不要跑全量 `pytest`（并行会互相干扰）；全量由主控在合并后跑。

## 1. 文件归属

| Agent | 拥有（可新建/修改） |
|---|---|
| A 排版 | `apps/api/app/services/studio_render.py`、`apps/api/app/modules/studio_layout.py`、`apps/api/tests/test_studio_layout.py` |
| B 媒体 | `apps/api/app/services/studio_media.py`、`apps/api/app/modules/studio_media.py`、`apps/api/tests/test_studio_media.py` |
| C 发布 | `apps/api/app/services/studio_publish.py`、`apps/api/app/modules/studio_publish.py`、`apps/api/tests/test_studio_publish.py` |
| D 前端 | `apps/web/src/api/studioExtra.js`、`apps/web/src/components/paper/*.vue` |

三个 router 已由主控在 `main.py` 注册，prefix 均为 `settings.api_prefix`（`/api/v1`），文件里写好 `router = APIRouter(...)` 即可，**路径里带上 `/studio` 前缀**。

## 2. 通用约定

- 权限：`dependencies=[require_permission("studio.use")]`（`from ..core.rbac import require_permission`）。
- 取稿件：参考 `app/modules/studio.py` 的 `_data(db, user, article_id)`；新模块里自己写一份等价的 `_load(db, user, article_id)`，**不要 import 私有的 `_data`**。
- 落库：`studio_repo.save(db, row, data)` + `db.commit()`，返回 `_out(data)`（等价于 `studio_repo.row_to_dict` 转 camelCase）。
- 错误：`ApiError(code, message, status_code)`；`not_found` 从 `..core.deps` 导入。注意签名是 **(code, message, status)**。
- 长任务走 `SP.JOBS` 那套模式（内存任务表 + daemon 线程 + 独立 SessionLocal），但**本轮的媒体与发布都需要前端轮询，不要用后台线程**：入队即返回，前端调 `sync` 拉结果。
- 时间统一 ISO 字符串存 JSON 字段。

## 3. 接口契约

### 3.1 排版（Agent A）

`POST /studio/articles/{article_id}/render`

```json
// 请求（全部可选）
{ "options": { "...": "WeChatStudioOptions 结构，见 schemas.WeChatStudioOptions" } }
// 响应
{ "html": "<section>...", "warnings": [{"field": "body", "level": "warning", "message": "..."}],
  "stats": {"images": 1, "readingMinutes": 3, "...": 0}, "options": { "...": "实际生效的选项" } }
```

- 不传 `options` 时用稿件已存的 `layout_options`；两者合并时以传入为准（浅合并）。
- 正文来源：`article_blocks.to_markdown(data)`（不要自己拼）。
- 标题：`options["title"]` 缺省用 `data["title"]`。
- 图片：`data["cover"]` 与 `data["inline_diagrams"]` 里已就绪的 `assetId` 收集成 `asset://<id>` 传 `image_urls`；`inline_diagrams` 中 status 非 ready 的不要传。
- 横幅：`data["banners"][data["banner_style"]]["spec"]` 存在时，用 `wechat_layout_engine.build_banner` 渲染后置于正文之前（`data["banner_style"] == "none"` 时跳过）。
- 直接复用 `wechat_layout_engine.render_studio_document(body, options, image_urls)`，并对 `warnings` 做与 `wechat_mp._studio_render` 相同的规整（字符串 → `{field, level, message}`）。**不要 import `_studio_render`（私有）**，把自己需要的那 6 行逻辑写在 service 里。
- `stats` 至少包含 `sourceChars` / `htmlChars` / `images` / `readingMinutes`。

`GET /studio/articles/{article_id}/render/options`

```json
{ "options": { "...": "稿件当前排版选项" }, "catalog": {
    "styles": [{"id": "editorial", "name": "编辑手记", "desc": "..."}],
    "themes": [{"id": "ink", "name": "墨黑", "swatches": ["#111", "#666"]}],
    "svgCards": [{"kind": "steps", "name": "步骤卡", "desc": "..."}]
} }
```

- `catalog` 从 `wechat_layout_engine` 的 `STUDIO_STYLES` / 主题常量 / `SVG_CARD_INFO` 读（照 `wechat_mp.layout_studio_catalog` 的取法写一遍，不 import 私有函数）。

### 3.2 媒体（Agent B）

封面状态写在 `studio_articles.cover`：

```json
{ "status": "idle|running|ready|failed", "jobId": "...", "assetId": "...", "url": "...",
  "prompt": "...", "aspectRatio": "16:9", "requestedAt": "...", "error": null }
```

`POST /studio/articles/{article_id}/cover/generate`
```json
// 请求
{ "prompt": "可选，不给则由服务端按标题+正文首段拼", "aspectRatio": "16:9" }
// 响应：完整 article（StudioArticleOut 结构，cover 里 status=running, jobId=...）
```
- 复用 `app/core/queue.enqueue(db, owner_user_id, queue="ai-image", kind="image-generation", payload={...})`，payload 与 `modules/ai_images.py` 的 `create_image_generation` 保持一致（`prompt` / `purpose="studio-cover"` / `aspectRatio` / `count=1`）。
- 未配置生图供应商时不要静默成功：捕获入队异常 → 把 `cover.status="failed"` + 明确 `error` 文案写回并 400。

`POST /studio/articles/{article_id}/cover/sync`
```json
// 请求 {}；响应：完整 article
```
- 读 `GET /jobs/{jobId}` 同源的 job 记录（`models.Job`），成功则把产物 assetId 写入 `cover = {status:"ready", assetId, url, ...}`，失败写 `status:"failed", error`。job 记录字段自行读 `app/services/jobs*` / `app/core/queue.py` 确认（**以代码为准**，不要猜字段名）。
- `url` 用 `sign_asset_url` 生成与别处一致的 `/api/v1/assets/{id}/raw?...` 形式（照 `storyboards._url` 的写法）。

`POST /studio/articles/{article_id}/cover/pick`
```json
{ "assetId": "..." }        // 也允许 null 表示清空
```
- 校验 asset 属于当前用户，写 `cover = {status:"ready", assetId, url}`。

`POST /studio/articles/{article_id}/inline/generate`
```json
// 请求
{ "blockId": "...", "prompt": "可选", "aspectRatio": "4:3", "position": "after" }
// 响应：完整 article；inline_diagrams 新增
{ "id": "…", "blockId": "…", "jobId": "…", "status": "running", "prompt": "…", "assetId": null, "url": null }
```

`POST /studio/articles/{article_id}/inline/sync`
- 遍历 `inline_diagrams` 里 `status=="running"` 的项查 job；成功→ `status="ready"` + assetId/url，并在对应 block 之后插入一个 `kind="media"` 的块（`text` 为 `![](asset://<assetId>)`，`inserted_diagram_id=<diagram.id>`）；失败→ `status="failed"` + error。返回完整 article。
- 插入要用 `article_blocks.apply_ops` 或直接改 blocks 列表后重排 `ordinal`，**保证已有块的 id 不变**。

`DELETE /studio/articles/{article_id}/inline/{diagram_id}`
- 删除 `inline_diagrams` 里该项，同时删除 `inserted_diagram_id == diagram_id` 的块。返回完整 article。

`POST /studio/articles/{article_id}/blocks/{block_id}/svg-card`
```json
// 请求
{ "kind": "steps" }
// 响应：完整 article
```
- 调 `wechat_svg_cards.draft_card(db, owner_user_id=..., kind=..., text=..., article_title=..., context_before=..., context_after=...)`。
- 成功后把返回的 fence 作为 **`kind="code"`** 的新块插入该段之后，`text` 为 fence 原文（与 `LayoutView` 的 `insertSvgCard` 一致，格式自行读 `apps/web/src/features/layout/useLayoutStudio.js` 第 ~210 行确认）。
- 校验失败（422）原样抛给前端，不要吞。

### 3.3 发布与复盘（Agent C）

`POST /studio/articles/{article_id}/publish`
```json
// 请求
{ "platforms": ["wechat-mp"], "scheduledAt": null, "dryRun": false }
// 响应
{ "contentItemId": "…", "versionId": "…", "publishJobIds": ["…"], "scheduledFor": null }
```
- 先调 `modules/studio.export_to_content(article_id, db, user)` 拿到 `{contentItemId, versionId}`（它是纯路由函数，可直接函数调用；返回值结构以代码为准）。**这是唯一允许 import 的 studio 内部符号，且只许调这一个。**
- 再按 `modules/publishing.py` 的入队方式创建发布任务（读它的 `POST` 路由实现，照抄参数组装，不 import 私有 helper）。
- `dryRun=true` 只做 export，不入队，`publishJobIds=[]`。
- 成功后写 `scheduled_for` / `publish_marked_at`。

`GET /studio/articles/{article_id}/review`
```json
{ "articleId": "…", "contentItemId": "…", "publishedAt": null,
  "platforms": [{"platform": "wechat-mp", "status": "queued|running|succeeded|failed",
                 "url": null, "at": "…", "error": null}],
  "stats": {"blocks": 12, "chars": 3400, "flagged": 2, "checked": true} }
```
- 平台任务数据从 publishing 相关表按 `contentItemId` 反查（表名/字段自行读 `models.py` 确认）。查不到就返回空数组，不要报错。

### 3.4 前端组件（Agent D）

三个组件都是**受控**的：只收 props、只发 emits，不自己拉 article、不自己跳路由。

```vue
<!-- PaperLayoutPanel.vue：右栏「排版」设置 -->
props: { article: Object, busy: String }
emits: ['saved'(article), 'notify'(text)]
// 内部可调 GET /studio/articles/{id}/render/options 拿 catalog；
// 改选项 → 300ms 防抖调 renderOptions 之外不渲染，仅 emit('preview', {...})？

<!-- PaperLayoutPreview.vue：中间栏实时预览 -->
props: { article: Object, options: Object, active: Boolean }
emits: []
// 自己调 POST /studio/articles/{id}/render（options 变化 400ms 防抖），
// iframe srcdoc 渲染 html，顶部显示 warnings/stats，可「复制 HTML」「打开新窗口」
```

为避免两个组件各拉一次渲染，**统一约定**：`PaperLayoutPreview.vue` 是唯一调 render 的组件；`PaperLayoutPanel.vue` 只维护选项并 `emit('change', options)`。父级把 options 传给 Preview。`PaperLayoutPreview` 额外 `emit('rendered', {html, warnings, stats})`，供发布/导出复用。

```vue
<!-- PaperMediaPanel.vue：右栏「封面 · 插图」 -->
props: { article: Object, selectedBlockId: String, busy: String }
emits: ['saved'(article), 'notify'(text)]
// 封面：输入提示词 → generate → 2s 轮询 sync 直到 ready/failed → emit saved
// 插图：对 selectedBlockId 生成，列表展示 running/ready，可移除
// SVG 卡：选 kind → 对 selectedBlockId 生成 → emit saved
```

```vue
<!-- PaperPublishPanel.vue：右栏「发布 · 复盘」 -->
props: { article: Object, busy: String }
emits: ['saved'(article), 'notify'(text)]
// 排期时间选择 → POST schedule → emit saved
// 「存入文章管理」→ export；「立即发布」→ publish 带平台勾选
// 复盘：GET review 展示平台状态表
```

- 接口封装放 `src/api/studioExtra.js`，用 `import { api } from './client'`（`api.get/post/patch/del` 形态，别自己写 fetch）。
- 样式跟随现有主题变量（`var(--color-*)`），不要引入 UI 库之外的新依赖。组件用 `<script setup>`。

## 4. 验收

- 各自 `pytest tests/test_studio_<x>.py -q` 全绿（后端）。
- 前端 `npm run build` 通过（Agent D 只保证组件自身可编译；PaperView 集成由主控做，D 不要改 PaperView）。
- 每个 agent 结束时在回复里给出：新增文件清单、接口实际路径、与契约的偏差（无偏差也要写明「无」）、已知风险。

## 5. 实施后的实际偏差（实现为准，契约以本节校正）

| 契约原写法 | 实际实现 | 原因 |
|---|---|---|
| `STUDIO_STYLES` 等常量 | `STYLES` / `THEMES` / `SVG_CARD_INFO`（service 内 alias） | 引擎里的真名；`wechat_mp` 用的是 alias |
| catalog `svgCards[].kind` | `svgCards[].id`，另加 `banners` 与 `defaults` | 与 `wechat_mp.layout_studio_catalog` 对齐，少一份字段映射 |
| 排版面板含字号/行距/圆角 | 只有 `style/theme/banner/outro/brandAuthor/brandSlogan/avatarUrl/title` | `WeChatStudioOptions` 是 `extra=forbid`，多传字段直接 422；这几个就是全部可调项 |
| 平台码 `wechat-mp` | 统一为 `wechat_official`，同时接受 `wechat-mp`/`wechat_mp`/`mp` 别名 | `connectors.PLATFORMS` 的真实码 |
| `render` 返回 `options` = 实际渲染选项 | 返回**生效选项**（含 `banner` 原值） | 内部为防重复横幅把 `banner` 改成 `none`；若回传，前端「保存为默认」会把自动横幅永久关掉 |
| `scheduledAt` 时间语义 | 无时区按 UTC 处理 | 与 `studio.set_schedule` 一致；前端统一 `toISOString()` 带 `Z` |

已知边界（未在契约内、但实现保留）：

- 生图依赖供应商配置，未配置时 `cover/generate`、`inline/generate` 明确 400。
- 发布依赖已连接的平台账号（`ConnectedAccount`），未绑定时报明确错误。
- `review` 只读现有任务，不触发对账。
