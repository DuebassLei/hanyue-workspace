"""接口集成测试：权限隔离、幂等、SSE、Agent 审批。"""

import io
import os
import tempfile

import pytest
from fastapi.testclient import TestClient


def login(client, subject="user-a"):
    res = client.post("/api/v1/auth/login", json={"subject": subject, "displayName": subject})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_login_returns_token_and_workspace(client):
    headers = login(client)
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    body = me.json()
    assert body["displayName"] == "user-a"
    # 账号面板要展示的身份信息：不靠前端猜，由服务端给出
    assert body["authSubject"] == "user-a"
    assert body["authProvider"] in {"local-dev", "idp"}
    assert body["createdAt"]


def test_update_me_changes_only_owned_fields(client):
    headers = login(client, "profile-owner")
    updated = client.patch(
        "/api/v1/auth/me",
        json={"displayName": "  新的名字  ", "timezone": "Asia/Tokyo"},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["displayName"] == "新的名字"
    assert body["timezone"] == "Asia/Tokyo"
    # 身份标识不可被请求体改写
    assert body["authSubject"] == "profile-owner"
    assert client.get("/api/v1/auth/me", headers=headers).json()["displayName"] == "新的名字"


def test_update_me_rejects_invalid_input(client):
    headers = login(client, "profile-invalid")
    blank = client.patch("/api/v1/auth/me", json={"displayName": "   "}, headers=headers)
    assert blank.status_code == 400
    assert blank.json()["code"] == "invalid_display_name"

    bad_tz = client.patch("/api/v1/auth/me", json={"timezone": "../etc"}, headers=headers)
    assert bad_tz.status_code == 400
    assert bad_tz.json()["code"] == "invalid_timezone"

    empty = client.patch("/api/v1/auth/me", json={}, headers=headers)
    assert empty.status_code == 400

    # 失败不改动已有资料
    assert client.get("/api/v1/auth/me", headers=headers).json()["timezone"] == "Asia/Shanghai"


def test_update_me_is_isolated_per_user(client):
    a = login(client, "iso-a")
    b = login(client, "iso-b")
    client.patch("/api/v1/auth/me", json={"displayName": "A 改名"}, headers=a)
    assert client.get("/api/v1/auth/me", headers=b).json()["displayName"] == "iso-b"
    assert client.patch("/api/v1/auth/me", json={"displayName": "x"}).status_code == 401


def test_requires_auth(client):
    assert client.get("/api/v1/content").status_code == 401


def test_topic_routes_removed(client):
    """选题库已下线：接口不再存在，热点灵感与 AI 写作仍需保留。"""
    headers = login(client, "topic-removed")
    assert client.post("/api/v1/topics", json={"title": "不再支持"}, headers=headers).status_code in (404, 405)
    assert client.get("/api/v1/topics", headers=headers).status_code in (404, 405)
    workspace = client.get("/api/v1/workspaces/current", headers=headers).json()
    assert client.get(
        f"/api/v1/workspaces/{workspace['id']}/topics", headers=headers
    ).status_code in (404, 405)
    assert client.post(
        "/api/v1/topics/whatever/ai-draft", json={}, headers=headers
    ).status_code in (404, 405)


def test_content_version_flow(client):
    headers = login(client)
    item = client.post("/api/v1/content", json={"title": "初稿"}, headers=headers).json()
    res = client.post(
        f"/api/v1/content/{item['id']}/versions",
        json={"title": "第二版", "body": "正文内容"},
        headers=headers,
    )
    assert res.status_code == 201
    assert res.json()["versionNo"] == 2
    detail = client.get(f"/api/v1/content/{item['id']}", headers=headers).json()
    assert detail["item"]["currentVersionId"] == res.json()["id"]
    assert len(detail["versions"]) == 2


def test_draft_autosave(client):
    """PATCH /content/:id/draft 更新草稿字段，不创建版本记录。"""
    headers = login(client, "draft-save")
    item = client.post("/api/v1/content", json={"title": "自动保存测试"}, headers=headers).json()
    # 新建时有 1 个版本
    detail_before = client.get(f"/api/v1/content/{item['id']}", headers=headers).json()
    version_count_before = len(detail_before["versions"])

    # 自动保存草稿
    patch = client.patch(
        f"/api/v1/content/{item['id']}/draft",
        json={"title": "自动保存标题", "body": "自动保存正文"},
        headers=headers,
    )
    assert patch.status_code == 200
    data = patch.json()
    assert data["draftTitle"] == "自动保存标题"
    assert data["draftBody"] == "自动保存正文"

    # 版本数不变
    detail_after = client.get(f"/api/v1/content/{item['id']}", headers=headers).json()
    assert len(detail_after["versions"]) == version_count_before

    # 草稿字段随稿件一起返回
    assert detail_after["item"]["draftTitle"] == "自动保存标题"
    assert detail_after["item"]["draftBody"] == "自动保存正文"


def test_draft_autosave_requires_owner(client):
    """不能修改他人草稿。"""
    a = login(client, "draft-a")
    b = login(client, "draft-b")
    item = client.post("/api/v1/content", json={"title": "A 的草稿"}, headers=a).json()
    res = client.patch(
        f"/api/v1/content/{item['id']}/draft",
        json={"body": "B 想改"},
        headers=b,
    )
    assert res.status_code == 404


def test_delete_content_cascades(client):
    headers = login(client, "delete-owner")
    item = client.post("/api/v1/content", json={"title": "待删除"}, headers=headers).json()
    client.post(
        f"/api/v1/content/{item['id']}/versions",
        json={"title": "第二版", "body": "正文"},
        headers=headers,
    )
    variant = client.post(
        f"/api/v1/content/{item['id']}/platform-variants",
        json={"platform": "wechat_official", "title": "平台版本", "body": "正文"},
        headers=headers,
    )
    assert variant.status_code == 201

    res = client.delete(f"/api/v1/content/{item['id']}", headers=headers)
    assert res.status_code == 204
    assert client.get(f"/api/v1/content/{item['id']}", headers=headers).status_code == 404
    listed = client.get("/api/v1/content", headers=headers).json()
    assert all(row["id"] != item["id"] for row in listed)


def test_builtin_writing_styles_seeded(client):
    headers = login(client, "style-seed")
    skills = client.get("/api/v1/skills?limit=100", headers=headers).json()
    styles = [s for s in skills if s["skillKey"].startswith("style-")]
    # 与 builtin_skills.yaml 的 style- 条目数对齐；新增内置写作风格时同步改这里
    assert len(styles) == 11
    assert all(s["source"] == "builtin" for s in styles)
    assert all("defaultTheme" not in s["manifest"] for s in styles)
    for style in styles:
        manifest = style["manifest"]
        assert manifest["tone"]
        assert manifest["styleRules"]
        assert manifest["promptTemplate"]


def test_delete_content_requires_owner(client):
    a = login(client, "delete-a")
    b = login(client, "delete-b")
    item = client.post("/api/v1/content", json={"title": "A 的稿件"}, headers=a).json()
    assert client.delete(f"/api/v1/content/{item['id']}", headers=b).status_code == 404


def test_delete_content_cleans_downstream(client):
    headers = login(client, "delete-downstream")
    item = client.post("/api/v1/content", json={"title": "下游清理"}, headers=headers).json()
    variant = client.post(
        f"/api/v1/content/{item['id']}/platform-variants",
        json={"platform": "wechat_official", "title": "平台版本", "body": "正文"},
        headers=headers,
    ).json()
    account = client.post(
        "/api/v1/platform-accounts", json={"platform": "wechat_official"}, headers=headers
    ).json()
    job = client.post(
        "/api/v1/publishing/jobs",
        json={
            "accountId": account["id"],
            "idempotencyKey": "delete-downstream-1",
            "title": "平台版本",
            "body": "正文",
            "variantId": variant["id"],
            "reviewed": True,
            "mode": "manual",
        },
        headers=headers,
    )
    assert job.status_code == 201, job.text
    job_id = job.json()["id"]
    recorded = client.post(
        f"/api/v1/publishing/jobs/{job_id}/record-manual-result",
        json={"remoteContentId": "remote-del-1"},
        headers=headers,
    )
    assert recorded.status_code == 200, recorded.text
    entry = client.post(
        "/api/v1/calendar",
        json={
            "title": "排期",
            "plannedAt": "2026-11-10T10:00:00+08:00",
            "contentItemId": item["id"],
        },
        headers=headers,
    )
    assert entry.status_code == 201, entry.text

    assert client.delete(f"/api/v1/content/{item['id']}", headers=headers).status_code == 204

    assert client.get("/api/v1/publishing/jobs", headers=headers).json() == []
    assert client.get("/api/v1/calendar", headers=headers).json() == []


def test_text_generation_sse(client):
    headers = login(client)
    with client.stream(
        "POST",
        "/api/v1/ai/text-generations",
        json={"taskType": "article", "prompt": "写一段关于选题的内容", "constraints": {"targetLength": 120}},
        headers=headers,
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    # 没有配置模型连接时必须明确报错，不得用本地占位文本冒充 AI 产出
    assert "generation.started" in body
    assert "generation.error" in body
    assert "provider_unavailable" in body
    assert "generation.delta" not in body
    assert "generation.done" not in body


def test_publish_requires_review_and_capability(client):
    headers = login(client)
    account = client.post(
        "/api/v1/platform-accounts", json={"platform": "wechat_official"}, headers=headers
    ).json()
    assert account["status"] == "pending_authorization"

    job = client.post(
        "/api/v1/publishing/jobs",
        json={
            "accountId": account["id"],
            "idempotencyKey": "key-000001",
            "title": "标题",
            "body": "正文",
            "reviewed": True,
        },
        headers=headers,
    )
    # 公众号自动群发未接入（publish=False）→ 拒绝自动发布，不伪造成功
    assert job.status_code == 409
    assert job.json()["code"] == "auto_publish_unavailable"

    unauthorized_review = client.post(
        "/api/v1/publishing/jobs",
        json={
            "accountId": account["id"],
            "idempotencyKey": "key-000002",
            "title": "标题",
            "body": "正文",
            "reviewed": False,
        },
        headers=headers,
    )
    assert unauthorized_review.status_code == 409


def test_idempotent_publish_job(client):
    """能力核验后同键重复提交只应产生一条任务。"""
    headers = login(client)
    account = client.post(
        "/api/v1/platform-accounts", json={"platform": "wechat_official"}, headers=headers
    ).json()

    from app import models
    from app.connectors import base as connectors
    from app.core.db import SessionLocal

    cap = connectors.CAPABILITIES["wechat_official"]
    original = (cap.confirmed, cap.publish)
    object.__setattr__(cap, "confirmed", True)
    object.__setattr__(cap, "publish", True)
    try:
        # 没有演示授权端点：直接把账号置为已连接（app_secret 凭据型）
        with SessionLocal() as db:
            row = db.get(models.ConnectedAccount, account["id"])
            row.status = "connected"
            row.auth_source = "app_secret"
            db.commit()
        client.post(
            f"/api/v1/platform-accounts/{account['id']}/toggle-auto-publish?enabled=true",
            headers=headers,
        )
        payload = {
            "accountId": account["id"],
            "idempotencyKey": "idem-abc-123",
            "title": "标题",
            "body": "正文",
            "reviewed": True,
        }
        first = client.post("/api/v1/publishing/jobs", json=payload, headers=headers)
        second = client.post("/api/v1/publishing/jobs", json=payload, headers=headers)
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
    finally:
        object.__setattr__(cap, "confirmed", original[0])
        object.__setattr__(cap, "publish", original[1])


def test_agent_requires_enabled_skill(client):
    headers = login(client)
    skills = client.get("/api/v1/skills", headers=headers).json()
    skill = next(s for s in skills if s["skillKey"] == "article-drafting")
    assert skill["enabled"] is False

    session = client.post("/api/v1/agent/sessions", json={"title": "测试"}, headers=headers).json()
    run = client.post(
        f"/api/v1/agent/sessions/{session['id']}/messages",
        json={"goal": "写一篇关于时间管理的稿子", "skillIds": [skill["id"]]},
        headers=headers,
    ).json()
    # Skill 未启用 → workflow 授权校验整次中止（workflow_unauthorized），不执行任何步骤、不产出草稿
    events = client.get(f"/api/v1/agent/runs/{run['id']}/events", headers=headers).json()
    joined = str(events)
    assert "workflow_unauthorized" in joined
    assert not any(event.get("type") == "step.started" for event in events)


def test_agent_run_with_enabled_skill_creates_draft(client):
    headers = login(client)
    skills = client.get("/api/v1/skills", headers=headers).json()
    skill = next(s for s in skills if s["skillKey"] == "article-drafting")
    enabled = client.patch(
        f"/api/v1/skills/{skill['id']}",
        json={"enabled": True, "grantedToolIds": skill["allowedToolIds"]},
        headers=headers,
    )
    assert enabled.json()["enabled"] is True

    session = client.post("/api/v1/agent/sessions", json={"title": "写作"}, headers=headers).json()
    run = client.post(
        f"/api/v1/agent/sessions/{session['id']}/messages",
        json={"goal": "写一篇关于时间管理的稿子", "skillIds": [skill["id"]], "maxRounds": 5},
        headers=headers,
    ).json()
    events = client.get(f"/api/v1/agent/runs/{events_run(run)}/events", headers=headers).json()
    assert any(e["eventType"] == "run.started" for e in events)
    assert any(e["eventType"] == "tool.result" for e in events)

    detail = client.get(f"/api/v1/agent/runs/{run['id']}", headers=headers).json()
    assert detail["status"] in ("succeeded", "awaiting_approval")
    # 选题工具已下线，运行事件里不应再出现选题写入
    events_text = str(client.get(f"/api/v1/agent/runs/{run['id']}/events", headers=headers).json())
    assert "create_topic" not in events_text and "search_topics" not in events_text


def events_run(run):
    return run["id"]


def _run_agent_with_article_json(client, monkeypatch, subject: str, skill_key: str = "article-drafting"):
    """启用内置 Skill 跑一轮 Agent，模型替身返回契约 JSON；返回 (markdown, JSON, 调用参数, 稿件版本, Skill)。"""
    import json

    from sqlalchemy import select

    from app import models
    from app.core.db import SessionLocal
    from app.services import ai_gateway

    headers = login(client, subject)
    skills = client.get("/api/v1/skills?limit=100", headers=headers).json()
    skill = next(s for s in skills if s["skillKey"] == skill_key)
    enabled = client.patch(
        f"/api/v1/skills/{skill['id']}",
        json={"enabled": True, "grantedToolIds": skill["allowedToolIds"]},
        headers=headers,
    )
    assert enabled.status_code == 200, enabled.text

    markdown = "## 时间管理\n\n- 先做最重要的事\n\n> 记录时间流向"
    payload = {
        "title": "时间管理的三个习惯",
        "markdown": markdown,
        "summary": "从记录、排序、复盘三步讲清时间管理。",
        "coverPrompt": "a quiet desk with a clock, warm light",
        "inlinePrompts": ["notebook on a desk"],
    }
    calls: list[dict] = []

    async def fake_generate_text(db, **kwargs):
        calls.append(kwargs)
        return {
            "text": json.dumps(payload, ensure_ascii=False),
            "modelCode": "test-model",
            "provider": "test-double",
            "errorCode": None,
        }

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text)
    from app.services import agent_runtime

    # 网关模块可能被重新导入成另一个对象，运行器实际调用的入口也一并替换
    monkeypatch.setattr(agent_runtime.ai_gateway, "generate_text", fake_generate_text)

    session = client.post("/api/v1/agent/sessions", json={"title": "写作"}, headers=headers).json()
    run = client.post(
        f"/api/v1/agent/sessions/{session['id']}/messages",
        json={"goal": "写一篇关于时间管理的稿子", "skillIds": [skill["id"]], "maxRounds": 5},
        headers=headers,
    ).json()
    assert run["status"] == "succeeded", run

    with SessionLocal() as db:
        # 大纲不落稿（WIP 新语义）：只有 generate_article 建稿，按 tool.result 的 versionId 精确取值
        events = client.get(f"/api/v1/agent/runs/{run['id']}/events", headers=headers).json()
        results = {
            e["payload"]["toolId"]: e["payload"]["result"]
            for e in events
            if e["eventType"] == "tool.result"
        }
        article = results["generate_article"]
        version = db.get(models.ContentVersion, article["versionId"])
        assert version.content_item_id == article["contentItemId"]
        outline = results.get("create_outline")
        if outline:
            assert "versionId" not in outline, "大纲只是中间产物，不应落稿件版本"
            assert outline.get("outline"), "大纲文本应随 tool.result 返回，供成文步骤注入"
        entry = db.scalar(
            select(models.SkillRegistryEntry).where(
                models.SkillRegistryEntry.skill_key == skill_key,
                models.SkillRegistryEntry.owner_user_id.is_(None),
            )
        )
        return markdown, payload, calls, version, entry


def test_agent_generate_article_writes_markdown_not_json(client, monkeypatch):
    markdown, payload, calls, version, entry = _run_agent_with_article_json(
        client, monkeypatch, "agent-article-json"
    )
    assert version.body == markdown
    assert "coverPrompt" not in version.body
    assert version.title == payload["title"]
    assert version.summary == payload["summary"]
    assert version.prompt_template_version == f"{entry.skill_key}@{entry.version}"[:32]
    checks = [c for c in calls if c.get("task_type") == "article" and "system_prompt" in c]
    assert checks, calls


def test_agent_generate_article_passes_skill_system_prompt(client, monkeypatch):
    markdown, _, calls, _, entry = _run_agent_with_article_json(
        client, monkeypatch, "agent-article-skill"
    )
    tone = entry.manifest["tone"]
    assert markdown
    # 大纲步骤用的是大纲系统提示词（无语气段），只验文章任务的 system_prompt
    article_calls = [c for c in calls if c.get("task_type") == "article"]
    assert article_calls, calls
    for call in article_calls:
        assert call["system_prompt"] and tone in call["system_prompt"], call


def test_skill_cannot_grant_tool_outside_manifest(client):
    headers = login(client)
    skills = client.get("/api/v1/skills", headers=headers).json()
    skill = next(s for s in skills if s["skillKey"] == "topic-research")
    # 选题工具已下线，授予已登记但 Skill 未声明的工具仍然必须拒绝
    res = client.patch(
        f"/api/v1/skills/{skill['id']}",
        json={"enabled": True, "grantedToolIds": ["publish_content"]},
        headers=headers,
    )
    assert res.status_code == 409


def test_customize_builtin_style_creates_personal_fork(client):
    """定制内置写作风格：生成个人副本并替换内置展示，删除副本恢复默认。"""
    headers = login(client)
    skills = client.get("/api/v1/skills", headers=headers).json()
    builtin = next(s for s in skills if s["skillKey"] == "style-tech-article")
    assert builtin["source"] == "builtin"
    assert builtin["customized"] is False

    # 内置 Skill 不允许直接 PUT 编辑
    denied = client.put(
        f"/api/v1/skills/{builtin['id']}",
        json={"manifest": {"tone": "改不动"}},
        headers=headers,
    )
    assert denied.status_code == 403

    # customize 创建个人副本
    forked = client.post(
        f"/api/v1/skills/{builtin['id']}/customize",
        json={
            "displayName": "科技数码·实测派（我的版本）",
            "manifest": {"tone": "更口语化"},
        },
        headers=headers,
    )
    assert forked.status_code == 201, forked.text
    fork = forked.json()
    assert fork["source"] == "custom"
    assert fork["customized"] is True
    assert fork["skillKey"] == "style-tech-article"
    assert fork["manifest"]["tone"] == "更口语化"

    # 列表中内置原版被副本替换，仅展示个人版本
    skills = client.get("/api/v1/skills", headers=headers).json()
    same_key = [s for s in skills if s["skillKey"] == "style-tech-article"]
    assert len(same_key) == 1
    assert same_key[0]["id"] == fork["id"]
    assert same_key[0]["customized"] is True

    # 副本可通过 PUT 继续修改
    updated = client.put(
        f"/api/v1/skills/{fork['id']}",
        json={"manifest": {"tone": "再口语一点"}},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["manifest"]["tone"] == "再口语一点"

    # 删除副本后恢复内置默认
    deleted = client.delete(f"/api/v1/skills/{fork['id']}", headers=headers)
    assert deleted.status_code == 204
    skills = client.get("/api/v1/skills", headers=headers).json()
    same_key = [s for s in skills if s["skillKey"] == "style-tech-article"]
    assert len(same_key) == 1
    assert same_key[0]["source"] == "builtin"
    assert same_key[0]["customized"] is False


def test_customize_style_isolated_per_user(client):
    """定制副本按用户隔离：A 的定制不影响 B 看到的内置原版。"""
    headers_a = login(client, "user-a")
    skills_a = client.get("/api/v1/skills", headers=headers_a).json()
    builtin = next(s for s in skills_a if s["skillKey"] == "style-finance-analysis")

    forked = client.post(
        f"/api/v1/skills/{builtin['id']}/customize",
        json={"manifest": {"tone": "A 的语气"}},
        headers=headers_a,
    )
    assert forked.status_code == 201

    # 用户 B 仍看到内置原版
    headers_b = login(client, "user-b")
    skills_b = client.get("/api/v1/skills", headers=headers_b).json()
    for_b = [s for s in skills_b if s["skillKey"] == "style-finance-analysis"]
    assert len(for_b) == 1
    assert for_b[0]["source"] == "builtin"
    assert for_b[0]["manifest"]["tone"] != "A 的语气"

    # B 不能删除 A 的副本
    denied = client.delete(f"/api/v1/skills/{forked.json()['id']}", headers=headers_b)
    assert denied.status_code == 404

    # 已有副本时不能重复定制
    skills_a = client.get("/api/v1/skills", headers=headers_a).json()
    mine = next(s for s in skills_a if s["skillKey"] == "style-finance-analysis")
    again = client.post(
        f"/api/v1/skills/{mine['id']}/customize",
        json={"manifest": {}},
        headers=headers_a,
    )
    assert again.status_code == 409


def test_publish_node_approval_flow(client):
    headers = login(client)
    skills = client.get("/api/v1/skills", headers=headers).json()
    skill = next(s for s in skills if s["skillKey"] == "publish-prep")
    client.patch(
        f"/api/v1/skills/{skill['id']}",
        json={
            "enabled": True,
            "grantedToolIds": ["prepare_platform_variant", "create_publish_preview"],
        },
        headers=headers,
    )
    session = client.post("/api/v1/agent/sessions", json={"title": "发布准备"}, headers=headers).json()
    run = client.post(
        f"/api/v1/agent/sessions/{session['id']}/messages",
        json={"goal": "准备发布预览", "skillIds": [skill["id"]]},
        headers=headers,
    ).json()
    approvals = client.get(f"/api/v1/agent/runs/{run['id']}/approvals", headers=headers).json()
    # 发布准备类工具属于可撤回写入，默认不强制审批
    assert all(a["riskLevel"] != "forbidden" for a in approvals)


def test_asset_upload_rejects_non_image(client):
    headers = login(client)
    res = client.post(
        "/api/v1/assets",
        files={"file": ("note.txt", io.BytesIO(b"hello"), "text/plain")},
        headers=headers,
    )
    assert res.status_code == 400
    assert res.json()["code"] == "unsupported_type"


def test_asset_upload_and_image_job(client):
    from PIL import Image

    headers = login(client)
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), (80, 120, 100)).save(buffer, format="PNG")
    buffer.seek(0)
    res = client.post(
        "/api/v1/assets",
        files={"file": ("cover.png", buffer, "image/png")},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    asset = res.json()
    assert asset["width"] == 400 and asset["height"] == 300

    job = client.post(
        "/api/v1/image-jobs",
        json={"assetId": asset["id"], "operation": "thumbnail", "options": {"maxSize": 200}},
        headers=headers,
    )
    assert job.status_code == 201
    assert job.json()["status"] == "succeeded"
    assert job.json()["outputAssetIds"]

    # 原图保留，派生图独立
    still_there = client.get(f"/api/v1/assets/{asset['id']}/raw", headers=headers)
    assert still_there.status_code == 200


def test_asset_raw_inline_mode_opens_in_browser(client):
    """原图直链默认下载；inline=1 时应在新标签直接显示，而不是又下载一次。"""
    from PIL import Image

    headers = login(client, "asset-inline")
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), (10, 20, 30)).save(buffer, format="PNG")
    buffer.seek(0)
    res = client.post(
        "/api/v1/assets",
        files={"file": ("shot.png", buffer, "image/png")},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    url = res.json()["url"]

    download = client.get(url)
    assert download.status_code == 200
    assert "attachment" in download.headers.get("content-disposition", "")

    inline = client.get(f"{url}&inline=1")
    assert inline.status_code == 200
    assert inline.headers.get("content-type", "").startswith("image/")
    assert "attachment" not in inline.headers.get("content-disposition", "")


def test_asset_search_by_keyword(client):
    from PIL import Image

    headers = login(client, "user-asset-search")
    for name in ("summer-cover.png", "winter-photo.png"):
        buffer = io.BytesIO()
        Image.new("RGB", (40, 30), (90, 120, 90)).save(buffer, format="PNG")
        buffer.seek(0)
        res = client.post(
            "/api/v1/assets",
            files={"file": (name, buffer, "image/png")},
            headers=headers,
        )
        assert res.status_code == 201, res.text

    assert len(client.get("/api/v1/assets", headers=headers).json()) == 2

    matched = client.get("/api/v1/assets?q=summer", headers=headers).json()
    assert [a["originalName"] for a in matched] == ["summer-cover.png"]

    # 关键字大小写不敏感
    assert len(client.get("/api/v1/assets?q=WINTER", headers=headers).json()) == 1

    # LIKE 通配符按字面匹配，不能把全部素材都返回
    assert client.get("/api/v1/assets?q=%25", headers=headers).json() == []


def test_asset_filter_by_source_format_and_days(client):
    from PIL import Image

    headers = login(client, "user-asset-filter")
    uploads = [("hero.png", "image/png", "PNG"), ("hero.webp", "image/webp", "WEBP")]
    for name, mime, fmt in uploads:
        buffer = io.BytesIO()
        Image.new("RGB", (30, 20), (70, 110, 90)).save(buffer, format=fmt)
        buffer.seek(0)
        res = client.post(
            "/api/v1/assets",
            files={"file": (name, buffer, mime)},
            headers=headers,
        )
        assert res.status_code == 201, res.text

    assert len(client.get("/api/v1/assets", headers=headers).json()) == 2

    png_only = client.get("/api/v1/assets?format=png", headers=headers).json()
    assert [a["originalName"] for a in png_only] == ["hero.png"]

    # 手动上传的 sourceType 为 upload
    uploaded = client.get("/api/v1/assets?sourceType=upload", headers=headers).json()
    assert len(uploaded) == 2
    assert client.get("/api/v1/assets?sourceType=derived", headers=headers).json() == []

    # 时间筛选：刚上传的素材落在近 7 天内
    assert len(client.get("/api/v1/assets?days=7", headers=headers).json()) == 2

    # 组合筛选：关键字 + 格式
    combined = client.get("/api/v1/assets?q=hero&format=webp", headers=headers).json()
    assert [a["originalName"] for a in combined] == ["hero.webp"]

    # 非法天数应被参数校验拦截
    assert client.get("/api/v1/assets?days=0", headers=headers).status_code == 422


def test_asset_filter_by_media_type(client):
    from PIL import Image

    from app import models
    from app.core.db import SessionLocal

    logged_in = client.post(
        "/api/v1/auth/login",
        json={"subject": "user-asset-media", "displayName": "media"},
    )
    assert logged_in.status_code == 200, logged_in.text
    body = logged_in.json()
    headers = {"Authorization": f"Bearer {body['token']}"}

    buffer = io.BytesIO()
    Image.new("RGB", (30, 20), (70, 110, 90)).save(buffer, format="PNG")
    buffer.seek(0)
    uploaded = client.post(
        "/api/v1/assets",
        files={"file": ("cover.png", buffer, "image/png")},
        headers=headers,
    )
    assert uploaded.status_code == 201, uploaded.text

    with SessionLocal() as db:
        image = db.get(models.Asset, uploaded.json()["id"])
        db.add_all(
            [
                models.Asset(
                    owner_user_id=image.owner_user_id,
                    workspace_id=image.workspace_id,
                    storage_key="media/clip.mp4",
                    original_name="clip.mp4",
                    mime_type="video/mp4",
                    source_type="video_render",
                ),
                models.Asset(
                    owner_user_id=image.owner_user_id,
                    workspace_id=image.workspace_id,
                    storage_key="media/voice.mp3",
                    original_name="voice.mp3",
                    mime_type="audio/mpeg",
                    source_type="generated",
                ),
            ]
        )
        db.commit()

    images = client.get("/api/v1/assets?mediaType=image", headers=headers).json()
    videos = client.get("/api/v1/assets?mediaType=video", headers=headers).json()
    audios = client.get("/api/v1/assets?mediaType=audio", headers=headers).json()
    assert [item["originalName"] for item in images] == ["cover.png"]
    assert [item["originalName"] for item in videos] == ["clip.mp4"]
    assert [item["originalName"] for item in audios] == ["voice.mp3"]
    # 素材选择弹窗用 format=image / format=audio 表示媒体大类
    assert [item["originalName"] for item in client.get("/api/v1/assets?format=audio", headers=headers).json()] == ["voice.mp3"]
    assert client.get("/api/v1/assets?mediaType=pdf", headers=headers).status_code == 422


def test_dashboard_shape(client):
    headers = login(client)
    data = client.get("/api/v1/dashboard", headers=headers).json()
    for key in ("publishProgress", "pendingReview", "failedJobs", "platformStatus", "recentContent"):
        assert key in data
    assert "topicBacklog" not in data, "选题库已下线，总览不应再返回选题库存"
    assert isinstance(data["activity"], list)


def test_platform_capabilities_lists_catalog_with_wechat_only(client):
    """渠道目录含占位平台，但只有微信公众号 available=True。"""
    headers = login(client)
    res = client.get("/api/v1/platforms/capabilities", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["platforms"]) >= 1
    first = body["platforms"][0]
    assert first["platform"] == "wechat_official"
    for key in ("platform", "label", "confirmed", "publish"):
        assert key in first
    assert [p for p in body["platforms"] if p.get("available")] == [first]


def test_manual_publish_mode_schedules_without_platform_call(client):
    """平台能力未核验时，manual 模式只做排期与记录，不调用平台接口。"""
    headers = login(client, "user-manual")
    acc = client.post(
        "/api/v1/platform-accounts",
        json={"platform": "wechat_official", "displayName": "手动账号"},
        headers=headers,
    ).json()

    res = client.post(
        "/api/v1/publishing/jobs",
        json={
            "accountId": acc["id"],
            "idempotencyKey": "manual-key-1",
            "title": "一周三篇图文",
            "body": "正文内容",
            "reviewed": True,
            "mode": "manual",
            "scheduledAt": "2099-11-10T10:00:00Z",
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    job = res.json()
    assert job["status"] == "queued"
    assert job["remoteContentId"] is None

    # 回填手发结果
    recorded = client.post(
        f"/api/v1/publishing/jobs/{job['id']}/record-manual-result",
        json={"remoteContentId": "note-123", "permalink": "https://example.com/p/1"},
        headers=headers,
    )
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["status"] == "succeeded"
    assert recorded.json()["remoteContentId"] == "note-123"


def test_manual_mode_still_requires_review(client):
    headers = login(client, "user-manual2")
    acc = client.post(
        "/api/v1/platform-accounts",
        json={"platform": "wechat_official", "displayName": "公众号账号"},
        headers=headers,
    ).json()
    res = client.post(
        "/api/v1/publishing/jobs",
        json={
            "accountId": acc["id"],
            "idempotencyKey": "manual-key-2",
            "title": "标题",
            "body": "正文",
            "reviewed": False,
            "mode": "manual",
        },
        headers=headers,
    )
    assert res.status_code == 409
    assert res.json()["code"] == "review_required"


def test_auto_mode_blocked_when_capability_unconfirmed(client):
    """公众号能力已核验，但自动群发未接入（publish=False），不得自动发布。"""
    headers = login(client, "user-auto")
    acc = client.post(
        "/api/v1/platform-accounts",
        json={"platform": "wechat_official", "displayName": "自动账号"},
        headers=headers,
    ).json()
    res = client.post(
        "/api/v1/publishing/jobs",
        json={
            "accountId": acc["id"],
            "idempotencyKey": "auto-key-1",
            "title": "标题",
            "body": "正文",
            "reviewed": True,
            "mode": "auto",
        },
        headers=headers,
    )
    assert res.status_code == 409
    assert res.json()["code"] == "auto_publish_unavailable"
    assert res.json()["retryable"] is False


def test_agent_policy_exposes_effective_risk_groups(client):
    """设置页展示的权限分级必须来自服务端真实策略，而不是前端另写一份文案。"""
    headers = login(client, "policy-user")
    res = client.get("/api/v1/agent/policy", headers=headers)
    assert res.status_code == 200
    groups = {g["risk"]: g for g in res.json()["groups"]}

    # 本机文件/命令/任意 SQL 必须是禁止级，且不会被标成「需批准」而给人可放行的错觉
    forbidden = [t["toolId"] for t in groups["forbidden"]["tools"]]
    assert {"run_shell", "raw_sql", "read_local_files"} <= set(forbidden)
    assert groups["forbidden"]["requiresApproval"] is False

    # 发布、解绑账号、改凭据属于外部副作用，必须逐次人工批准
    side_effects = {t["toolId"] for t in groups["external_side_effect"]["tools"]}
    assert {"publish_content", "unbind_platform_account", "update_model_credential"} <= side_effects
    assert groups["external_side_effect"]["requiresApproval"] is True

    assert all(t["toolId"] for g in groups.values() for t in g["tools"])


def test_connection_can_be_edited_and_keeps_credential(client):
    headers = login(client, "conn-edit-user")
    created = client.post(
        "/api/v1/models/connections",
        json={
            "displayName": "初版名称",
            "providerType": "openai_compatible",
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-secret-123",
        },
        headers=headers,
    ).json()
    masked_before = created["keyMasked"]

    # 只改名称与供应商类型，不传 apiKey：密钥必须原样保留
    updated = client.patch(
        f"/api/v1/models/connections/{created['id']}",
        json={"displayName": "改名后", "providerType": "vendor_native"},
        headers=headers,
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["displayName"] == "改名后"
    assert body["providerType"] == "vendor_native"
    assert body["keyMasked"] == masked_before


def test_connection_edit_validates_name_and_base_url(client):
    headers = login(client, "conn-validate-user")
    conn = client.post(
        "/api/v1/models/connections",
        json={"displayName": "校验用", "baseUrl": "https://api.example.com/v1"},
        headers=headers,
    ).json()

    blank = client.patch(
        f"/api/v1/models/connections/{conn['id']}",
        json={"displayName": "   "},
        headers=headers,
    )
    assert blank.status_code == 422 or blank.json().get("code") == "invalid_display_name"

    insecure = client.patch(
        f"/api/v1/models/connections/{conn['id']}",
        json={"baseUrl": "http://insecure.example.com"},
        headers=headers,
    )
    assert insecure.json().get("code") == "invalid_base_url"


def test_model_code_can_be_renamed(client):
    headers = login(client, "catalog-user")
    conn = client.post(
        "/api/v1/models/connections",
        json={"displayName": "目录用", "apiKey": "sk-x"},
        headers=headers,
    ).json()
    entry = client.post(
        "/api/v1/models/catalog",
        json={"connectionId": conn["id"], "modelCode": "old-model"},
        headers=headers,
    ).json()

    renamed = client.patch(
        f"/api/v1/models/catalog/{entry['id']}", json={"modelCode": "new-model"}, headers=headers
    )
    assert renamed.status_code == 200
    assert renamed.json()["modelCode"] == "new-model"

    # 连接详情里应能看到改名后的模型
    listed = client.get("/api/v1/models/connections", headers=headers).json()
    assert [m["modelCode"] for c in listed if c["id"] == conn["id"] for m in c["models"]] == [
        "new-model"
    ]


def test_model_edit_is_isolated_between_users(client):
    from tests.conftest import login as login_with_role

    owner = login(client, "catalog-owner")
    other = login(client, "catalog-other")
    # 本文件的 login 不指定角色（默认被夹具提权为超管）；这里要显式建一个普通成员
    member = login_with_role(client, subject="catalog-member", role="member")
    conn = client.post(
        "/api/v1/models/connections", json={"displayName": "私有连接"}, headers=owner
    ).json()
    entry = client.post(
        "/api/v1/models/catalog",
        json={"connectionId": conn["id"], "modelCode": "m1"},
        headers=owner,
    ).json()
    url = f"/api/v1/models/catalog/{entry['id']}"

    # 普通成员没有 models.manage：连目录写接口都进不去
    assert client.patch(url, json={"modelCode": "hacked"}, headers=member).status_code == 403

    # 超管能跨用户管理（设计如此），但默认必须被挡下，要求显式确认并留审计
    denied = client.patch(url, json={"modelCode": "hacked"}, headers=other)
    assert denied.status_code == 409
    assert denied.json()["code"] == "cross_user_confirmation_required"
    assert client.delete(url, headers=other).status_code == 409

    confirmed = client.patch(
        url, json={"modelCode": "hacked"}, headers=other,
        params={"confirmCrossUser": "true"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["modelCode"] == "hacked"


def test_agent_status_reports_mode_and_real_paths(client):
    """设置页要能看出 Agent 能不能干活、数据落在哪，这些必须来自运行时真实探测。"""
    headers = login(client, "status-user")
    res = client.get("/api/v1/agent/status", headers=headers)
    assert res.status_code == 200
    data = res.json()

    # 新用户没有模型连接 → 本地演练，且明确说明不是真实模型产出
    assert data["mode"]["code"] == "local"
    assert "本地" in data["mode"]["detail"]
    assert data["model"] is None

    kinds = {p["kind"]: p for p in data["paths"]}
    assert {"database", "storage", "config", "skills"} <= set(kinds)
    assert kinds["database"]["value"].endswith(".db")
    assert kinds["database"]["exists"] is True
    assert kinds["storage"]["exists"] is True
    # 内置 Skill 清单随代码发布，界面不能暗示它可以原地修改
    assert kinds["skills"]["writable"] is False

    assert data["skills"]["total"] >= 3
    assert data["runs"]["pendingApprovals"] == 0
    assert data["workspace"]["id"]


def test_agent_status_stays_degraded_until_connection_verified(client):
    headers = login(client, "status-ready")
    conn = client.post(
        "/api/v1/models/connections",
        json={
            "displayName": "主力连接",
            "providerType": "openai_compatible",
            "baseUrl": "https://api.example.com/v1",
            "apiKey": "sk-test",
        },
        headers=headers,
    ).json()
    client.post(
        "/api/v1/models/catalog",
        json={"connectionId": conn["id"], "modelCode": "test-model"},
        headers=headers,
    )

    data = client.get("/api/v1/agent/status", headers=headers).json()
    # 配置齐全但未通过「测试连接」时不能宣称就绪
    assert data["mode"]["code"] == "degraded"
    assert "连通性" in data["mode"]["detail"]
    assert data["model"]["displayName"] == "主力连接"
    assert data["model"]["modelCode"] == "test-model"


def test_agent_status_flags_connection_missing_credential(client):
    """有连接但缺密钥时必须报降级，而不是显示成可用，否则用户会以为在用真实模型。"""
    headers = login(client, "status-degraded")
    client.post(
        "/api/v1/models/connections",
        json={"displayName": "缺密钥", "baseUrl": "https://api.example.com/v1"},
        headers=headers,
    )

    data = client.get("/api/v1/agent/status", headers=headers).json()
    assert data["mode"]["code"] == "degraded"
    assert "API Key" in data["mode"]["detail"]

# ---------------- 发布中心重构 P1：筛选 / 汇总 / 详情 / 改期 / 审批 / 重试 ----------------


def _seed_job(
    job_id: str,
    *,
    status: str | None = None,
    payload_patch: dict | None = None,
    history: list[dict] | None = None,
):
    """直改库里的任务状态，模拟非 API 入口产生的状态（pending_review、执行失败等）。"""
    from app import models
    from app.core.db import SessionLocal

    with SessionLocal() as db:
        job = db.get(models.PublishJob, job_id)
        if status is not None:
            job.status = status
            job.status_history = []  # 由调用方决定是否传入初始历史
        if payload_patch:
            job.payload_snapshot = {**(job.payload_snapshot or {}), **payload_patch}
        if history is not None:
            job.status_history = history
        db.commit()


def _create_manual_job(client, headers, subject, key, title, scheduled=None):
    account = client.post(
        "/api/v1/platform-accounts", json={"platform": "wechat_official"}, headers=headers
    ).json()
    res = client.post(
        "/api/v1/publishing/jobs",
        json={
            "accountId": account["id"],
            "idempotencyKey": key,
            "title": title,
            "body": "正文内容",
            "reviewed": True,
            "mode": "manual",
            **({"scheduledAt": scheduled} if scheduled else {}),
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_publish_jobs_filter_search_and_summary(client):
    headers = login(client, "pub-filter")
    job_a = _create_manual_job(client, headers, "pub-filter", "filter-key-1", "每周选题复盘", "2099-11-10T10:00:00Z")
    job_b = _create_manual_job(client, headers, "pub-filter", "filter-key-2", "产品更新公告")
    _create_manual_job(client, headers, "pub-filter", "filter-key-3", "第三篇草稿")

    summary = client.get("/api/v1/publishing/jobs/summary", headers=headers).json()
    assert summary["total"] == 3
    assert summary["byStatus"]["queued"] == 1
    assert summary["byStatus"]["approved"] == 2

    # 状态筛选：单值与逗号分隔多值
    queued = client.get("/api/v1/publishing/jobs?status=queued", headers=headers).json()
    assert [job["id"] for job in queued] == [job_a["id"]]
    both = client.get("/api/v1/publishing/jobs?status=approved,queued", headers=headers).json()
    assert len(both) == 3

    # 未知状态明确报 400，不静默当空结果
    bad = client.get("/api/v1/publishing/jobs?status=bogus", headers=headers)
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_status_filter"

    # 搜索命中 payload 标题与请求号
    by_title = client.get("/api/v1/publishing/jobs?q=复盘", headers=headers).json()
    assert [job["id"] for job in by_title] == [job_a["id"]]
    by_request = client.get(
        f"/api/v1/publishing/jobs?q={job_b['requestId'][:12]}", headers=headers
    ).json()
    assert [job["id"] for job in by_request] == [job_b["id"]]

    # 平台 / 账号筛选
    assert len(client.get("/api/v1/publishing/jobs?platform=wechat_official", headers=headers).json()) == 3
    assert client.get("/api/v1/publishing/jobs?platform=douyin", headers=headers).json() == []
    account_id = client.get("/api/v1/platform-accounts", headers=headers).json()[0]["id"]
    assert len(client.get(f"/api/v1/publishing/jobs?accountId={account_id}", headers=headers).json()) == 1


def test_publish_job_detail_timeline_and_records(client):
    headers = login(client, "pub-detail")
    job = _create_manual_job(client, headers, "pub-detail", "detail-key-1", "详情时间线")
    recorded = client.post(
        f"/api/v1/publishing/jobs/{job['id']}/record-manual-result",
        json={"remoteContentId": "remote-1", "permalink": "https://example.com/p/1"},
        headers=headers,
    )
    assert recorded.status_code == 200, recorded.text

    detail = client.get(f"/api/v1/publishing/jobs/{job['id']}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    # 时间线：创建（approved）→ 手动回填（succeeded），触发方与备注齐全
    assert [entry["status"] for entry in body["statusHistory"]] == ["approved", "succeeded"]
    assert body["statusHistory"][-1]["actor"] == "user"
    assert "回填" in body["statusHistory"][-1]["note"]
    assert body["records"][0]["resultStatus"] == "manual_recorded"
    assert body["records"][0]["permalink"] == "https://example.com/p/1"
    # 列表字段同步暴露 mode / permalink，供行内上下文操作判断
    assert body["mode"] == "manual"
    assert body["permalink"] == "https://example.com/p/1"

    assert client.get("/api/v1/publishing/jobs/nonexistent", headers=headers).status_code == 404


def test_publish_job_patch_reschedule_and_edit(client):
    headers = login(client, "pub-patch")
    job = _create_manual_job(client, headers, "pub-patch", "patch-key-1", "原标题", "2099-11-10T10:00:00Z")

    # 改期：状态不变但历史留痕
    patched = client.patch(
        f"/api/v1/publishing/jobs/{job['id']}",
        json={"scheduledAt": "2099-12-01T09:00:00Z"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["scheduledAt"].startswith("2099-12-01")
    detail = client.get(f"/api/v1/publishing/jobs/{job['id']}", headers=headers).json()
    assert detail["statusHistory"][-1]["note"] == "修改计划时间"
    assert detail["statusHistory"][-1]["status"] == "queued"

    # 改标题：同步反映到列表标题
    renamed = client.patch(
        f"/api/v1/publishing/jobs/{job['id']}", json={"title": "新标题"}, headers=headers
    )
    assert renamed.status_code == 200
    assert renamed.json()["contentTitle"] == "新标题"

    # 过去时间拒绝；排队任务显式清空计划时间拒绝（会永久滞留）
    assert (
        client.patch(
            f"/api/v1/publishing/jobs/{job['id']}",
            json={"scheduledAt": "2020-01-01T00:00:00Z"},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/v1/publishing/jobs/{job['id']}", json={"scheduledAt": None}, headers=headers
        ).status_code
        == 422
    )

    # 终态不可编辑
    client.post(
        f"/api/v1/publishing/jobs/{job['id']}/record-manual-result",
        json={"remoteContentId": "remote-x"},
        headers=headers,
    )
    denied = client.patch(
        f"/api/v1/publishing/jobs/{job['id']}", json={"title": "迟到修改"}, headers=headers
    )
    assert denied.status_code == 409
    assert denied.json()["code"] == "job_not_editable"


def test_publish_job_approve_flow(client):
    headers = login(client, "pub-approve")
    job = _create_manual_job(client, headers, "pub-approve", "approve-key-1", "待审任务")
    # pending_review 目前只由 Agent 受控链路产生：直改库模拟（带初始历史，模拟真实创建路径）
    from app.services import publish_state as ps

    _seed_job(
        job["id"],
        status="pending_review",
        history=ps.initial_history("pending_review", actor="system", note="Agent 创建，待人工审核"),
    )

    approved = client.post(f"/api/v1/publishing/jobs/{job['id']}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    detail = client.get(f"/api/v1/publishing/jobs/{job['id']}", headers=headers).json()
    assert [entry["status"] for entry in detail["statusHistory"]] == ["pending_review", "approved"]
    assert detail["statusHistory"][-1]["note"] == "人工审核通过"

    # 已审批任务不能重复审批
    again = client.post(f"/api/v1/publishing/jobs/{job['id']}/approve", headers=headers)
    assert again.status_code == 409
    assert again.json()["code"] == "invalid_transition"


def test_publish_retry_reenqueues_real_platform_job(client, monkeypatch):
    """草稿箱任务重试必须重新投递后台队列，否则会永久滞留在排队。"""
    from app import models
    from app.core import queue
    from app.core.db import SessionLocal
    from sqlalchemy import select

    # 这个用例只关心「有没有入队」；让 dispatch 不真正执行，避免线程池拿假账号去调微信
    monkeypatch.setattr(queue, "dispatch", lambda *a, **kw: None)

    def background_job_count():
        with SessionLocal() as db:
            return len(
                db.scalars(
                    select(models.BackgroundJob).where(models.BackgroundJob.kind == "wechat_draft")
                ).all()
            )

    headers = login(client, "pub-retry")
    draft_job = _create_manual_job(client, headers, "pub-retry", "retry-key-1", "草稿重试")
    _seed_job(draft_job["id"], status="failed", payload_patch={"mode": "draft"})

    before = background_job_count()
    retried = client.post(f"/api/v1/publishing/jobs/{draft_job['id']}/retry", headers=headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "queued"
    assert background_job_count() == before + 1

    # manual 模式重试由用户手动执行，不投递队列
    manual_job = _create_manual_job(client, headers, "pub-retry", "retry-key-2", "手动重试")
    _seed_job(manual_job["id"], status="failed")
    before_manual = background_job_count()
    assert client.post(f"/api/v1/publishing/jobs/{manual_job['id']}/retry", headers=headers).status_code == 200
    assert background_job_count() == before_manual

    # 清掉这条合成任务留下的执行记录：测试库全库共用，遗留的后台任务会被后续到期扫描重投
    from sqlalchemy import delete

    with SessionLocal() as db:
        for ident in db.scalars(
            select(models.BackgroundJob.id).where(models.BackgroundJob.kind == "wechat_draft")
        ).all():
            if (db.get(models.BackgroundJob, ident).payload or {}).get("publishJobId") == draft_job["id"]:
                db.execute(delete(models.BackgroundJob).where(models.BackgroundJob.id == ident))
        db.commit()
