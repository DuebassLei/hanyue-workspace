# 成稿台：段落级编辑编排（移植说明）

> 来源：`dsh-wechat-studio`（DeepSeek Harness 插件「公众号成稿台」）的编辑编排模块。
> 本文记录搬了什么、怎么落地、与原版哪里不同，以及后续接线点。

---

## 为什么不是「复制文件」

原版是 Node 宿主插件：`src/pipeline.js` 跑在 Host 里、状态存分片 JSON、模型走插件自带的 provider 选择。
本项目是 FastAPI + Vue，模型统一走 `ai_gateway`、数据落 PostgreSQL、权限走 RBAC。
所以移植的是**行为契约**，不是源码：

| 原版 | 本项目 | 说明 |
| --- | --- | --- |
| `src/article.js` | `services/article_blocks.py` | 纯函数：分段、块类型、`applyOps`、快照、版本 diff、修复分组 |
| `src/zhuque.js` | `services/article_quality.py` | 朱雀 classify 调用 + 片段按文字对回段落 |
| `src/report.js` | 同上 | 报告文字解析、AIGC 值分档、标红落盘；PDF 抽取为可选依赖 |
| `src/pipeline.js` | `services/studio_pipeline.py` | 写正文/出标题/对话改稿/单段修复/一键处理/横幅 |
| `src/host.js` 的 article 路由 | `modules/studio.py` | 28 条接口，按 owner 隔离 |
| `src/banner.js` | `studio_pipeline` 内的横幅规格 | 规格归一与校验复用 `wechat_layout_engine.check_banner` |
| 分片 JSON store | `studio_articles` / `article_blocks` / `article_revisions` | 段落一行一段，id 稳定 |
| `client/app.jsx` 稿纸 | `views/PaperView.vue` | 段号稿纸、标红段、对话栏、标题候选、横幅 |

---

## 数据模型

```
studio_articles   稿件头：选题/素材/标题/候选/摘要/横幅/质检轮次/对话/排期/归档
article_blocks    段落：ordinal + text + kind + check(JSON) + prev_text
article_revisions 快照：改动前的 title/titles/blocks，撤销与版本对比都读它
```

三条硬约束：

1. **段落 id 稳定**。手动改稿把一段拆成两段时，第一段沿用原 id，新增段才发新 id；
   版本对比按 id 对齐，所以前面增删不会让后面全部显示成「改写」。
2. **快照最多 20 条**，超出丢最旧；一次改动最终没变内容时，刚存的快照会被删掉，不留空条目。
3. **段号一次性解析**。模型给的一批操作里，段号都指向「修改前」的位置；先解析成段落再执行，
   避免批内插入/删除导致后续段号漂移。

---

## 接口一览（`/api/v1/studio`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/state` | 稿件列表 + 质检凭据状态 |
| GET/POST | `/settings` | 朱雀 Key / endpoint（只允许 https） |
| POST | `/articles/new` | 新建：给 Markdown 就导入；只给选题素材则让模型写 |
| GET | `/articles/{id}` | 整篇快照（含正在跑的任务） |
| POST | `/articles/{id}/chat` | 对话改稿，模型只回白名单 ops |
| POST | `/articles/{id}/titles` | 出 5 个候选标题 + 摘要 + 主推理由 |
| POST | `/articles/{id}/title/pick` | 选定标题（记快照） |
| PATCH | `/articles/{id}/blocks/{bid}` | 手改一段；支持 `expectedText` 乐观锁；改出空行会拆段 |
| POST | `/articles/{id}/blocks/{bid}/fix` | 单段按朱雀结论改写，原文留 `prev_text` |
| POST | `/articles/{id}/blocks/{bid}/keep` | 接受现状，不再提示 |
| POST | `/articles/{id}/blocks/{bid}/revert` | 退回 `prev_text` |
| POST | `/articles/{id}/fix-all` | 一键处理：按相邻分组、3 组并发、可停止 |
| POST | `/articles/{id}/job/stop` | 让一键处理在当前组结束后停下 |
| POST | `/articles/{id}/undo` | 撤销一步 |
| GET | `/articles/{id}/revisions` | 逐次改动的 diff（按段落 id 对齐） |
| POST | `/articles/{id}/zhuque/detect` | 配了 Key 走朱雀；没配走本地规则并标注来源 |
| POST | `/articles/{id}/zhuque/report` | 报告回填：PDF 原始字节或 `{"text": "..."}` |
| POST | `/articles/{id}/banner` | 生成开头横幅规格并自查，问题让模型改一次 |
| POST | `/articles/{id}/schedule` | 记计划发布时间（发布队列仍归 publishing） |
| POST | `/articles/{id}/export` | 落成 ContentItem + ContentVersion，接文章管理与排版 |

所有返回都是整篇快照，页面按快照整体替换，不做局部合并——后台任务改稿时不会拼出中间态。

---

## 一键处理的规则

照原版 `zhuque` skill 的做法，三类标红段跳过：

- 已经改过两轮（`fixes >= 2`）：留给作者自己重说；
- 改完反而更重（`worse`）：建议退回上一版；
- PDF 报告里 0.5–0.6 的噪声带：不值得动。

组内相邻的段一起改（朱雀常把几段短段落判成一个片段，并列/对仗问题也跨段），
每组最多 5 段、约 900 字；含 AI 特征的组先改。单组失败只计数，不影响其他组。

---

## 质检的诚实边界

- 配了朱雀 Key：`source=api`，分数是朱雀置信度，界面显示「朱雀 API」。
- 上传报告：`source=pdf`，分数是报告里的 AIGC 值，界面显示「朱雀报告」。
- 没配 Key：`source=local`，走 `article_workflow.check_ai_tone` 的词表 + 结构规则打分，
  界面必须显示「本地规则（未接朱雀）」，且 `zhuque.note` 写明「不能与朱雀结论直接比较」。

本地打分只用来排序和提示，不冒充检测结论。

---

## 段落编辑：点开就改，改完才算

改一段的默认路径只有一条（与参考项目一致，细节见 `docs/paper-editing-contract.md`）：

1. **点正文任意位置**进入编辑（段号上也会露出铅笔）；后台任务在跑时整段锁住，
   不给「点进去写完才被 409 拒掉」的路径。
2. **显式保存**：`保存` / `保存并改下一段` / `删除` / `取消`；
   快捷键 `⌘/Ctrl+Enter` 保存、`⌘/Ctrl+Shift+Enter` 保存并改下一段、`Esc` 逐层退出。
3. **草稿落本机**：输入即写 `localStorage`，切稿、刷新都不丢；工具条显示「N 段未保存」，
   段落上挂角标，点开能直接跳回那一段。草稿不落库——半成品不该被后台质检当成正文。
4. **冲突不吞稿**：别处改过这一段时后端返回 409，编辑器不关，草稿保留，给「复制修改」。
5. **清空即删除**：清空后主按钮变「删除段落」，但快捷键不会删——删除必须是明确动作。
   删错了用顶部「撤销一步」恢复。
6. **批注有主见**：一句结论 + 一条建议（改重了→退回上一版；改过两轮→自己重说；
   0.5–0.6 噪声带→可以不改），有历史时给「看改动」逐字 diff。
7. **格式化与插入成套**：工具条 `B / 色 / U` 对应 `**` / `==` / `++`（引擎认的内联记号）；
   「插入」菜单三组十四项，全是排版引擎能渲染的卡片与围栏——引用卡、金句卡、小技巧、注意、
   说明、省流版、小标题、终端 ×2、长提示词 ×2、图片、长图、分隔线。
   图片 / 长图 / 长提示词先弹窗收内容（支持本地选图、截屏直接粘贴），其余点一下出占位并选中占位字。
   菜单项刻意不放引擎不认识的类型：`> [!GOLDEN]` 这类标记会在正文里印出英文标签。

---

## 排版内置：一个菜单完成全流程

成稿台中间栏有「段号稿纸 / 排版预览」两个视图，切到排版预览时右栏自动跟到「排版」页签，
**不再跳去独立的排版工作台**——排版要跟着稿件走，跳走再回来容易丢上下文。

```
稿纸（逐段改 + 质检） → 排版预览（调样式，所见即公众号） → 素材（封面/插图/SVG 卡） → 发布（排期/入队/复盘）
```

- 渲染接口：`POST /studio/articles/{id}/render`，只在预览组件里调一次，选项变化 400ms 防抖。
- 选项：`GET /studio/articles/{id}/render/options` 给目录 + 默认值；「保存为默认」写回 `layout_options`。
- 封面/插图都是**入队 + 轮询**：`generate` 只建生图任务并回 `running`，`sync` 才把 assetId 写回并插图；
  未配置生图供应商时明确 400，不会留一个永远 running 的假任务。
- 发布：`publish` 先复用 `studio.export_to_content` 落成正式稿件，再按平台建 `publish_jobs`；
  `dryRun=true` 只存稿不入队。平台码用 `wechat_official`（也接受 `wechat-mp` 别名）。
- 复盘：`GET /studio/articles/{id}/review` 按 `contentItemId` 反查任务，前端表格展示各平台状态。

### 已接能力

| 能力 | 落点 |
| --- | --- |
| 封面生成 | `POST /studio/articles/{id}/cover/generate` + `cover/sync` + `cover/pick`（走 `ai_images` 队列） |
| 正文插图 | `POST .../inline/generate` + `inline/sync` + `DELETE .../inline/{id}`，成功后在该段后插 `media` 块 |
| SVG 动画卡 | `POST .../blocks/{blockId}/svg-card`，`wechat_svg_cards.draft_card` 产出 fence 作为 `code` 块 |
| 排期发布 | `POST .../publish`（含 `scheduledAt`）、`GET .../review`；排期仍记在 `scheduled_for` |
| 排版渲染 | `POST .../render`、`GET .../render/options` |

### 仍然存在的边界

- 生图依赖供应商配置；未配时只能选已有素材，界面会说明原因。
- 发布依赖已连接的平台账号（`ConnectedAccount`）；未绑定时报明确错误，不假装成功。
- PDF 报告解析需要 `pypdf`，当前环境未安装，接口会 422 并提示粘贴文字。

## 回归

- `tests/test_studio_blocks.py`：分段、块类型、`applyOps` 段号语义、修复分组、版本 diff、撤销。
- `tests/test_studio_pipeline.py`：建稿、手改段落（含乐观锁 409、清空删段、任务期 409）、对话改稿、
  本地质检、一键处理、报告回填、导出、排期校验。
- `tests/test_studio_layout.py`：渲染返回 html/stats、标题回退、横幅前置、目录结构、owner 隔离。
- `tests/test_studio_media.py`：无供应商明确报错、封面轮询、插图插块与幂等、SVG 卡插入、越权 404。
- `tests/test_studio_publish.py`：dryRun 不入队、发布建任务、非法平台/过去时间 400、复盘空数据不报错。
- 全量：`pytest -q` 1077 passed；前端 `npm run build` 通过、`node --test tests/*.test.mjs` 25 passed。
