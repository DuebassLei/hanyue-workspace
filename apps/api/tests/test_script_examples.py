"""常用脚本示例：每份示例都要能编译、过契约、回滚执行成功。

改 app/resources/script_examples.py 的示例时，这里的执行验证就是验收标准——
示例是给用户直接复制的，坏一个就是坑一个。
"""

from __future__ import annotations

import uuid

import pytest

from app.resources.script_examples import EXAMPLES
from app.services import script_runtime


def _example_codes():
    return [(item["key"], item["code"]) for item in EXAMPLES]


@pytest.fixture(scope="module")
def owner_id(client):
    """示例执行需要一个真实存在的用户（batch_drafts 会校验 ctx.owner）。"""
    subject = f"script-ex-{uuid.uuid4().hex[:6]}"
    res = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return res.json()["user"]["id"]


def test_examples_have_unique_keys_and_contract():
    keys = [item["key"] for item in EXAMPLES]
    assert len(keys) == len(set(keys)), "示例 key 不能重复"
    assert len(keys) >= 5, "常用示例至少 5 个"
    for key, code in _example_codes():
        script_runtime.validate_published(code), f"示例 {key} 未通过契约校验"


@pytest.mark.parametrize(("key", "code"), _example_codes(), ids=[k for k, _ in _example_codes()])
def test_example_executes_and_rolls_back(client, owner_id, key, code, monkeypatch):
    """逐个回滚执行：示例必须真实可跑，且不留数据。"""
    params = {}
    if key == "hot_ingest":
        # 采集示例会打外网榜单：外部可用性不该决定 CI 成败，打桩成一条 fixture
        import requests

        class _FakeFeed:
            status_code = 200
            headers = {"content-type": "application/json"}

            def raise_for_status(self):
                return None

            def json(self):
                return {"data": {"list": [{"word": "示例用的热点标题", "hot_value": "1,000"}]}}

        monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _FakeFeed())
        params = {
            "platform": "微博",
            "sourceUrl": "https://example.com/weibo",
            "itemsPath": "data.list",
            "titleKey": "word",
            "hotKey": "hot_value",
        }
    if key == "http_fetch":
        # 外部网络不可作为测试依赖：打桩 requests.get（示例代码 import 的是同一模块）
        import requests

        class _FakeResponse:
            status_code = 200
            headers = {"content-type": "application/json"}
            text = '{"ok": true}'

            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True}

        monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _FakeResponse())
        params = {"url": "https://example.com/api"}

    # 与测试端点同款：独立连接 + 外部事务，commit 中性、退出回滚
    with script_runtime.script_test_session() as test_db:
        output = script_runtime.run_script(
            code=code,
            params=params,
            context={"testRun": True, "taskType": f"example.{key}"},
            timeout_seconds=30,
            db=test_db,
            owner_user_id=owner_id,
            task_type=f"example.{key}",
        )

    assert output["result"].get("summary"), f"示例 {key} 必须产出 summary"


def test_example_batch_drafts_rolls_back(client, owner_id):
    """batch_drafts 走真实建稿：验证回滚语义在写路径同样成立。"""
    from sqlalchemy import select

    from app import models
    from app.core.db import SessionLocal

    key, code = next(
        (item["key"], item["code"]) for item in EXAMPLES if item["key"] == "batch_drafts"
    )
    with script_runtime.script_test_session() as test_db:
        output = script_runtime.run_script(
            code=code,
            params={"titles": ["示例回滚稿件A", "示例回滚稿件B"]},
            context={"testRun": True},
            timeout_seconds=30,
            db=test_db,
            owner_user_id=owner_id,
            task_type=f"example.{key}",
        )

    assert output["result"]["created"] == ["示例回滚稿件A", "示例回滚稿件B"]
    with SessionLocal() as db:
        count = db.scalar(
            select(models.ContentItem.id).where(
                models.ContentItem.title == "示例回滚稿件A"
            )
        )
    assert count is None, "示例执行必须回滚，不能落库"
