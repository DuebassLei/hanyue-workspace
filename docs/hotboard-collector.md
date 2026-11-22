# hotboard 数据源分析与定时采集脚本

> 目标站点：<https://yiktt.github.io/hotboard/>（今日热榜 · 46 平台聚合）
> 采集脚本：`docs/hotboard-to-hotspots.script.py`（通用定时任务 / 用户脚本）
> 落库去处：热点选题库（`hot_items` → `hot_clusters`），与手动刷新同一条链路

---

## 1. 数据源分析

### 1.1 站点本质

hotboard 是**纯静态页面**（原生 HTML/CSS/JS，无后端、无构建）。页面 HTML 里**没有任何榜单数据**，全部数据由浏览器 JS 在运行时直接请求各平台公开接口。这意味着：

- 不能抓 `yiktt.github.io/hotboard/` 的 HTML 拿数据——那里只有骨架。
- 真正的数据源是它 JS（`hot.js`）里 `SOURCES` 配置指向的第三方接口。
- 页面上的 `cors.redoc.ly` 代理是**浏览器专用的 CORS 中转**；本脚本在服务端运行、没有同源限制，一律**直连源站**，少一层依赖、更稳定。

### 1.2 46 个平台的两种来源

| 类型 | 数量 | 接口 | 稳定性 |
|---|---|---|---|
| Codelife 主源 | 31 | `https://api.codelife.cc/api/top/list?lang=cn&id=<cid>` | 结构统一、最稳，带热度值 |
| 直连源 | 15 | HN / dev.to / 豆瓣 / X 趋势 / App Store / 7 路 RSS / 新浪 / 网易 / 澎湃 | 接口各异，部分是 HTML/JSONP，较脆弱 |

**Codelife 主源**（31 个）返回统一结构，是采集主力：

```json
{ "code": 200, "data": [ { "index": 1, "title": "标题", "hotValue": "1939万", "link": "https://..." } ] }
```

> 注意：Codelife 是第三方聚合快照，可能滞后于平台官方榜；脚本里 `hotValue` 会用「万 / 亿」换算成整数热度，`index` 作为名次。

**15 个直连源**的解析口径（均取自 `hot.js` 的 `loadSource` 分支）：

| 平台 | 源站接口 | 取用字段 | 热度 |
|---|---|---|---|
| Hacker News | `hn.algolia.com/api/v1/search?tags=front_page` | `hits[].title / points / url / objectID` | points |
| dev.to | `dev.to/api/articles?top=7&per_page=20` | `[].title / positive_reactions_count / url / path` | reactions |
| 豆瓣电影 | `movie.douban.com/j/search_subjects?...tag=热门` | `subjects[].title / url` | 无 |
| X 趋势 | `getdaytrends.com/<region>/` | HTML `#trends tr td.main a` | 无 |
| App Store | `itunes.apple.com/<cc>/rss/topfreeapplications/limit=N/json` | `feed.entry[].im:name / id` | 无 |
| BBC/纽约时报/卫报/半岛/NPR/德国之声/中国新闻网 | 各家官方 RSS feed | `item/entry` 的 `title / link` | 无 |
| 新浪热榜 | `top.news.sina.com.cn/ws/GetTopDataList.php?...` | JSONP `data[].title / top_num / url` | top_num |
| 网易新闻 | `m.163.com/fe/api/hot/news/flow` | `data.list[].title / url` | 无 |
| 澎湃新闻 | `cache.thepaper.cn/contentapi/wwwIndex/rightSidebar` | `data.hotNews[].name / link / contId` | 无 |

### 1.3 与项目现有采集源的关系

热点选题现在**不内置任何采集源**：平台差异全在定时任务的参数与脚本里（见 `docs/hot-topics-config.md`）。本脚本就是「一个任务采多个榜」的另一种写法，与「一个平台一条任务」并存、互不影响：

- 每条榜单调用一次公开入口 `hot_topics.ingest(platform, source_url, items)`，按天入库、聚簇、评分；
- 落库时 `platform` 用的是 hotboard 的中文榜名（如「微博热搜」「GitHub 趋势」），`source_url` 记该榜这一轮真正请求的地址；
- 评分、聚簇、幂等（`platform + 归一化标题`）、复用历史簇等逻辑与其它平台任务**完全一致**。

---

## 2. 把脚本配置成定时任务

脚本遵循项目统一的「用户脚本定时任务」契约：`run(params, ctx) -> dict`。项目里所有定时任务都是脚本，界面在 `/schedules`，接口 `/api/v1/schedules/*`（详见 `docs/scheduled-tasks.md`）。

### 2.1 操作步骤

1. 打开工作台「定时任务」页 → 新建脚本。
2. 把 `docs/hotboard-to-hotspots.script.py` 的**全部内容**粘贴进脚本编辑器。
3. 在「脚本参数」里填 JSON（见 §3），留 `{}` 则采默认精选源。
4. 保存即发布（存草稿 + 发布快照 + 建定时任务）。设置 cron，例如：
   - 每 2 小时一轮：`0 */2 * * *`
   - 每天 8/12/18/22 点整：`0 8,12,18,22 * * *`
5. 点「测试验证」——回滚执行，不落库，确认能跑通。
6. 点「立即执行」验证落库，再等定时触发。

> 脚本超时建议设为 120 秒以上：`platforms: "all"` 时要顺序请求 46 个源，串行耗时较长。默认精选源（12 个）20~40 秒通常够。

### 2.2 CLI 播种

项目不再提供「热点采集」的 CLI 播种脚本（旧版一次拉全部内置源的那条已废除，用
`scripts/retire_hot_board_task.py` 停用）。本脚本以界面配置为主；需要 CLI 时可仿照
`scheduled_tasks` 的「按归属人 + 名称复用、代码变才发新版本」思路自己写一个。

---

## 3. 参数说明

任务参数是一个 JSON 对象，所有字段都可省略：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `platforms` | 数组 / `"all"` / 省略 | 默认精选 12 源 | 要采哪些榜。见 §4 |
| `limit` | int | `30` | 每榜取前 N 条，范围 1~50 |
| `timeout` | int | `12` | 单源超时秒数，范围 3~30 |
| `date` | string | 今天 | 归档日期 `YYYY-MM-DD`，一般不用填 |
| `region` | object | `{}` | X 趋势 / App Store 地区覆盖 |

几个常见配置：

```json
// 默认精选源（12 个稳定的主流榜）
{}

// 指定平台：id 或中文榜名都行，可混用
{ "platforms": ["weibo", "知乎热榜", "hackernews", "bili"], "limit": 20 }

// 采全部 46 个榜（超时建议 180 秒）
{ "platforms": "all", "timeout": 15 }

// 换地区：X 看全球趋势，App Store 看中国区
{ "platforms": ["x", "appstore"], "region": { "x": "worldwide", "appstore": "cn" } }
```

返回值里 `summary` 是界面摘要，`sources/sourcesTotal` 是成功/总数，`degraded` 列出降级的源，`notes` 是每个源的采集条数。

---

## 4. 后续如何采集「不同平台」

这是本脚本的设计重点：**改参数即可换平台，不用改代码**。

### 4.1 选平台

`platforms` 支持三种写法（脚本的 `_choose_sources` 处理）：

1. **省略 / `[]`** → 只采 `DEFAULT_IDS` 里的 12 个精选源（稳定、快）。
2. **`"all"`** → 采全部 46 个平台，包括易失效源。
3. **数组** → 按 `id` 或中文 `name` 精确点名，可混用。填了不存在的名字会明确报错并列出可选 id，不会静默漏采。

全部可用 id（按 hotboard 分类）：

- **综合热搜**：`weibo` `zhihu` `baidu` `toutiao` `wechat` `smzdm` `tieba` `sinahot` `netease`
- **科技开发**：`github` `juejin` `sspai` `ithome` `kr36` `csdn` `zcool` `huxiu` `oschina` `renren`
- **海外视野**：`phunt` `appstore` `hackernews` `devto` `x` `bbc` `nyt` `guardian` `aljazeera` `npr` `dw`
- **新闻资讯**：`txnews` `sina` `cbn` `xueqiu` `thepaper` `chinanews` `wallstreetcn`
- **影音娱乐**：`bili` `douyin` `douban` `hupu` `weread` `dongqiudi` `kuaishou` `maoyan` `imdb`

### 4.2 新增 / 调整平台

平台清单的唯一真相是脚本里的 `SOURCES` 数组。要加一个 hotboard 上新收录的榜：

- **Codelife 源**（绝大多数）：加一行
  ```python
  dict(id="新id", name="中文榜名", cat="分类", kind="codelife", cid="从 hot.js 查到的 cid"),
  ```
- **直连源**：确认 `hot.js` 的 `loadSource` 用了哪种 `api`，在 `LOADERS` 里实现对应解析函数，再在 `SOURCES` 加 `dict(..., kind="你注册的 key")`。
- 想让新源进默认精选，把它的 `id` 加进 `DEFAULT_IDS`。

> cid / feed / 字段若与上游不符，以 hotboard 仓库 `hot.js` 的 `SOURCES` 与 `LOADERS` 为准——本脚本就是照它 1:1 移植到服务端的。

### 4.3 易失效源提醒

`appstore` / `x` / `douban` 三个源标记为 `stable=False`（不走默认精选）：

- X 趋势和 App Store 依赖分区页面 / RSS，地区参数敏感；
- 豆瓣用官方搜索接口替代 HTML 抓取，但上游仍可能改版或限流；
- 任何源失败都只记入 `notes` / `degraded`，不影响其它源；全部源失败才抛错（避免「0 条」假成功）。

---

## 5. 注意事项与限制

- **SSRF 防护**：所有出站请求统一走 `app.core.ssrf.safe_request`（HTTPS / 公网 IP / 逐跳校验）。不要用 `requests` 直连替换。
- **数据时效**：hotboard 和 Codelife 都是第三方聚合，数据可能滞后官方榜；热度值口径各平台不同，评分里只做同平台归一。
- **串行采集**：`platforms: "all"` 串行请求 46 个源，慢且对上游压力较大；日常建议只配需要的平台，或降低频率。
- **幂等**：条目身份是 `platform + 归一化标题`，重跑同一批数据不会重复建条目；名次变化只更新采样，不算新条目。
- **脚本进程内全信任执行**：脚本持有 DB 会话与全部依赖，等同服务端 RCE；只由 `scheduled-script.admin` 高信任角色维护（见 `docs/scheduled-tasks.md`）。
- **版本快照**：每次发布都会存快照，执行只认发布时的代码版本；改脚本后需重新发布才对新一轮生效。

---

## 6. 验证记录

脚本已完成以下验证（不打外网，打桩依赖）：

- `py_compile` 语法编译通过；
- `run()` 契约执行：`platforms: "all"` 全部 46 源解析成功，产出 77 条、落库链路（ingest/upsert/commit）各调用正确；
- `platforms` 按 id / 中文名筛选正确（3 个源精确命中）；
- 未知平台明确报错；
- 单源失败降级：某源异常只记 `notes`、不中断其它源，全部失败才抛错。
