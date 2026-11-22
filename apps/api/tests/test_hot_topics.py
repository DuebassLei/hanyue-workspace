"""热点选题：聚簇/评分纯函数 + 入库 → 聚簇 → 评分 → 成稿的完整链路。

采集不在服务端：每个平台一条定时任务，自己请求、自己解析，再调 ingest。
所以这里不打外网，直接验 ingest 契约——外部榜单的可用性不该决定 CI 成败。
"""

from __future__ import annotations

PREFIX = "/api/v1"
# 同一轮 ingest 共用一个采集时刻：评分基准固定，结果才可复现
CAPTURED = "2026-11-13 11:00"
DEFAULT_SOURCE = "https://example.com/hot"


def _login(client, subject="hot-user"):
    response = client.post(f"{PREFIX}/auth/login", json={"subject": subject})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _ingest(
    client,
    headers,
    platform,
    items,
    *,
    source_url=DEFAULT_SOURCE,
    captured_at=CAPTURED,
    date=None,
):
    """按固定格式入库一批。`date` 用来隔离各用例的数据（测试共用一个库）。"""
    body = {
        "platform": platform,
        "sourceUrl": source_url,
        "capturedAt": captured_at,
        "items": items,
    }
    if date:
        body["date"] = date
    return client.post(f"{PREFIX}/hot-topics/ingest", json=body, headers=headers)


def _clusters(client, headers, day):
    return client.get(f"{PREFIX}/hot-topics/clusters?date={day}", headers=headers).json()


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


# ══════════════════════════════════════════════════════════════════════
# 入库契约
# ══════════════════════════════════════════════════════════════════════

def test_ingest_rejects_missing_or_invalid_source_url(client):
    """sourceUrl 必填且必须是 http/https 绝对地址：缺了整批 422，不入库。"""
    headers = _login(client, "hot-contract")
    body = {"platform": "微博", "items": [{"title": "某标题"}]}
    missing = client.post(f"{PREFIX}/hot-topics/ingest", json=body, headers=headers)
    assert missing.status_code == 422

    for bad in ("", "   ", "weibo/hot", "ftp://example.com/hot", "javascript:alert(1)"):
        response = client.post(
            f"{PREFIX}/hot-topics/ingest",
            json={**body, "sourceUrl": bad},
            headers=headers,
        )
        assert response.status_code == 422, f"{bad!r} 应被拒绝"

    blank_platform = client.post(
        f"{PREFIX}/hot-topics/ingest",
        json={"platform": "  ", "sourceUrl": DEFAULT_SOURCE, "items": []},
        headers=headers,
    )
    assert blank_platform.status_code == 422


def test_ingest_drops_blank_titles_without_failing_batch(client):
    headers = _login(client, "hot-blank")
    day = "2026-02-01"
    response = _ingest(
        client,
        headers,
        "微博-空标题",
        [{"title": "   "}, {"title": ""}, {"title": "空标题不该让整批失败", "rank": 2}],
        date=day,
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == 1

    rows = _clusters(client, headers, day)
    assert len(rows) == 1
    assert rows[0]["title"] == "空标题不该让整批失败"


def test_second_platform_ingest_keeps_first_platform_items(client):
    """先写平台 A 再写平台 B：A 的条目仍在，同一事件合成一个跨平台簇。

    这是「按平台拆任务」的前提：后写入的一轮要读当天全部条目重算，
    而不是只算自己这一批。
    """
    headers = _login(client, "hot-cross")
    day = "2026-02-02"

    first = _ingest(
        client, headers, "微博-跨平台", [{"title": "某地宣布延长供暖期", "rank": 1}], date=day
    )
    assert first.status_code == 200, first.text
    assert first.json()["clusters"] == 1

    second = _ingest(
        client,
        headers,
        "知乎-跨平台",
        [{"title": "如何看待某地宣布延长供暖期？", "rank": 3}],
        date=day,
    )
    assert second.status_code == 200, second.text
    # 平台 B 入库后仍是同一个事件，不是两个
    assert second.json()["clusters"] == 1
    assert second.json()["matchedClusters"] == 1

    rows = _clusters(client, headers, day)
    assert len(rows) == 1
    assert rows[0]["platformCount"] == 2
    detail = client.get(
        f"{PREFIX}/hot-topics/clusters/{rows[0]['id']}", headers=headers
    ).json()
    # 平台 A 的条目没有被后一轮挤掉
    assert {item["platform"] for item in detail["items"]} == {"微博-跨平台", "知乎-跨平台"}


def test_ingest_empty_items_keeps_existing_data(client):
    headers = _login(client, "hot-empty-batch")
    day = "2026-02-09"
    assert _ingest(
        client, headers, "微博-空批", [{"title": "某条保留的热点", "rank": 1}], date=day
    ).status_code == 200

    empty = _ingest(client, headers, "微博-空批", [], date=day)
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == 0
    # 空数组只表示「这轮没有条目」，不删除已入库数据
    assert empty.json()["clusters"] == 1
    assert len(_clusters(client, headers, day)) == 1


def test_source_url_is_recorded_and_updates_per_platform(client):
    headers = _login(client, "hot-source-url")
    day = "2026-02-03"

    assert _ingest(
        client, headers, "微博-换源", [{"title": "某市发布新的落户政策", "rank": 1}],
        source_url="https://feed.one/weibo", date=day,
    ).status_code == 200
    assert _ingest(
        client, headers, "知乎-换源", [{"title": "某市落户新政有哪些细节", "rank": 2}],
        source_url="https://feed.one/zhihu", date=day,
    ).status_code == 200

    def source_urls():
        mapping = {}
        for row in _clusters(client, headers, day):
            detail = client.get(
                f"{PREFIX}/hot-topics/clusters/{row['id']}", headers=headers
            ).json()
            for item in detail["items"]:
                mapping[item["platform"]] = item["sourceUrl"]
        return mapping

    assert source_urls() == {
        "微博-换源": "https://feed.one/weibo",
        "知乎-换源": "https://feed.one/zhihu",
    }

    # 同一平台换采集地址：该平台条目跟着更新，其它平台不变
    assert _ingest(
        client, headers, "微博-换源", [{"title": "某市发布新的落户政策", "rank": 1}],
        source_url="https://feed.two/weibo", date=day,
    ).status_code == 200
    assert source_urls() == {
        "微博-换源": "https://feed.two/weibo",
        "知乎-换源": "https://feed.one/zhihu",
    }


def test_reingest_does_not_reset_manual_status(client):
    headers = _login(client, "hot-status-keep")
    day = "2026-02-04"
    assert _ingest(
        client, headers, "微博-状态", [{"title": "某品牌发布新款手机", "rank": 1}], date=day
    ).status_code == 200

    cluster_id = _clusters(client, headers, day)[0]["id"]
    patched = client.patch(
        f"{PREFIX}/hot-topics/clusters/{cluster_id}",
        json={"status": "watching"},
        headers=headers,
    )
    assert patched.status_code == 200

    # 下一轮入库（同平台、新增一条）会重算分数，但不能把人工状态改回 new
    assert _ingest(
        client,
        headers,
        "微博-状态",
        [{"title": "某品牌发布新款手机", "rank": 2}, {"title": "某品牌新机开箱视频流出", "rank": 3}],
        date=day,
    ).status_code == 200

    detail = client.get(
        f"{PREFIX}/hot-topics/clusters/{cluster_id}", headers=headers
    ).json()
    assert detail["status"] == "watching"


def test_over_long_title_is_clipped_and_uid_stays_within_column(client):
    """超长「标题」（酷安/微信热文其实是整段正文）不能把整轮入库打成 500。

    线上表现：item_uid 拼了完整归一化标题 → 超过 VARCHAR(255) →
    StringDataRightTruncation，整批 500。
    """
    from app.services import hot_topics

    headers = _login(client, "hot-long-title")
    day = "2026-02-06"
    long_title = "双十一在横道店买的k100promax，大伙儿都说他家后封，" + "手机屏幕有点弯曲，" * 40
    response = _ingest(
        client,
        headers,
        "酷安-长标题",
        [{"title": long_title, "rank": 3, "url": "https://www.coolapk.com/feed/1"}],
        date=day,
    )
    assert response.status_code == 200, response.text

    from app.core.db import SessionLocal
    from app.models import HotItem

    db = SessionLocal()
    try:
        item = db.query(HotItem).filter(HotItem.platform == "酷安-长标题").one()
        assert len(item.item_uid) <= hot_topics.ITEM_UID_MAX_CHARS
        assert len(item.title) <= hot_topics.TITLE_MAX_CHARS
        assert len(item.title_norm) <= hot_topics.TITLE_MAX_CHARS
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════
# 重算 / 列表 / 详情 / 成稿
# ══════════════════════════════════════════════════════════════════════

def test_recalculate_is_idempotent(client, monkeypatch):
    """重算只按已入库数据算分：连跑两次，分数与簇数完全一致。"""
    monkeypatch.setenv("HOT_TOPICS_REFRESH_COOLDOWN", "0")
    headers = _login(client, "hot-recalc")
    day = "2026-02-10"

    assert _ingest(
        client, headers, "微博-重算", [{"title": "某地出台新的租房补贴政策", "rank": 1}], date=day
    ).status_code == 200
    assert _ingest(
        client, headers, "百度-重算", [{"title": "租房补贴新政落地首日", "rank": 2}], date=day
    ).status_code == 200

    first = client.post(f"{PREFIX}/hot-topics/recalculate", json={"date": day}, headers=headers)
    assert first.status_code == 200, first.text
    scores_first = {row["title"]: row["score"] for row in _clusters(client, headers, day)}

    second = client.post(f"{PREFIX}/hot-topics/recalculate", json={"date": day}, headers=headers)
    assert second.status_code == 200, second.text
    scores_second = {row["title"]: row["score"] for row in _clusters(client, headers, day)}

    assert scores_first == scores_second
    assert second.json()["newClusters"] == 0


def test_recalculate_without_data_returns_400(client, monkeypatch):
    monkeypatch.setenv("HOT_TOPICS_REFRESH_COOLDOWN", "0")
    headers = _login(client, "hot-recalc-empty")
    response = client.post(
        f"{PREFIX}/hot-topics/recalculate", json={"date": "1999-01-01"}, headers=headers
    )
    assert response.status_code == 400
    assert response.json()["code"] == "no_hot_data"


def test_cluster_list_detail_and_filters(client):
    headers = _login(client, "hot-list")
    day = "2026-02-07"
    assert _ingest(
        client, headers, "微博-列表",
        [{"title": "赵丽颖哄自己上班引热议", "rank": 1, "hot": 1200000},
         {"title": "OpenAI 发布新一代推理模型，成本下降 40%", "rank": 2, "hot": 900000}],
        date=day,
    ).status_code == 200
    assert _ingest(
        client, headers, "知乎-列表",
        [{"title": "如何看待赵丽颖说自己每天哄自己上班？", "rank": 3}],
        date=day,
    ).status_code == 200
    assert _ingest(
        client, headers, "百度-列表", [{"title": "多地官宣调整中小学上学时间", "rank": 1}], date=day
    ).status_code == 200

    rows = _clusters(client, headers, day)
    assert len(rows) == 3
    # 默认按分数倒序
    assert [row["score"] for row in rows] == sorted(
        [row["score"] for row in rows], reverse=True
    )
    top = next(row for row in rows if "赵丽颖" in row["title"])
    assert top["platformCount"] == 2
    assert top["grade"] in {"强烈推荐", "可做", "备用", "不建议"}

    # 平台筛选（JSON 数组元素）
    only_zhihu = client.get(
        f"{PREFIX}/hot-topics/clusters?date={day}&platform=知乎-列表", headers=headers
    ).json()
    assert [row["id"] for row in only_zhihu] == [top["id"]]

    # 赛道筛选
    ai_rows = client.get(
        f"{PREFIX}/hot-topics/clusters?date={day}&track=AI 与科技", headers=headers
    ).json()
    assert any("OpenAI" in row["title"] for row in ai_rows)

    detail = client.get(f"{PREFIX}/hot-topics/clusters/{top['id']}", headers=headers)
    assert detail.status_code == 200
    detail_body = detail.json()
    assert len(detail_body["items"]) == 2
    assert detail_body["items"][0]["platform"]
    assert all(item["sourceUrl"] == DEFAULT_SOURCE for item in detail_body["items"])
    # 详情里要能看到评分证据，界面才能回答「凭什么给这个分」
    assert detail_body["scoreDetail"]["track"] == "娱乐与情感"

    missing = client.get(f"{PREFIX}/hot-topics/clusters/not-exist", headers=headers)
    assert missing.status_code == 404


def test_status_and_draft_flow(client):
    headers = _login(client, "hot-draft")
    day = "2026-02-08"
    assert _ingest(
        client, headers, "微博-成稿", [{"title": "某部电影周末票房破纪录", "rank": 1}], date=day
    ).status_code == 200
    rows = _clusters(client, headers, day)
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


def test_summary_and_degraded(client):
    headers = _login(client, "hot-summary")
    day = "2026-02-05"
    assert _ingest(
        client, headers, "微博-概要",
        [{"title": "某地暴雨导致地铁停运", "rank": 1, "hot": 500000},
         {"title": "某明星官宣新片", "rank": 2}],
        date=day,
    ).status_code == 200

    summary = client.get(
        f"{PREFIX}/hot-topics/summary?date={day}", headers=headers
    ).json()
    assert summary["items"] == 2
    assert summary["clusters"] == 2
    assert summary["platforms"] == {"微博-概要": 2}

    degraded = client.get(
        f"{PREFIX}/hot-topics/degraded?date={day}", headers=headers
    ).json()
    dims = {row["dim"] for row in degraded}
    # 只入库了一轮：升降与日内新鲜度都不可算
    assert {"rise", "fresh", "hot"} <= dims
    # 采集侧的降级说明已随内置采集一起删除
    assert "source" not in dims


def test_sources_endpoint_is_gone(client):
    headers = _login(client, "hot-no-sources")
    response = client.get(f"{PREFIX}/hot-topics/sources", headers=headers)
    assert response.status_code == 404


def test_dashboard_exposes_hot_highlights(client):
    from app.services import hot_topics

    headers = _login(client, "hot-dashboard")
    assert _ingest(
        client, headers, "微博-首页", [{"title": "某地发布高温红色预警", "rank": 1}],
        date=hot_topics.today_str(),
    ).status_code == 200

    payload = client.get(f"{PREFIX}/dashboard", headers=headers).json()
    highlights = payload["hotHighlights"]
    assert highlights["topics"]
    assert highlights["topics"][0]["title"]
    assert "score" in highlights["topics"][0]
    assert "grade" in highlights["topics"][0]


def test_inspiration_panel_reads_clusters(client):
    """创作空间「灵感」面板只读已评分簇，不再回落抓外网。"""
    from app.services import hot_topics

    headers = _login(client, "hot-inspiration")
    title = "某地试行四天半工作制"
    assert _ingest(
        client, headers, "微博-灵感", [{"title": title, "rank": 1}],
        date=hot_topics.today_str(),
    ).status_code == 200

    panel = client.get(f"{PREFIX}/hot-topics", headers=headers).json()
    assert panel
    assert all(row["source"] == "热点选题" for row in panel)

    status = client.get(f"{PREFIX}/hot-topics/status", headers=headers).json()
    assert status["configured"] is True
    assert "微博-灵感" in status["note"]
