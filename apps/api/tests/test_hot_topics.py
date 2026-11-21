"""热点选题：聚簇/评分纯函数 + 采集→聚簇→评分→成稿的完整链路。

采集一律走 monkeypatch，测试不打外网——外部榜单的可用性不该决定 CI 成败。
"""

from __future__ import annotations

PREFIX = "/api/v1"

FIXTURE_ROWS = [
    {"platform": "微博", "rank": 1, "title": "赵丽颖哄自己上班引热议", "hot": 1200000, "url": "https://s.weibo.com/1"},
    {"platform": "知乎", "rank": 3, "title": "如何看待赵丽颖说自己每天哄自己上班？", "hot": None, "url": ""},
    {"platform": "抖音", "rank": 2, "title": "赵丽颖哄自己上班", "hot": 9800000, "url": "https://www.douyin.com/hot/1"},
    {"platform": "微博", "rank": 2, "title": "OpenAI 发布新一代推理模型，成本下降 40%", "hot": 900000, "url": ""},
    {"platform": "百度", "rank": 1, "title": "多地官宣调整中小学上学时间", "hot": 500000, "url": ""},
    {"platform": "微博", "rank": 5, "title": "网传某地一商场发生爆炸", "hot": 300000, "url": ""},
    {"platform": "今日头条", "rank": 1, "title": "央行宣布降准0.5个百分点", "hot": 800000, "url": ""},
    {"platform": "今日头条", "rank": 2, "title": "央行宣布降息0.5个百分点", "hot": 700000, "url": ""},
]


def _login(client, subject="hot-user"):
    response = client.post(f"{PREFIX}/auth/login", json={"subject": subject})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


# ══════════════════════════════════════════════════════════════════════
# 纯函数
# ══════════════════════════════════════════════════════════════════════

def test_normalize_strips_platform_prefix():
    from app.services import hot_ranking as ranking

    assert ranking.normalize_title("如何看待赵丽颖说自己每天哄自己上班？") == (
        ranking.normalize_title("赵丽颖说自己每天哄自己上班")
    )
    assert ranking.normalize_title("ＡＢＣ：全角标点！") == "abc全角标点"
    assert ranking.normalize_title("") == ""


def test_similar_merges_same_event_across_platforms():
    from app.services import hot_ranking as ranking

    a = ranking.normalize_title("赵丽颖哄自己上班引热议")
    b = ranking.normalize_title("如何看待赵丽颖说自己每天哄自己上班？")
    assert ranking.similar(a, b) is True


def test_similar_rejects_antonym_and_different_entity():
    from app.services import hot_ranking as ranking

    # 降准 / 降息是两条对立的新闻，合并会让选题直接写错
    assert ranking.similar(
        ranking.normalize_title("央行宣布降准0.5个百分点"),
        ranking.normalize_title("央行宣布降息0.5个百分点"),
    ) is False
    # 不同主体
    assert ranking.similar(
        ranking.normalize_title("赵丽颖哄自己上班引热议"),
        ranking.normalize_title("杨幂哄自己上班引热议"),
    ) is False


def test_rare_term_ignores_shared_units_but_keeps_proper_nouns():
    from app.services import hot_ranking as ranking

    # 真实数据里的误合并：两条都含「亿美元」，但一条是 Anthropic 估值、一条是黄仁勋股权。
    # 「亿美元」当天只出现几次，会被判成稀有片段；数量单位不能当作同一事件的证据。
    titles = [
        ("微博", "Anthropic启动IPO路演，估值预期回落至1.25–1.75万亿美元"),
        ("百度", "Anthropic 官宣首次公开募股（IPO）"),
        ("知乎", "黄仁勋将失去1亿美元未归属股权奖励"),
        ("今日头条", "某公司完成1亿美元融资"),
    ]
    items = [
        {
            "id": str(i), "platform": platform, "title": title,
            "title_norm": ranking.normalize_title(title), "rank": i + 1,
            "hot": None, "url": "",
        }
        for i, (platform, title) in enumerate(titles)
    ]
    rare = ranking.build_rare_terms(items)
    assert "亿美元" in rare, "前提：单位片段确实稀有，修复不能靠调高阈值绕过"

    a, b, c, d = (item["title_norm"] for item in items)
    assert ranking.similar(a, c, rare) is False
    # 两条都只是在讲金额，也不能靠「1亿美元」互相合并
    assert ranking.similar(c, d, rare) is False
    # 专名仍然要能靠稀有片段合并，否则等于把捷径关掉
    assert ranking.similar(a, b, rare) is True
    assert [len(group) for group in ranking.cluster_items(items)] == [2, 1, 1]


def test_cluster_and_score_are_deterministic():
    from app.services import hot_ranking as ranking

    members = [
        {
            "id": "1", "title": "赵丽颖哄自己上班引热议",
            "title_norm": ranking.normalize_title("赵丽颖哄自己上班引热议"),
            "platform": "微博", "rank": 1, "hot": 1200000, "url": "",
            "first_seen_at": "2026-11-13 11:00", "prev_rank": None,
            "best_rank": 1, "days_seen": 1,
        },
        {
            "id": "2", "title": "如何看待赵丽颖说自己每天哄自己上班？",
            "title_norm": ranking.normalize_title("如何看待赵丽颖说自己每天哄自己上班？"),
            "platform": "知乎", "rank": 3, "hot": None, "url": "",
            "first_seen_at": "2026-11-13 11:00", "prev_rank": None,
            "best_rank": 3, "days_seen": 1,
        },
    ]
    first = ranking.score_cluster(members, "2026-11-13 11:00", {"微博": 1200000})
    second = ranking.score_cluster(members, "2026-11-13 11:00", {"微博": 1200000})
    assert first == second
    total, detail = first
    # 双平台共振：8×2 = 16；赛道判对（娱乐与情感）并且有实体证据
    assert detail["resonance"] == 16
    assert detail["track"] == "娱乐与情感"
    assert detail["track_points"] > 0
    assert 0 <= total <= 100
    assert ranking.score_grade(total) in {"强烈推荐", "可做", "备用", "不建议"}


def test_cluster_items_groups_same_event():
    from app.services import hot_ranking as ranking

    items = []
    for index, (platform, title) in enumerate([
        ("微博", "赵丽颖哄自己上班引热议"),
        ("知乎", "如何看待赵丽颖说自己每天哄自己上班？"),
        ("今日头条", "央行宣布降准0.5个百分点"),
        ("今日头条", "央行宣布降息0.5个百分点"),
    ]):
        items.append({
            "id": str(index), "platform": platform, "rank": index + 1,
            "title": title, "title_norm": ranking.normalize_title(title),
            "hot": None, "first_seen_at": "2026-11-13 11:00",
            "prev_rank": None, "best_rank": index + 1, "days_seen": 1,
        })
    groups = ranking.cluster_items(items)
    assert len(groups) == 3
    assert max(len(group) for group in groups) == 2


def test_risk_flags_skip_unverified_when_multi_platform():
    from app.services import hot_ranking as ranking

    single, single_penalty = ranking.risk_flags("网传某地一商场发生爆炸", platform_count=1)
    assert "未证实" in single and single_penalty < 0
    _multi, multi_penalty = ranking.risk_flags("网传某地一商场发生爆炸", platform_count=3)
    assert multi_penalty > single_penalty


# ══════════════════════════════════════════════════════════════════════
# 接口链路
# ══════════════════════════════════════════════════════════════════════

def _patch_collect(monkeypatch, rows=None):
    from app.services import hot_sources

    payload = FIXTURE_ROWS if rows is None else rows

    def fake_collect(platforms=None):
        return [dict(row) for row in payload], ["fixture：%d 条" % len(payload)]

    monkeypatch.setattr(hot_sources, "collect", fake_collect)


def _patch_now(monkeypatch, stamp="2026-11-13 11:00"):
    from app.services import hot_topics

    monkeypatch.setattr(hot_topics, "now_stamp", lambda: stamp)


def test_refresh_is_idempotent(client, monkeypatch):
    monkeypatch.setenv("HOT_TOPICS_REFRESH_COOLDOWN", "0")
    _patch_collect(monkeypatch)
    _patch_now(monkeypatch)
    headers = _login(client, "hot-refresh")

    first = client.post(f"{PREFIX}/hot-topics/refresh", json={}, headers=headers)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["items"] == len(FIXTURE_ROWS)
    # 赵丽颖 3 条合并成 1 个事件；其余 5 条各自成簇（降准/降息不合并）
    assert first_body["clusters"] == 6

    listed_first = client.get(f"{PREFIX}/hot-topics/clusters", headers=headers).json()
    scores_first = {row["title"]: row["score"] for row in listed_first}

    second = client.post(f"{PREFIX}/hot-topics/refresh", json={}, headers=headers)
    assert second.status_code == 200, second.text
    listed_second = client.get(f"{PREFIX}/hot-topics/clusters", headers=headers).json()
    scores_second = {row["title"]: row["score"] for row in listed_second}

    assert scores_first == scores_second
    assert second.json()["newClusters"] == 0


def test_refresh_rebuilds_membership_instead_of_accumulating(client, monkeypatch):
    """掉榜的条目必须从簇里退出。

    旧实现只给本轮条目写 cluster_id，不清理上一轮：一次误合并留下的成员会永远挂在
    那张卡片上，itemCount 和详情成员数对不上，拆簇也拆不掉。
    """
    monkeypatch.setenv("HOT_TOPICS_REFRESH_COOLDOWN", "0")
    _patch_collect(monkeypatch)
    _patch_now(monkeypatch)
    headers = _login(client, "hot-membership")
    assert client.post(f"{PREFIX}/hot-topics/refresh", json={}, headers=headers).status_code == 200

    rows = client.get(f"{PREFIX}/hot-topics/clusters", headers=headers).json()
    target = next(row for row in rows if "赵丽颖" in row["title"])
    detail = client.get(f"{PREFIX}/hot-topics/clusters/{target['id']}", headers=headers).json()
    assert len(detail["items"]) == 3

    # 第二轮只剩微博一条赵丽颖（知乎、抖音已掉榜）
    _patch_collect(monkeypatch, [FIXTURE_ROWS[0], FIXTURE_ROWS[3]])
    assert client.post(f"{PREFIX}/hot-topics/refresh", json={}, headers=headers).status_code == 200

    rows = client.get(f"{PREFIX}/hot-topics/clusters", headers=headers).json()
    target = next(row for row in rows if "赵丽颖" in row["title"])
    detail = client.get(f"{PREFIX}/hot-topics/clusters/{target['id']}", headers=headers).json()
    assert target["itemCount"] == 1
    assert [item["platform"] for item in detail["items"]] == ["微博"]


def test_cluster_list_detail_and_filters(client, monkeypatch):
    monkeypatch.setenv("HOT_TOPICS_REFRESH_COOLDOWN", "0")
    _patch_collect(monkeypatch)
    _patch_now(monkeypatch)
    headers = _login(client, "hot-list")
    assert client.post(f"{PREFIX}/hot-topics/refresh", json={}, headers=headers).status_code == 200

    rows = client.get(f"{PREFIX}/hot-topics/clusters", headers=headers).json()
    assert rows, "刷新后应该有簇"
    # 默认按分数倒序
    assert [row["score"] for row in rows] == sorted(
        [row["score"] for row in rows], reverse=True
    )
    top = rows[0]
    assert top["title"] == "赵丽颖哄自己上班引热议"
    assert top["platformCount"] == 3
    assert top["grade"] == "强烈推荐"

    # 平台筛选（JSON 数组元素）
    only_zhihu = client.get(
        f"{PREFIX}/hot-topics/clusters?platform=知乎", headers=headers
    ).json()
    assert [row["id"] for row in only_zhihu] == [top["id"]]

    # 赛道筛选
    ai_rows = client.get(
        f"{PREFIX}/hot-topics/clusters?track=AI 与科技", headers=headers
    ).json()
    assert any("OpenAI" in row["title"] for row in ai_rows)

    detail = client.get(f"{PREFIX}/hot-topics/clusters/{top['id']}", headers=headers)
    assert detail.status_code == 200
    detail_body = detail.json()
    assert len(detail_body["items"]) == 3
    assert detail_body["items"][0]["platform"]
    # 详情里要能看到评分证据，界面才能回答「凭什么给这个分」
    assert detail_body["scoreDetail"]["track"] == "娱乐与情感"

    missing = client.get(f"{PREFIX}/hot-topics/clusters/not-exist", headers=headers)
    assert missing.status_code == 404


def test_status_and_draft_flow(client, monkeypatch):
    monkeypatch.setenv("HOT_TOPICS_REFRESH_COOLDOWN", "0")
    _patch_collect(monkeypatch)
    _patch_now(monkeypatch)
    headers = _login(client, "hot-draft")
    assert client.post(f"{PREFIX}/hot-topics/refresh", json={}, headers=headers).status_code == 200
    rows = client.get(f"{PREFIX}/hot-topics/clusters", headers=headers).json()
    cluster_id = rows[0]["id"]

    # 还没有成稿，不允许手工标 generated
    rejected = client.patch(
        f"{PREFIX}/hot-topics/clusters/{cluster_id}",
        json={"status": "generated"},
        headers=headers,
    )
    assert rejected.status_code == 400

    ignored = client.patch(
        f"{PREFIX}/hot-topics/clusters/{cluster_id}",
        json={"status": "ignored"},
        headers=headers,
    )
    assert ignored.status_code == 200
    assert ignored.json()["status"] == "ignored"

    created = client.post(
        f"{PREFIX}/hot-topics/clusters/{cluster_id}/draft", headers=headers
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["title"] == rows[0]["title"]

    # 重复点击返回同一篇，不重复建稿
    again = client.post(
        f"{PREFIX}/hot-topics/clusters/{cluster_id}/draft", headers=headers
    )
    assert again.status_code == 201
    assert again.json()["id"] == item["id"]

    detail = client.get(f"{PREFIX}/hot-topics/clusters/{cluster_id}", headers=headers).json()
    assert detail["status"] == "generated"
    assert detail["contentItemId"] == item["id"]


def test_summary_and_degraded(client, monkeypatch):
    monkeypatch.setenv("HOT_TOPICS_REFRESH_COOLDOWN", "0")
    _patch_collect(monkeypatch)
    _patch_now(monkeypatch)
    headers = _login(client, "hot-summary")
    assert client.post(f"{PREFIX}/hot-topics/refresh", json={}, headers=headers).status_code == 200

    summary = client.get(f"{PREFIX}/hot-topics/summary", headers=headers).json()
    assert summary["items"] == len(FIXTURE_ROWS)
    assert summary["clusters"] == 6
    assert summary["recommended"] >= 1
    assert summary["tracks"]

    degraded = client.get(f"{PREFIX}/hot-topics/degraded", headers=headers).json()
    dims = {row["dim"] for row in degraded}
    # 只采集了一轮：升降与日内新鲜度都不可算
    assert {"rise", "fresh", "hot"} <= dims


def test_over_long_title_is_clipped_and_uid_stays_within_column(client, monkeypatch):
    """超长「标题」（酷安/微信热文其实是整段正文）不能把整轮采集打成 500。

    线上表现：item_uid 拼了完整归一化标题 → 超过 VARCHAR(255) →
    StringDataRightTruncation，refresh 直接 500。
    """
    from app.services import hot_topics

    monkeypatch.setenv("HOT_TOPICS_REFRESH_COOLDOWN", "0")
    long_title = "双十一在横道店买的k100promax，大伙儿都说他家后封，" + "手机屏幕有点弯曲，" * 40
    _patch_collect(monkeypatch, [
        {"platform": "酷安", "rank": 3, "title": long_title, "url": "https://www.coolapk.com/feed/1"},
    ])
    _patch_now(monkeypatch)
    headers = _login(client, "hot-long-title")

    response = client.post(f"{PREFIX}/hot-topics/refresh", json={}, headers=headers)
    assert response.status_code == 200, response.text

    from app.core.db import SessionLocal
    from app.models import HotItem

    db = SessionLocal()
    try:
        item = db.query(HotItem).filter(HotItem.platform == "酷安").one()
        assert len(item.item_uid) <= hot_topics.ITEM_UID_MAX_CHARS
        assert len(item.title) <= hot_topics.TITLE_MAX_CHARS
        assert len(item.title_norm) <= hot_topics.TITLE_MAX_CHARS
    finally:
        db.close()


def test_make_item_uid_is_stable_and_collision_free_for_long_titles():
    from app.services import hot_topics

    short = hot_topics.make_item_uid("微博", "赵丽颖哄自己上班")
    assert short == "微博\x1f赵丽颖哄自己上班"

    head = "甲" * 400
    a = hot_topics.make_item_uid("酷安", head + "结尾一")
    b = hot_topics.make_item_uid("酷安", head + "结尾二")
    assert len(a) == hot_topics.ITEM_UID_MAX_CHARS
    # 同一标题每次算出来必须一致，否则幂等失效
    assert a == hot_topics.make_item_uid("酷安", head + "结尾一")
    # 前缀相同、结尾不同的两条长帖必须是两个身份，不能被截断合并成一条
    assert a != b


def test_all_sources_failed_returns_502(client, monkeypatch):
    monkeypatch.setenv("HOT_TOPICS_REFRESH_COOLDOWN", "0")
    from app.services import hot_sources

    def boom(platforms=None):
        raise hot_sources.HotSourcesError("所有热点源都不可用：fixture")

    monkeypatch.setattr(hot_sources, "collect", boom)
    headers = _login(client, "hot-fail")

    response = client.post(f"{PREFIX}/hot-topics/refresh", json={}, headers=headers)
    assert response.status_code == 502
    # ApiError 的 handler 直接把 detail 作为响应体，没有再包一层 "detail"
    assert response.json()["code"] == "all_sources_failed"


def test_sources_endpoint(client):
    headers = _login(client, "hot-sources")
    payload = client.get(f"{PREFIX}/hot-topics/sources", headers=headers).json()
    assert payload["platforms"]
    assert "微博" in payload["enabled"]
    # 灵感雷达移植进来的四路：AIHOT 热点/日报、微信爆文池、微信文章搜索
    names = {row["platform"] for row in payload["platforms"]}
    assert {"AIHOT 热点与早报", "AIHOT 日报", "微信 10w+ 爆文池", "微信文章搜索"} <= names


def test_aihot_agent_markdown_parsing():
    from app.services.hot_sources import _rows_aihot_agent

    hot = _rows_aihot_agent(
        "AIHOT 热点与早报",
        "第 1 名：[OpenAI 发布新模型](https://example.com/a)\n"
        "   信源：The Verge、TechCrunch\n"
        "   来龙去脉：https://aihot.news/story/1\n"
        "第 2 名：[国产芯片量产](https://example.com/b)\n"
        "   信源：IT之家\n",
        "hot",
    )
    assert [row["title"] for row in hot] == ["OpenAI 发布新模型", "国产芯片量产"]
    assert hot[0]["hot"] == 2
    assert hot[0]["url"] == "https://example.com/a"

    daily = _rows_aihot_agent(
        "AIHOT 日报",
        "【模型】\n1. [某模型开源](https://example.com/c) · 某博客\n   模型权重已公开。\n",
        "daily",
    )
    assert daily[0]["title"] == "某模型开源"
    assert daily[0]["rank"] == 1


def test_wechat_10w_snapshot_parsing():
    from app.services.hot_sources import _rows_wechat_10w

    rows = _rows_wechat_10w({
        "articles": [
            {"title": "为什么你总是存不下钱", "userName": "某账号", "clicksCount": "10w+",
             "oriUrl": "https://mp.weixin.qq.com/s/abc"},
            {"title": ""},
        ]
    })
    assert len(rows) == 1
    assert rows[0]["platform"] == "微信 10w+ 爆文池"
    assert rows[0]["title"].startswith("某账号｜")
    assert rows[0]["hot"] == 100000
    assert rows[0]["url"].startswith("https://mp.weixin.qq.com/")


def test_wechat_search_html_parsing():
    from app.services.hot_sources import _rows_wechat_search

    html = (
        '<a href="/link?url=AAA" target="_blank">为什么年轻人开始<font>反向消费</font></a>'
        '<a href="/link?url=BBB">短</a>'
    )
    rows = _rows_wechat_search(html)
    assert len(rows) == 1
    assert rows[0]["title"] == "为什么年轻人开始反向消费"
    assert rows[0]["url"] == "https://weixin.sogou.com/link?url=AAA"
    assert _rows_wechat_search("<html></html>") == []


def test_clean_url_strips_tracking_and_caps_length():
    """今日头条热榜 Url 带 800+ 字符的 log_pb 追踪参数，曾撑爆 VARCHAR(500)。"""
    from app.services.hot_sources import URL_MAX_LENGTH, _clean_url

    tracking = "&".join(f"p{i}={'x' * 40}" for i in range(20))
    long_url = f"https://www.toutiao.com/trending/7708571384561074203/?{tracking}"
    assert _clean_url(long_url) == "https://www.toutiao.com/trending/7708571384561074203/"

    # 去参也救不回来的，硬截到列宽以内，不能让入库炸掉
    monster = "https://example.com/" + "A" * 600
    assert len(_clean_url(monster)) == URL_MAX_LENGTH

    # 没超限的 URL 必须原样保留——搜狗 /link?url= 的载荷就在查询串里
    sogou = "https://weixin.sogou.com/link?url=AAA&_ast=123"
    assert _clean_url(sogou) == sogou


def test_uapi_hotboard_parsing():
    from app.services.hot_sources import _rows_uapi, _uapi_hot_value

    # hot_value 是自由格式字符串，要归一成 int；多指标串宁缺毋滥给 None
    assert _uapi_hot_value("472995播放") == 472995
    assert _uapi_hot_value("热度:549") == 549
    assert _uapi_hot_value("1,234.5万") == 12345000
    assert _uapi_hot_value("评论:6 点赞:17") is None
    assert _uapi_hot_value("") is None

    rows = _rows_uapi("B站", {
        "type": "bilibili",
        "list": [
            {"index": 1, "title": "某视频标题", "hot_value": "472995播放",
             "url": "https://www.bilibili.com/video/BV1"},
            {"index": 2, "title": "", "hot_value": "1", "url": ""},
        ],
    })
    assert len(rows) == 1
    assert rows[0]["hot"] == 472995
    assert rows[0]["url"] == "https://www.bilibili.com/video/BV1"


def test_openrank_html_parsing():
    from app.services.hot_sources import _rows_openrank

    def item(rank, title, key, stars, forks, delta):
        return (
            f'<article class="ornav-repo ready" data-orank-target-url="/zh-cn/repo/{key}.html">'
            f'<div class="ornav-rank">{rank}</div>'
            f'<h3><a href="/zh-cn/repo/{key}.html">{title}</a></h3>'
            '<span class="ornav-repo-stat-icon"><svg><use href="/x.svg#orank-icon-star"/>'
            f'</svg></span><strong><bdi dir="ltr">{stars}</bdi></strong><small>Stars</small>'
            '<span class="ornav-repo-stat-icon"><svg><use href="/x.svg#orank-icon-fork"/>'
            f'</svg></span><strong><bdi dir="ltr">{forks}</bdi></strong><small>Forks</small>'
            f'<strong class="up"><bdi dir="ltr">+{delta}</bdi></strong><small>7 日增长</small>'
            "</article>"
        )

    html = item(1, "REA：逆向工程工具层", "morluto-rea", "465,369", "113,902", 16640) + item(
        2, "某项目", "foo-bar", "1,000", "10", 50
    )
    rows = _rows_openrank("开源榜·每周飙升", html)
    assert [row["title"] for row in rows] == ["REA：逆向工程工具层", "某项目"]
    # 热度取增长数，不能错抓成 Stars/Forks（Star 总数当热度会让老仓库永远霸榜）
    assert rows[0]["hot"] == 16640
    assert rows[0]["url"] == "https://kaiyuanbang.cn/zh-cn/repo/morluto-rea.html"
    assert _rows_openrank("开源榜·每周飙升", "<html></html>") == []


def test_wechat_search_skipped_without_keyword(monkeypatch):
    """没配关键词时跳过而不是报失败，其余源照常返回。"""
    from app.services import hot_sources

    monkeypatch.delenv("HOT_TOPICS_WECHAT_KEYWORD", raising=False)

    def refuse(url, headers, retries=1):
        raise AssertionError(f"不该请求外网：{url}")

    monkeypatch.setattr(hot_sources, "_fetch_json", refuse)
    monkeypatch.setattr(hot_sources.ssrf, "safe_request", refuse)
    rows, notes = hot_sources.collect(["微信文章搜索"])
    assert rows == []
    assert any("未配置关键词" in note for note in notes)
