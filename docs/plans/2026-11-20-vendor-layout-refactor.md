# vendor/layout 排版增强引擎重构蓝图

## 最新决策：独立排版工作台重构（2026-11-20）

本节取代下文第一轮「在旧 token 体系上增加片段」的产品方案；下文保留为第一轮迁移记录，不作为新入口设计依据。

- **工作流**：文章管理 → 排版预览 → 独立微信排版工作台 → 保存排版；创作空间 → 发布 → 选择平台 → 选择账号并推送。不在发布面板内再做排版。
- **界面**：`views/LayoutView.vue` 替换三个旧 Tab，采用左侧文章稿纸、右侧设置面板；直接配置 10 种正文风格、15 套配色、poster/neo/card/none 开头横幅、文末关注、品牌文案、头像地址。旧主题页与组件库不再是新流程入口。
- **正文引擎**：`services/wechat_layout_studio.py` 实现参考正文结构与风格；`wechat_layout_engine.render_studio_document(body, options, image_urls=None)` 统一预览与发布，不再以旧 18-token 覆盖近似正文风格。SVG 卡、横幅复用第一轮已迁移的纯 Python 引擎。
- **配置契约**：`options` 包含 `style`、`theme`、`banner`、布尔 `outro`、`brandAuthor`、`brandSlogan`、`avatarUrl`、`title`。头像限制安全 HTTPS 地址；未知选项和非法枚举明确报错。
- **存储**：`ContentVersion.layout_options` 保存独立排版配置，数据库启动进行幂等补列。只改排版不增加正文版本；工作台修改正文则创建新版本后保存配置，保留旧版本。
- **接口**：`GET/PUT /wechat-mp/layout-studio/articles/{contentItemId}?versionId=...` 读取/保存配置；`POST /wechat-mp/layout-studio/render` 提交 `{body,options}` 渲染。文章管理用 `/wechat-layout?articleId=...&versionId=...` 进入真实稿件，不把样文冒充真实文章。
- **发布**：请求带 `contentItemId` 与 `versionId`，服务端检查所有权并将已保存配置冻结到任务快照，worker 使用同一新引擎。用户进一步明确不需要历史兼容：删除旧主题接口、旧渲染器及发布回退，不再读取旧 token；不清空数据库业务数据。下文关于保留旧协议的描述仅为已撤销的历史决策。
- **复制与隔离**：公众号复制写入 `text/html` 富文本；源码复制单独提供。稿纸 iframe 无脚本沙箱，错误时清除过期预览；未保存离开提示，配置写入失败可重试，不显示虚假成功。
- **平台边界**：平台选择仅展示真实支持的微信公众号草稿箱，不宣称其他平台已具备发布适配器；本次开发不执行真实外部发布。

### 第二轮验证

前端新增持久化、版本跳转、错误与富文本剪贴板回归；后端新增风格、输入安全、配置权限、发布快照及迁移回归。

最终验收（2026-11-20）：
- 后端全量：`957 passed`，3 个既有弃用告警（Starlette/httpx、Pydantic `min_items`/`max_items`）。
- 排版相关后端定向：`148 passed`。
- 前端回归：`33 passed`。
- 前端生产构建：`3856 modules transformed`，成功。
- PostgreSQL schema：`export_pg_schema.py --check` 一致。
- 运行中的 API 已确认注册 `/api/v1/wechat-mp/layout-studio/render`、`/articles/{content_item_id}`、`/catalog` 三个独立排版路由。
- 未执行真实微信公众号推送。

---

## 第一轮记录（历史方案）

> 状态：**全部完成**。阶段 1 引擎移植完成（8 张卡与原 JS 引擎逐字节一致）；阶段 2 后端接入完成；阶段 3 前端接入完成；阶段 4 测试与文档完成。
> 验证：后端全量 `852 passed`；引擎单测 `134 passed`；前端 `vite build` 3867 modules 通过。
> 关联：`apps/api/app/services/wechat_layout_engine.py`、`apps/api/app/services/wechat_layout.py`、`apps/web/src/features/layout`、`apps/api/tests/test_wechat_layout_engine.py`
> 日期：2026-11-20

## 1. 背景与目标

### 1.1 为什么移植

`dsh-wechat-studio` 的 `vendor/layout`（原始出处 `lqshow/wechat-article-skills`，MIT）提供了一套表现力明显强于现有排版器的能力：SMIL 动画 SVG 卡、开头横幅、多套风格与配色的组合、以及一个带配色切换与自查报告的完整预览页。把这些能力接进当前产品，可以让长文在「结构清晰」之外，进一步具备「可视化表达」的手段——表格、对比、时间线、步骤、柱状图、概念图、手机对话演示等都能以微信可接受的形态进入正文。

现有排版器 `wechat_layout.py` 已经很稳定：它用 18 个主题的 tokens 驱动一整套 section 渲染，预览与推送共用 `render_document`。因此本次不是替换它，而是**在它旁边增量增加一组自包含片段**，让增强能力以围栏 block 的形式补进正文。

### 1.2 为什么用 Python 而不是直接搬 JS

| 约束 | 结论 |
|---|---|
| 后端是 FastAPI/Python，没有 Node runtime；引入 JS 引擎意味着进程模型、依赖、部署三方面都变重 | 引擎必须在 Python 侧可用，保持「标准库 + 纯函数」 |
| `render_document` 是唯一的渲染源，预览和推送都走它 | 增强片段必须能在 `render_document` 的分派链里被调用，而不是另起一套 JS 渲染管线 |
| 公众号正文只接受内联样式，`<style>` / `class` / `<script>` / `background-image` 都会被剥离或报错 | 片段生成逻辑要显式保证白名单，JS 原版里依赖 DOM/CSS 的部分不能照搬 |
| 需要 golden 测试与发布链路复用 | 纯函数（输入确定则输出确定）是最低要求 |

所以本次移植的是「与正文无关的自包含片段」：目录数据、SVG 卡、banner、完整预览页。正文级样式 tokens 依旧由 `wechat_layout.py` 负责，两边不重叠。

### 1.3 目标与非目标

**做**：15 套配色、10 套风格、8 种动画卡、banner、完整 HTML 预览页；微信安全输出；零运行时依赖；可测试。

**不做**：不改 18-token 契约；不引入 Node / 第三方依赖；不让完整预览页的 `<style>` / `<script>` 进入正文；不复制原 JS 引擎的 DOM、Buffer、文件 I/O 逻辑。

## 2. 架构决策

| 决策 | 内容 | 理由 |
|---|---|---|
| 增量注册围栏 block | 新能力以 `:::svg-table`、`:::banner` 之类的围栏注册进 `parse_blocks` / `render_document`，不改 `normalize_tokens` 的 18-token 结构 | 老文章 body 不需要迁移；主题切换、校对、发布链路全部保持原样；新问题只影响新 block |
| 预览 = 发布共用 `render_document` | 新增 block 的渲染函数在 `render_document` 分派里只实现一次；预览接口和推送接口拿到的是同一段 HTML | 保证「所见即所得」；避免预览好看、推送走样的经典事故 |
| 完整 HTML 页只进 iframe | `render_preview_page` 产出带 `<style>` / `<script>` 的单文件页面，仅用于本地预览容器 | 正文白名单不允许这些标签；把交互（配色切换、复制、自查报告）隔离在预览层 |
| 纯函数 + 标准库 | 引擎模块只依赖 `html` / `math` / `re` / `collections`，不读写文件、不碰数据库、不依赖 Node | 便于并行测试；发布链路可重复调用；移植面上的 JS Buffer/DOM 依赖被挡在门外 |
| 安全默认 | 所有片段只用 presentation 属性 + 内联 `style`；非法输入回退为可读代码框并给出 warning，不抛异常 | 用户粘贴的围栏内容不可信，渲染器必须「永不炸正文」 |

## 3. 四类能力如何映射

| 能力 | 引擎入口 | 适用场景 | 依赖 | 输出边界 |
|---|---|---|---|---|
| SVG 动画卡 | `render_svg_card(kind, title, lines, *, style, theme)` | 表格、柱状对比、步骤、时间线、拆解、左右对比、概念图、手机对话演示 | `THEMES` / `STYLES` / `resolve_style_theme` | 微信安全的 `<section><svg>...</svg></section>` |
| 开头横幅 | `render_banner(spec, *, theme)` + `check_banner(spec)` | 文章开头的视觉锚点，poster / neo / card 三种骨架 | 独立的 `_BANNER_THEMES`（3 套横幅配色） | 微信安全 `<section><svg>`，含字数自检 warning |
| 风格 × 配色 | `STYLES`（10）× `THEMES`（15）+ `resolve_style_theme` | 给上面两类能力提供可组合的视觉变量；非法值回退 `std` / `orange` | 纯数据字典 | 不直接输出，作为渲染上下文 `T` 注入 |
| 完整预览页 | `render_preview_page(article_html, *, title, style, theme, report)` | iframe 里的所见即所得预览：配色切换、复制全文、自查报告 | `article_html`（已由 `render_document` 产出）+ 目录数据 | 完整单文件 HTML，允许 `<style>` / `<script>`，**只进 iframe** |

### 3.1 SVG 动画卡

8 种卡由 `SVG_CARD_KINDS` 暴露，内容用围栏内的行文本描述（`|` 分列、`★` 标记重点、`>` 标记金句、`!` 标记注释）。渲染结果全部由 `<section>` / `<svg>` / `<text>` / `<rect>` / `<path>` 与 SMIL `<animate>` / `<animateTransform>` 组成，经测试确认 8 种卡都含动画标记且不含 `class=` / ` id=` / `<style` / `<script` / `background-image`。

格式不满足约定时（例如 `svg-compare` 只有一个表头），渲染器捕获 `CardFormatError` 等异常，回退成一个可读的 `<pre>` 代码框，并在 `warnings` 里给出原因。这是「永不炸正文」的关键兜底。

### 3.2 开头横幅 banner

`check_banner` 先做文案规格自检：poster/neo 检查眉标 ≤10、标题两行各 ≤8、底纹大字 ≤2；card 检查眉标 ≤10、标题 ≤12、副标题 ≤16；非法 style 也会给出提示。`render_banner` 即使收到超长文案也会照常出图，超长部分由 warning 告知调用方去修。

横幅配色用独立的 `_BANNER_THEMES`（blue / orange / teal），与正文的 15 套 `THEMES` 不是同一份数据；非法 theme 回退 orange。

### 3.3 10 风格 × 15 配色

`resolve_style_theme(style, theme)` 合成渲染上下文 `T`（配色 + `st` + 圆角 `r`），并**返回副本**，永不改动全局目录。非法值回退 `std` / `orange`，因此前端传错值也不会让渲染崩。

实测：15 套配色的输出两两不同；有独立 frame 分支的风格（std / mag / min / geek / bold / neo / dark）输出两两不同。涉及的依赖是 `THEMES` / `STYLES` 两张只读表。

### 3.4 完整预览页

`render_preview_page` 把已经渲染好的 `article_html` 塞进 `.sheet`，并注入：`var D = {...}`（含 palette / themes 数据）、配色圆点按钮、复制按钮、自查报告 `<pre>`。页面会以 `<!doctype>` 开头，配色 payload 用 `json.dumps(ensure_ascii=False)` 并转义 `</` 防止提前闭合 `<script>`。

它是**纯消费者**：调用后 `THEMES` / `STYLES` 不变，同一参数再调 SVG 卡渲染得到完全相同的正文 HTML。测试已覆盖这一点。

## 4. 分阶段计划与状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1. 引擎移植 | 建立 `wechat_layout_engine.py`，导出目录、8 卡、banner、预览页、resolve 函数；保证微信安全与确定性 | 已完成（8 卡与 JS 原版逐字节一致） |
| 2. 后端接入 | 注册新围栏 block 并接入 `render_document` 分派；同步 `LAYOUT_COMPONENTS`；新增 catalog 只读端点 | 已完成（后端集成 agent） |
| 3. 前端接入 | 在 `apps/web` 的排版组件/ composable 里暴露新 block 的插入、配色选择、预览入口 | 已完成（前端 agent） |
| 4. 测试与文档 | 新建引擎单元测试 + 本蓝图；验证微信安全、降级、确定性、交叉组合 | 已完成（134 条引擎用例全绿；全量 852 passed；前端构建通过） |

四阶段均已完成并通过回归。后端全量测试 `.venv/Scripts/python.exe -m pytest -q` → `852 passed`；前端 `npm run build` → `✓ 3867 modules transformed`（如构建环境对旧 `dist/assets` 的批量删除有限制，可加 `--outDir <空目录> --emptyOutDir false` 绕过，不影响代码正确性）。

## 5. 风险与不宜照搬项

1. **SMIL 在公众号客户端的兼容性需实测。** 引擎层面按白名单生成 `<animate>` / `<animateTransform>`，但微信内置浏览器对 SMIL 的支持没有统一保证。上线前应在 iOS / Android 微信真机上打开推送后的草稿各看一遍；若动画被忽略，卡片应仍能静态可读（当前设计里动画多控制 opacity/width，静态首帧是可接受的降级）。
2. **JS 原版的 Buffer 依赖不宜照搬。** 原 `vendor/layout` 用 Buffer 处理部分文本/字节逻辑；Python 侧用标准 `str` / `html.escape` 等价实现即可，不为对齐实现细节引入 `bytes` 处理层。
3. **不引入 Node runtime。** 后端无 Node，前端也不应为这一组能力增加构建期/运行期 Node 依赖；所有派生逻辑留在 Python 纯函数里。
4. **`note` / `zen` / `bento` 目前在 SVG 卡里与 `std` 输出相同。** 它们被登记在 `STYLES` 中，但 `_sFrame` 没有为它们设置独立分支，因此落到默认样式（圆角 `r` 只在代码框降级路径生效）。这不是测试失败，而是移植范围上的已知限制；若后续要让 10 种风格在卡上都可见，需要给 `_sFrame` 补分支——那属于引擎改动，不在本任务范围内。
5. **主题字典键差异。** 横幅用 `_BANNER_THEMES` 的 3 套色，正文用 `THEMES` 的 15 套色，传错 key 会静默回退。调用方应把「选中的配色名」同时映射到两套字典，或在 UI 上限制横幅配色为 blue / orange / teal。
6. **完整页的 script 边界。** payload 已做 `</` 转义，但任何新增的内联文案都需继续遵守「不把未转义的用户输入写进 `<script>`」的规则；`render_preview_page` 只对 title / report 做了 HTML 转义。

## 6. 验证方式

### 6.1 自动化测试

```bash
cd apps/api
./.venv/Scripts/python.exe -m pytest tests/test_wechat_layout_engine.py -q
```

预期：`134 passed`。覆盖目录完整性（15/10/8）、逐卡渲染与微信安全、10 风格 × 5 配色交叉、非法 style/theme 回退、格式错误降级为 `<pre>`、banner 字数自检与安全输出、完整预览页结构与无污染、确定性、SMIL 标记。

### 6.2 人工验收路径

1. 在编辑器里插入一个新围栏 block（例如表格卡或 banner），预览确认出现 SMIL 动画与正确配色；切换配色圆点，正文片段的主色随之变化。
2. 复制全文得到的是 `render_document` 产出的正文 HTML；把它粘到公众号草稿，确认动画片段没有被剥离、没有 `class` / `<style>` 残留。
3. 故意写错围栏内容（如对比卡只有一个表头），确认正文显示可读代码框、自查报告里出现 warning，且预览/推送都不报错。
4. iOS 与 Android 微信真机各打开一次草稿，确认动画卡首帧可读（见风险 1）。
5. 完整预览页只在 iframe / 本地浏览器打开，不复制其 HTML 进正文。

## 附：测试文件说明

`apps/api/tests/test_wechat_layout_engine.py` 全部针对纯函数，不依赖 conftest 里的 `client` / 数据库夹具，可单独运行。用例按 7 组组织：目录完整性、逐卡安全渲染、风格配色交叉与回退、格式降级、banner、完整预览页与无污染、确定性。所有中文断言信息用于失败时定位，代码内字符串统一使用 ASCII 直引号。
