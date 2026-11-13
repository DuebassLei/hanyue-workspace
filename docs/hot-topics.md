# 热点选题

> 页面：`/hot-topics`　接口：`/api/v1/hot-topics/*`　代码：`modules/hot_topics.py`、`services/hot_topics.py`、`services/hot_sources.py`、`services/hot_ranking.py`

## 1. 它解决什么

「每日热榜」回答**今天大家搜什么**，一条一条列出来；「热点选题」回答**我该写哪一个**。

同一件事会同时出现在微博、知乎、抖音，标题还各不相同。直接看榜单，人要在几十条重复信息里自己判断：哪条是多平台共振的真热点、哪条已经连续在榜三天、哪条一看就不能碰。这个页面把这段判断做成可复算的分数，并**解释每一分从哪来**。

边界：

- 热点**直接成稿**：点「写这篇」创建一篇空白 `content_item` 并跳进创作空间，不恢复已下线的选题库，不生成占位正文。
- 只做「选题」，不做「自动写作」：稿件内容仍由创作空间的 AI 写作完成。
- 分数只由后端算，前端只排序/筛选/展示；前端不重算分数，避免两套口径。

## 2. 链路

```
7 路采集 → 逐条归一/去重 → 同一事件合并（聚簇）→ 100 分评分 → 列表/详情 → 一键成稿
hot_sources.collect   hot_ranking.cluster_items   hot_ranking.score_cluster   content_items
```

一次 `POST /hot-topics/refresh` 完成前三步并落库；后续读取只查库，不再访问外网。

### 2.1 采集（`hot_sources.py`）

| 平台 | 方式 | 原始链接 |
|---|---|---|
| 今日头条 | 榜单 JSON 直取 | 有 |
| 百度 | 榜单 JSON 直取 | 有 |
| 抖音 | 热搜 JSON 直取 | 有 |
| 微博 | 60s 聚合 | 无（留空，不用搜索页冒充） |
| 知乎 | 60s 聚合 | 无 |
| AI·热点 | AIHOT JSON（多来源报道数当热度） | 有 |
| AI·资讯 | AIHOT JSON（24h 资讯流） | 有 |

约束：

- 所有出站请求走 `core/ssrf.safe_request`（仅 HTTPS、端口白名单、不跟随重定向、IP 固定）。3xx 按失败处理，避免把重定向页当 JSON 解析成空榜单。
- **单源失败只进 `notes`**，其余源照常返回；7 路全失败才抛 `all_sources_failed`（502，`retryable=true`），不返回「0 个事件」的假成功。
- 搜索页 URL（`/s?word=`、`/search/` 等）按「拿不到原文」处理，留空。

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `HOT_TOPICS_PLATFORMS` | 空（全部 7 路） | 逗号分隔，只跑指定平台，如 `微博,知乎` |
| `HOT_TOPICS_TIMEOUT` | `12` | 单次请求超时（秒，最小 3） |
| `HOT_TOPICS_REFRESH_COOLDOWN` | `15` | 刷新冷却（秒），0 表示关闭 |

### 2.2 合并同一事件（`hot_ranking.cluster_items`）

1. `normalize_title`：全半角归一、去「热搜/独家/最新」类前缀、去标点、保留中英文数字，`X方回应` 归一为 `X回应`。
2. 两两算相似度：`0.55 × LCS-Dice + 0.25 × 字二元组 Jaccard + 0.20 × 实体证据`。当天稀有片段（台风名、品牌专名）共现时**加 0.10**，但只加分、不单独决定合并——否则「董事会」「背后的」这种当天只出现两三次的普通词组会把整片新闻连成一簇。数量单位（亿美元、万元、个百分点……）从证据里剔除：两条都写「1 亿美元」不代表同一件事。
3. **硬否决**优先于相似度：反义冲突（涨/跌、承认/否认……）、命中不同已知实体（人名/机构）→ 直接判定不是同一事件。通用词（回应、官宣、通报……）不参与证据加分；纯英文片段至少 4 个字母才算实体证据（`gen`/`ace` 不算）。
4. 并查集合并，`MERGE_THRESHOLD = 0.56`。代表标题取信息量最大的一条，不做拼接。

簇的身份是 `fingerprint(归一标题)`（前 120 字符）。同一事件第二天再上榜会被复用为同一簇，保留 `content_item_id` 与人工状态。

### 2.3 评分（`score_cluster`）

满分 100 = 共振 30 + 势能 25 + 时效 20 + 赛道 15 + 可写性 10 − 风险（0~30）。

| 维度 | 上限 | 依据 |
|---|---|---|
| 共振 | 30 | `8 × 平台数`，≥3 个平台再加 6 |
| 势能 | 25 | 最高名次分 + 名次上升分（≤6）+ 同平台归一热度分（≤7） |
| 时效 | 20 | 首见时间新鲜度 + 连续在榜天数 |
| 赛道 | 15 | 标题命中的赛道词与赛道基分（社会/财经/科技/娱乐/体育/汽车/健康…） |
| 可写性 | 10 | 有明确主体、事件、冲突/结论等可展开证据 |
| 风险 | −30~0 | 风险词（谣言、去世、未成年、医疗断言、政策敏感等）逐项扣分；**簇内任一条命中都算**——宁可高估风险，也不把写不了的题材排到最前 |

档位阈值只写在 `score_grade()` 一处：≥70 强烈推荐、≥55 可做、≥40 备用、其余不建议。

时间基准是 `captured_at`（本轮采集时间），不是 `now()`：同一批数据任何时候重算，分数必须一致。

`score_detail` 保存全部中间量（`resonance`/`momentum`/`rank_points`/`rise_points`/`hot_points`/`hot_evidence`/`fresh_points`/`days_points`/`track_hits`/`writability_evidence`/`risk_flags`/`risk_penalty`/`total`/`evidence`），界面上的分数条与悬浮说明直接读它，不二次推断。

### 2.4 数据不足时如实显示

一次刷新只有一个时间点，所以：

| 采样轮数（同日不同 `captured_at`） | 不可计算的维度 |
|---|---|
| 1 轮 | 名次升降、日内新鲜度 |
| 2 轮 | 日内新鲜度（首见可能落在两轮之间） |
| ≥3 轮 | 无（跨平台热度始终只做同平台归一） |

`GET /hot-topics/degraded` 返回 `[{dim, reason}]`。界面用**斜纹条**表示整维不可信，用「升降未算」这类注记表示子项缺失——不显示 0 冒充「算出来是 0」。

### 2.5 幂等

- 条目身份：`platform + "\x1f" + title_norm`（`item_uid` 唯一）。
- 名次采样：`(item_id, captured_at)` 唯一，重复刷新同一时刻不会重复插入。
- 簇：按 `fingerprint` 复用；用户设的 `ignored` / `watching` / `generated` 在刷新后保留。
- 成员归属**每轮重建**：刷新时先清空当天条目的 `cluster_id` 再按本轮分组写回。掉榜的条目会退出事件卡，簇的成员数只反映本轮，不会跨轮累积；同一轮里一个已有簇只能被一个分组认领（避免多个分组把成员堆进同一张卡片）。
- 成稿：同一簇重复点「写这篇」返回同一篇稿件。

## 3. 接口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/hot-topics/sources` | 公开 | 采集源清单与启用状态 |
| POST | `/hot-topics/refresh` | 登录 | 采集 → 聚簇 → 评分；body `{date?}`；冷却内返回 429 `too_frequent` |
| GET | `/hot-topics/clusters` | 登录 | 列表。`date` / `minScore` / `track` / `platform` / `status` / `q` / `sort=score\|time\|resonance` / `limit` |
| GET | `/hot-topics/clusters/{id}` | 登录 | 详情：成员条目、名次采样、关联稿件标题 |
| PATCH | `/hot-topics/clusters/{id}` | 登录 | `{status}`：`new` / `watching` / `ignored`；`generated` 只能由成稿写入 |
| POST | `/hot-topics/clusters/{id}/draft` | 登录 | 热点 → 稿件（201，返回 `ContentOut`） |
| GET | `/hot-topics/summary` | 登录 | 概要素材数/事件数/≥70 分数量/已出稿数/赛道分布 |
| GET | `/hot-topics/degraded` | 登录 | 本轮数据不足的维度与原因 |

列表默认按分数降序，不按 id：热点的意义就是最值得写的在最前。`platform` 过滤在 Python 侧完成——JSON 列的 LIKE 在 SQLite/PostgreSQL 行为不一致，当天簇数量有限，多扫一遍比写方言分支便宜。

### 兼容旧契约

创作空间「灵感」面板仍调 `GET /api/v1/hot-topics`（`modules/ai_content.py`）。该端点现在优先读当日已评分簇（`title` 为事件标题，`hot` 为 `"82 分 · 强烈推荐"`），库中一条簇都没有时才回落到旧的单源 `HOT_TOPICS_API_URL`。旧行为未被破坏。

## 4. 数据表

| 表 | 用途 | 关键约束 |
|---|---|---|
| `hot_items` | 各平台原始条目 | `item_uid` 唯一；`(day, platform, rank)` 索引 |
| `hot_rank_history` | 名次采样，用于升降/新鲜度 | `(item_id, captured_at)` 唯一 |
| `hot_clusters` | 合并后的事件与评分 | `fingerprint` 复用；`(last_seen_day, score DESC, id)` 索引 |

迁移走 `core/db.py` 的 `_ADDED_COLUMNS` / `_INDEXES` 机制，`init_db()` 幂等补列补索引；旧库无需手工迁移。

## 5. 页面

`apps/web/src/views/HotTopicsView.vue` + `features/hot/HotClusterCard.vue` + `features/hot/HotScoreBars.vue`

- 顶部概要：素材 → 事件 → 值得写 → 已出稿，赛道分布条，采集源与数据不足提示。
- 筛选：日期 / 赛道 / 平台 / 最低分 / 排序（价值、最新、共振）/ 只看未出稿 / 标题搜索（250ms 防抖）。
- 事件卡：分数与档位、平台与风险标签、赛道/首见/连续天数/最高名次/原文数、五维分数条（悬浮看证据）。
- 「展开原文」才请求详情（含各平台原始条目与链接）。
- 「写这篇」→ `POST .../draft` → 跳转 `/studio?id=<contentItemId>`；已出稿的卡片显示「打开已生成的稿件」。
- 「忽略 / 恢复」写回服务端状态，刷新后保留。

## 6. 测试

`apps/api/tests/test_hot_topics.py`（12 项）：

- 纯函数：标题归一、同事件合并与反义/实体否决、确定性评分（同一 `captured_at` 结果一致）、风险词逐条累计、聚簇分组。
- 路由：重复刷新幂等、列表筛选、详情、状态流转、成稿幂等与跳转依据、概要、数据不足、全源失败 502、源清单。

测试用 `monkeypatch` 替换 `hot_sources.collect`，**不访问外网**；`now_stamp` 固定为常量，保证评分可复现。

## 7. 与参考实现（DSH 工作台）的对应

| DSH | 本项目 |
|---|---|
| `tools/gen-materials.py` 多源采集 | `services/hot_sources.py`（改为统一走 SSRF） |
| `tools/tracks.py` 赛道词表与打分 | `services/hot_ranking.py` |
| `tools/hot-topics.py` 聚簇/评分/报告 | `services/hot_ranking.py` + `services/hot_topics.py` |
| `lib/client.template.js` 的 `HotTopicRadar` | `HotTopicsView.vue` + `HotClusterCard.vue` + `HotScoreBars.vue` |

算法（阈值、权重、否决顺序、代表标题选择）按参考实现移植，未重新发明；差异只有三处：SSRF 硬约束、落库幂等、直接成稿。
