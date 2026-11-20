# 排版工作台参考项目像素一致性迁移 — 未完成状态

**状态：未完成，不能按“与参考项目完全一致”验收。**  
**目标：** 将当前 Python 正文渲染结果与 `dsh-wechat-studio` 的权威参考实现逐项对齐，使生成 HTML、内联样式细节和浏览器输出一致。参考实现是 `vendor/layout/layout.js`、`banner.js`、`py.js`、`pyre.js`，不是旧版 `skills/wechat-svg-cards/scripts/layout.py`。

## 已完成

- 已确认参考项目位置：`C:\Users\11301\Downloads\dsh-wechat-studio-main\dsh-wechat-studio-main`。
- 已审计参考 `layout.js` 的主要解析语义：H1 只作为元信息、不自动输出标题；H2 只按 ASCII `|` 拆分；只有 `>` 开头才进入引用解析；正文最后先输出作者卡，再输出外链注释；浅色正文容器使用参考字体与 `padding:0 4px;`，dark 容器使用 `#0F1218`、`padding:26px 18px 30px`、`border-radius:18px`。
- 在 `apps/api/app/services/wechat_layout_studio.py` 做了第一批对齐：BOM/换行预处理、基础 front matter 读取、上述标题/二级标题/引用语义与 wrapper/order 修正；保留现有 API 和旧 `{{imageN}}` 等产品兼容能力。
- 当前定向验证：`cd apps/api && pytest tests/test_wechat_layout_studio.py -q --tb=short`，**79 passed**。

## 尚未完成（阻塞最终验收）

1. **缺少 Node ↔ Python 差分测试器和输入语料。** 尚未对照参考 `render()` 对多个 Markdown 样例逐字节比较 HTML；“79 passed”只证明现有产品测试通过，不证明参考一致。
2. **正文组件仍需逐项核对与移植。** `inline`（转义、粗体/标记符、链接注释）、`quoteSvg`、图片和图注、表格、列表续行/嵌套、H2/H3/H4、callout、prompt/longimage、空装饰占位 `fixEmpty`、darkFix 等尚未证明逐字节一致。
3. **预处理语义未完整对齐。** 需要覆盖 `cleanMd` 的飞书转义处理、front matter 字段解析、`joinLines` 的中英文拼接规则、自动图注条件、unclosed fence 的警告和 `check()` 的报告。
4. **旧接口兼容边界需明确。** `{{imageN}}`、`:::svg-*` 和图片 URL 安全校验属于当前产品兼容逻辑；需确保这些扩展只影响旧格式输入，不改变参考原生 Markdown 输入的结果。
5. **Banner parity 未验证。** Python `render_banner` 尚未与 `vendor/layout/banner.js` 在真实输入上做 HTML 字符串对照。
6. **SVG 卡需重新跑全量 parity。** 先前有 8 张卡逐字节一致的结论，但应保留/补齐可重复测试，避免后续改动回归。
7. **文档中的旧验收状态需避免误读。** `docs/plans/2026-11-20-vendor-layout-refactor.md` 的第二轮验收记录的是工作台功能与测试通过，不等于本文件目标的逐字节/像素一致验收。

## 建议的完成顺序

1. 固定参考 Node 版本/命令，建立可复用的 Node 与 Python JSON-lines dump harness；收集覆盖正文组件的 Markdown corpus。
2. 对 HTML 按第一个差异字节定位，并分组件修复；只把兼容行为放在统一入口边界。
3. 为 10 种风格、15 种主题组合及边界输入补充自动差分回归；将当前 8 张 SVG 卡结果加入 golden 对照。
4. 对 poster/neo/card banner 做 Node/Python 对照，并验证 theme、标题长度、默认值和异常输入。
5. 在固定浏览器版本、视口、字体和设备像素比下做截图像素比较；记录渲染环境，避免把字体差异误判为模板差异。
6. 跑排版相关和后端全量测试；更新验收文档，只有差分与像素测试通过后才将状态改为完成。

## 当前结论

当前进度是**部分语义修正 + 现有测试通过**，不是完成参考实现迁移。下一步优先产出可重复的 Node/Python HTML 差分基准，再逐个补齐差异；在此之前，不应宣称 HTML、样式细节或输出像素完全一致。
