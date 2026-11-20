"""写作流程面板接口：逐条规则报告、词表概览、编辑器内容实时检查。

覆盖：
- GET 基于最新已保存版本，source=saved，审稿步骤附逐条规则报告；
- POST 携带编辑器正文（含未保存草稿），source=draft，不影响已保存版本；
- 个人副本规则覆盖全局后，逐条报告按覆盖后的规则输出；
- AI 味步骤附词表概览（来源=默认/自定义），配图步骤附生效的配图策略。
"""

from __future__ import annotations

from .conftest import login


def _create(client, headers, title="流程稿"):
    res = client.post("/api/v1/content", json={"title": title}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _save_version(client, headers, item_id, body):
    res = client.post(
        f"/api/v1/content/{item_id}/versions",
        json={"title": "流程稿", "body": body},
        headers=headers,
    )
    assert res.status_code in (200, 201), res.text


def _steps(flow):
    return {s["tool"]: s for s in flow["steps"]}


def _rule_ids(step):
    return {row["id"] for row in step["result"]["rules"]}


def test_flow_get_reports_each_review_rule(client):
    headers = login(client, subject="flow-user-a")
    item_id = _create(client, headers)
    _save_version(client, headers, item_id, "这是一篇长度达标的正文。" * 10)

    res = client.get(f"/api/v1/content/{item_id}/writing-flow", headers=headers)
    assert res.status_code == 200, res.text
    flow = res.json()
    assert flow["source"] == "saved"
    assert flow["versionId"]

    review = _steps(flow)["review_article"]
    assert review["kind"] == "check"
    assert review["result"]["ok"] is True
    # 出厂规则两条 + 内置占位检查一条，逐条列出（通过的也在）
    assert _rule_ids(review) == {"has-body", "no-absolute", "image-placeholders"}
    assert all(row["ok"] is True for row in review["result"]["rules"])

    tone = _steps(flow)["check_ai_tone"]
    lex = tone["result"]["lexicon"]
    # 出厂即把词表播种进规则表，custom 只说明来源是规则表还是代码内置，不断言具体值
    assert isinstance(lex["buzzwords"]["custom"], bool)
    assert lex["buzzwords"]["count"] >= 3
    assert lex["sentenceOpeners"]["count"] >= 1

    images = _steps(flow)["plan_images"]
    assert "policy" in images["result"]


def test_flow_post_checks_editor_body_not_saved_version(client):
    headers = login(client, subject="flow-user-b")
    item_id = _create(client, headers)
    _save_version(client, headers, item_id, "这是一篇长度达标的正文。" * 10)

    res = client.post(
        f"/api/v1/content/{item_id}/writing-flow",
        json={"body": "太短了", "title": "流程稿"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    flow = res.json()
    assert flow["source"] == "draft"
    assert flow["versionId"] is None

    review = _steps(flow)["review_article"]
    assert review["result"]["ok"] is False
    failing = [row for row in review["result"]["rules"] if row["id"] == "has-body"]
    assert failing and failing[0]["ok"] is False
    # 出厂规则自带提示文案，报告原样透传
    assert failing[0]["message"] == "正文过短"
    assert failing[0]["kind"] == "min_chars"

    # 已保存版本不受影响：GET 仍是通过的
    saved = client.get(f"/api/v1/content/{item_id}/writing-flow", headers=headers)
    assert saved.status_code == 200, saved.text
    assert _steps(saved.json())["review_article"]["result"]["ok"] is True


def test_flow_post_without_body_falls_back_to_saved(client):
    headers = login(client, subject="flow-user-c")
    item_id = _create(client, headers)
    _save_version(client, headers, item_id, "这是一篇长度达标的正文。" * 10)

    res = client.post(f"/api/v1/content/{item_id}/writing-flow", json={}, headers=headers)
    assert res.status_code == 200, res.text
    flow = res.json()
    assert flow["source"] == "saved"
    assert flow["versionId"]


def test_flow_personal_rule_override_reported(client):
    headers = login(client, subject="flow-user-d")
    item_id = _create(client, headers)
    _save_version(client, headers, item_id, "这是一篇长度达标的正文。" * 10)

    put = client.put(
        "/api/v1/writing-rules/skills/article-drafting/rules?scope=personal",
        headers=headers,
        json={
            "rules": [
                {
                    "ruleType": "review_rule",
                    "ruleKey": "long-body",
                    "payload": {
                        "id": "long-body",
                        "kind": "min_chars",
                        "value": 2000,
                        "field": "markdown",
                        "message": "正文太短",
                    },
                }
            ]
        },
    )
    assert put.status_code == 200, put.text

    body = "赋能" + "这是一篇中等长度的正文。" * 20  # 约 200 字：低于个人规则、高于出厂规则
    res = client.post(
        f"/api/v1/content/{item_id}/writing-flow", json={"body": body}, headers=headers
    )
    assert res.status_code == 200, res.text
    review = _steps(res.json())["review_article"]
    ids = {row["id"] for row in review["result"]["rules"]}
    # 个人副本整组覆盖出厂规则：只剩 long-body 与内置占位
    assert ids == {"long-body", "image-placeholders"}
    row = next(r for r in review["result"]["rules"] if r["id"] == "long-body")
    assert row["ok"] is False and row["message"] == "正文太短"

    tone = _steps(res.json())["check_ai_tone"]
    assert any(h["ruleId"] == "buzzword-enable" for h in tone["result"]["hits"])
