# 自媒体智能创作工作台：产品与系统设计说明书

**文档版本：** 1.3  
**状态：** 初始设计基线（评审稿）。实现状态请以 `README.md` 的「已实现的能力边界」与 `docs/setup.md` 为准；本文 §14 里标注「尚未实现」的条目是设计当时的记录，多条已后续落地，不代表当前代码状态。  
**日期：** 2026-11-06  
**产品对象：** 以图文为主、兼做漫图和视频脚本的个人创作者  
**目标平台：** 小红书、抖音、视频号、公众号、B站

---

## 0. 本轮需求变更与设计决策

用户明确提出：**希望包含自动发布和平台数据自动抓取**，并询问后续图形处理使用 NestJS 还是 Python。

本版变更：

1. 将自动发布与平台数据采集从“不做”改为目标能力，但按各平台官方开放能力逐个平台接入，不能假设五个平台都支持相同权限和流程。
2. 增加“平台连接器”边界，统一业务接口、平台专属适配器；发布和数据抓取均需处理授权、审核、限流、内容规范及接口变化。
3. 后端采用 **FastAPI 模块化单体 + Python Worker**，覆盖业务 API、模型接入、图像处理及后续漫图能力。
4. 前端采用 **Vue 3 + Vite**；UI 规范覆盖桌面端、移动端和暗色模式。（实现按纯 JS 落地，未引入 TypeScript。）
5. 多模型 API Key 只由服务端持有，服务端加密；浏览器不存储供应商长期密钥。

**1.2 版补充（实现侧对齐）：**

6. Agent 增加**状态与路径自省**：`/agent/status` 返回运行模式与数据落盘位置，`/agent/policy` 返回工具风险分级；设置页直接渲染服务端结果，不再前端硬编码。
7. 模型连接补齐**编辑能力**：`ConnectionPatch` 支持 `provider_type`，模型目录支持 `PATCH`/`DELETE`，改名不丢密钥。
8. 明确「不伪造」原则：无模型返回 → `failed`；无图像供应商 → `skipped`；无授权账号 → `platform_account_required`。任何情况下都不写占位文本冒充成功。
9. 验收标准补充第 13–18 条，并新增 §14.3 记录当前实现状态与未覆盖项。

**1.3 版补充（图文卡片工作台）：**

11. 新增「图文卡片」页面（`/cards`）：把 Markdown 转换为平台固定画幅的序列卡片，支持 4 种平台预设（3:4 / 9:16 / 1:1 / 4:3）与自定义宽高。
12. 视觉能力对齐成熟工具：8 套内置模板、7 种 SVG 底纹、6 种边框装饰 + 自定义透明边框图、自定义背景图与透明度、页眉/页脚/页码/安全区参考线与全局字号缩放。
13. 分卡策略三种：智能高度分页（标题防孤行）、仅按 `---` 分页、按二级标题分卡。前端 `paginator.js` 与后端 `card_paginator.py` 使用同一套估算公式，保证同一输入的 AST 一致。
14. 自定义模板由 localStorage 迁移到服务端持久化（`card_templates`，按 owner 隔离），支持创建、改名、删除。
15. 双引擎：默认浏览器内 `html-to-image` 直出 3x PNG / ZIP 图集；可切换「后端 API 协同」模式，由 `/cards/paginate` 返回 AST 供渲染队列复用，并有契约弹窗展示请求体、响应结构与无头渲染参考代码。
16. 卡片样式不引入 Tailwind：沿用 `--color-*` token + scoped CSS，避免 preflight 污染全站，并让工作台界面跟随明暗主题（卡片画布本身按模板固定配色）。

**本轮确认：**
- 自动发布与平台数据自动抓取是产品目标；五个平台先并行调研能力，不等于五个平台同时具备或同时上线。
- 自动发布前每篇人工确认，作为默认审核策略。
- 首期图形处理范围：基础裁剪压缩 + 漫图分格与处理。漫图分格先做可控的图像裁切/人工确认，不承诺自动理解分镜；如需自动识别格线、气泡和语义，再评估 Python 视觉服务。

**关键前置条件：** 平台自动发布/数据获取能力依赖平台官方 API、应用资质、账号权限、审核和具体接口范围。设计上支持逐平台接入，但在核实每个平台现行开放接口前，不承诺任一平台可自动发布或可取到全部指标。平台不提供或未授权的能力只提供人工导入/手动登记，不采用浏览器登录态复用、私有接口或绕过风控的方式抓取。

---

## 1. 产品目标、范围与边界

### 1.1 产品定位

面向个人自媒体创作者的 AI 内容生产与运营工作台，覆盖：

`灵感 → 选题 → 内容规划 → AI 创作 → 图文处理 → 多平台适配 → 发布/定时发布 → 数据回流 → 周复盘`

### 1.2 首要目标

- 将选题、稿件、配图、平台版本、发布任务、平台表现和复盘关联起来。
- 支持多个文本模型和图像模型按任务选择，支持供应商兼容适配。
- 在平台官方接口允许时，连接账号、发布内容、抓取可授权数据。
- 所有自动动作可预览、可确认、可追踪；失败不丢内容，支持补偿与人工接管。

### 1.3 产品边界

- 自动发布仅通过平台公开/获批的官方接口或平台明确支持的方式；不模拟用户登录、不破解客户端、不绕过风控。
- 数据抓取仅限用户授权账号及平台允许访问的数据，不使用非授权爬虫抓取受限数据。
- 自动发布设置须能按平台、账号、内容类型、是否定时单独开启；默认关闭，用户明确授权并启用。
- 用户须在发布前审核内容、配图、标题、标签及目标账号；可设置发布前人工确认，首期默认开启。
- 平台不支持的能力显示“暂不可自动化”，允许导出内容/提醒用户手动完成，并记录发布状态。
- AI 生成的文章与图片均需人工审核；不承诺流量或增长结果。

### 1.4 产品指标

| 指标 | 定义 |
|---|---|
| 周发布完成率 | 用户本周已完成发布数 / 本周计划发布数 |
| 自动发布成功率 | 平台确认成功的自动发布任务数 / 已执行任务数；按平台拆分 |
| 数据同步成功率 | 成功同步的平台账号数 / 本轮应同步账号数 |
| 创作转化率 | AI 生成内容中被保存、编辑或发布的比例 |
| 图像处理耗时 | 上传/生成到处理完成的 P50、P95 时间 |
| 人工介入率 | 需要用户重新授权、编辑或手动发布的任务比例 |

不将模型调用次数、未经平台确认的“发布成功”或缺失数据当成业务成功指标。

---

## 2. 用户角色与权限

### 2.1 首期角色

- **工作区所有者：** 绑定平台账号、配置模型、创作、批准和查看本人的数据。
- **协作者（后续）：** 仅当产品引入团队时增加编辑、审核、只读等工作区角色。
- **系统运维：** 维护服务和排障，不默认访问用户正文、平台令牌或 API Key 明文。

### 2.2 访问控制

所有用户数据均以认证会话中的 `owner_user_id`/`workspace_id` 限定；不信任客户端提供的用户 ID。平台授权凭据、模型凭据与业务内容分层存储，访问模型密钥和平台令牌需要服务端最小权限服务。

### 2.3 平台授权

- 通过平台官方 OAuth/授权流程获取授权；只请求业务必需的权限范围。
- 记录授权范围、过期时间、刷新状态、最后成功同步时间。
- 用户可随时解除账号绑定；解除后停止抓取和发布，并删除或吊销令牌。
- 授权过期、撤回或权限不足时，暂停任务并提示重新授权，不无限重试。

---

## 3. 信息架构与页面

### 3.1 导航结构

```
创作总览
├── 选题库
├── 内容日历
├── 创作空间
│   ├── AI 写作与稿件版本
│   ├── 多平台改写
│   ├── AI 配图与图形处理
│   └── 草稿箱
├── 图文卡片
│   ├── Markdown 编辑与分卡
│   ├── 模板 / 底纹 / 边框 / 布局
│   └── 单张 PNG 与 ZIP 图集导出
├── 发布中心
│   ├── 发布队列
│   ├── 定时计划
│   ├── 发布结果/失败处理
│   └── 账号授权
└── 设置
    ├── 模型与 API
    ├── Agent 与 Skills
    ├── 平台账号与权限
    ├── 个人风格档案
    ├── 安全、备份与数据管理
    └── 图像处理偏好
```

当前实现为 8 个页面（总览、选题、创作、日历、图文卡片、发布、素材、设置），侧栏 / 移动端底部导航共用同一路由表。设置页现有 4 个分组：外观与主题、模型与 API、Agent 与 Skills、数据与安全；「个人风格档案」与「图像处理偏好」尚未拆出独立分组。

### 3.6 AI Agent 执行层

工作台建议集成 AI Agent，但不把 Claude Code 直接运行在浏览器里，也不让 Agent 默认拥有整台电脑的读写权限。工作台负责任务、上下文、权限、审核和结果归档；Agent 运行器负责规划步骤并调用受控工具。

**推荐落地方式：**

- FastAPI 后端增加 Agent Orchestrator，第一阶段可使用 Python Agent SDK；如果需要调用本机 Claude Code CLI，则由受控 Worker 以子进程方式运行，不在前端启动。
- Skill 作为可版本化的能力包，描述触发条件、输入输出、允许工具、风险等级和人工确认点。工作台只加载已安装、已启用且符合工作区权限的 Skill。
- MCP 用于连接外部资料库、平台连接器和图像处理工具；每个 MCP Server 以独立连接配置管理，按任务授予工具，不把所有工具一次性暴露给 Agent。
- Agent 通过结构化工具调用使用工作台能力，例如 `read_content`、`create_outline`、`generate_article`、`generate_image`、`prepare_platform_variant`、`create_publish_preview`、`sync_metrics`。工具结果回写业务实体。
- 发布、删除、修改模型凭据、解绑平台账号、执行设备级命令等高风险操作永远需要用户确认；Agent 可以准备方案和预览，但不能静默执行。
- 低风险只读任务可自动执行；内容生成默认允许自动运行但结果进入草稿/待审核；外部发布和数据写入按风险分级拦截。
- Agent 会话、Skill 版本、工具调用、审批结果、模型与费用信息均记录，便于恢复、审计和复盘；不把完整密钥或不必要的正文写入日志。

**与 Claude Code 的边界：** Claude Agent SDK 官方支持内置工具、Hooks、MCP、权限、会话、Skills、Commands 和 Plugins；生产集成使用 API Key 等受支持的自动化认证，不把用户的 Claude.ai 登录态当作第三方产品的通用授权。产品界面应使用自己的 Agent/工作台品牌，不伪装成 Claude Code 产品。若用户只是想在本机开发目录中运行 Claude Code，可提供“连接本地 Agent 会话”入口，但该会话必须被工作区授权、沙箱/目录范围和工具白名单约束。

### 3.7 图文卡片工作台

定位：把一篇 Markdown 长文稳定切成「一屏一图」的序列卡片，直接产出可上传小红书 / 抖音的 PNG，而不是让用户在通用设计工具里手工排版。

**工作台布局（三栏）：**

| 区域 | 内容 |
|---|---|
| 左：编辑器 | Markdown 文本域、字符统计、三种分卡策略、插入分页符 / 高亮 / 表格 / 本地图片、三份预置样例 |
| 中：预览 | 按当前画幅逐张渲染卡片，显示 `CARD 01 / 06`、版面占用百分比与溢出提示；可过滤单张、切 85% / 100% 缩放、开关安全区参考线、单张导出 |
| 右：检查器 | 三个页签：模板库（8 套内置 + 自建，可保存/删除）、底纹与边框、尺寸与页脚 |

**画幅预设：** 3:4（360×480）、9:16（360×640）、1:1（380×380）、4:3（480×360），固定 `exportScale = 3`，即 1080×1440 / 1080×1920 / 1080×1080 / 1440×1080。自定义宽高限制在 280–640 × 320–800，避免估算与实际排版严重偏离。

**模板配置（`CardTemplate`）字段：** `bgStyle` / `bgColorHex` / `customBgImage` / `bgImageOpacity` / `patternType`（none / grid / dots / ruled-lines / paper-grain / crosshairs / diagonal）/ `patternColor` / `patternOpacity` / `borderFrameType`（none / double-editorial / viewfinder-corners / stamp-perforated / film-sprocket / gallery-filigree / swiss-grid）/ `borderColor` / `borderInset` / `customBorderImage` / `fontMode`（editorial-serif / modern-sans / technical-mono）/ `textColor` / `mutedColor` / `accentColor` / `accentBgSoft` / `cardSurfaceBg` / `tableBorderColor` / `paddingX` / `paddingY` / `borderRadius`。

**图层顺序：** 背景色或渐变 → 自定义背景图 → SVG 底纹 → 边框装饰（SVG 画框或自定义透明边框图）→ 页眉 → 正文块 → 页脚 / 页码。自定义图片一律转 dataURL 后再渲染与导出，避免 `html-to-image` 遇到跨域图片时产出空白。

**分卡规则：**

1. `smart-auto`：按估算高度逐块累加，超过可用高度就换卡；`h2` / `h3` 带「防孤行」附加权重，避免标题落在卡片末尾。可用高度 = 画布高 − 上下 padding − 页眉预留 − 页脚预留。
2. `manual-hr`：`---` 之间的一段一卡，作者完全控制分页。
3. `by-h2`：遇到 `h1` / `h2` 换卡，按章节自然分页。

估算公式按块类型区分（标题按字符数折算行数、表格按行数、列表按条目行数、代码按行数），前后端同构：`apps/web/src/features/cards/paginator.js` 与 `apps/api/app/services/card_paginator.py` 必须同时改，否则双引擎结果会漂移。

**双引擎：**

- 纯前端（默认）：`html-to-image` 的 `toPng(pixelRatio = 3)` 逐张导出，全部卡片可打包 ZIP，内含 `card_manifest.json`（平台、模板、meta 与卡片 AST）。导出前 `await document.fonts.ready`，否则字体会回退。
- 后端 API 协同：`POST /api/v1/cards/paginate` 返回 `renderSpec` + `template` + `meta` + `cards`，供服务端渲染队列或无头浏览器出图；切换后编辑内容会以 280ms 防抖同步，后端不可用时保留本地卡片不打断编辑。

**输出契约：** 每张卡片为 `{ index, total, isCover, blocks[], estimatedUsedHeight, maxAvailableHeight, isOverflowing }`，块类型为 `h1 / h2 / h3 / paragraph / blockquote / image / table / ul / ol / code`。行内语法支持 `**加粗**`、`*斜体*`、`==高亮==`、`` `行内代码` `` 与链接，渲染前统一转义，不使用第三方 Markdown 解析器。

**已知限制：** 高度是估算而非真实测量，极长英文单词、超宽表格或大量 emoji 可能造成轻微溢出，因此界面保留「建议开启智能高度分页」提示；导出依赖浏览器渲染能力，Safari 下 WebP 背景图可能不被 `html-to-image` 支持，优先使用 PNG/JPG/SVG。

### 3.8 视觉方向与设计原则

**方向：** 安静、清晰、有编辑感的创作者工作台。避免营销落地页式的大型 Banner、过多装饰卡片和强烈渐变；首屏直接呈现今日创作任务、待审核发布和快捷创作入口。沿用用户已选择的柔和低饱和色调与卡片手账感，但控制卡片嵌套，重点是内容可读和重复操作高效。

**信息优先级：**
1. 一级：当前任务、稿件标题、发布/生成状态、明确的主操作。
2. 二级：内容摘要、平台、截止时间、模型或账号。
3. 三级：帮助提示、更新时间、技术细节和来源。

**桌面端工作区：** 主内容最大宽度 1440px；左侧固定主导航（约 224px），中间工作区，创作页按需使用右侧 AI 助手面板（约 320px，可折叠）。阅读/编辑正文最大宽度约 760px。总览用 12 列栅格，指标区不超过 5 项；卡片只用于独立数据对象、任务或重复记录，不将整个页面 section 层层包卡片。

**移动端工作区：** 宽度 < 768px 时切换为顶部简化标题 + 底部 5 项导航（总览、选题、创作、发布、我的）；列表改为单列；编辑器和 AI 助手切为可切换面板/底部抽屉；发布审核按钮固定在安全区上方但不遮挡内容；表格改为摘要列表或横向滚动容器；触控目标至少 44×44 CSS px；支持安全区 `env(safe-area-inset-*)`。

**断点：** `sm 640px / md 768px / lg 1024px / xl 1280px`。断点决定布局变化，不用视口宽度缩放字号。桌面侧栏在平板折叠成图标栏，在手机转为底部导航。

### 3.9 设计系统与主题

#### 颜色 Token

使用语义 Token，不在业务组件里直接写颜色值。主题由 `data-theme="light|dark"` 或系统偏好驱动；用户显式选择优先于系统偏好，选择保存在用户设置中。

| Token | 浅色模式 | 暗色模式 | 用途 |
|---|---|---|---|
| `--color-bg` | `#F5F6F4` | `#171A18` | 页面底色 |
| `--color-surface` | `#FFFFFF` | `#202522` | 主工作区、对话层 |
| `--color-surface-raised` | `#FBFCFA` | `#272D29` | 卡片、输入区 |
| `--color-text-primary` | `#202723` | `#E8EEE9` | 主文字 |
| `--color-text-secondary` | `#58645D` | `#B1BDB5` | 次级说明 |
| `--color-text-muted` | `#77827B` | `#8F9D94` | 时间、辅助元信息 |
| `--color-border` | `#DDE3DE` | `#39423C` | 分隔线、边框 |
| `--color-accent` | `#527A68` | `#92B9A4` | 主要交互、选中状态 |
| `--color-accent-hover` | `#416553` | `#A7C9B6` | hover/按下 |
| `--color-accent-soft` | `#E8F0EA` | `#2D4035` | 选中背景、轻提示 |
| `--color-info` | `#3F6F91` | `#8DB8D4` | 信息状态 |
| `--color-success` | `#3F7558` | `#8FC49E` | 成功状态 |
| `--color-warning` | `#976D2F` | `#E2B96F` | 等待/提醒 |
| `--color-danger` | `#A44F4B` | `#E29A94` | 错误/危险操作 |

品牌主色采用柔和植物绿，不把整个界面染成同一色系；状态色只用于状态文字、图标、边线或小面积底色。暗色模式使用深灰绿而非纯黑背景，避免高对比刺眼。文字与背景组合需满足 WCAG AA：普通文字对比度至少 4.5:1，大字号至少 3:1；不能只用颜色表达状态，同时配文字/图标。

#### 排版与间距

| Token | 值 | 使用 |
|---|---:|---|
| `font-xs` | 12px | 时间、辅助标签，不用于关键说明 |
| `font-sm` | 13px | 次要信息、表格辅助内容 |
| `font-body` | 14px | 默认正文与表单 |
| `font-section` | 16px | 页面小标题 |
| `font-page` | 20px | 页面标题 |
| `font-display` | 24px | 少量关键数据，不做巨型营销标题 |
| `space-1..8` | 4/8/12/16/20/24/32/40px | 统一间距刻度 |
| `radius-sm/md/lg` | 6/10/14px | 标签/输入、普通控件、弹层/大面板 |
| `control-height` | 40px 桌面；44px 移动 | 输入框、按钮、选择器 |

字体优先系统 UI 字体：`Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif`。正文行高 1.55–1.7；编辑器内容最大行宽约 70–80 个汉字，保证长文易读。移动端字号保持稳定，不随 viewport 用 `vw` 缩放。

#### 组件规范

| 组件 | 规则与状态 |
|---|---|
| 主按钮 | 每个视图一个明确主操作；默认、hover、pressed、disabled、loading 状态完整；避免多个同权重实心按钮竞争 |
| 次按钮/图标按钮 | 次要操作用描边或无底色；图标按钮提供可访问名称和 tooltip，触屏仍有 44px 点击区 |
| 表单 | 标签常驻，不依靠 placeholder 作为唯一说明；校验错误贴近字段并说明修复方法；密码默认遮蔽 |
| 内容卡片 | 选题/草稿/发布任务卡片字段顺序一致：标题→状态→平台/时间→操作；避免卡片中再套卡片 |
| 状态标签 | 状态色 + 文本；任务状态用“排队中/发布中/需处理”等稳定词汇，不单靠颜色 |
| 对话框/抽屉 | 桌面优先对话框；手机改全屏或底部抽屉，支持关闭、返回键和焦点管理 |
| 编辑器 | 自动保存状态明确；AI 结果先预览，不覆盖原文；版本恢复前提示影响 |
| 图表 | 显示时间范围、单位、来源和缺失状态；暗色主题重设网格线/Tooltip/图例，避免低对比 |
| 危险操作 | 删除账号、解绑授权、清空素材等要求二次确认，说明不可逆影响 |

### 3.10 关键 UX 流程与状态

#### 主题切换

设置菜单提供“跟随系统 / 浅色 / 暗色”。首次加载遵循系统 `prefers-color-scheme`；用户手动选择后保存到服务端用户偏好；主题切换不刷新页面，所有图表和编辑器同步更新。预渲染时先应用主题，避免白屏闪烁。

#### 发布审核

发布编辑 → 选择账号/时间 → 平台规则预检 → 预览内容与图片顺序 → 人工确认 → 队列状态 → 成功/失败/需核实。确认按钮须明确显示平台、账号和时间；重复点击由幂等保护。手机审核页把预览放在首屏，确认/取消在底部安全区，不能只显示一排小图标。

#### 空、加载、错误、离线状态

- **空状态：** 说明这里是什么、为何为空、下一步可做什么，并提供一个主要 CTA，例如“记录第一个选题”。
- **加载状态：** 页面骨架屏用于首屏；长任务使用有阶段/进度的任务卡，避免无限转圈。
- **错误状态：** 保留用户输入和已生成部分；说明错误、影响和下一步；提供重试前判断，不对发布失败盲目重试。
- **离线状态：** 显示离线条，允许继续编辑本地未提交内容；恢复网络后提醒同步冲突，不静默覆盖服务器版本。
- **权限/未授权：** 明确说明缺少哪个平台授权或模型配置，引导用户前往对应设置。
- **无数据：** 区分“尚未同步”“接口不支持”“暂无数据”“真实为 0”。

### 3.11 无障碍与响应式验收

- 全站键盘可操作；焦点顺序跟随视觉顺序，焦点环清晰；弹层打开后焦点限制在弹层内，关闭后返回触发控件。
- 按钮、导航、图标和图表有可访问名称；表单错误通过 `aria-describedby` 关联。
- 支持 `prefers-reduced-motion`；动画只用于反馈，不承载唯一信息。
- 不依靠颜色单独传达成功/失败/选中；图表提供可读文本摘要/数据表入口。
- 浏览器缩放 200% 和窄屏 320px 下无横向整页溢出；复杂表格可在局部容器内横向滚动。
- 桌面、平板、移动端均验证：导航、编辑器、AI 输出、图像预览、发布审核、主题切换和错误提示。

---

## 4. 核心流程

### 4.1 选题到多平台内容

1. 用户记录灵感并完善受众问题和内容承诺。
2. 选题可调用指定文本模型扩展多个角度；确认后进入待创作。
3. 创建主稿，选择目标平台及风格，AI 生成提纲或初稿。
4. 用户编辑后保存版本；从主稿生成平台版本，每个平台独立可编辑。
5. 需要配图时，从稿件摘要构造图像描述，选择图像模型和画幅。
6. 用户审核文字和图片后，可保存草稿、进入平台发布中心或导出手动发布。

### 4.2 自动发布

1. 首次绑定平台账号，完成官方授权并展示实际获得权限。
2. 选择支持发布的账号与内容类型；平台不支持时禁用自动发布入口。
3. 用户选择立即发布或定时发布，预览最终内容并勾选确认。
4. 服务端校验权限、模型/平台格式要求、媒体格式、文案限制、授权有效期和用户发布规则。
5. 创建幂等发布任务；任务队列按平台限流执行。
6. 调用平台官方接口，记录平台返回的内容 ID、状态和请求号。
7. 结果不确定时进入“需核实”状态，通过官方查询接口核实，不直接二次发布。
8. 成功后写入发布记录，排入后续数据同步任务。

### 4.3 数据自动抓取

1. 用户绑定账号并授权数据读取范围。
2. 首次同步拉取平台允许访问的近期内容和指标；范围按 API 限制配置。
3. 定时任务增量同步，使用平台游标/更新时间/内容 ID，支持断点续跑。
4. 归一化指标同时保留平台原字段和口径元数据，不丢弃平台特有指标。
5. 同步失败按错误类型退避；令牌失效暂停并通知用户重新授权。
6. 仪表盘显示最后成功时间、失败原因、待处理账号。

### 4.4 图形处理

1. 用户选择本地上传图片或 AI 生成图片。
2. 服务端检查文件类型、大小、像素、恶意文件风险，生成安全对象 key。
3. 基础处理：读取尺寸与方向、生成缩略图、调整尺寸、转 WebP/JPEG/PNG、裁切/压缩。
4. 进阶处理任务（OCR/抠图/视觉理解/漫图分格）依据能力路由至图像服务。
5. 处理完成后产生新素材版本，保留原图和参数记录；不覆盖原件。
6. 用户预览并选择用于封面、正文、视频脚本配图或下载。

---

## 5. 技术架构：Vue 3 + FastAPI

### 5.1 推荐结论：Vue 3 + TypeScript 前端，FastAPI 单后端

本方案采用 Vue 3 + TypeScript 构建工作台 Web 前端，FastAPI 作为主后端。

| 工作 | 建议运行位置 | 原因 |
|---|---|---|
| Web 工作台 | Vue 3 + TypeScript + Vite | 与现有前端方向一致，适合稿件编辑、队列和数据视图 |
| 用户、选题、稿件、日历、权限、模型配置 | FastAPI | Python 类型校验与 OpenAPI 生态，统一业务 API |
| 多供应商模型网关 | FastAPI 服务模块 | 集中鉴权、凭据、配额、审计、路由及 SSE |
| 平台发布/数据连接器 | FastAPI 服务模块 | OAuth 回调、任务状态、事务、限流与平台适配集中管理 |
| 常规图片裁剪/缩放/压缩/缩略图/格式转换 | Python Worker + Pillow 或 libvips 绑定 | 统一 Python 技术栈，任务异步执行 |
| OCR、抠图、视觉理解、漫图切格 | Python Worker/视觉模块 | 复用 Python 图像和机器学习生态，按需使用 CPU/GPU |
| 长耗时图片/平台任务 | Redis + Celery/RQ Worker | 解耦 HTTP 请求、允许状态跟踪、重试和取消 |

首期不建议把 API、AI、图像处理都拆成多个微服务。先按 FastAPI 内部模块分层，并把耗时任务放到 Worker；当视觉任务需要独立扩缩容或 GPU 资源隔离时，再拆 Python 视觉服务。由于主后端也是 Python，这种拆分是部署边界，而非第二种语言栈。

### 5.2 Vue 3 选型

继续使用 Vue 3 + Vite：页面级路由、Pinia 状态管理与组件化足以覆盖当前工作台。

### 5.3 为什么选择 FastAPI 主后端

- AI 请求、图像处理和后续视觉能力主要使用 Python 生态，少一次跨语言任务协议。
- 类型声明、请求校验与 OpenAPI 文档可由同一套接口定义维护。
- 对 PostgreSQL、Redis 任务队列、对象存储和模型 SDK 均有成熟 Python 生态。
- FastAPI 的异步路由适用于等待外部模型/平台响应的 I/O 场景；CPU/GPU 图像计算仍应进入 Worker，不能阻塞事件循环。

### 5.4 总体架构图

```text
┌──────────────────── Web 客户端 ─────────────────────┐
│ Vue 3 + TypeScript + Vite                            │
│ 编辑器 / 日历 / AI 工作区 / 发布中心                │
└───────────────────┬────────────────────────────────┘
                    │ HTTPS REST + SSE
┌───────────────────▼────────────────────────────────┐
│ FastAPI 模块化单体                                   │
│ Auth / Workspace / Content / Topics / Assets         │
│ AI Gateway / Agent Orchestrator / Skill Registry      │
│ Platform Connectors / Publishing / Analytics          │
└─────────┬──────────────┬──────────────┬─────────────┘
          │              │              │
     PostgreSQL       Redis Queue   Object Storage
                         │
                  ┌──────▼──────────┐
                  │ Python Workers  │
                  │ Agent Runtime   │
                  │ AI/Image/Publish│
                  │ Sync/Comic      │
                  └──────┬──────────┘
                         │
           ┌─────────────┴───────────────┐
           │                             │
    AI Provider Adapters          Platform Connectors
           │                             │
    多供应商文本/生图           官方授权 API（逐平台验证）
```

平台连接器按官方接口授权范围各自实现；每个平台独立适配，不存在一个通用 API 自动覆盖所有平台。

### 5.5 前端建议

- Vue 3 + TypeScript + Vite；采用 Vue Router 管页面，TanStack Query for Vue 管服务端数据，Pinia 管客户端状态；避免 Pinia 与服务端缓存重复持有同一份业务数据。
- Tiptap/ProseMirror 编辑器，稿件版本与媒体节点由应用数据管理。
- 设计系统通过 CSS variables 实现语义色和主题切换；页面组件使用 UI primitives，不在业务组件写死色值。
- 前端仅使用短期登录会话；平台 token、模型 Key、对象存储长期凭据不落浏览器。
- SSE 用于文本流和任务通知；页面刷新后通过服务端任务 ID 恢复状态。

### 5.6 后端建议

- FastAPI 模块化单体先行，不拆微服务。
- SQLAlchemy 2 + Alembic（或 SQLModel；生产项目建议 SQLAlchemy/Alembic 显式管理模型与迁移）。
- PostgreSQL 作为主数据存储；Redis + Celery（或 RQ，项目选定一种）承载发布、同步、图片处理与长耗时生成任务。
- Pydantic DTO 与 OpenAPI 契约；统一错误码、分页格式、认证依赖和权限检查。
- FastAPI API 层负责鉴权、会话、Skill/工具权限、审批和事件流；Python Agent Worker 运行模型推理与工具循环。

### 5.7 Agent Runtime 与 Skill 执行器

FastAPI 负责 Agent 会话和产品权限，实际推理与工具循环由 Python Agent SDK 运行在隔离 Worker 中。运行器只接收 `workspaceId`、用户任务、允许的 Skill ID、明确授权的工具集合及会话上下文引用；不能接收任意本机路径或任意 shell 命令。

- 默认提供工作台原生 Agent Runtime；可选 Claude Agent SDK Provider，以 API Key 认证并遵循 Anthropic 商业条款。
- Skill 清单从工作台受信任的 Skill Registry 获取，记录版本、来源、签名/校验值、权限声明和启停状态。首期不支持任意导入后自动执行；新 Skill 先审核、展示权限再启用。
- Skill 接口统一为 `manifest + input schema + output schema + allowed tools + risk level`。Skill 调用记录固定 Skill ID 与版本，保证后续可复现。
- MCP Server 由服务端受控运行或连接，按 Agent 任务分配最小工具集合；不直接把所有工作台连接器和本机 MCP 暴露给每次会话。
- Agent 会话与后台任务解耦：SSE 发送 token/工具事件，数据库保存消息、运行状态和事件游标；浏览器断开后可恢复/取消。
- 工具执行经过统一 `Tool Gateway`，执行身份来自认证用户和工作区 ACL。写入业务数据必须走领域服务，不能让 Agent 直接执行 SQL。
- 如接入 Claude Code CLI 的本地开发能力，必须作为可选“本地 Agent 连接器”：用户明确连接、限定项目目录、操作系统沙箱、网络域名白名单、禁止读取凭据目录；未授权时不启动子进程。

Agent 任务风险分层：

| 等级 | 示例 | 默认策略 |
|---|---|---|
| 只读 | 查选题、检索已授权素材、总结数据 | 可自动执行，结果可追踪 |
| 可撤回写入 | 创建草稿、保存选题、生成图片任务 | 可自动执行，进入草稿/待确认状态 |
| 外部副作用 | 发布内容、解绑账号、删除素材、改变模型/权限设置 | 运行前逐项向用户展示并取得确认 |
| 禁止 | 任意 shell/SQL、越权读其他工作区、绕过平台审核、无授权抓取 | 工具层直接拒绝，不交由模型判断是否允许 |

SDK 权限、Hook 和回调是纵深防护的一部分，但最终授权由工作台自己的服务端策略与工具网关强制执行，不能仅依赖 System Prompt。

---

### 5.8 Python 图像处理与 Worker 约束

- 图片裁剪、缩放、压缩、缩略图和基础漫图分格由 Worker 执行，不能在 FastAPI 请求事件循环中跑 CPU 密集处理。
- OCR、抠图、视觉理解、漫图格线/气泡识别按需加入 Worker；GPU 任务使用独立队列和资源限制。
- 接收对象存储内部 key + 操作参数，不在队列消息中放大文件二进制，也不传模型密钥或平台 token。
- 返回新对象 key、尺寸、格式、耗时、质量和错误码；任务幂等，不覆盖原图。
- 容器限制 CPU、内存、像素总量、输入大小和执行超时；异常文件隔离，不返回内部栈信息。

---

## 6. 模型连接与 AI Gateway

### 6.1 连接与模型分离

一个供应商连接可以配置多个模型。连接保存供应商类型、Base URL 和加密凭据；模型目录保存模型名、能力和参数支持情况。

**模型能力：** `text.generate`, `text.stream`, `text.rewrite`, `image.generate`, 后续 `vision.input`, `ocr`, `embedding`。选择器按能力过滤，不让不支持生图/流式的模型被误选。

### 6.2 统一接口与适配器

- `OpenAICompatibleChatAdapter`：文本 Chat Completions/SSE。
- `OpenAICompatibleImageAdapter`：只针对确认兼容 Images API 的服务。
- `VendorNativeAdapter`：各家原生协议按需实现。
- 图片模型结果统一为可持久化媒体引用，不假设一定返回 URL；兼容 URL、base64、异步任务 ID 等实际响应形式。
- 用户可配置多个文本/图像模型，分别设置任务默认值；调用时可以覆盖。

### 6.3 AI 任务契约

```typescript
interface TextGenerationRequest {
  taskType: 'idea_expand' | 'outline' | 'article' | 'rewrite' | 'polish' | 'summarize';
  modelId: string;
  topicId?: string;
  contentVersionId?: string;
  platform?: PlatformCode;
  styleProfileId?: string;
  prompt: string;
  constraints?: { targetLength?: number; tone?: string; audience?: string };
}

interface ImageGenerationRequest {
  modelId: string;
  purpose: 'cover' | 'article_illustration' | 'comic_panel' | 'reference';
  prompt: string;
  negativePrompt?: string;
  aspectRatio?: string;
  count: number;
  topicId?: string;
  contentVersionId?: string;
}
```

供应商不支持的字段需要在 UI 明确提示；不得静默声称参数已生效。

### 6.4 模型选择、降级和重试

- 明确选定的模型失败时默认保留错误和用户输入，不静默切换供应商。
- 用户可启用“同能力备用模型”；系统只对超时、临时不可用、可重试限流按策略切换一次。
- 认证失败、无权限、参数错误、内容拒绝不自动重试。
- 文本流式返回已有部分时，不能把另一模型输出拼接到同一篇内容；保留部分结果，提示重新生成或手动切换。
- 图片任务通过队列执行，按供应商限速和用户配额调度。

### 6.5 密钥和提示词

- 模型 Key 在服务端加密保存；前端只接收配置名、类型、可用状态和掩码。
- 服务端日志永不记录 Authorization、密钥、完整平台令牌。
- 提示词模板版本化；系统规则由服务端维护，用户输入不进入 system 角色。
- 默认只发送本次生成所需的选题/稿件片段；用户确认后才注入个人风格档案。
- 输出按安全 Markdown 展示，不执行模型返回的 HTML/脚本。
- 清楚提示稿件会发送至用户所选供应商，用户须核对该供应商的隐私条款。

### 6.6 SSE 与任务事件

文本生成事件：`generation.started`、`generation.delta`、`generation.usage`、`generation.done`、`generation.error`。图片和平台任务使用异步任务状态：`queued → running → succeeded | failed | cancelled | needs_attention`。断线后由 `GET /jobs/:id` 恢复。

---

## 7. 平台连接器、发布与数据同步

### 7.1 平台 Connector 抽象

```typescript
interface PlatformConnector {
  getCapabilities(account: ConnectedAccount): Promise<PlatformCapabilities>;
  refreshAuthorization?(account: ConnectedAccount): Promise<AuthRefreshResult>;
  validateDraft(input: PlatformDraftInput): Promise<ValidationIssue[]>;
  publish?(input: PublishInput, idempotencyKey: string): Promise<PublishResult>;
  getPublishStatus?(remoteContentId: string): Promise<PublishStatus>;
  syncAccountMetrics?(account: ConnectedAccount, cursor?: string): Promise<MetricSyncPage>;
  syncContentMetrics?(remoteContentIds: string[]): Promise<MetricSyncResult[]>;
}
```

Connector 必须显式声明 `publish`、定时发布、图片发布、草稿上传、账号指标、单篇指标等能力；缺少能力时返回不支持，不能返回假成功。

### 7.2 平台能力矩阵（上线前必须逐项核实）

| 平台 | 账号授权 | 自动发布 | 定时发布 | 内容数据读取 | 备注 |
|---|---|---|---|---|---|
| 小红书 | 待核实官方开放范围 | 待核实 | 待核实 | 待核实 | 必须确认账号类型、应用资质、接口审批与发布类型限制 |
| 抖音 | 待核实具体开放平台产品 | 待核实 | 待核实 | 待核实 | 发布权限及数据范围可能依应用审核与授权 scope 而异 |
| 视频号 | 待核实微信开放能力和账号主体 | 待核实 | 待核实 | 待核实 | 公众号/视频号能力不可混为一谈 |
| 公众号 | 待核实草稿/发布/数据接口权限 | 待核实 | 待核实 | 待核实 | 常见能力可能受认证主体和接口权限限制 |
| B站 | 待核实官方开放平台接口 | 待核实 | 待核实 | 待核实 | 需确认投稿与数据接口覆盖范围 |

该矩阵是**开发前检查清单，不代表接口当前存在或已获授权**。平台 API 具有时效性，实施时需查平台官方文档、实际应用权限与审核结果，并记录证据日期、scope、配额和账号要求。若平台不开放自动发布或指标 API，则设计降级到人工发布/用户导入数据。

### 7.3 授权生命周期

`未连接 → 等待授权 → 已连接 → 即将过期 → 需重新授权 / 已解绑`。

- OAuth 回调校验 state/nonce，绑定授权结果到当前用户和目标平台。
- Refresh token 加密存储；根据供应商返回期限提前刷新。
- 刷新失败或授权撤回后，将账号置为 `reauthorization_required`，暂停相关队列。
- 用户解绑后吊销令牌（平台支持时）并清除本地凭据。

### 7.4 发布任务和幂等

发布任务至少含：目标账号、平台版本、媒体集合、计划时间、审核人、幂等键、任务状态、尝试次数、远端 ID、错误分类、请求追踪 ID。

为避免“请求超时但远端已发布”导致重复内容：

- 每个用户发布动作生成唯一 `idempotencyKey`。
- 调用前持久化任务状态并使用数据库锁/唯一约束阻止并发重复。
- 供应商支持幂等键时透传；否则超时后先查询发布状态/远端列表核实。
- 状态不明进入 `needs_attention`，由用户确认后再重试。

### 7.5 数据同步与平台指标口径

归一化公共字段（按平台能力可空）：`views`, `plays`, `likes`, `comments`, `shares`, `saves`, `followersGained`, `engagementRate`。另存 `rawMetrics jsonb`、采集时间、平台原始定义和同步游标。不同平台指标定义不一，展示时标注平台，跨平台对比只用清晰定义的共有指标。

同步机制：定时扫描连接账号 → 领取锁 → 读取 cursor → 分页请求 → upsert 数据快照 → 推进 cursor → 更新最后同步状态。使用指数退避和随机抖动；仅对网络/5xx/可重试限流重试。授权错误需用户介入。

---

## 8. 图像处理设计

### 8.1 首期基础能力

- 图片上传与格式/尺寸/容量校验。
- 方向纠正、缩略图、多尺寸派生、压缩、WebP/JPEG/PNG 转换。
- 指定比例裁切：小红书竖版、横版封面、方图等。
- 生成图入库、封面文字安全区预览、内容/选题关联。
- 处理记录可追溯，原图不覆盖。

**技术建议：** FastAPI 接收上传元数据并创建处理任务；Python Worker 完成基础图片变换与漫图分格，把派生结果写入对象存储。图像处理不在 HTTP 请求事件循环里执行。

### 8.2 进阶能力与 Python

满足以下任一情形时，再启用独立 Python Vision Service：

- 需要 OCR 批量识字、文字区域定位或版面识别。
- 需要高质量背景移除、人像/物体分割、图像修复。
- 需要漫图自动分格、气泡识别、分镜语义分析。
- 需要 OpenCV/Pillow/PyTorch 生态或 GPU 推理。
- 图像处理需要独立扩缩容，CPU/GPU 资源与 API 服务明显不同。

工作流程为：FastAPI 将图像任务写入队列，Python Worker 执行并回传结果；Worker 不读取平台令牌或模型供应商 API Key。输入输出通过对象存储 key 传递，避免大文件跨服务反复传输。若未来视觉模型需独立 GPU 扩缩容，可将 Python Worker 单独部署，但仍沿用任务契约。

### 8.3 图像任务规格

`ImageJob`: `id, ownerUserId, assetId, operation, options, status, progress, outputAssetIds[], errorCode, createdAt, startedAt, completedAt`。

操作枚举：`thumbnail`, `resize`, `crop`, `compress`, `convert`, `watermark`，后续 `ocr`, `remove_background`, `comic_panel_detect`。

### 8.4 资源安全

- 限制输入大小、宽高和总像素，防止解压炸弹/内存耗尽。
- 只允许白名单格式，解码后重新编码；不信任扩展名或客户端 MIME。
- 存储私有，下载通过签名链接；检查 EXIF 并按隐私策略清理地理位置元数据。
- 任务设置超时、并发和队列配额；异常文件隔离，不返回栈信息。

---

## 9. 数据库与实体关系

### 9.1 关系概览

```
User → Workspace → Topic → ContentItem → ContentVersion → PlatformVariant
Workspace → CalendarEntry / WeekReview
ContentVersion ↔ Asset (ContentAsset)
PlatformVariant → PublishJob → PublishRecord → MetricSnapshot
User → ModelConnection → ModelCatalogEntry → AiGeneration
User → ConnectedAccount → PlatformSyncJob → MetricSnapshot
ImageAsset → ImageJob
User → CardTemplate
```

### 9.2 核心表字段

| 表 | 主要字段 |
|---|---|
| `users` | id, auth_subject, timezone, created_at |
| `workspaces` | id, owner_user_id, name, settings jsonb |
| `topics` | id, workspace_id, owner_user_id, title, audience_problem, promise, platforms, format, status, priority, source_url, tags, scheduled_at |
| `content_items` | id, workspace_id, owner_user_id, topic_id, title, type, status, current_version_id |
| `content_versions` | id, content_item_id, version_no, parent_version_id, source_type, model_id, prompt_template_version, title, body, summary |
| `platform_variants` | id, content_item_id, base_version_id, platform, revision_no, title, body, status |
| `calendar_entries` | id, workspace_id, topic_id, content_item_id, platform, planned_at, status |
| `assets` | id, owner_user_id, workspace_id, storage_key, mime_type, size_bytes, width, height, source_type, prompt, license_note, checksum |
| `content_assets` | content_version_id, asset_id, usage_role, sort_order |
| `model_connections` | id, owner_user_id, provider_type, display_name, base_url, credential_ciphertext, key_version, status |
| `model_catalog_entries` | id, connection_id, model_code, capabilities, enabled, supports_streaming, options jsonb |
| `ai_generations` | id, owner_user_id, task_type, model_id, status, request_summary, prompt_version, duration_ms, token_counts, error_code |
| `connected_accounts` | id, owner_user_id, platform, platform_subject, scopes, access_token_ciphertext, refresh_token_ciphertext, expires_at, status |
| `publish_jobs` | id, owner_user_id, platform_variant_id, connected_account_id, scheduled_at, idempotency_key, status, attempt_count, remote_content_id, error_code |
| `publish_records` | id, publish_job_id, platform, remote_content_id, published_at, permalink, result_status |
| `metric_snapshots` | id, connected_account_id, platform, remote_content_id, captured_at, normalized_metrics jsonb, raw_metrics jsonb, source |
| `platform_sync_jobs` | id, connected_account_id, sync_type, cursor, status, started_at, completed_at, error_code |
| `image_jobs` | id, owner_user_id, asset_id, operation, options jsonb, status, output_asset_ids, error_code |
| `agent_sessions` | id, owner_user_id, workspace_id, provider_id, status, created_at, updated_at |
| `agent_runs` | id, session_id, status, permission_snapshot jsonb, budget_limit, started_at, completed_at, error_code |
| `agent_events` | id, run_id, seq, event_type, safe_payload jsonb, created_at |
| `skill_registry_entries` | id, source, skill_key, version, manifest, checksum, trust_status, enabled_at |
| `workspace_skills` | workspace_id, skill_entry_id, enabled, granted_tool_ids, approval_policy, updated_at |
| `agent_tool_calls` | id, run_id, tool_id, input_summary, status, approval_id nullable, result_ref nullable, error_code, started_at, completed_at |
| `agent_approvals` | id, run_id, tool_call_id, requested_action, risk_level, status, decided_by, decided_at, expires_at |
| `agent_provider_connections` | id, owner_user_id, provider, credential_ciphertext, key_version, status, created_at |
| `week_reviews` | id, workspace_id, week_start, planned_count, published_count, learnings, blockers, next_week_focus |
| `card_templates` | id, owner_user_id, name, subtitle, config jsonb（底纹/边框/配色/padding 整份模板配置）, created_at, updated_at |

### 9.3 数据约束与索引

- 每条用户数据含 owner 或可通过工作区关系验证 owner。
- 发布幂等键唯一：`(owner_user_id, idempotency_key)`。
- 稿件版本唯一：`(content_item_id, version_no)`。
- 平台远端内容唯一：`(connected_account_id, remote_content_id)`，允许远端 ID 为空的待发布记录。
- 关键索引：选题状态/更新时间、日历时间、发布任务状态/计划时间、账号同步状态、指标内容 ID/采集时间。
- 原始平台响应仅在必要且脱敏后短期留存；长期使用归一化指标和平台口径信息。

---

## 10. API 设计

统一前缀 `/api/v1`；REST JSON 使用 camelCase；错误结构含 `code`, `message`, `requestId`, `retryable`。分页使用 cursor/limit。

### 10.1 内容管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/workspaces/current` | 当前用户默认工作区（含名称与 ID） |
| GET | `/dashboard` | 总览聚合数据 |
| GET | `/hot-topics` | 热点灵感列表（创作空间「灵感」面板数据源） |
| GET/POST | `/content` | 稿件列表/创建 |
| GET | `/content/:id` | 稿件详情 |
| POST | `/content/:id/versions` | 新建内容版本 |
| POST | `/content/:id/platform-variants` | 生成/创建平台版本 |
| GET/POST | `/calendar` | 日历查询/安排 |
| GET/POST | `/reviews` | 周复盘查询/创建 |

**未实现**：独立的 `GET /content/:id/versions`（版本随稿件详情返回）。

### 10.2 模型、Agent 与 AI

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/models/connections` | 模型连接列表/创建 |
| PATCH/DELETE | `/models/connections/:id` | 更新/删除连接；可改名称、供应商类型、Base URL、密钥、状态，密钥留空表示保留原值 |
| POST | `/models/connections/:id/test` | 测试连接 |
| GET/POST | `/models/catalog` | 查询/添加模型与能力 |
| PATCH/DELETE | `/models/catalog/:id` | 修改模型代号/显示名/能力、删除目录项 |
| POST | `/ai/text-generations` | 文本生成，SSE |
| GET | `/ai/generations` | 生成记录列表 |
| GET | `/ai/generations/:id` | 查询生成状态 |
| GET | `/agent/status` | Agent 运行状态：模式（ready/degraded/local）、当前模型、Skill 授权、进行中与待批准数、数据路径探测 |
| GET | `/agent/policy` | 工具风险分级（只读/可回滚写入/外部副作用/禁止），设置页直接渲染 |
| GET | `/agent/providers` | 查询可用 Agent Provider（脱敏） |
| GET/POST | `/agent/sessions` | Agent 会话列表/创建，绑定工作区与权限快照 |
| POST | `/agent/sessions/:id/messages` | 提交任务，返回规划/工具/结果事件 |
| GET | `/agent/runs/:id` | 查询运行状态和安全事件摘要 |
| GET | `/agent/runs/:id/events` | 运行事件流（按 seq 增量） |
| POST | `/agent/runs/:id/pause` | 暂停运行 |
| POST | `/agent/runs/:id/resume` | 继续运行，重新校验权限 |
| POST | `/agent/runs/:id/cancel` | 取消运行 |
| GET | `/agent/runs/:id/approvals` | 查询待审批动作 |
| POST | `/agent/approvals/:id/decision` | 用户批准或拒绝工具动作 |
| GET | `/skills` | 查询受信任 Skill 清单 |
| PATCH | `/skills/:id` | 启停 Skill；工作区级授权记录在 `workspace_skills` |

**尚未实现**：`/ai/image-generations` 及其 cancel、`/workspaces/:id/skills/:skillId`（当前用 `/skills/:id`）。

### 10.3 平台账号与发布

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/platforms/capabilities` | 当前版本平台能力目录（`confirmed=false` 的项前端禁用入口） |
| GET | `/platform-accounts` | 已绑定账号（脱敏） |
| POST | `/platform-accounts` | 手动登记账号（开发态） |
| POST | `/platform-accounts/:id/simulate-authorized` | 开发态模拟授权完成，替代 OAuth 回调 |
| POST | `/platform-accounts/:id/toggle-auto-publish` | 账号级自动发布开关（默认关闭） |
| DELETE | `/platform-accounts/:id` | 解绑账号 |
| POST | `/publishing/preview` | 发布前校验与预览 |
| POST | `/publishing/jobs` | 创建已审核发布任务（带幂等键） |
| GET | `/publishing/jobs` | 查询任务队列 |
| POST | `/publishing/jobs/:id/cancel` | 取消未执行任务 |
| POST | `/publishing/jobs/:id/retry` | 用户确认后重试 |
| POST | `/publishing/jobs/:id/record-manual-result` | 手动发布后回填结果 |

**未实现**：`GET /platforms/:platform/connect` 与 `/callback`（真实 OAuth 跳转），以及 `POST /platform-accounts/:id/test`（当前用 `simulate-authorized` 与 `keyMasked` 状态呈现）。

### 10.4 图像处理

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/assets` | 完成上传并写元数据 |
| GET | `/assets` | 素材列表 |
| GET | `/assets/:id/raw` | 读取原始文件（按 owner 校验） |
| DELETE | `/assets/:id` | 删除素材 |
| POST | `/image-jobs` | 创建处理任务 |
| GET | `/image-jobs/:id` | 查询处理状态 |

**未实现**：`GET /platform-accounts/:id/sync-status`、`POST /assets/upload-sessions`（当前为单请求直传 `POST /assets`）。

### 10.5 图文卡片

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/cards/paginate` | 入参 `markdown`、`presetId`、`customWidth` / `customHeight`、`templateId` 或 `customTemplate`、`meta`、`paginationMode`；返回 `renderSpec`、`template`、`meta`、`totalCards` 与卡片 AST |
| GET | `/cards/presets` | 平台画幅预设与 8 套内置模板，作为前后端共用契约 |
| GET | `/cards/templates` | 当前用户的自定义模板列表（按创建时间倒序） |
| POST | `/cards/templates` | 新建自定义模板：`name`、`subtitle`、`config` |
| PATCH | `/cards/templates/:id` | 改名或覆盖 `config` |
| DELETE | `/cards/templates/:id` | 删除自定义模板 |

关键语义：

- 模板读取严格按 `owner_user_id` 隔离：访问他人模板一律返回 404，不泄露是否存在。
- `customTemplate` 优先于 `templateId`；两者都没有时回落到第一套内置模板，保证接口永远能返回可用结果。
- `paginationMode` 只接受 `smart-auto` / `manual-hr` / `by-h2`，非法值由 Pydantic 直接 422；自定义宽高限制 240–1200 × 240–1600。
- `markdown` 上限 200000 字符，避免一次请求拖垮进程。
- 该接口只做分页与配置归一化，不落库、不排队、不生成图片，便于前端在编辑时高频调用。

---

## 11. 可靠性、任务队列和可观测性

### 11.1 队列划分

- `ai-image`：异步生图与供应商等待任务。
- `image-processing`：Python Worker 图像任务。
- `platform-publish`：按平台与账号分区限速。
- `platform-sync`：账号及内容指标同步。

文本 SSE 可同步建立连接，但生成记录与部分结果需持久化；服务端应为长任务建立可恢复的 generation ID。

**当前实现的队列现状与缺口：**

| 任务 | 当前执行方式 | 缺口 |
|---|---|---|
| 文本生成 | 请求内 SSE 流式返回 | 连接断开即中断，客户端的部分结果不落库（`ai_generations` 记录成功/失败，不保存中间 token） |
| 图像处理 | 请求内同步执行 Pillow | 大图会占用请求时长，未进 Worker |
| 发布 / 同步 | 同步处理 + 手动触发 | 无分区限速队列，平台侧限流保护未实现 |

### 11.2 重试策略

- 网络失败/5xx：指数退避 + 随机抖动，有最大尝试次数。
- 429：遵守 Retry-After，按平台账号维度限流。
- 401/403：暂停任务，要求重新授权或调整权限。
- 内容校验失败：不重试，显示具体字段问题。
- 发布超时：先查远端是否成功，再决定状态；未确认前不重发。
- 图片任务失败：原始素材保留，可由用户重新运行处理任务。

### 11.3 观测指标

`requestId`, `jobId`, `workspaceId`, `platform`, `modelId`, `status`, `durationMs`, `retryCount`, `errorCode`。禁止日志记录完整 API Key、平台令牌、完整正文或私有图片 URL。按平台/模型统计 P50/P95、成功率、限流率和人工介入率。

---

## 12. 安全、隐私与内容合规

### 12.1 SSRF 与凭据

- 用户配置的 Base URL 可能导致 SSRF；仅允许 HTTPS，拒绝 localhost、内网、链路本地、云元数据地址；校验 DNS 解析和重定向目标。
- API Key、OAuth access/refresh token 使用服务端密钥管理加密；日志与错误响应脱敏。
- 平台 OAuth 使用 state、nonce、PKCE（平台支持时），回调严格校验。
- Cookie 会话启用 Secure、HttpOnly、SameSite；写请求加 CSRF 防护。

### 12.2 自动发布保护

- 默认关闭自动发布；用户逐账号授权并单独开启。
- 首期默认发布前人工审核；自动定时发布需单独开关和可撤销队列。
- 发布前校验文件大小、格式、宽高、平台文本限制和权限范围。
- 对敏感内容、疑似侵权图片或不可核实事实提示人工检查。
- 任何未完成用户授权的平台接口不得由备用方式绕过。

### 12.3 Agent 与 Skill 安全边界

- Skill 包在启用前校验来源、版本、校验值和权限清单；来源不明或权限声明缺失的 Skill 不允许运行。
- Agent Worker 按工作区和会话隔离；默认无任意文件系统、shell、数据库直连、外网访问权限。
- Agent 只能调用工具网关暴露且对该用户授权的工具；每次运行保存工具权限快照，恢复运行时重新鉴权。
- 写操作通过业务 API，并校验资源 owner/workspace；禁止让模型生成 SQL、shell 命令后直接执行。
- 用户上传文档、网页和 MCP 返回内容均视为不可信数据；不得允许其覆盖系统规则或扩大工具权限。敏感动作前再次校验服务端策略。
- 每次运行限制最大轮数、执行时长、并发工具数、上下文大小和模型成本预算；触顶后暂停并提示，不自动继续消耗。
- 审批请求绑定具体工具、参数摘要、目标平台/账号、预计副作用和过期时间；审批后参数不可被模型或客户端悄悄修改。参数变化需重新审批。
- 发布动作继续遵循逐篇人工确认；Agent 只能创建发布预览/待审核任务，不能代替人确认。
- Agent 运行事件做脱敏审计；不得记录完整密钥、完整 OAuth token 或非必要的稿件正文。
- Claude Agent SDK 用于产品服务时使用官方支持的 API Key/企业授权方式；不把 Claude.ai/Max 订阅登录转售或作为第三方产品的默认认证方式，并遵守商业条款与品牌规范。

**当前实现中的强制点（可核对到代码）：**

| 约束 | 落点 |
|---|---|
| 工具风险分级与禁止级拒绝 | `services/agent_policy.py` 定义 `ToolRisk`，`services/agent_runtime.py` 的 `assert_tool_callable` 在工具层拦截；`run_shell` / `raw_sql` / `read_local_files` 为 `forbidden`，永不执行 |
| 外部副作用逐次批准 | `publish_content` / `unbind_platform_account` / `update_model_credential` 等标记为 `external_side_effect`，运行时生成审批记录，未批准不执行 |
| 权限真相只在服务端 | `GET /agent/policy` 返回分组成员与 `requiresApproval`，设置页直接渲染；前端不再复写一份文案 |
| 路径可见即可审计 | `GET /agent/status` 返回数据库/素材/配置/Skill 注册表的绝对路径、`exists`、`writable`，向用户明示数据落在哪 |
| 无产出不伪造 | 生成任务空文本 → `failed` + `generation_unavailable`；配图无供应商 → `skipped` + `image_provider_unavailable` |

### 12.4 数据隐私

- 模型请求前告知供应商和发送内容范围；支持最小化上下文。
- 平台数据只存储用户授权范围内的内容；提供解绑、导出、删除机制。
- 数据保留期按业务需要设定；日志不保留稿件全文。
- 账号解绑停止后续抓取和发布任务，删除凭据并保留必要的发布历史元数据（用户可请求清除）。

---

## 13. 部署拓扑与运维

### 13.1 环境

`local → staging → production`，各环境数据库、Redis、对象存储、模型凭据和平台应用隔离。禁止将生产授权凭据复制到测试环境。

### 13.2 部署组件

```
Vue 静态应用 → CDN/托管站点
FastAPI → 容器/托管运行环境
Python Workers → 独立 Worker 实例（CPU/GPU 队列可分开）
PostgreSQL → 托管关系数据库
Redis → 托管 Redis（任务队列/限流）
Object Storage → 私有 Bucket，签名 URL
```

### 13.3 备份恢复

- PostgreSQL 定时备份并验证恢复流程。
- 对象存储启用版本化/生命周期策略；原始图片与派生图片分别记录。
- 用户支持导出稿件、选题、发布记录和配置元数据；绝不把密钥写入导出包。
- localStorage 仅用作前端临时偏好，不作为生产数据主存储。

---

## 14. 测试与验收标准

### 14.1 测试类型

- 单元：平台能力判断、发布状态机、幂等策略、AI 参数映射、图像尺寸处理、Agent 风险分级和 Skill 权限合并。
- 契约：每个 Provider Adapter / Platform Connector / Agent Tool 的协议映射和错误分类。
- 集成：OAuth 回调、密钥加解密、数据库权限过滤、对象存储、队列重试、Agent 会话恢复和审批回调。
- E2E：创建稿件→生成平台稿→上传/生成图片→预览审核→发布模拟→同步模拟指标→复盘；另测“选题→Agent 规划→调用 Skill 生成草稿→人工审核”。
- 安全：越权、SSRF、XSS、凭据泄漏、重复发布、授权撤销、恶意图片和资源耗尽。
- 沙箱验证：平台仅在官方沙箱/测试账号允许时执行；真实账号发布需用户显式操作。

### 14.2 首期验收

1. 多条文本/图片模型连接可添加、测试、停用、删除；凭据不可被客户端读回。
2. AI 生成可流式展示/中断，失败时保留用户输入与已生成部分。
3. 图片任务完成后对象存储可访问，原图不被覆盖，元数据可查。
4. 选题、稿件版本、日历、素材、发布任务、指标快照跨会话保存且用户隔离。
5. 发布任务在平台能力未确认/未授权时无法提交；启用平台自动发布需逐账号授权与人工审核。
6. 发布超时不会盲目重发；进入核实状态并可查询/人工处理。
7. 数据采集结果带平台来源和采集时间；无数据与零值严格区分。
8. 平台不支持某项接口时显示降级流程，不伪造成功或指标。
9. 日志、前端响应、导出文件和错误上报均不包含模型 Key 或平台令牌。
10. 浅色/暗色/跟随系统三种主题可用，用户选择跨会话保存；所有状态色和图表在两种主题下均可辨认。
11. 320px 窄屏、手机、平板和桌面下核心流程可用；无整页横向溢出，触控目标不小于 44×44 CSS px。
12. 键盘导航、清晰焦点环、表单错误关联和屏幕阅读器名称通过人工检查；普通文字对比度达到 WCAG AA。
13. Agent 只能运行已启用 Skill 和授权工具；高风险工具会展示具体参数并要求人工确认，拒绝后任务停止且无外部副作用。
14. Agent 运行可以暂停、恢复和取消，断线后能通过运行 ID 查看事件和结果；单次运行超过轮数/时长/预算会自动停止。
15. 生成任务的产出必须来自真实调用：模型未返回内容时明确失败并带 `generation_unavailable`，不得写入占位文本；缺少供应商时明确跳过并带原因码。
16. 设置页展示的 Agent 权限分级与数据路径必须来自服务端探测（`/agent/status`、`/agent/policy`），前端不得另写一份可能漂移的文案；禁止级工具必须显示为「不提供」，不能显示成「需批准」。
17. 模型连接的编辑不得导致密钥丢失：不传密钥字段表示保留，显式清空才清除；改名称、改类型后 `keyMasked` 应保持不变。
18. 图文卡片：同一段 Markdown 在前端引擎与 `/cards/paginate` 下必须产出相同的卡片数量与块结构；四种画幅与自定义宽高导出尺寸精确为 `宽×3` / `高×3`；自定义模板只能被创建者看到、修改和删除；导出 PNG 不含空白卡片或缺失底纹/边框。

### 14.3 当前实现状态

- 已自动化覆盖：13–18，其中 18 覆盖分页模式、尺寸计算与模板归属隔离。
- 图文卡片补充：导出 PNG 为 3x 精确尺寸、模板切换与控制台零报错已由浏览器实测确认；前后端 AST 一致性依赖两份同构实现，尚未加跨语言对比用例。
- 待真实环境验证：1、3、4、5、6、7、9 依赖真实平台授权与对象存储；目前平台相关能力以 `confirmed=false` 呈现，前端入口禁用。
- 未覆盖：12（屏幕阅读器人工检查）、移动端完整交互回归。

---

## 15. 分阶段交付计划

### Phase 0：平台与技术可行性验证

- 对五个平台分别检查官方开放平台文档、账号类型、发布权限、数据 scope、申请和审核要求、限流与费用。
- 取得至少一个目标平台测试应用/测试账号，验证 OAuth、发布和数据读取的实际流程。
- 对图像处理目标明确：首期只做基础处理，还是包含 OCR/抠图/漫图切格。
- 验证部署环境、密钥管理、数据库、对象存储和队列的可用性。

**出口条件：** 形成平台能力矩阵（接口证据、权限范围、不可用项）和技术选型决策记录。未验证的平台保持人工发布/手动记录。

### Phase 1：创作工作台与资产管理

登录、工作区、选题、日历、稿件版本、素材库、手动发布记录、数据手工录入和导出。

### Phase 2：多模型 AI、Agent 与图像基础处理

多供应商连接、服务端密钥加密、AI 文本流式生成、工作台 Agent Runtime、受控 Skill/MCP 工具、Agent 会话与审批、AI 生图任务、Python 图像 Worker、对象存储、模型调用记录。首期 Agent 只操作工作台内的受控工具，不直接操作本机文件和任意命令。

### Phase 3：首批平台 Connector

只接入 Phase 0 已验证且取得授权的一个或两个平台；上线发布前审核、幂等、任务队列、指标同步和授权过期处理。以平台为单位逐步放量，不五平台一起上线。

### Phase 4：扩展平台与视觉处理

逐平台新增 Connector；按 OCR/抠图/漫图识别需要扩展 Python Worker，若出现独立 GPU 扩缩容需求再拆视觉部署单元。同步扩展质量、成本和资源监控。

---

## 16. 仓库结构建议

设计目标结构与当前实际实现的对应关系：

```text
creative-workbench/                # 当前实际目录名 hanyue-workspace/
├── apps/
│   ├── web/                       # Vue 3 + Vite（纯 JS，无 TypeScript）
│   │   └── src/                   # views/（8 个页面）+ features/cards/ + stores/ + api/ + styles/
│   ├── api/                       # FastAPI 模块化单体
│   │   ├── app/modules/           # auth/topics/content/models_module/agent/cards/...
│   │   ├── app/services/          # ai_gateway/agent_policy/agent_runtime/agent_status
│   │   │                          # publish_state/image_service/card_paginator
│   │   └── tests/                 # pytest（含 test_cards.py）
│   └── worker/                    # Python Worker（图像任务契约，尚未接入队列）
├── packages/                      # 尚未拆分（契约类型目前由 Pydantic CamelModel 直接产出）
├── migrations/                    # 尚未引入 Alembic；当前用 core/db.py 的加列迁移
├── infra/                         # docker-compose.yml
└── docs/                          # design.md、plan.md
```

差异说明：`packages/contracts` 与 `packages/platform-connectors` 未拆出；`migrations/` 未引入 Alembic，SQLite 阶段用 `_apply_additive_migrations()` 兜底，切 PostgreSQL 前必须补 Alembic。

---

## 17. 风险与待决事项

| 项目 | 当前状态 | 处理方式 |
|---|---|---|
| 首期平台范围 | 用户已选择先对五个平台并行调研；连接和发布按获得的官方权限逐个平台实现，不承诺同批上线 | 做官方能力矩阵，按资质/价值排序后分批接入 |
| 自动发布审核 | 每篇发布前人工确认（已确认） | 即使后续启用定时发布，也保留可撤销队列和明确的账号级设置 |
| 自动发布接口可用性 | 未逐个官方核验 | Phase 0 逐平台查文档并以测试授权验证；不得据此宣称全部可自动发布 |
| 平台指标 API 范围 | 未逐个核验 | 只展示授权且实际返回的指标，支持手动补记 |
| 内容自动审核责任 | 首期由人作最终发布决定 | AI 可提示风险，不能替代发布人责任 |
| 自带模型 Key 的合规和费用 | 需确认 | 用户与模型供应商承担各自合同/费用；密钥服务端加密、按连接计调用 |
| 图像处理需求深度 | 首期基础裁剪压缩 + 漫图分格 | 漫图先裁切/预览/人工确认；自动识别格线、气泡和语义作为 Python 视觉服务候选 |
| 是否多用户 SaaS | 本版按多用户隔离设计 | 可以先限制为个人工作区，但数据模型不使用全局共享数据 |
| 自动发布的地区/账号政策 | 需在接入时核实 | 每个平台、账号类型单独保留验证记录和发布日期 |
| 图像生成供应商缺失 | 未接入任何文生图服务 | 图像生成任务明确失败并带 `image_provider_unavailable`，宁可失败也不生成假图；接入后改为真实调用 |
| 模型默认回退 | 无连接时 `ai_gateway` 回退到本地确定性生成器（`local-dev`） | 界面必须通过 `/agent/status` 的 `mode` 明示当前是本地演练，避免误认为在用真实模型 |
| Agent 步骤规划 | `plan_steps` 为确定性规则 | 刻意保留：拆解规则稳定可测；工具调用侧已是真实 LLM。若要 LLM 规划应作为可选开关而非替换 |
| 图文卡片导出 | 依赖浏览器 `html-to-image` 与系统字体 | 导出前等待 `document.fonts.ready`；自定义图片统一转 dataURL 规避跨域；高度为估算值，超长内容用溢出提示引导改用智能分页 |

**推荐实施顺序：** 先完成 Phase 0 平台能力核验，再做基础资产管理和多模型 AI；平台发布与数据同步按验证过的 Connector 逐个上线。采用 Vue 3 + FastAPI + Python Worker；首期图像操作和漫图基础分格均由 Python Worker 执行。
