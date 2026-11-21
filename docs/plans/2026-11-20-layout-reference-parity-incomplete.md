# 排版工作台参考项目像素一致性迁移 — 进度状态

**目标：** 将当前 Python 正文渲染结果与 `dsh-wechat-studio` 的权威参考实现逐项对齐，使生成 HTML、内联样式细节和浏览器输出一致。参考实现是 `vendor/layout/layout.js`、`banner.js`、`py.js`、`pyre.js`，不是旧版 `skills/wechat-svg-cards/scripts/layout.py`。
**参考项目位置：** `C:\Users\11301\Downloads\dsh-wechat-studio-main\dsh-wechat-studio-main`（可用环境变量 `LAYOUT_REF_DIR` 覆盖；Node v22 验证通过）。

## 已完成

### 1. Node ↔ Python 差分 harness 与语料（本轮完成）

- `apps/api/tests/parity/ref_dump.mjs`：Node 侧 harness，调用参考 `renderPage`，从生成页面的 `const D=<JSON>` 提取 `data.styles`（正文 HTML，颜色为 TOK 占位符），按主题 paint 成最终配色，逐 case 输出 JSONL。
- `apps/api/tests/parity/py_dump.py`：Python 侧 harness，对同一份 cases.jsonl 调用 `render_studio_document` 输出同格式 JSONL。
- `apps/api/tests/parity/corpus/`：9 份 Markdown 语料，覆盖行内标记/链接/转义、标题层级与 pill、引用/callout/GOLDEN/QUOTE、图片/图注/长图、表格、列表（嵌套/续行/飞书空行/类型切换）、围栏（terminal/prompt/promptbox/longimage/未闭合）、视频占位、front matter（style/theme/h2: pill/caption: off）。
- `apps/api/tests/test_layout_reference_parity.py`：pytest 差分回归。44 case（每份语料 std/orange；引用与混合语料 × 10 风格；行内语料 × 15 配色；front matter 元数据驱动；默认作者卡），按 id 去重后 43 个唯一 case。失败时按第一个差异字节输出上下文。**当前 43/43 唯一 case 全部逐字节一致**（运行：`cd apps/api && pytest tests/test_layout_reference_parity.py`）。

### 2. 正文渲染器重写为参考忠实移植（本轮完成）

`apps/api/app/services/wechat_layout_studio.py` 的 `StudioRenderer` 已重写，逐组件对齐 layout.js：

- `inline`：esc 语义（quote=False）、stash 顺序、strong/xStrong/neo、chip/xChip、下划线 underlineCss、删除线、em、链接注释（mp.weixin 内链 vs 外链 notes）、行内图片（无 alt 属性、垂直居中）。
- 块级：围栏（fence 首三字符结束匹配、body 首尾空行裁剪、terminal/promptBox/xPrompt/dashedBox/longimage）、H1 只写 meta.title、H2 pill 变体、h2Part/xH2/neoH2 全风格、h3/h4（step 匹配在 inline 之后）、hr/xHr、引用段落拆分（`\n\n` → `<p style="margin:0 0 6px;">`）、quote/golden/callout/xCallout 全风格、VIDEO_RE 视频占位、块级图片（fr 圆角表、`imageAlt` 反转义、caption() 变体）、LI_RE 列表（`、` 序号、层级 cap 2、缩进续行 joinLines、飞书空行合并、类型切换分块）、表格（neo/dark/其余三套、单元格补齐、分隔行剔除）。
- 预处理：`cleanMd`（BOM/换行/飞书转义反斜杠，围栏与行内代码内不动）、`frontMatter`、`joinLines`（CJK 邻接不加空格）、自动图注（media 状态、≤46 字、CAP_END、前缀标记检查）、手动 `^` 图注。
- 收尾：`fixEmpty`（空 `<span>` 占位 → `decoSvg`，含 border-top 虚线、linear-gradient 90deg 渐变拆条、rotate、opacity）、`darkFix`（SVG 保护性替换表）、作者卡（GIF_URL_HERE 占位、AUTHOR/SLOGAN/CTA 默认值、meta.author/slogan/outro_cta）、参考链接 notesBlock、wrapper。

### 3. 兼容边界收敛（本轮完成）

`{{imageN}}` 占位、`:::svg-*` 旧围栏、`image_urls` 注入追加、URL 安全校验（`{{imageN}}`/`image_urls`/`avatarUrl` 路径，markdown 原生合法 URL 不改写）只在旧格式/注入边界生效，不影响参考原生 Markdown 的输出。

### 4. 验证

- 差分回归：`pytest tests/test_layout_reference_parity.py` → 正文 43/43 逐字节一致。
- 后端全量：`pytest tests` → **1001 passed**。
- 测试调整两处（均为对齐参考语义）：`SafetyParser` 放行 `GIF_URL_HERE` 页面占位符；列表无序/有序切换时 stats.lists 期望值 1 → 2（参考语义按类型分块）。

### 5. Banner parity（poster / neo / card 全路径，本轮完成）

- `apps/api/app/services/wechat_layout_engine.py`：`render_banner` / `check_banner` 重写为 `vendor/layout/banner.js` 的忠实移植。
  - `build_banner(spec, themes=None)` 对应 `B.build`：poster / neo 的完整左栏（eyebrow 胶囊、neo 叠影与底纹大字、标题 `_fit` 自适应、主色高亮块）、brand 注入与 `avatar:false` 分支、`GIF_URL_HERE` 占位符；poster 的 flow / tags / stat / steps 四种右栏；card 的全部 12 种 RIGHTS 右栏（steps / remix / pick / timeline / converge / tree / compare / storyboard / chat / stats / checklist / terminal）。
  - `check_banner(spec, svgs=None)` 对应 `B.checkSpec`：字数（按 CJK=1、ASCII=0.5 的字宽）、数量区间、右栏合法性、flow 必填、SVG 违禁元素（id/class/style/script/渐变/use）检查，文案与参考逐字一致。
  - 数字语义按 py.js 对齐：`fl`（floatStr）、`R`（`f"{v:.1f}"` 去尾零）、`round`（银行家舍入）、`String(number)`（整数值 float 不带 `.0`）；异常回退为 `{"html": "", "warnings": [...]}`，不中断排版。
  - 范围外：banner.js 的 scene 模板（LAYOUTS）与点阵品牌头未移植；`build` 对未知 style 返回空 html，`checkSpec` 保留场景模板自检分支。
- 入口语义与参考 `renderPage` 一致：`render_banner(spec, theme=X)` 把 `THEMES` 15 套映射成 `{main, light: tint, line, border: tint, bg: soft}`；`theme=None` 用 banner.js 自己的 THEMES。产品侧 `wechat_layout_studio.py` 的字符串 banner 按参考字段构造 spec（card: eyebrow/title1/title2/sub + steps/result；poster/neo: eyebrow/title1/title2/big + flow 的 ask/result/result_sub），并按 renderPage 注入 `spec.brand = {author, slogan}`；前端与 API 只下发 `none|poster|neo|card` 字符串，无旧字段 dict spec 兼容负担。
- 差分回归：`tests/parity/dump_banner.mjs`（Node 侧 B.build / B.checkSpec，含主题映射）、`tests/parity/dump_banner_py.py`（Python 侧独立实现同一映射）、`tests/test_layout_reference_parity.py::build_banner_cases` 生成 63 个 case（poster flow × 15 配色、tags/stat/steps、brand/avatar/author-only 分支、默认与未知 theme、未知 right、超长与空 spec；neo 同上；card 12 种右栏 + steps 的 flow 覆盖、timeline 无 result、chat 无 window_title、avatar/brand/空 spec/超长/未知 right）。pytest 逐 id 比较 html 与 issues。
- 验证：`pytest tests/test_layout_reference_parity.py` → 正文 43/43 + banner 63/63 逐字节一致。

### 6. SVG 卡 golden 回归（本轮完成）

- `tests/parity/dump_svg_cards.mjs`：Node 侧调用参考 `layout.js` 的 `renderSvgCard(kind, title, lines, {style, theme})`。
- `tests/parity/dump_svg_cards_py.py`：Python 侧调用 `engine.render_svg_card`，输出 `{html, warnings}` 同格式 JSONL。
- `tests/test_layout_reference_parity.py::build_svg_card_cases`：**80 个 case**——8 种卡 std/orange 基准；10 种风格 × 代表性卡（svg-table/svg-bars/svg-phone）；15 套配色 × svg-table；8 种卡缺省标题；8 种卡空 lines；数据格式边界（缺列、非数值、值文案、无描述、单表头、无 track、无场景）；未知 kind 回退。
- 断言：所有非产品扩展 case 的 html 与 warnings 逐字节一致；`note/zen/bento` 是本项目卡框扩展（参考 `sFrame` 只有 7 个分支），只断言能正常出卡、不参与逐字节比较（固定 9 个 case 防漏检）。

### 7. 像素级 screenshot 对比（本轮完成）

- `apps/api/tests/test_layout_pixel_parity.py`：固定环境（Chromium 159 headless shell、视口 390×844、DPR 1、`--full-page`、zh-CN 字体栈）分别截图参考 HTML 与 Python HTML，用 Pillow 逐像素比较，断言 0 差异像素。
- 覆盖 6 个代表 case：std / dark / neo / mag / bento / min 各一（结构差异最大的语料）。HTML 先做逐字节一致性前置断言，不一致直接报「像素对比无意义」。
- 运行方式：本地启动 `http.server` 提供静态页，截图用已缓存的 Playwright CLI（`_resolve_cli()` 会自动挑选与已装 `chromium-headless-shell` 版本匹配的 `cli.js`，可用 `PLAYWRIGHT_CLI` 覆盖）。缺 Node/Playwright/Pillow 时自动 skip，skip 信息给出安装命令（`npx playwright install chromium-headless-shell`）。
- 验证：`pytest tests/test_layout_pixel_parity.py` → 6/6 case 全页截图 **0 像素差异**（约 25s）。

## 尚未完成

1. **check() 报告对齐。** 自查问题列表（飞书临时链接、本地路径、外部图床、SVG 违禁元素、占位符统计、WARN 文案）尚未与参考 `check()` 对齐；当前保留产品自有文案，正文差分不覆盖 warnings（banner 的 checkSpec 已对齐）。
2. **banner scene 模板。** banner.js 的 10 种 scene 骨架（pipeline/fly/path/…）与点阵品牌头仍未移植；产品入口只允许 poster/neo/card，暂不影响输出。

## 已收敛

- **meta.title 取值链（本轮完成）。** `wechat_layout_studio.StudioRenderer.render` 改为参考顺序 `meta.title || H1`，产品 API 的 `options.title` 仅在两者都缺失时兜底；不再提前写入 meta。影响面仍限于 stats/banner 默认标题，正文 HTML 不变。回归：`tests/test_wechat_layout_studio.py::test_title_chain_follows_reference_order`。

## 备注

- `docs/plans/2026-11-20-vendor-layout-refactor.md` 的第二轮验收记录的是工作台功能与测试通过，不等于本文件的逐字节/像素一致验收。
- 差分 harness 的比较目标：参考 `data.styles` paint 后的正文 HTML ≡ Python `render_studio_document(..., {'style','theme','outro':'true',...})['html']`（无 banner 时）。banner 在参考中单独存放在 `data.banner`，Python 端拼接在正文之前，与参考页面组装方式一致。
