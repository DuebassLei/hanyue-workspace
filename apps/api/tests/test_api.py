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
    assert me.json()["displayName"] == "user-a"


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
    assert len(styles) == 10
    assert all(s["source"] == "builtin" for s in styles)
    assert {
        s["manifest"]["defaultTheme"] for s in styles
    } <= {"ink-green", "deep-blue", "warm-orange", "mono"}
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
    # Skill 未启用 → 没有工具可用，运行不应产生草稿
    events = client.get(f"/api/v1/agent/runs/{run['id']}/events", headers=headers).json()
    joined = str(events)
    assert "create_outline" not in joined


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
            "manifest": {"tone": "更口语化", "defaultTheme": "deep-blue"},
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
        json={"manifest": {"tone": "再口语一点", "defaultTheme": "deep-blue"}},
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


def test_dashboard_shape(client):
    headers = login(client)
    data = client.get("/api/v1/dashboard", headers=headers).json()
    for key in ("publishProgress", "pendingReview", "failedJobs", "platformStatus", "recentContent"):
        assert key in data
    assert "topicBacklog" not in data, "选题库已下线，总览不应再返回选题库存"


def test_platform_capabilities_returns_wechat_official_only(client):
    """平台收敛后能力接口只返回微信公众号。"""
    headers = login(client)
    res = client.get("/api/v1/platforms/capabilities", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["platforms"]) == 1
    first = body["platforms"][0]
    assert first["platform"] == "wechat_official"
    for key in ("platform", "label", "confirmed", "publish"):
        assert key in first


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
            "scheduledAt": "2026-11-10T10:00:00Z",
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
    owner = login(client, "catalog-owner")
    other = login(client, "catalog-other")
    conn = client.post(
        "/api/v1/models/connections", json={"displayName": "私有连接"}, headers=owner
    ).json()
    entry = client.post(
        "/api/v1/models/catalog",
        json={"connectionId": conn["id"], "modelCode": "m1"},
        headers=owner,
    ).json()

    assert (
        client.patch(
            f"/api/v1/models/catalog/{entry['id']}", json={"modelCode": "hacked"}, headers=other
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/models/catalog/{entry['id']}", headers=other).status_code == 404
    )


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
