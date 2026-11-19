"""大纲→正文联动：大纲不落稿件库、产物注入正文 prompt、字数读 manifest。"""

import json

from tests.conftest import login


def _enable_article_skill(client, headers):
    skills = client.get("/api/v1/skills", headers=headers).json()
    skill = next(s for s in skills if s["skillKey"] == "article-drafting")
    res = client.patch(
        f"/api/v1/skills/{skill['id']}",
        json={"enabled": True, "grantedToolIds": skill["allowedToolIds"]},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return skill


def _run(client, headers, skill, goal="写一篇关于时间管理的稿子"):
    session = client.post("/api/v1/agent/sessions", json={"title": "写作"}, headers=headers).json()
    return client.post(
        f"/api/v1/agent/sessions/{session['id']}/messages",
        json={"goal": goal, "skillIds": [skill["id"]], "maxRounds": 10},
        headers=headers,
    ).json()


def test_outline_feeds_article_and_no_extra_item(client, monkeypatch):
    from app.services import ai_gateway

    calls = []

    async def fake_generate_text(db, **kwargs):
        calls.append(kwargs)
        if kwargs.get("task_type") == "outline":
            text = "## 大纲\n- 开头：一个具体的拖延场景\n- 主体：三个可执行方法\n- 结尾：回到场景"
        else:
            text = json.dumps({
                "title": "时间管理", "markdown": "## 开头\n正文内容。" * 20,
                "summary": "摘要", "coverPrompt": "", "inlinePrompts": [],
            }, ensure_ascii=False)
        return {"text": text, "modelCode": "test-double", "provider": "test-double", "errorCode": None}

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text)
    headers = login(client, subject="outline-chain-user")
    skill = _enable_article_skill(client, headers)
    run = _run(client, headers, skill)
    assert run["status"] == "succeeded", run

    events = client.get(f"/api/v1/agent/runs/{run['id']}/events", headers=headers).json()
    tool_results = [e["payload"] for e in events if e["eventType"] == "tool.result"]
    outline_results = [p for p in tool_results if p["toolId"] == "create_outline"]
    assert outline_results and outline_results[0]["result"].get("outline")
    assert "contentItemId" not in outline_results[0]["result"]

    article_calls = [c for c in calls if c.get("task_type") == "article"]
    assert article_calls, "generate_article 应走 article 任务"
    assert "参考大纲" in article_calls[0]["prompt"]
    assert "一个具体的拖延场景" in article_calls[0]["prompt"]
    # manifest targetLength=1200 生效，不再硬编码 900
    assert article_calls[0]["constraints"]["targetLength"] == 1200

    # 大纲不再单独落一篇稿件：整个工作区只有 generate_article 建的那篇
    contents = client.get("/api/v1/content?fields=list&limit=50", headers=headers).json()
    assert len(contents) == 1