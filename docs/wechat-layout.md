# 微信公众号排版设计

> 状态：P0 + P1 已实现（2026-11-06），P2 未开始
> 关联：`docs/design.md` §7 平台连接器、`apps/api/app/services/wechat_article.py`、`apps/api/app/modules/wechat_mp.py`
> 前置能力：公众号草稿箱接入（已完成）

## 1. 目标与边界

**目标**：让推送出去的公众号文章具备「可读、好看、风格统一」的排版，而不是现在这种只有 `<p>` 和 `<ul>` 的裸文本。

**做**
- 一套可切换的排版主题（字号 / 行高 / 字距 / 段距 / 配色 / 标题装饰）
- 一套公众号常用区块库（引导关注、引用、卡片、图注、分割线）
- 推送前**所见即所得**的手机预览（375px），预览 HTML 与真正推送的 HTML 是同一份
- 排版兼容性检查（外链、非法标签、背景图、深色模式风险）

**不做**
- 不做完整富文本编辑器（不引入 Tiptap / 富文本 contenteditable），避免把 HTML 当数据存
- 不复刻 135 编辑器 / 秀米的素材市场
- 不做自动配图、自动生成封面

## 2. 微信侧的真实约束（设计前提）

这些不是猜测，是公众号正文渲染的实际行为，直接决定技术选型：

| 约束 | 影响 | 应对 |
|---|---|---|
| 只保留**内联样式**，`<style>` 块与 class 会被剥离 | 不能写 CSS 文件、不能靠 class 组织样式 | 渲染时把主题 token 逐元素写进 `style=""` |
| 标签白名单有限，`div` 不可靠，`h1~h6` 样式会被重置 | 标题不能直接用 `<h1>` | 统一用 `section` + `span/strong` 模拟标题 |
| 正文图片必须是微信域名（`mmbiz.qpic.cn`） | 外链图会 45166 | 已有 `media/uploadimg` 上传流程，渲染时只接受上传后的 URL |
| `<a>` 指向外部域名会提示「外部链接」 | 影响阅读体验与合规 | 外链降级为纯文本 + 提示，仅保留公众号内链 |
| 不支持 `background-image` | 视觉方案不能依赖背景图 | 用纯色底 + 边框；纹理需求用 img 元素 |
| 深色模式下微信会反色处理 | 硬编码 `#fff` 背景会变黑块 | 禁止纯白/纯黑，用 `#333` 文字、`rgba(0,0,0,0.03)` 浅底 |
| 不支持 web font | 字体只能用系统栈 | 固定 `-apple-system / PingFang SC / Microsoft YaHei` 栈 |
| 正文上限 20000 字、正文图 1MB、封面 10MB | 已有校验 | 保留现有 `validate_article` |

## 3. 排版模型

三层：**Theme（全局 tokens）→ Block（区块）→ Inline（行内标记）**。

### 3.1 Theme：一套 tokens

主题是纯数据（JSON），不是代码。内置预设 + 用户自定义，同一套渲染器消费。

```jsonc
{
  "name": "墨绿简约",
  "tokens": {
    "fontFamily": "-apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif",
    "bodySize": 16,            // 正文字号 px，建议 15~16
    "bodyLineHeight": 1.8,     // 行高，公众号舒适区 1.7~1.9
    "letterSpacing": 0.5,      // 字距 px，中文 0.5~1
    "paragraphGap": 1.5,       // 段间距 em
    "textColor": "#333333",    // 近黑，避免 #000
    "mutedColor": "#888888",
    "primaryColor": "#0F6E56",
    "primarySoft": "#E1F5EE",  // 浅底（强调卡/引用）
    "quoteBg": "#F7F7F7",
    "dividerColor": "#E5E5E5",
    "strongColor": "#333333",  // 加粗文字颜色，缺省跟随正文色
    "align": "justify",        // left | justify
    "indent": 0,               // 首行缩进 em，0 为现代公众号主流
    "imageRadius": 6,          // 图片圆角 px
    "h1Style": "bar",          // bar | block | number | center | underline | number-divider | capsule
    "h2Style": "tag",          // tag | underline | plain | bar | strong | capsule | hairline
    "listMarker": "dot"        // dot | square | number | circle
  }
}
```

预设 10 套。前 6 套是原有风格，后 4 套对齐参考排版包的骨架（亲和 / 资讯 / 书卷 / 杂志），只搬几何和配色，不搬中文字体名（微信里无效）：

| 预设 | 主色 | 标题样式 | 适用 |
|---|---|---|---|
| 墨绿简约 | `#0F6E56` | 左侧竖线 + 加粗 | 知识、职场、干货 |
| 深海蓝 | `#185FA5` | 色块标签 | 科技、财经、分析 |
| 暖橙生活 | `#BA7517` | 居中大字 + 短下划线 | 生活、情感、美食 |
| 极简黑白 | `#2C2C2A` | 序号 + 细线 | 深度长文、访谈 |
| 暖陶土 | `#D97757` | 下划线主标题 + 竖线副标题 | 工具推荐、产品测评 |
| 商务蓝 | `#003BB3` | 大号编号 + 分割线 | 职场干货、产品教程、商业分析 |
| 资讯 | `#1F6F5F` | 下划线主标题 + 发丝线副标题 | 快讯、评测、行业观察 |
| 书卷 | `#9E3D30` | 居中标题 + 色条 | 人文、读书、深度长文 |
| 杂志 | `#4A5D6E` | 居中标题、大留白 | 品牌故事、访谈、生活方式 |
| 亲和 | `#6B5B95` | 胶囊标题、大圆角 | 教程、面向新手的解释 |

### 3.2 Block：区块库

| Block | 语法（作者视角） | 渲染要点 |
|---|---|---|
| 一级标题 | `# 标题` | 按 `h1Style` 装饰：竖线/色块/序号/居中 |
| 二级标题 | `## 小标题` | 按 `h2Style`：标签底/下划线/纯加粗 |
| 段落 | 空行分隔 | 段间距 `paragraphGap`，行高、字距、对齐来自 theme |
| 引用 | `> 内容` | 左竖线 `primaryColor` + `quoteBg` 底 + 灰色字 |
| 列表 | `- 条目` | 自定义 marker（圆点/方块/数字），条目间留 0.4em |
| 图片 | `{{image1}}` / `{{image2|图注文字}}` | 居中、圆角、可选图注（13px 灰色） |
| 分割线 | `---` | 居中短横线，不撑满 |
| 强调卡 | `:::card 标题` … `:::` | `primarySoft` 底 + 圆角 + 无边框 |
| 引导关注 | `:::follow` … `:::` | 灰底卡片，放在文末 |
| 文末署名 | `:::sign 作者\|公众号名` … `:::` | 细分割线 + 灰字居中 |
| 导语 | `:::lead` … `:::` | 主色短条 + 17.5px / 500 字重的开篇 |
| 数据 | `:::stat` 每行 `数字 \| 说明` | 大号等宽数字 + 灰色说明，基线对齐 |
| 步骤 | `:::steps` 每行 `名称 \| 说明` | 主色圆圈自动编号，不要自己写「1.」 |
| 对比 | `:::compare 左 / 右` 每行 `左 \| 右` | 左灰底、右浅主色，圆角两列 |
| 分层 | `:::layers` 每行 `标签 \| 正文` | 叠放的圆角卡片 |
| 清单 | `:::checklist` 每行 `done\|todo\|warn \| 事项` | ✓ / ○ / !；不认识的状态按 ○，正文不掉进标记列 |
| 金句 | `:::quote-card 出处` … `:::` | 大引号 + 19px / 600，出处放在括号里；和 `>` 引用分开 |

> 语法是 Markdown 的超集，纯文本可读、可版本对比、可 AI 生成；解析器不依赖第三方 Markdown 库，规则全部在白名单内。

### 3.3 Inline：行内标记

`**加粗**`、`*斜体*`、`==高亮==`（`primarySoft` 底）、`~~删除线~~`、`` `行内代码` ``。

渲染一律映射到 `strong / em / span + inline style`，不使用 `<b>/<i>`。

## 4. 渲染管线

```
body 文本
  → ① 解析（Parser）：逐块识别 Block，得到 AST
  → ② 校验（Lint）：标签白名单 / 外链 / 图片 URL / 长度 / 深色模式风险
  → ③ 渲染（Renderer）：AST + Theme tokens → 内联样式 HTML
  → ④ 统计：字数、图数、预计阅读时长、渲染后字符数
```

关键设计：**预览与推送共用同一个渲染函数**（`render_wechat_html(body, theme, image_urls)`），杜绝「预览好看、推出去变样」。

解析器规则（实现时逐条落测）：
- 代码围栏与行内代码优先提取，避免误判 `#`、`-`
- 未闭合的 `:::block` 视为普通段落并给 warning，不静默吞内容
- 未知 `:::xxx` 保留原文并 warning（不丢内容）
- 所有文本节点强制 HTML 转义，只输出白名单标签

## 5. 数据模型

```python
class WeChatTheme(Base, TimestampMixin):
    __tablename__ = "wechat_themes"
    id: str
    owner_user_id: str | None      # None = 系统预设（只读，不可删）
    name: str
    tokens: dict                   # JSON，见 §3.1
    is_preset: bool
```

`PublishJob` 增列：
- `theme_id`：引用的主题
- `layout_snapshot`：推送时实际使用的 tokens 快照（JSON）

> 快照是必须的：主题后续被改，历史任务仍能复现当时的排版。

## 6. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/wechat-mp/themes` | 预设 + 我的主题列表 |
| POST | `/wechat-mp/themes` | 基于预设复制或新建自定义主题（校验 token 范围） |
| PATCH | `/wechat-mp/themes/{id}` | 更新自定义主题（预设拒绝 403） |
| DELETE | `/wechat-mp/themes/{id}` | 删除自定义主题（预设拒绝 403） |
| POST | `/wechat-mp/drafts/render` | 渲染预览：`{title, body, themeId, assetIds}` → `{html, warnings, stats}` |
| POST | `/wechat-mp/drafts/preview` | 扩展：加 `themeId`，issues 增加排版类告警 |
| POST | `/wechat-mp/drafts` | 扩展：加 `themeId`（缺省用默认预设） |

`render` 返回的 `html` 直接给前端 `<iframe srcdoc>` 预览，宽度锁定 375px。

校验规则（`tokens` 白名单，防注入与离谱值）：

| token | 范围 |
|---|---|
| `bodySize` | 13–18 |
| `bodyLineHeight` | 1.4–2.2 |
| `letterSpacing` | 0–2 |
| `paragraphGap` | 0.8–3 |
| `indent` | 0–2 |
| 颜色 | 必须 `#rrggbb` 或 `rgba()`，拒绝 `url(` / `expression` / `javascript:` |
| 枚举 | `h1Style/h2Style/listMarker/align` 必须在白名单内 |

## 7. 前端交互

草稿弹窗拆成两个 Tab：

**内容**：标题 / 摘要 / 作者 / 正文 / 封面 / 正文图 / 原文链接（现有字段）

**排版**
- 主题选择：4 张预设卡片（缩略图用同一段示例文字实时渲染）
- 微调：字号滑块、行高滑块、段距滑块、主色选择、对齐切换、首行缩进
- 区块插入工具条：标题 / 引用 / 列表 / 分割线 / 强调卡 / 图注
- 右侧手机预览（375px 宽，真机宽度），懒渲染（输入停止 300ms）
- 底部统计：字数 / 图数 / 预计阅读时长 / 兼容性告警数

推送按钮旁显示「将使用：墨绿简约 · 16px · 行高 1.8」，让用户明确知道推出去长什么样。

## 8. 兼容性检查（Lint）

| 规则 | 级别 | 提示 |
|---|---|---|
| 正文含外部链接 | warning | 「外部链接在公众号内不可点击，已降级为文本」 |
| 使用了 `<div>`/`<table>` 等非白名单标签 | error | 内部错误，不应出现（渲染器保证） |
| 图片超过 1MB 或非 jpg/png | error | 已有转码流程兜底 |
| 使用了纯白/纯黑 | warning | 「深色模式下可能显示异常」 |
| 正文超过 20000 字 | error | 已有 |
| 未闭合 `:::` 块 | warning | 「内容按普通段落渲染」 |
| 无图注的长图（>3 张） | info | 「建议为图片补充图注」 |

## 9. 验收标准

1. 同一份 body + theme，`/drafts/render` 与推送任务产出的 HTML **逐字节相同**
2. 渲染结果中不含 `<style>`、`class=`、`id=`、`<div>`、`<h1>`、`background-image`
3. 所有文本内容 HTML 转义，注入 `<script>` 后渲染结果无脚本
4. 4 套预设 + 自定义主题的 tokens 越界值全部被拒绝（422）
5. 预览页在 375px 下无横向滚动
6. 推送任务 `layout_snapshot` 落库，改主题后旧任务 HTML 可复现
7. 回归：现有 12 个公众号用例仍通过（`# / ## / - / {{imageN}}` 语法向后兼容）

## 10. 分期

| 期 | 内容 | 状态 |
|---|---|---|
| P0 | 解析器重构 + 主题 tokens + 4 套预设 + 内联样式渲染 + 手机预览 + `render` 端点 | ✅ 已实现 |
| P1 | 区块库（强调卡 / 引导关注 / 图注 / 分割线）+ 自定义主题 CRUD + `layout_snapshot` | ✅ 已实现 |
| P2 | 一键排版（自动分段、标点统一、空行规范）、主题导入导出 | ⬜ 未开始 |

实现对照（P0 + P1，代码在 `apps/api/app/services/wechat_layout.py`）：

- 主题：`PRESET_THEMES` 四套 + `wechat_themes` 表自定义；`normalize_tokens` 校验区间/颜色/枚举，**未知参数直接报错并列出可用项**（不静默丢弃）。
- 区块：`parse_blocks` 支持标题三级 / 引用 / 有序无序列表 / 分割线 / 代码块 / `:::card`、`:::follow`、`:::sign`；未闭合区块按原样输出并给告警，不吞内容。
- 渲染：`render_document` 返回 `html` / `tokens` / `stats` / `warnings`；`lint_html` 单独做兼容性检查；预览与推送共用同一函数。
- API：`GET/POST/PATCH/DELETE /wechat-mp/themes`、`POST /wechat-mp/drafts/render`；`POST /wechat-mp/drafts` 接受 `themeId` 并把 tokens 写入 `PublishJob.layout_snapshot`。
- 前端：推送弹窗主题下拉框 + 375px `iframe[srcdoc]` 手机预览，改主题即时重渲染。
- 测试：`tests/test_wechat_layout.py` 12 项覆盖校验/渲染/lint/CRUD/快照推送。

## 11. 风险

| 风险 | 应对 |
|---|---|
| 微信后续调整渲染规则 | 兼容性 Lint 独立成模块，规则可热更新；推送失败保留真实 errcode |
| 自定义主题做出难看的结果 | token 范围限制 + 预设为默认值 + 预览必须可见 |
| 排版导致正文超 20000 字 | 渲染后二次校验长度（内联样式会增加字符数） |
| 解析器扩展后破坏旧语法 | 保留向后兼容用例，`# / ## / - / {{image1}}` 行为不变 |
