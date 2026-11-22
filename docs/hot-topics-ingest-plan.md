# 热点选题改为通用入库接口

状态：**已实施**（2026-11-22）。实现见 `services/hot_topics.py` 的 `ingest` / `recalculate`、
`modules/hot_topics.py` 的两个 POST 接口、`resources/script_examples.py` 的 `HOT_INGEST`、
`scripts/retire_hot_board_task.py`；操作手册见 `docs/hot-topics-config.md`。

## 1. 要改掉什么

现在的采集入口是 `hot_topics.refresh()` → `hot_sources.collect()`。平台清单、URL、JSON 路径、HTML 正则都在 `apps/api/app/hot_sources/catalog.yaml` 和 `parsers.py` / `adapters.py` 里。定时任务「热点榜单采集」只是调用这套内置解析。所以换一个源就要改仓库。

热点选题真正要守住的只有后半段，这些保持不变：

- 条目身份：`platform + 归一化标题`
- 入库：`hot_items`
- 聚簇和 100 分制评分：`hot_ranking` / `hot_clusters`
- 展示、状态、写成稿件：现有 `/hot-topics/clusters*` 接口和页面

采集不在这个边界里。

## 2. 目标形态

```text
定时任务（一个平台一条）
    自己请求上游、自己解析
    转成固定格式
        ↓
POST /api/v1/hot-topics/ingest
        ↓
只做：校验 → 写入 hot_items → 用当天全部已入库条目重算聚簇和分数
        ↓
热点选题页照旧展示
```

热点接口不再替任何平台发外网请求。因此也不再有「源注册表上的 SSRF 开关」。请求发生在定时脚本里；脚本本来就是服务端高信任代码，这点不变。

## 3. 固定格式

一次请求代表**一个平台的一轮榜单**，不是 22 个源打包。

```json
{
  "platform": "微博",
  "sourceUrl": "https://60s.viki.moe/v2/weibo",
  "capturedAt": "2026-11-22 09:05",
  "items": [
    {
      "title": "标题，必填",
      "rank": 1,
      "hot": 1234567,
      "url": "https://example.com/a"
    }
  ]
}
```

`sourceUrl` 和 `items[].url` 不是一回事：

- `sourceUrl`：这一轮实际请求的采集地址，例如热榜接口或榜单页。换源就是换这个地址。
- `items[].url`：这一条热点自己的链接，没有就可以空着。

采集地址放在批次上，不放进每一条。一次 ingest 就是一个平台的一轮抓取，整批共用同一个源地址。

| 字段 | 规则 |
| --- | --- |
| `platform` | 必填，展示名。最长 32 字。同一平台每次必须用同一个名字，改名等于另一个平台。 |
| `sourceUrl` | 必填。必须是 `http` 或 `https` 绝对地址。记的是这一轮脚本真正发出的请求地址，不是任务参数里打算用、但没请求到的地址。 |
| `capturedAt` | 可空。空则服务端填本轮时间，格式 `YYYY-MM-DD HH:MM`。同一请求里所有条目共用这一个时间，保证这轮分数可复现。 |
| `items[].title` | 必填。空标题丢弃，不报整批失败。 |
| `items[].rank` | 可空整数。没有名次就空着，评分按「无名次」处理，不要填 0 冒充第一。 |
| `items[].hot` | 可空整数。没有热度就空着。 |
| `items[].url` | 可空。单条内容链接，不是采集源地址。 |

现有 `hot_items.url` 只存单条链接，没有采集源地址。实施时给 `hot_items` 增加 `source_url`（TEXT）。本批写入或更新的条目记上这次的 `sourceUrl`；其它平台的条目不动。条目详情和簇成员里把这个地址展示出来，方便核对「这条是从哪个榜来的」。源地址不参与身份键，也不参与评分。同一平台换地址后，新一轮会把该平台条目上的 `source_url` 更新成新地址。

服务端继续做现有的标题裁剪和超长身份键哈希，调用方不用自己截断。

非法请求（没有 platform、没有合法 sourceUrl、items 不是数组、rank/hot 不是整数）直接 422，不入库。`items` 可以是空数组，表示这一轮该平台没有条目；空数组**不删除**这个平台今天已经入库的数据，也不改写它们已记下的源地址。

返回沿用现在刷新结果里和入库有关的字段：`day`、`capturedAt`、`items`（本批实际写入条数）、`clusters`、`newClusters`、`matchedClusters`。不再返回「哪个内置源降级」。

## 4. 评分必须看当天全部数据

现有 `_upsert_clusters` 会按传入的条目重算当天归属。如果只把微博这一批传进去，其它平台当天的条目会从簇里掉出去，跨平台合并就坏了。

所以 ingest 的顺序是：

1. 只把本批写入 / 更新 `hot_items`。
2. 再从库里读出**当天全部平台**的条目，做聚簇和评分。
3. 不删除其它平台的条目，不覆盖人工状态（`watching` / `ignored` / `generated`）。

这样每条平台任务互不知道对方，但后写入的一轮会把已经在库里的平台一起算进分数。平台任务之间不需要互相调用。

代价：一天里每个平台每跑一次，就全量重算一次当天数据。按现在的量级（每平台几十到上百条，平台数几十）可以接受。不另做「采集任务」和「评分任务」两种类型，避免加平台时还要记得去改第二条任务。

## 5. 定时任务约定

一个平台一条任务，名称建议就是平台名，例如「采集·微博」。

脚本只做三件事：请求、把上游字段映射成第 3 节的格式、调用入库。推荐在脚本里直接调服务，不绕 HTTP，这样不用在脚本里管登录令牌：

```python
def run(params, ctx):
    # 1. 请求与解析完全属于这条任务
    rows = fetch_and_parse(params)
    # 2. 转成固定格式
    source_url = params["sourceUrl"]
    payload = [
        {"title": row["title"], "rank": row.get("rank"),
         "hot": row.get("hot"), "url": row.get("url") or ""}
        for row in rows
    ]
    # 3. 入库并触发当天重算。sourceUrl 用这一轮实际请求的地址
    from app.services import hot_topics
    return hot_topics.ingest(
        ctx.db, platform=params["platform"], source_url=source_url, items=payload
    )
```

任务参数放这条平台自己的配置，例如：

```json
{
  "platform": "微博",
  "sourceUrl": "https://example.com/weibo",
  "itemsPath": "data.list",
  "titleKey": "word",
  "hotKey": "hot_value",
  "urlKey": "url"
}
```

参数形状由脚本作者决定，热点接口不解释这些字段。换地址、换字段名，只改这条任务的参数或脚本草稿，然后发布。

频率也按平台配。热榜可以 `5 8-22 * * *`，日报类源可以每天一次。不再存在一条总任务统一拉 22 个源。

仓库里最多留**一个**示例脚本，演示「任意 JSON → 固定格式 → ingest」。不把现在的 22 路解析搬进 `script_examples.py`，否则只是把注册表换了个地方。

## 6. 接口与页面

新增：

- `POST /api/v1/hot-topics/ingest`  
  权限沿用 `hot_topics.view` 不合适，入库会改数据。给 `hot_topics.ingest`；定时脚本走服务层，不走这个权限。HTTP 给外部或手工补数用。

保留：

- `GET /hot-topics/clusters`、详情、改状态、写成稿、summary、degraded
- 创作空间灵感面板继续读已评分簇

去掉或改义：

| 现在 | 之后 |
| --- | --- |
| `POST /hot-topics/refresh` 拉全部内置源 | 删除。页面上的「刷新」改成「按已入库数据重算」，调用只重算、不抓取的接口 |
| `GET /hot-topics/sources` 读 catalog | 删除。平台列表改为当天 `hot_items` 里出现过的 `platform` |
| `HOT_TOPICS_PLATFORMS`、`HOT_TOPICS_TIMEOUT`、`HOT_TOPICS_WECHAT_*` | 删除。超时和关键词写在对应任务里 |
| `HOT_TOPICS_API_URL` 单源回落 | 删除 |
| `HOT_TOPICS_REFRESH_COOLDOWN` | 只留给「重算」按钮，避免连点；定时 ingest 不走冷却 |
| 种子任务「热点榜单采集」 | 停用并不再种子化。已有库里的那条任务在迁移说明里让人关掉，避免它还去调已删除的 `collect()` |

## 7. 删除范围

运行路径上删除，不留兼容开关：

- `apps/api/app/hot_sources/catalog.yaml`
- `catalog.py`、`adapters.py`、`parsers.py` 以及仅被它们使用的脚本
- `hot_sources.collect()` 和 `refresh()` 里的采集分支
- `seed_hot_schedule.py` 里「一条任务拉全部内置源」的行为
- 环境变量样例和 `docs/hot-topics-config.md` 里「22 路内置源」的章节；那份文档改写成「如何新增一条平台任务」

测试跟着改：删掉「catalog 有 22 个源」「某个解析器能解析微博」这类用例，改成 ingest 契约、空标题丢弃、单平台写入后其它平台仍在簇里、人工状态不被覆盖。

## 8. 实施顺序

1. **契约**  
   实现 `hot_topics.ingest` 和 `POST /hot-topics/ingest`。重算必须读取当天全部条目。补上跨平台不丢数据的测试。此步不删旧入口，避免页面突然没数据。

2. **示例任务**  
   加一个示例脚本和一篇短说明：参数里写 URL 和字段名，脚本映射后调用 `ingest`。用一个真平台试跑，确认热点页能看到。

3. **切页面**  
   热点选题去掉「拉取全部源」。刷新改为重算。来源筛选改为读已入库平台。

4. **拆掉内置源**  
   删除 catalog、解析器、总采集任务种子。更新 `.env.example` 和配置文档。全量回归里与热点、定时脚本相关的测试要绿。

5. **收尾**  
   在定时任务页停用旧的「热点榜单采集」。从这一步起，没有配任务的平台不会再有新数据。

不在本计划里做：可视化的「字段映射表单」、代拉外网的通用 HTTP 适配器、把旧 22 路自动迁成 22 条任务。需要哪个平台，就在定时任务里加一条。

## 9. 验收

- 仓库里不再有 `catalog.yaml`，热点服务不再 import `hot_sources.collect`。
- 只调用 ingest、不配任何内置源，也能把一批标题送进热点页并出分。缺 `sourceUrl` 或地址不合法时整批 422。
- 入库后能看到这轮的采集源地址，且它和单条内容链接分开存。换 `sourceUrl` 再入库，该平台条目上的源地址跟着更新，其它平台不变。
- 先写入平台 A，再写入平台 B，A 的条目仍在，且同一事件能合成一个跨平台簇。
- 把 B 的任务 URL 或字段名改掉并重新发布后，新数据进库，全程不改热点模块代码。
- 旧「热点榜单采集」任务即使还在，也不能再走到已删除的内置解析；迁移说明要求停用它。
