# 热点选题：怎么加一个平台

热点选题只负责「入库 → 聚簇 → 评分 → 出稿」。**采集不在服务端**：每个平台一条
定时任务，脚本自己请求、自己解析，然后把结果转成固定格式调用入库接口。

所以加平台、换采集地址、换字段名，只改那条任务，不改热点模块的代码。

```text
定时任务「采集·微博」（一个平台一条）
    请求上游 → 解析 → 映射成固定格式
        ↓  hot_topics.ingest / POST /api/v1/hot-topics/ingest
只做：校验 → 写 hot_items → 用当天全部已入库条目重算聚簇和分数
        ↓
热点选题页照旧展示（分数、平台、赛道、成稿）
```

---

## 1. 固定入库格式

一次请求代表**一个平台的一轮榜单**，不是一个平台打包所有源。

```json
{
  "platform": "微博",
  "sourceUrl": "https://60s.viki.moe/v2/weibo",
  "capturedAt": "2026-11-22 09:05",
  "items": [
    {"title": "某地宣布延长供暖期", "rank": 1, "hot": 1234567, "url": "https://example.com/a"}
  ]
}
```

| 字段 | 规则 |
| --- | --- |
| `platform` | 必填，展示名，最长 32 字。**同名才是同一个平台**，改名等于换一个平台。 |
| `sourceUrl` | 必填，必须是 `http`/`https` 绝对地址。记的是这一轮**真正请求**的地址。 |
| `capturedAt` | 可空，`YYYY-MM-DD HH:MM`。空则服务端填当前时间；同一请求共用这一个时刻，保证这轮分数可复现。 |
| `date` | 可空，`YYYY-MM-DD`。补历史数据时才用，平时留空 = 今天。 |
| `items[].title` | 必填。空标题在这一批里**丢弃**，不会让整批失败。 |
| `items[].rank` | 可空整数。没有名次就空着，不要填 0 冒充第一。 |
| `items[].hot` | 可空整数。没有热度就空着。 |
| `items[].url` | 可空。**单条内容链接**，不是采集源地址。 |

`sourceUrl` 和 `items[].url` 是两件事：前者是「这轮从哪个榜抓的」，后者是「这条内容在哪儿」。

几条硬规则：

- 缺 `platform`、缺 `sourceUrl`、`sourceUrl` 不是 http/https 绝对地址、`items` 不是数组、`rank`/`hot` 不是整数 → **422**，整批不入库。
- `items` 可以是空数组：表示这轮该平台没条目，**不删除**已入库数据。
- 采集源地址存在 `hot_items.source_url`：本批写入/更新的条目记上这一轮的地址；同一平台换地址后下一轮会更新它，其它平台不受影响。它不参与判重、不参与评分。
- 超长标题由服务端按列宽裁剪、身份键做哈希，脚本不用自己截断。

返回：

```json
{
  "day": "2026-11-22",
  "capturedAt": "2026-11-22 09:05",
  "platform": "微博",
  "sourceUrl": "https://60s.viki.moe/v2/weibo",
  "items": 50,
  "clusters": 132,
  "newClusters": 3,
  "matchedClusters": 129,
  "recommended": 12
}
```

`items` 是本批实际入库条数，`clusters` 是当天事件总数。

---

## 2. 加一个平台：四步

入口：左侧「定时脚本」（`/schedules`）。

### 第一步：新建脚本

1. 点「新建脚本」，名称写平台名，例如 `采集·微博`。
2. 在「插入常用示例」里选 **「热点入库（按平台配一条）」**，代码会填进编辑器。
3. 改任务参数（见下一节）。
4. 点「测试验证」：会用当前参数真跑一次，但**回滚不落库**，只回显 `summary`。
5. 点「保存并发布」，同时配时间（cron + 时区）。

### 第二步：任务参数

参数形状由脚本决定，热点接口不解释它们。示例脚本认这些键：

```json
{
  "platform": "微博",
  "sourceUrl": "https://60s.viki.moe/v2/weibo",
  "titleKey": "word",
  "itemsPath": "data.list",
  "rankKey": "rank",
  "hotKey": "hot_value",
  "urlKey": "url",
  "timeout": 15
}
```

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `platform` | 是 | 展示名，会和热点页的平台条一致。 |
| `sourceUrl` | 是 | 真正请求的地址；会被记进条目，用于核对来源。 |
| `titleKey` | 是 | 标题字段名。 |
| `itemsPath` | 否 | 榜单数组在响应里的位置，点号路径（`data.list`、`result.0.items`）。空 = 响应本身就是数组。 |
| `rankKey` | 否 | 名次字段名，默认 `rank`。 |
| `hotKey` | 否 | 热度字段名，默认 `hot`。 |
| `urlKey` | 否 | 单条链接字段名，默认 `url`。 |
| `timeout` | 否 | 请求超时秒数，默认 15，最大 60。 |

### 第三步：频率

- 热榜类：`5 8-22 * * *`（每天 8~22 点每小时第 5 分），时区 `Asia/Shanghai`。
- 日报类：`10 8 * * *`（每天早上 8:10）。
- 同一平台跑得越勤，「名次升降」和「日内新鲜度」越算得出来（各需 ≥2 / ≥3 轮）。

每个平台的任务互相独立：谁都不知道对方存在，也不需要互相调用。每次入库都会用
**当天全部已入库条目**重算，所以后跑的平台会把先跑的也算进分数，跨平台事件照样合并。

### 第四步：验证

1. 在列表里点「立即执行」，等结果回执。
2. 回执摘要应形如：`微博：本轮 50 条，当天 2026-11-22 共 300 条入库 · 132 个事件（新 3 / 并入 129）`。
3. 打开「热点选题」页：平台条上出现该平台，事件列表有分数。

---

## 3. 换采集源 / 换字段名

同一平台换了榜单地址或上游改了字段名时：

1. 编辑那条脚本任务的参数：改 `sourceUrl`、`itemsPath`、`titleKey` 等。
2. 点「测试验证」确认能解析出条目。
3. 「保存并发布」。

不需要改热点模块代码，也不需要重启 API。下一轮入库会把该平台条目上的
`source_url` 更新成新地址。

上游结构变化大到映射参数表达不了（比如要先拼两个接口、要翻页）时，才改脚本代码：
在示例脚本上写自己的请求与解析，最后仍调用同一个入口：

```python
from app.services import hot_topics

result = hot_topics.ingest(
    ctx.db, platform="微博", source_url=source_url, items=items
)
```

---

## 4. 排障

| 现象 | 原因与处理 |
| --- | --- |
| 接口 422，`sourceUrl` 相关 | 地址不是 http/https 绝对地址，或没填。填真正请求到的地址。 |
| 接口 422，`rank`/`hot` 相关 | 传了字符串或小数。脚本里转成整数或留空。 |
| 入库成功但事件数没涨 | 这一批的标题和当天已有事件合并了，看 `matchedClusters`。 |
| 事件分数偏低、提示「名次升降未计算」 | 当天只有 1 轮采集。把频率调密，或等下一轮。 |
| 平台上没有新数据 | 该平台的任务停用了 / 没建。热点页只显示**当天入库过**的平台。 |
| 页面「重算评分」没反应 | 重算冷却（`HOT_TOPICS_REFRESH_COOLDOWN`，默认 15 秒）；重算只对已入库条目算分，不会抓新数据。 |
| 想补历史某天的数据 | ingest 传 `date` 与 `capturedAt`，用那天的数据重放一遍。 |

---

## 5. 从旧版迁移

旧版是「一条总任务拉 22 个内置源」（`app/hot_sources/catalog.yaml`），已删除。

1. 在 `apps/api` 目录执行一次：

   ```bash
   .venv/Scripts/python.exe scripts/retire_hot_board_task.py
   ```

   它会把名为「热点榜单采集」的脚本与定时任务停用（幂等，可反复跑；加 `--dry-run` 先看会停哪些）。
2. 重启一次 API，让运行中的调度器不再装载这条任务。
3. 需要哪个平台，就按第 2 节加一条任务。**没配任务的平台不会再有新数据**——这是预期行为。
4. 旧环境变量 `HOT_TOPICS_PLATFORMS` / `HOT_TOPICS_TIMEOUT` / `HOT_TOPICS_WECHAT_*` /
   `HOT_TOPICS_API_URL` 已废弃，可以从 `.env` 里删掉；只有
   `HOT_TOPICS_REFRESH_COOLDOWN`（重算按钮冷却，默认 15 秒，0 关闭）还有效。

---

## 6. 代码位置

| 作用 | 位置 |
| --- | --- |
| 入库 / 重算 / 聚簇 / 评分 | `apps/api/app/services/hot_topics.py`（`ingest` / `recalculate`） |
| 接口 | `apps/api/app/modules/hot_topics.py`（`POST /hot-topics/ingest`、`POST /hot-topics/recalculate`） |
| 入库格式校验 | `apps/api/app/schemas.py`（`HotIngestIn`） |
| 示例脚本 | `apps/api/app/resources/script_examples.py` 的 `HOT_INGEST` |
| 停用旧任务 | `apps/api/scripts/retire_hot_board_task.py` |
| 页面 | `apps/web/src/views/HotTopicsView.vue` |
