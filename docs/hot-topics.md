# 热点选题

> 页面：`/hot-topics`　接口：`/api/v1/hot-topics/*`　代码：`modules/hot_topics.py`、`services/hot_topics.py`、`services/hot_ranking.py`

## 1. 它解决什么

「每日热榜」回答**今天大家搜什么**，一条一条列出来；「热点选题」回答**我该写哪一个**。

同一件事会同时出现在微博、知乎、抖音，标题还各不相同。直接看榜单，人要在几十条重复信息里自己判断：哪条是多平台共振的真热点、哪条已经连续在榜三天、哪条一看就不能碰。这个页面把这段判断做成可复算的分数，并**解释每一分从哪来**。

边界：

- 热点**直接成稿**：点「写这篇」创建一篇空白 `content_item` 并跳进创作空间，不恢复已下线的选题库，不生成占位正文。
- 只做「选题」，不做「自动写作」：稿件内容仍由创作空间的 AI 写作完成。
- 分数只由后端算，前端只排序/筛选/展示；前端不重算分数，避免两套口径。

## 2. 链路

```
定时任务（一个平台一条，脚本自己请求+解析）
   → POST /hot-topics/ingest（固定格式）→ 逐条归一/去重 → 同一事件合并（聚簇）→ 100 分评分 → 列表/详情 → 一键成稿
                                 hot_ranking.cluster_items   hot_ranking.score_cluster   content_items
```

一次 ingest 完成写入 + 聚簇 + 评分并落库；后续读取只查库，不再访问外网。

### 2.1 入库（`hot_topics.ingest`）

服务端不认识任何平台，只收一种固定格式：一个平台的一轮榜单。

```json
{
  "platform": "微博",
  "sourceUrl": "https://60s.viki.moe/v2/weibo",
  "capturedAt": "2026-11-22 09:05",
  "items": [{"title": "某地宣布延长供暖期", "rank": 1, "hot": 1234567, "url": "https://example.com/a"}]
}
```

- `platform` 必填（≤32 字），同名才是同一个平台；`sourceUrl` 必填，必须 http/https 绝对地址，记这一轮**真正请求**的地址。
- `items[].title` 必填，空标题在批内丢弃、不让整批失败；`rank` / `hot` 可空整数；`items[].url` 是单条内容链接。
- 非法请求（缺 platform、缺/非法 sourceUrl、items 不是数组、rank/hot 不是整数）→ 422，不入库。
- `items` 允许空数组：表示这轮该平台没条目，不删除已入库数据。
- 采集源地址落 `hot_items.source_url`（批次级），与单条 `url` 分开；不参与身份键、不参与评分。

**怎么配一个平台**（加平台、换源、换字段名）见 `docs/hot-topics-config.md`，只在「定时脚本」里改任务，不改本模块。

约束：

- 采集发生在定时脚本里（服务端高信任代码），热点接口自身不再发外网请求，所以没有「源注册表上的 SSRF 开关」。
- 平台清单不再登记在仓库：热点页的平台条 = 当天 `hot_items` 里出现过的 `platform`。
- 单平台写失败由那条任务自己记账（任务运行记录里能看到错误码），不影响其它平台；服务端不伪造「0 个事件」的成功。

环境变量只剩一个：

| 变量 | 默认 | 说明 |
|---|---|---|
| `HOT_TOPICS_REFRESH_COOLDOWN` | `15` | 页面「重算评分」的冷却（秒），0 表示关闭；定时 ingest 不受它影响 |

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
- 名次采样：`(item_id, captured_at)` 唯一，同一轮重复入库不会重复插入。
- 簇：按 `fingerprint` 复用；用户设的 `ignored` / `watching` / `generated` 在重算后保留。
- **每轮用当天全部已入库条目重算**：一轮 ingest 只写本平台条目，聚簇输入却是当天全部条目（含刚写入的），所以先写平台 A、再写平台 B，A 不会从簇里掉出去，跨平台合并照样成立；同一轮里一个已有簇只能被一个分组认领。
- 重算（`POST /hot-topics/recalculate`）不写条目、不抓数据，只按库里当天条目重算一遍；连跑两次分数一致。
- 成稿：同一簇重复点「写这篇」返回同一篇稿件。

## 3. 接口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/hot-topics/ingest` | `hot_topics.ingest` + `hot_topics.view` | 一个平台的一轮榜单入库；格式非法 422 |
| POST | `/hot-topics/recalculate` | 登录 | 按已入库数据重算聚簇与分数，不抓取；body `{date?}`；冷却内 429 `too_frequent`，当天无数据 400 `no_hot_data` |
| GET | `/hot-topics/clusters` | 登录 | 列表。`date` / `minScore` / `track` / `platform` / `status` / `q` / `sort=score\|time\|resonance` / `limit` |
| GET | `/hot-topics/clusters/{id}` | 登录 | 详情：成员条目（含 `sourceUrl`）、名次采样、关联稿件标题 |
| PATCH | `/hot-topics/clusters/{id}` | 登录 | `{status}`：`new` / `watching` / `ignored`；`generated` 只能由成稿写入 |
| POST | `/hot-topics/clusters/{id}/draft` | 登录 | 热点 → 稿件（201，返回 `ContentOut`） |
| GET | `/hot-topics/summary` | 登录 | 概要素材数/事件数/≥70 分数量/已出稿数/赛道分布/各平台事件数 |
| GET | `/hot-topics/degraded` | 登录 | 本轮数据不足的维度与原因 |

列表默认按分数降序，不按 id：热点的意义就是最值得写的在最前。`platform` 过滤在 Python 侧完成——JSON 列的 LIKE 在 SQLite/PostgreSQL 行为不一致，当天簇数量有限，多扫一遍比写方言分支便宜。

旧的 `GET /hot-topics/sources` 与 `POST /hot-topics/refresh` 已删除：平台列表改读当天已入库平台（`summary.platforms`），「刷新并评分」改成「重算评分」。

### 兼容旧契约

创作空间「灵感」面板仍调 `GET /api/v1/hot-topics`（`modules/ai_content.py`）。该端点只读当日已评分簇（`title` 为事件标题，`hot` 为 `"82 分 · 强烈推荐"`），没有数据时返回空列表——不再回落到任何外部单源。

## 4. 数据表

| 表 | 用途 | 关键约束 |
|---|---|---|
| `hot_items` | 各平台原始条目 | `item_uid` 唯一；`(day, platform, rank)` 索引；`url` 是单条链接，`source_url` 是本轮采集地址 |
| `hot_rank_history` | 名次采样，用于升降/新鲜度 | `(item_id, captured_at)` 唯一 |
| `hot_clusters` | 合并后的事件与评分 | `fingerprint` 复用；`(last_seen_day, score DESC, id)` 索引 |

迁移走 `core/db.py` 的 `_ADDED_COLUMNS` / `_INDEXES` 机制，`init_db()` 幂等补列补索引；旧库无需手工迁移。

## 5. 页面

`apps/web/src/views/HotTopicsView.vue` + `features/hot/HotClusterCard.vue` + `features/hot/HotScoreBars.vue` + `features/hot/HotPlatformTabs.vue`

- 顶部概要：素材 → 事件 → 值得写 → 已出稿，赛道分布条，当天已入库平台与数据不足提示。
- 平台切换条：一行带条数的 chip，条数来自 `summary.platforms`（当天该平台的事件数），点一下只看该渠道，再点一下回到全部；条数为 0 的平台置灰不可点；左右方向键 / Home / End 可移动；窄屏横向滚动、触控目标 44px。选中平台写进 URL（`?platform=微博`），刷新、分享、后退都能还原。
- 右上角「重算评分」：调用 `POST /hot-topics/recalculate`，只按已入库条目重算，不抓外网。
- 其余筛选：日期 / 赛道 / 最低分 / 排序（价值、最新、共振）/ 只看未出稿 / 标题搜索（250ms 防抖）。筛选栏滚动时贴顶；宽屏排一行，搜索在第二行。
- 空态区分「当天还没有平台入库」「筛选挡光了」；前者给「去配定时任务」入口，后者说明该平台当天有几个事件，并给「看全部平台 / 清空筛选」两个出口。
- 事件卡（扫读用，宽屏单卡约 70px）：左侧分数与档位，右侧两行——标题行，以及「平台 | 赛道·首见·名次·原文数 | 五维分数条 | 写这篇/忽略/展开原文」。五维分数条在卡片里是紧凑单行（`HotScoreBars variant="compact"`），悬浮看证据，完整明细仍看「展开原文」。窄屏（≤900px）隐藏次要元信息，≤560px 时分数条与操作各占一行。
- 「展开原文」才请求详情（含各平台原始条目、单条链接与采集源地址）；采集源地址和单条链接分两块显示。
- 「写这篇」→ `POST .../draft` → 跳转 `/studio?id=<contentItemId>`；已出稿的卡片显示「打开已生成的稿件」。
- 「忽略 / 恢复」写回服务端状态，刷新后保留。

## 6. 测试

`apps/api/tests/test_hot_topics.py`（23 项）：

- 纯函数：标题归一、同事件合并与反义/实体否决、确定性评分（同一 `captured_at` 结果一致）、风险词逐条累计、聚簇分组、超长标题的身份键。
- 入库契约：缺/非法 `sourceUrl` 与空 `platform` 一律 422；空标题只丢该条；空 `items` 不删已有数据；`sourceUrl` 落库并在换源后按平台更新；重复入库不改人工状态；超长标题不撑爆列宽。
- 跨平台：先写平台 A 再写 B，A 的条目仍在且合成一个跨平台簇。
- 重算：连跑两次分数与簇数一致；当天无数据返回 400 `no_hot_data`；`/hot-topics/sources` 已下线。
- 读取与成稿：列表排序/平台与赛道筛选、详情、状态流转、成稿幂等、概要、数据不足、首页热榜、灵感面板。

测试**不访问外网**：直接打 ingest 契约。涉及落库的用例用独立 `date` 隔离数据，并固定 `captured_at`，避免互相污染。

## 7. 与参考实现（DSH 工作台）的对应

| DSH | 本项目 |
|---|---|
| `tools/gen-materials.py` 多源采集 | 不在仓库：各平台定时任务自己采集，服务端只收入库 |
| `tools/tracks.py` 赛道词表与打分 | `services/hot_ranking.py` |
| `tools/hot-topics.py` 聚簇/评分/报告 | `services/hot_ranking.py` + `services/hot_topics.py` |
| `lib/client.template.js` 的 `HotTopicRadar` | `HotTopicsView.vue` + `HotClusterCard.vue` + `HotScoreBars.vue` |

算法（阈值、权重、否决顺序、代表标题选择）按参考实现移植，未重新发明；差异只有三处：采集与评分彻底解耦、落库幂等、直接成稿。

## 8. 首页热榜

`GET /dashboard` 额外返回 `hotHighlights`：`{day, capturedAt, clusters, topics[]}`，
其中 `topics` 是当日按分数排序的 Top 5 簇（标题 / 分数 / 档位 / 平台数），
复用 `list_clusters(sort=score)` + `summary()`，不引入第二套查询口径。
`OverviewView.vue` 的「今日热点」卡片展示这 5 条，点击进 `/hot-topics`；
库里一条簇都没有时展示空态，不影响首页其它区块。
