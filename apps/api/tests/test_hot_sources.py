"""热榜采集通用平台：契约、catalog、三类 Adapter 与编排降级。

所有采集一律 monkeypatch，不打外网——外部榜单可用性不该决定 CI 成败。
"""

from __future__ import annotations

import httpx
import pytest

PREFIX = "/api/v1"

ROWS = [
    {"platform": "微博", "rank": 1, "title": "首页热点测试条目", "hot": 100, "url": ""},
    {"platform": "知乎", "rank": 2, "title": "另一个热点测试条目", "hot": None, "url": ""},
]


class FakeResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _source(source_id: str) -> dict:
    from app.hot_sources.catalog import load_catalog

    return next(s for s in load_catalog()["sources"] if s["id"] == source_id)


def _login(client, subject="hot-source-user"):
    res = client.post(f"{PREFIX}/auth/login", json={"subject": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _patch_collect(monkeypatch, rows=None, notes=None):
    from app.services import hot_sources

    payload = ROWS if rows is None else rows

    def fake_collect(platforms=None):
        return [dict(row) for row in payload], (notes or ["fixture：%d 条" % len(payload)])

    monkeypatch.setattr(hot_sources, "collect", fake_collect)


# ══════════════════════════════════════════════════════════════════════
# 契约
# ══════════════════════════════════════════════════════════════════════

def test_hot_raw_item_to_row_ignores_captured_at_and_extra():
    from app.hot_sources.contracts import HotRawItem

    item = HotRawItem(
        platform="微博", title="标题", captured_at="2026-11-19T08:00:00+00:00",
        rank=1, hot=99, url="https://x", extra={"summary": "略"},
    )
    assert item.to_row() == {
        "platform": "微博", "rank": 1, "title": "标题", "hot": 99, "url": "https://x",
    }


def test_collect_context_safe_get_uses_injected_request():
    from app.hot_sources.contracts import CollectContext

    calls = []

    def fake(method, url, *, headers=None, timeout=None):
        calls.append((method, url, timeout, headers))
        return FakeResponse()

    ctx = CollectContext(source={}, request_fn=fake, timeout=7, retries=1)
    ctx.safe_get("https://example.com/api", headers={"User-Agent": "ua"})
    assert calls[0][0] == "GET"
    assert calls[0][2] == 7
    assert calls[0][3]["User-Agent"] == "ua"
    assert "application/json" in calls[0][3]["Accept"]


def test_collect_context_maps_http_and_ssrf_errors():
    from app.core import ssrf
    from app.hot_sources.contracts import CollectContext, HotSourcesError

    ctx = CollectContext(
        source={}, request_fn=lambda *a, **k: FakeResponse(status_code=500), retries=0
    )
    with pytest.raises(HotSourcesError) as http_error:
        ctx.safe_get("https://example.com")
    assert http_error.value.code == "http_status"

    def blocked(*a, **k):
        raise ssrf.UnsafeUrl("禁止访问")

    ctx2 = CollectContext(source={}, request_fn=blocked, retries=2)
    with pytest.raises(HotSourcesError) as ssrf_error:
        ctx2.safe_get("https://example.com")
    assert ssrf_error.value.code == "ssrf_blocked"


# ══════════════════════════════════════════════════════════════════════
# catalog：单一真相源
# ══════════════════════════════════════════════════════════════════════

def test_catalog_declares_all_platforms():
    from app.hot_sources.catalog import all_platforms, load_catalog

    sources = load_catalog()["sources"]
    assert len(sources) == 22
    assert len({s["id"] for s in sources}) == 22
    assert len({s["name"] for s in sources}) == 22
    assert set(all_platforms()) == {
        "今日头条", "百度", "抖音", "微博", "知乎", "AI·热点", "AI·资讯",
        "AIHOT 热点与早报", "AIHOT 日报", "微信 10w+ 爆文池", "微信文章搜索",
        "开源榜·每日飙升", "开源榜·每周飙升", "开源榜·Star 总榜",
        "B站", "贴吧", "36氪", "虎嗅", "IT之家", "酷安", "掘金", "少数派",
    }
    assert all(s["requires_ssrf_safe"] for s in sources)
    assert all(s["kind"] in {"json", "markdown", "html", "script"} for s in sources)


def test_catalog_filter_and_source_list(monkeypatch):
    from app.hot_sources import enabled_platforms, source_catalog

    assert len(source_catalog()) == 22
    monkeypatch.setenv("HOT_TOPICS_PLATFORMS", "微博,知乎")
    assert enabled_platforms() == ["微博", "知乎"]
    from app.hot_sources import enabled_sources

    assert [s["id"] for s in enabled_sources()] == ["weibo", "zhihu"]


def test_catalog_url_env_override(monkeypatch):
    monkeypatch.setenv("HOT_TOPICS_WECHAT_10W_URL", "https://example.com/snapshot.json")
    assert _source("wechat_10w")["url"] == "https://example.com/snapshot.json"


# ══════════════════════════════════════════════════════════════════════
# 三类 Adapter
# ══════════════════════════════════════════════════════════════════════

def test_json_adapter_direct_parses_and_keeps_url():
    from app.hot_sources.adapters import JsonAdapter
    from app.hot_sources.contracts import CollectContext

    payload = {"data": [{"Title": "标题甲", "HotValue": "123", "Url": "https://x/a"}]}
    ctx = CollectContext(
        source=_source("toutiao"), request_fn=lambda *a, **k: FakeResponse(payload)
    )
    result = JsonAdapter().collect(ctx)
    assert result.ok and len(result.items) == 1
    assert result.items[0].to_row() == {
        "platform": "今日头条", "rank": 1, "title": "标题甲", "hot": 123, "url": "https://x/a",
    }
    assert not any("selector_may_changed" in note for note in result.notes)


def test_json_adapter_marks_selector_may_changed_on_empty():
    from app.hot_sources.adapters import JsonAdapter
    from app.hot_sources.contracts import CollectContext

    ctx = CollectContext(
        source=_source("weibo"), request_fn=lambda *a, **k: FakeResponse({"data": []})
    )
    result = JsonAdapter().collect(ctx)
    assert result.ok and result.items == []
    assert any("selector_may_changed" in note for note in result.notes)


def test_json_adapter_degrades_without_raising():
    from app.hot_sources.adapters import JsonAdapter
    from app.hot_sources.contracts import CollectContext

    bad_json = CollectContext(
        source=_source("weibo"), request_fn=lambda *a, **k: FakeResponse(None)
    )
    result = JsonAdapter().collect(bad_json)
    assert not result.ok and result.error_code == "invalid_response"

    def boom(*a, **k):
        raise httpx.ConnectError("unreachable")

    network = CollectContext(source=_source("weibo"), request_fn=boom, retries=0)
    result = JsonAdapter().collect(network)
    assert not result.ok and result.error_code == "fetch_failed"


def test_text_adapter_aihot_agent_and_empty():
    from app.hot_sources.adapters import TextPatternAdapter
    from app.hot_sources.contracts import CollectContext

    text = "第 1 名：[某模型发布](https://example.com/a)\n   信源：The Verge、TechCrunch\n"
    ctx = CollectContext(
        source=_source("aihot_agent_hot"), request_fn=lambda *a, **k: FakeResponse(text=text)
    )
    result = TextPatternAdapter().collect(ctx)
    assert result.ok and result.items[0].title == "某模型发布"
    assert result.items[0].hot == 2

    empty = CollectContext(
        source=_source("aihot_agent_daily"), request_fn=lambda *a, **k: FakeResponse(text="<html/>")
    )
    empty_result = TextPatternAdapter().collect(empty)
    assert empty_result.ok and not empty_result.items
    assert any("selector_may_changed" in note for note in empty_result.notes)


def test_text_adapter_wechat_search_skips_without_keyword(monkeypatch):
    from app.hot_sources.adapters import TextPatternAdapter
    from app.hot_sources.contracts import CollectContext

    monkeypatch.delenv("HOT_TOPICS_WECHAT_KEYWORD", raising=False)

    def refuse(*a, **k):
        raise AssertionError("不该请求外网")

    result = TextPatternAdapter().collect(
        CollectContext(source=_source("wechat_search"), request_fn=refuse)
    )
    assert result.ok and result.items == []
    assert any("未配置关键词" in note for note in result.notes)

    monkeypatch.setenv("HOT_TOPICS_WECHAT_KEYWORD", "测试关键词")
    seen = {}

    def capture(method, url, *, headers=None, timeout=None):
        seen["url"] = url
        return FakeResponse(text="<html></html>")

    TextPatternAdapter().collect(
        CollectContext(source=_source("wechat_search"), request_fn=capture)
    )
    # 关键词按 URL 编码后拼进 query（与原实现一致，不打未编码的中文）
    from urllib.parse import quote

    assert seen["url"].endswith("query=" + quote("测试关键词"))


def test_script_adapter_loads_configured_function(monkeypatch):
    import sys
    import types

    from app.hot_sources.adapters import ScriptAdapter
    from app.hot_sources.contracts import CollectContext, CollectResult, HotRawItem

    module = types.ModuleType("fake_hot_script_for_test")

    def collect(ctx):
        return CollectResult(
            platform=ctx.config["name"], ok=True,
            items=[HotRawItem(platform=ctx.config["name"], title="脚本条目", captured_at=ctx.now())],
            notes=["脚本：1 条"],
        )

    module.collect = collect
    monkeypatch.setitem(sys.modules, module.__name__, module)
    source = dict(_source("toutiao"), name="脚本源", kind="script", adapter=f"{module.__name__}:collect")
    result = ScriptAdapter().collect(CollectContext(source=source))
    assert result.ok and result.items[0].title == "脚本条目"


# ══════════════════════════════════════════════════════════════════════
# 编排：降级与全源失败
# ══════════════════════════════════════════════════════════════════════

class _FixedAdapter:
    def __init__(self, result):
        self.result = result

    def collect(self, ctx):
        return self.result


def test_collect_keeps_going_on_single_source_failure(monkeypatch):
    from app import hot_sources
    from app.hot_sources.contracts import CollectResult, HotRawItem

    good = dict(_source("toutiao"), name="甲源")
    bad = dict(_source("baidu"), name="乙源")
    monkeypatch.setattr(hot_sources.catalog, "enabled_sources", lambda platforms=None: [bad, good])

    def adapter_for(source):
        if source["name"] == "乙源":
            return _FixedAdapter(CollectResult(
                platform="乙源", ok=False, error_code="fetch_failed",
                notes=["乙源：失败（fetch_failed）"],
            ))
        return _FixedAdapter(CollectResult(
            platform="甲源", ok=True,
            items=[HotRawItem(platform="甲源", title="成功条目", captured_at="now")],
            notes=["甲源：1 条"],
        ))

    monkeypatch.setattr(hot_sources, "adapter_for", adapter_for)
    rows, notes = hot_sources.collect()
    assert [row["title"] for row in rows] == ["成功条目"]
    assert any("乙源" in note and "fetch_failed" in note for note in notes)


def test_collect_raises_when_all_sources_fail(monkeypatch):
    from app import hot_sources
    from app.hot_sources.contracts import CollectResult

    monkeypatch.setattr(
        hot_sources.catalog, "enabled_sources",
        lambda platforms=None: [dict(_source("toutiao"), name="全挂源")],
    )

    def adapter_for(source):
        return _FixedAdapter(CollectResult(
            platform="全挂源", ok=False, error_code="ssrf_blocked", notes=["全挂源：失败（ssrf_blocked）"],
        ))

    monkeypatch.setattr(hot_sources, "adapter_for", adapter_for)
    with pytest.raises(hot_sources.HotSourcesError):
        hot_sources.collect()


# ══════════════════════════════════════════════════════════════════════
# 接口联调：notes 进 degraded、首页热榜
# ══════════════════════════════════════════════════════════════════════

def test_collect_notes_reach_degraded_endpoint(client, monkeypatch):
    monkeypatch.setenv("HOT_TOPICS_REFRESH_COOLDOWN", "0")
    from app.services import hot_topics

    hot_topics.reset_cooldown()
    # 独立日期 + 与基线测试相同的采集时刻：degraded 不按日过滤时会统计全部
    # captured_at，沿用同一时刻才不会让「只采一轮」的基线断言失效。
    monkeypatch.setattr(hot_topics, "now_stamp", lambda: "2026-11-13 11:00")
    _patch_collect(monkeypatch, notes=["微博：selector_may_changed（解析到 0 条）", "知乎：失败（fetch_failed）"])
    headers = _login(client, "hot-source-degraded")
    assert client.post(
        f"{PREFIX}/hot-topics/refresh", json={"date": "2026-01-01"}, headers=headers
    ).status_code == 200

    degraded = client.get(
        f"{PREFIX}/hot-topics/degraded?date=2026-01-01", headers=headers
    ).json()
    source_notes = [row["reason"] for row in degraded if row["dim"] == "source"]
    assert any("selector_may_changed" in note for note in source_notes)
    assert any("fetch_failed" in note for note in source_notes)


def test_dashboard_exposes_hot_highlights(client, monkeypatch):
    monkeypatch.setenv("HOT_TOPICS_REFRESH_COOLDOWN", "0")
    from app.services import hot_topics

    hot_topics.reset_cooldown()
    # 同上：独立日期隔离数据，采集时刻与基线一致，不增加 distinct captured_at
    monkeypatch.setattr(hot_topics, "now_stamp", lambda: "2026-11-13 11:00")
    _patch_collect(monkeypatch)
    headers = _login(client, "hot-source-dashboard")
    assert client.post(
        f"{PREFIX}/hot-topics/refresh", json={"date": "2026-01-01"}, headers=headers
    ).status_code == 200

    payload = client.get(f"{PREFIX}/dashboard", headers=headers).json()
    highlights = payload["hotHighlights"]
    assert highlights["topics"]
    assert highlights["topics"][0]["title"]
    assert "score" in highlights["topics"][0]
    assert "grade" in highlights["topics"][0]
