# 文章创作质量优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按分析报告顺序修复文章创作链路的 10 项不足：大纲联动、审校修复闭环、主路径质量关卡、热点素材注入、AI 味治理、风格范文、原创性比对、封面叠字。

**Architecture:** 后端 FastAPI 服务层（agent_runtime / article_workflow / ai_article / models_module）+ 前端 Vue3 StudioView。全部复用现有纯函数检查工具与 SSE 事件管道，不新增数据库列、不新增出站 HTTP。

**Tech Stack:** Python 3 + FastAPI + SQLAlchemy 2 + Pydantic v2 + Pillow；Vue 3 + naive-ui；pytest（Windows venv）。

**Spec:** 本文件「背景与目标」节（自包含，源自 2026-11-19 文章创作不足分析）。

## 背景与目标（分析结论 → 任务映射）

| # | 不足 | 任务 |
|---|------|------|
| 1 | 大纲步骤产物不传给正文生成；大纲落库成稿件污染稿件库 | Task 1 |
| 2 | repairPolicy 声明但无消费；审校只报告不修复 | Task 2 |
| 3 | 主创作路径（SSE）完全绕过审校 | Task 3 |
| 4 | 热点只注入标题，无素材；资讯风格要求与事实能力错配 | Task 4 |
| 5 | AI 味规则窄（6 短语+3 词）、只报不改 | Task 5 |
| 6 | 风格 Skill 纯规则无范文；styleRules 与 reviewRules 不联动 | Task 6 |
| 7 | 无原创性/查重 | Task 7 |
| 8 | 封面无「无字图+代码叠字」路线 | Task 8 |
| 9 | 单次 JSON 契约脆弱、解析失败残骸落库 | Task 2（requireContract）+ Task 3 |
| 10 | 字数控制只有一句提示词、Agent 路径硬编码 900 | Task 1（manifest targetLength）+ Task 2（length_rules）+ Task 3 |

**明确不在本次范围**：多平台发布适配器（需要平台选型、凭据与 platform-evidence 核验，属产品决策）；联网搜索/RAG 素材库（本次用热点簇内素材解决事实来源）。

## Global Constraints

- 测试命令（Windows）：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/<file> -q`。**只跑受影响测试文件**，全量只在 Task 9。
- 测试文件顶层不要 import `app.core.db`；参照 `tests/test_article_workflow.py` 的既有写法（conftest 已在收集阶段设好临时库）。
- 禁止占位文本冒充成功：模型无返回必须明确失败/报错码。
- API 响应 camelCase（CamelModel）；本计划**不新增数据库列**。
- 内置 Skill 只改 `apps/api/app/resources/skills/builtin_skills.yaml`，改内容必须抬 version（article-drafting 依次 1.3.0→1.4.0→1.5.0→1.6.0）。
- **工作区有大量用户未提交 WIP（videos 模块删除等）**：每个任务只 `git add` 本任务 Files 列出的文件，绝不 `git add -A`。
- 提交信息用中文 conventional commit（参照 `fix(wechat-layout): ...` 风格）。
- 任务串行执行（共享 StudioView.vue / builtin_skills.yaml / article_workflow.py）；每个任务的 spec 审查与质量审查两个 reviewer 可并行。
- 修改 `ai_gateway.SYSTEM_RULES` / `build_prompt` 才需要抬 `PROMPT_TEMPLATE_VERSION`；本计划不改这两处。

---

### Task 1: 大纲 → 正文真实联动（Agent 路径）

**Files:**
- Modify: `apps/api/app/services/skill_manifest.py`（SkillManifest 加 targetLength）
- Modify: `apps/api/app/services/agent_runtime.py:360-425`（create_outline / generate_article 分支）、`651-665`（start_run 循环）
- Modify: `apps/api/app/resources/skills/builtin_skills.yaml:20-43`（article-drafting version + targetLength）
- Test: `apps/api/tests/test_agent_outline_chain.py`（新建）

**Interfaces:**
- Consumes: `ai_gateway.generate_text(db, *, owner_user_id, task_type, prompt, constraints=None, system_prompt=None) -> {"text","modelCode","errorCode",...}`
- Produces:
  - `create_outline` 工具结果 `{"outline": str, "chars": int, "modelCode": str}`（不再建 ContentItem）
  - `generate_article` 接受 `args["outline"]: str`（拼进 prompt）与 `args["contentItemId"]: str`（追加版本而非新建稿件，Task 2 依赖）
  - `generate_article` 结果增加 `"contractOk": bool`（Task 2 依赖）
  - manifest 字段 `targetLength: int`（100..5000，可选；Task 2/3 依赖）
  - start_run 循环维护 `context: dict[tool_id, result]`，generate_article 自动注入上一步 outline（Task 2 依赖）

- [ ] **Step 1: 写失败测试**

新建 `apps/api/tests/test_agent_outline_chain.py`：

```python
"""大纲→正文联动：大纲不落稿件库、产物注入正文 prompt、字数读 manifest。"""

import json

from conftest import login


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
    tool_results = [e["safePayload"] for e in events if e["eventType"] == "tool.result"]
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_agent_outline_chain.py -q`
Expected: FAIL（create_outline 结果无 outline 字段 / prompt 无「参考大纲」/ targetLength 900 / 稿件数 2）

- [ ] **Step 3: skill_manifest.py 加 targetLength**

在 `SkillManifest` 类 `defaultTheme` 字段后加：

```python
    targetLength: int | None = Field(default=None, ge=100, le=5000)
```

- [ ] **Step 4: 重写 agent_runtime 的 create_outline / generate_article 分支**

`agent_runtime.py` 顶部 import 改为 `from sqlalchemy import func, select`。

在 `APPROVAL_TTL_SECONDS` 常量后加：

```python
OUTLINE_SYSTEM_PROMPT = (
    "你是资深公众号主笔。只输出文章大纲本身：用 Markdown 列表给出标题建议与 3-6 个章节要点，"
    "每节一句话说明写什么。不要输出 JSON、不要解释、不要写正文。"
)
```

把 `_execute_tool` 中 `if tool_id in {"create_outline", "generate_article", "prepare_platform_variant"}:` 整块（约 360-425 行）替换为：

```python
    if tool_id == "create_outline":
        prompt = str(args.get("prompt") or run.goal or "写一篇短文章")
        generated = await ai_gateway.generate_text(
            db,
            owner_user_id=run.owner_user_id,
            task_type="outline",
            prompt=prompt,
            constraints={"targetLength": 400},
            system_prompt=OUTLINE_SYSTEM_PROMPT,
        )
        raw_text = generated["text"]
        if not raw_text:
            raise AgentRuntimeError(
                f"模型未返回内容（{generated.get('errorCode') or 'empty_response'}），请检查模型连接"
            )
        return {"outline": raw_text.strip(), "chars": len(raw_text), "modelCode": generated["modelCode"]}

    if tool_id in {"generate_article", "prepare_platform_variant"}:
        task_type = {
            "generate_article": "article",
            "prepare_platform_variant": "platform_variant",
        }[tool_id]
        prompt = str(args.get("prompt") or run.goal or "写一篇短文章")
        outline = str(args.get("outline") or "").strip()
        if outline:
            prompt = f"{prompt}\n\n参考大纲（结构可微调，不要照抄标题）：\n{outline[:2000]}"
        skill = _resolve_run_skill(db, run)
        manifest = skill.manifest if skill and isinstance(skill.manifest, dict) else {}
        target = manifest.get("targetLength")
        if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
            target = 900
        system_prompt = ai_article.build_system_prompt(skill)
        generated = await ai_gateway.generate_text(
            db,
            owner_user_id=run.owner_user_id,
            task_type=task_type,
            prompt=prompt,
            constraints={"targetLength": target},
            system_prompt=system_prompt,
        )
        raw_text = generated["text"]
        if not raw_text:
            raise AgentRuntimeError(
                f"模型未返回内容（{generated.get('errorCode') or 'empty_response'}），请检查模型连接"
            )
        title, body, summary = prompt[:80], raw_text, f"Agent 工具 {tool_id} 生成（未按契约解析）"
        contract_ok = False
        if tool_id == "generate_article":
            draft = ai_article.parse_llm_output(raw_text)
            contract_ok = draft.ok
            if draft.ok:
                title = draft.title or prompt[:80]
                body = draft.markdown
                summary = draft.summary[:300] if draft.summary else f"Agent 工具 {tool_id} 生成"
            else:
                parsed_title, parsed_body = ai_article.article_from_raw_output(raw_text)
                title = parsed_title or prompt[:80]
                body = parsed_body or raw_text
        # 修复轮（Task 2）传 contentItemId：在同一篇稿件上追加版本，不再新建
        item = None
        requested_item_id = args.get("contentItemId")
        if isinstance(requested_item_id, str) and requested_item_id:
            candidate = db.get(models.ContentItem, requested_item_id)
            if candidate and candidate.workspace_id == run.workspace_id:
                item = candidate
        if item is None:
            item = models.ContentItem(
                workspace_id=run.workspace_id,
                owner_user_id=run.owner_user_id,
                title=title,
                type="article",
                status="draft",
            )
            db.add(item)
            db.flush()
        next_no = (db.scalar(
            select(func.max(models.ContentVersion.version_no)).where(
                models.ContentVersion.content_item_id == item.id
            )
        ) or 0) + 1
        version = models.ContentVersion(
            content_item_id=item.id,
            version_no=next_no,
            parent_version_id=item.current_version_id,
            source_type="agent",
            model_id=generated["modelCode"],
            title=title,
            body=body,
            summary=summary,
            prompt_template_version=(
                f"{skill.skill_key}@{skill.version}"[:32]
                if skill
                else ai_gateway.PROMPT_TEMPLATE_VERSION
            ),
        )
        db.add(version)
        db.flush()
        item.current_version_id = version.id
        return {
            "contentItemId": item.id,
            "versionId": version.id,
            "chars": len(body),
            "modelCode": generated["modelCode"],
            "contractOk": contract_ok,
        }
```

- [ ] **Step 5: start_run 循环注入大纲上下文**

把 `start_run` 中 `for step in steps:` 循环（约 651-665 行）替换为：

```python
    context: dict[str, dict] = {}
    for step in steps:
        if agent_policy.budget_exceeded(run.rounds_used, run.budget_limit or {}):
            run.status = "stopped_budget"
            emit(db, run, "run.stopped", {"reason": "budget_exceeded"})
            return
        if step["tool"] == "generate_article" and "outline" not in step["args"]:
            outline = (context.get("create_outline") or {}).get("outline")
            if outline:
                step["args"]["outline"] = outline
        run.rounds_used += 1
        try:
            outcome = await execute_step(db, run, step, allowed_tools)
        except AgentRuntimeError as exc:
            run.status = "failed"
            run.error_code = "tool_failed"
            emit(db, run, "run.failed", {"message": str(exc)})
            return
        if outcome["status"] == "awaiting_approval":
            return
        if isinstance(outcome.get("result"), dict):
            context[step["tool"]] = outcome["result"]
```

- [ ] **Step 6: builtin YAML 抬版本 + targetLength**

`builtin_skills.yaml` article-drafting：`version: "1.2.0"` → `"1.3.0"`；manifest 的 `tone:` 行前加：

```yaml
      targetLength: 1200
```

- [ ] **Step 7: 跑测试确认通过 + 回归**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_agent_outline_chain.py tests/test_api.py tests/test_skills.py -q`
Expected: PASS（test_api 的既有 agent 用例 maxRounds=5，本任务步骤数未变，应仍通过）

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/skill_manifest.py apps/api/app/services/agent_runtime.py apps/api/app/resources/skills/builtin_skills.yaml apps/api/tests/test_agent_outline_chain.py
git commit -m "feat(agent): 大纲产物真正注入正文生成，大纲不再落稿件库，字数读 manifest"
```

---

### Task 2: Agent 路径审校 → 自动修复闭环（消费 repairPolicy）

**Files:**
- Modify: `apps/api/app/services/article_workflow.py`（新增 length_rules / build_repair_prompt）
- Modify: `apps/api/app/services/agent_runtime.py`（_run_writing_check 注入字数规则；start_run 修复调度）
- Modify: `apps/api/app/services/skill_manifest.py:106-112`（RepairPolicy 注释更新）
- Modify: `apps/api/app/resources/skills/builtin_skills.yaml`（article-drafting repairPolicy + version 1.4.0）
- Modify: `apps/api/tests/test_api.py:373`（maxRounds 5 → 10）
- Test: `apps/api/tests/test_agent_repair_loop.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `context`、`args["contentItemId"]`、`result["contractOk"]`、manifest `targetLength`
- Produces:
  - `article_workflow.length_rules(target_length) -> list[dict]`（非法输入返回 []；规则 id：`target-length-min` / `target-length-max`，阈值 0.7× / 1.4×）
  - `article_workflow.build_repair_prompt(markdown, violations, tone_hits, target_length=None) -> str`（Task 3 复用）
  - start_run 在检查步后按 repairPolicy.retries 追加一次 `generate_article` 修复步（带 contentItemId），发 `repair.scheduled` 事件
  - `review_article` 工具结果在 manifest 有 targetLength 时含字数违规

- [ ] **Step 1: 写失败测试**

新建 `apps/api/tests/test_agent_repair_loop.py`：

```python
"""审校→修复闭环：违规触发一次修复轮，修复稿追加为同一稿件的新版本。"""

import json

from conftest import login
from app.services.article_workflow import build_repair_prompt, length_rules


def test_length_rules_bounds():
    rules = length_rules(1000)
    assert {r["id"] for r in rules} == {"target-length-min", "target-length-max"}
    assert rules[0]["value"] == 700 and rules[1]["value"] == 1400
    assert length_rules(None) == []
    assert length_rules("abc") == []
    assert length_rules(True) == []


def test_build_repair_prompt_contains_problems_and_original():
    prompt = build_repair_prompt(
        "原文正文。",
        [{"id": "x", "message": "正文过短"}],
        [{"ruleId": "sentence-opener", "excerpt": "此外", "offset": 0}],
        target_length=1000,
    )
    assert "正文过短" in prompt
    assert "「此外」" in prompt
    assert "原文正文。" in prompt
    assert "1000" in prompt


def test_violation_triggers_one_repair_round(client, monkeypatch):
    from app.services import ai_gateway

    article_calls = []

    async def fake_generate_text(db, **kwargs):
        if kwargs.get("task_type") == "outline":
            return {"text": "## 大纲\n- 要点", "modelCode": "test-double", "provider": "test-double", "errorCode": None}
        if kwargs.get("task_type") == "article":
            article_calls.append(kwargs)
            # 第一稿故意过短（< reviewRules min_chars 80），修复稿给足长度
            markdown = "短。" if len(article_calls) == 1 else "这是一段足够长的正文内容。" * 20
            text = json.dumps({
                "title": "时间管理", "markdown": markdown,
                "summary": "摘要", "coverPrompt": "", "inlinePrompts": [],
            }, ensure_ascii=False)
            return {"text": text, "modelCode": "test-double", "provider": "test-double", "errorCode": None}
        return {"text": "检查类任务不应调模型", "modelCode": "test-double", "provider": "test-double", "errorCode": None}

    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text)
    headers = login(client, subject="repair-loop-user")
    skills = client.get("/api/v1/skills", headers=headers).json()
    skill = next(s for s in skills if s["skillKey"] == "article-drafting")
    client.patch(
        f"/api/v1/skills/{skill['id']}",
        json={"enabled": True, "grantedToolIds": skill["allowedToolIds"]},
        headers=headers,
    )
    session = client.post("/api/v1/agent/sessions", json={"title": "写作"}, headers=headers).json()
    run = client.post(
        f"/api/v1/agent/sessions/{session['id']}/messages",
        json={"goal": "写一篇关于时间管理的稿子", "skillIds": [skill["id"]], "maxRounds": 10},
        headers=headers,
    ).json()
    assert run["status"] == "succeeded", run

    events = client.get(f"/api/v1/agent/runs/{run['id']}/events", headers=headers).json()
    assert any(e["eventType"] == "repair.scheduled" for e in events)
    assert len(article_calls) == 2, "只修复一轮，不无限重试"
    assert "必须修复的问题" in article_calls[1]["prompt"]

    # 修复稿落在同一篇稿件的 v2，而不是新建稿件
    contents = client.get("/api/v1/content?fields=list&limit=50", headers=headers).json()
    assert len(contents) == 1
    detail = client.get(f"/api/v1/content/{contents[0]['id']}", headers=headers).json()
    assert len(detail["versions"]) == 2
    assert "足够长" in detail["versions"][0]["body"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_agent_repair_loop.py -q`
Expected: FAIL（length_rules / build_repair_prompt 不存在；无 repair.scheduled）

- [ ] **Step 3: article_workflow 加两个纯函数**

在 `article_workflow.py` 的「审稿」小节末尾（`review_article` 函数后）加：

```python
def length_rules(target_length) -> list[dict]:
    """把 manifest/请求里的目标字数收成审稿规则：低于七成或超 1.4 倍都算违规。"""
    if isinstance(target_length, bool) or not isinstance(target_length, int) or target_length <= 0:
        return []
    return [
        {
            "id": "target-length-min",
            "kind": "min_chars",
            "value": int(target_length * 0.7),
            "message": f"正文低于目标字数 {target_length} 的七成，请扩写",
        },
        {
            "id": "target-length-max",
            "kind": "max_chars",
            "value": int(target_length * 1.4),
            "message": f"正文超过目标字数 {target_length} 的 1.4 倍，请压缩",
        },
    ]


def build_repair_prompt(markdown: str, violations, tone_hits, target_length=None) -> str:
    """按检查报告拼修复指令；Task 3 主路径复用同一份，避免两端口径分叉。"""
    lines = [
        "下面是一篇公众号文章草稿，请按问题清单修复后重新输出完整文章。",
        "保持原文的立场、素材与结构，只改有问题的地方；不要新增未经核实的事实。",
    ]
    if isinstance(target_length, int) and not isinstance(target_length, bool) and target_length > 0:
        lines.append(f"目标字数：约 {target_length} 字。")
    messages = [
        str(v.get("message") or v.get("id"))
        for v in (violations or [])
        if isinstance(v, dict) and (v.get("message") or v.get("id"))
    ]
    if messages:
        lines.append("必须修复的问题：")
        lines.extend(f"- {m}" for m in messages)
    excerpts: list[str] = []
    for hit in tone_hits or []:
        if isinstance(hit, dict) and hit.get("excerpt") and hit["excerpt"] not in excerpts:
            excerpts.append(str(hit["excerpt"]))
    if excerpts:
        lines.append("去掉或改写这些 AI 腔表达：" + "、".join(f"「{e}」" for e in excerpts[:12]))
    lines.append("原文：")
    lines.append(markdown or "")
    return "\n".join(lines)
```

- [ ] **Step 4: _run_writing_check 注入字数规则**

`agent_runtime.py` `_run_writing_check` 的 review_article 分支改为：

```python
    if tool_id == "review_article":
        skill = _resolve_run_skill(db, run)
        manifest = skill.manifest if skill and isinstance(skill.manifest, dict) else {}
        rules = list(manifest.get("reviewRules") or [])
        rules.extend(article_workflow.length_rules(manifest.get("targetLength")))
        draft = {"title": version.title or "", "markdown": markdown, "summary": version.summary or ""}
        result = article_workflow.review_article(draft, rules)
        return {**result, "versionId": version.id}
```

- [ ] **Step 5: start_run 修复调度**

在 Task 1 改过的循环基础上继续改。循环前（`context: dict[str, dict] = {}` 旁）加：

```python
    flow_manifest = flow_skill.manifest if flow_skill and isinstance(flow_skill.manifest, dict) else {}
    repair_policy = flow_manifest.get("repairPolicy") if isinstance(flow_manifest.get("repairPolicy"), dict) else {}
    repair_budget = int(repair_policy.get("retries") or 0)
    repair_used = 0
    article_item_id: str | None = None
    tone_in_plan = any(s["tool"] == "check_ai_tone" for s in steps)
```

循环内 `if isinstance(outcome.get("result"), dict): context[step["tool"]] = outcome["result"]` 之后加：

```python
        result = outcome.get("result") or {}
        if step["tool"] == "generate_article" and result.get("contentItemId"):
            article_item_id = result["contentItemId"]
        if not article_item_id or repair_used >= repair_budget:
            continue
        # 契约失败：requireContract 开启时立刻安排修复，不等检查步
        if step["tool"] == "generate_article" and repair_policy.get("requireContract") and result.get("contractOk") is False:
            version = db.get(models.ContentVersion, result.get("versionId"))
            repair_prompt = (
                "上次输出无法按 JSON 契约解析。请严格只输出一个 JSON 对象"
                "（title/markdown/summary/coverPrompt/inlinePrompts），把下面的内容整理成完整文章：\n"
                + ((version.body if version else "") or "")[:4000]
            )
            steps.append({"tool": "generate_article", "args": {
                "prompt": repair_prompt, "contentItemId": article_item_id,
            }})
            repair_used += 1
            emit(db, run, "repair.scheduled", {"reason": "contract_failed"})
            continue
        # 检查步完成后统一调度：有 check_ai_tone 就在它之后，否则在 review_article 之后
        is_check_end = step["tool"] == "check_ai_tone" or (
            step["tool"] == "review_article" and not tone_in_plan
        )
        if not is_check_end:
            continue
        review = context.get("review_article") or {}
        tone = context.get("check_ai_tone") or {}
        violations = review.get("violations") or []
        hits = tone.get("hits") or []
        if not violations and not hits:
            continue
        version = db.get(models.ContentVersion, review.get("versionId") or tone.get("versionId"))
        if not version:
            continue
        repair_prompt = article_workflow.build_repair_prompt(
            version.body or "", violations, hits, flow_manifest.get("targetLength")
        )
        steps.append({"tool": "generate_article", "args": {
            "prompt": repair_prompt, "contentItemId": article_item_id,
        }})
        repair_used += 1
        emit(db, run, "repair.scheduled", {
            "reason": "check_failed", "violations": len(violations), "toneHits": len(hits),
        })
```

注意：修复步追加进 `steps` 后，`for step in steps` 会继续执行到它；修复步本身是 generate_article，其结果不会再触发检查（检查步已过），repair_used 已达上限，天然只修一轮。

- [ ] **Step 6: YAML + RepairPolicy 注释 + 既有测试预算**

`builtin_skills.yaml` article-drafting：version → `"1.4.0"`；`repairPolicy:` 改为：

```yaml
      repairPolicy:
        retries: 1
        requireContract: true
```

`skill_manifest.py` RepairPolicy docstring 改为 `"""生成后按检查报告自动修复的轮数上限（0=只报告，1=最多修一轮）。"""`

`tests/test_api.py:373` `"maxRounds": 5` → `"maxRounds": 10`（工作流加修复步后 5 轮预算会 stopped_budget）。

- [ ] **Step 7: 跑测试确认通过 + 回归**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_agent_repair_loop.py tests/test_agent_outline_chain.py tests/test_article_workflow.py tests/test_api.py tests/test_skills.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/article_workflow.py apps/api/app/services/agent_runtime.py apps/api/app/services/skill_manifest.py apps/api/app/resources/skills/builtin_skills.yaml apps/api/tests/test_api.py apps/api/tests/test_agent_repair_loop.py
git commit -m "feat(agent): 消费 repairPolicy——审校/AI味/字数/契约违规自动修复一轮"
```

---

### Task 3: 主创作路径（SSE）生成后检查 + 自动修复

**Files:**
- Modify: `apps/api/app/modules/models_module.py:15`（import）、`530-665`（event_stream）
- Modify: `apps/web/src/api/client.js:181-188`（新事件分发）
- Modify: `apps/web/src/views/StudioView.vue:111`（refs）、`883-945`（runAi）、`1800`（模板）
- Test: `apps/api/tests/test_main_path_review.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `article_workflow.length_rules` / `build_repair_prompt`；manifest `repairPolicy`
- Produces:
  - SSE 新事件 `generation.repair`（`{reason, violations?, toneHits?}`）、`generation.repaired`（`{text, modelCode}`）、`generation.review`（`{generationId, contract, ok, violations, toneHits, repaired, contractFailed?}`）
  - client.js handlers：`onRepair` / `onRepaired` / `onReview`
  - 修复只在 `repairPolicy.retries >= 1` 时发生；修复稿问题数不高于原稿才采用；落库/记录用修复后文本

- [ ] **Step 1: 写失败测试**

新建 `apps/api/tests/test_main_path_review.py`：

```python
"""主创作路径：article 生成后跑审校+AI味+字数检查，违规自动修复一轮再落库。"""

import json

from conftest import login


def _article_json(markdown):
    return json.dumps({
        "title": "测试标题", "markdown": markdown, "summary": "摘要",
        "coverPrompt": "", "inlinePrompts": [],
    }, ensure_ascii=False)


def test_short_article_gets_repaired_and_persisted(client, monkeypatch):
    from app.services import ai_gateway

    fixed_markdown = "这是一段足够长的正文内容，用来通过字数检查。" * 10

    async def fake_stream_text(db, **kwargs):
        yield {"event": "generation.delta", "data": {"text": _article_json("短。"), "provider": "test", "isPlaceholder": False}}
        yield {"event": "generation.done", "data": {"durationMs": 1, "provider": "test", "isPlaceholder": False}}

    async def fake_generate_text(db, **kwargs):
        assert kwargs.get("task_type") == "rewrite"
        assert "必须修复的问题" in kwargs.get("prompt", "")
        return {"text": _article_json(fixed_markdown), "modelCode": "fix-model", "provider": "test", "errorCode": None}

    monkeypatch.setattr(ai_gateway, "stream_text", fake_stream_text)
    monkeypatch.setattr(ai_gateway, "generate_text", fake_generate_text)

    headers = login(client, subject="main-path-review-user")
    item = client.post("/api/v1/content", json={"title": "测试稿", "type": "article"}, headers=headers).json()
    res = client.post("/api/v1/ai/text-generations", json={
        "taskType": "article",
        "prompt": "写一篇关于时间管理的稿子",
        "contentItemId": item["id"],
        "skillKey": "article-drafting",
        "constraints": {"targetLength": 300, "platform": "wechat_official"},
    }, headers=headers)
    assert res.status_code == 200
    body = res.text
    assert "generation.review" in body
    assert "generation.repair" in body
    assert "generation.repaired" in body

    detail = client.get(f"/api/v1/content/{item['id']}", headers=headers).json()
    assert detail["versions"][0]["body"] == fixed_markdown


def test_clean_article_no_repair(client, monkeypatch):
    from app.services import ai_gateway

    clean_markdown = "## 开头\n这是一个具体的场景。\n\n## 方法\n把任务拆小，立刻开始第一步。" * 8

    async def fake_stream_text(db, **kwargs):
        yield {"event": "generation.delta", "data": {"text": _article_json(clean_markdown), "provider": "test", "isPlaceholder": False}}
        yield {"event": "generation.done", "data": {"durationMs": 1, "provider": "test", "isPlaceholder": False}}

    async def fail_generate_text(db, **kwargs):
        raise AssertionError("干净稿件不应触发修复调用")

    monkeypatch.setattr(ai_gateway, "stream_text", fake_stream_text)
    monkeypatch.setattr(ai_gateway, "generate_text", fail_generate_text)

    headers = login(client, subject="main-path-clean-user")
    item = client.post("/api/v1/content", json={"title": "干净稿", "type": "article"}, headers=headers).json()
    res = client.post("/api/v1/ai/text-generations", json={
        "taskType": "article",
        "prompt": "写一篇关于时间管理的稿子",
        "contentItemId": item["id"],
        "skillKey": "article-drafting",
        "constraints": {"targetLength": 300},
    }, headers=headers)
    body = res.text
    assert "generation.review" in body
    assert "generation.repaired" not in body
```

注意：干净稿也要避开 AI 味词表与 reviewRules（不含「100%有效」、无图片占位、字数在 300×0.7..300×1.4 之间——上面 clean_markdown 约 300 字，若跑出来字数越界就调整重复次数使 len 落在 210..420）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_main_path_review.py -q`
Expected: FAIL（无 generation.review / generation.repaired 事件）

- [ ] **Step 3: models_module 后端检查 + 修复**

`models_module.py:15` import 改为：

```python
from ..services import ai_article, ai_gateway, article_workflow, provider_probe
```

在 `event_stream()` 内、候选循环结束后（`# 只有真实供应商完整返回才写入稿件版本` 注释之前）插入：

```python
            # 生成后质量关卡：审校 + AI 味 + 字数；repairPolicy 允许时自动修复一轮
            repaired = False
            if payload.task_type == "article" and completed and not error_code:
                raw_text = "".join(collected)
                draft = ai_article.parse_llm_output(raw_text)
                manifest = (
                    selected_skill.manifest
                    if selected_skill and isinstance(selected_skill.manifest, dict)
                    else {}
                )
                repair_policy = manifest.get("repairPolicy") if isinstance(manifest.get("repairPolicy"), dict) else {}
                retries = int(repair_policy.get("retries") or 0)
                target = (payload.constraints or {}).get("targetLength")
                rules = list(manifest.get("reviewRules") or [])
                rules.extend(article_workflow.length_rules(target))

                def _check(d):
                    review = article_workflow.review_article(
                        {"title": d.title, "markdown": d.markdown, "summary": d.summary}, rules
                    )
                    return review, article_workflow.check_ai_tone(d.markdown)

                async def _repair_call(prompt: str) -> dict:
                    return await ai_gateway.generate_text(
                        db,
                        owner_user_id=user.id,
                        task_type="rewrite",
                        prompt=prompt,
                        constraints=payload.constraints,
                        system_prompt=system_prompt,
                    )

                if draft.ok:
                    review, tone = _check(draft)
                    if retries > 0 and (not review["ok"] or tone["hits"]):
                        yield _sse("generation.repair", {
                            "generationId": generation.id,
                            "reason": "check_failed",
                            "violations": review["violations"],
                            "toneHits": tone["hits"],
                        })
                        fixed = await _repair_call(article_workflow.build_repair_prompt(
                            draft.markdown, review["violations"], tone["hits"],
                            target if isinstance(target, int) and not isinstance(target, bool) else None,
                        ))
                        fixed_draft = ai_article.parse_llm_output(fixed.get("text") or "")
                        if fixed_draft.ok:
                            fixed_review, fixed_tone = _check(fixed_draft)
                            before = len(review["violations"]) + len(tone["hits"])
                            after = len(fixed_review["violations"]) + len(fixed_tone["hits"])
                            if after <= before:
                                collected = [fixed["text"]]
                                draft, review, tone = fixed_draft, fixed_review, fixed_tone
                                repaired = True
                                yield _sse("generation.repaired", {
                                    "generationId": generation.id,
                                    "text": fixed["text"],
                                    "modelCode": fixed.get("modelCode"),
                                })
                    yield _sse("generation.review", {
                        "generationId": generation.id,
                        "contract": ai_article.review_contract(draft),
                        "ok": review["ok"] and not tone["hits"],
                        "violations": review["violations"],
                        "toneHits": tone["hits"],
                        "repaired": repaired,
                    })
                else:
                    contract_failed = raw_text.lstrip().startswith("{")
                    if retries > 0 and contract_failed:
                        yield _sse("generation.repair", {
                            "generationId": generation.id, "reason": "contract_failed",
                        })
                        fixed = await _repair_call(
                            "上次输出无法按 JSON 契约解析。请严格只输出一个 JSON 对象"
                            "（title/markdown/summary/coverPrompt/inlinePrompts），把下面的内容整理成完整文章：\n"
                            + raw_text[:4000]
                        )
                        fixed_draft = ai_article.parse_llm_output(fixed.get("text") or "")
                        if fixed_draft.ok:
                            collected = [fixed["text"]]
                            draft = fixed_draft
                            repaired = True
                            yield _sse("generation.repaired", {
                                "generationId": generation.id,
                                "text": fixed["text"],
                                "modelCode": fixed.get("modelCode"),
                            })
                    yield _sse("generation.review", {
                        "generationId": generation.id,
                        "contract": ai_article.review_contract(draft) if draft.ok else {"ok": False, "issues": ["invalid_llm_output"]},
                        "ok": draft.ok,
                        "violations": [],
                        "toneHits": [],
                        "repaired": repaired,
                        "contractFailed": not draft.ok,
                    })
```

`finally` 块里 `generation.request_summary = { ... }` 的 dict 中追加一行 `"repaired": repaired,`（`repaired` 在 try 前定义：把 `repaired = False` 移到 `collected: list[str] = []` 旁，插入块里删掉重复定义）。

说明：修复走 `generate_text` 的默认候选链（不继承钉死模型），`generation.repaired` 事件如实带 modelCode；这是有意取舍，修复是后台补充调用。

- [ ] **Step 4: client.js 分发新事件**

`client.js` 事件分发链（`else if (event === 'generation.saved') ...` 之后）加：

```javascript
        else if (event === 'generation.review') handlers.onReview?.(data)
        else if (event === 'generation.repair') handlers.onRepair?.(data)
        else if (event === 'generation.repaired') handlers.onRepaired?.(data)
```

- [ ] **Step 5: StudioView 状态与处理器**

`StudioView.vue:111`（`const aiError = ref('')` 旁）加：

```javascript
const aiReview = ref(null)
const aiRepairing = ref(false)
```

`runAi()` 开头 `aiError.value = ''` 后加：

```javascript
  aiReview.value = null
  aiRepairing.value = false
```

`runAi()` 的 handlers 对象里（`onSaved` 之后）加：

```javascript
      onRepair: () => { aiRepairing.value = true },
      onRepaired: (data) => {
        if (data.text) aiOutput.value = data.text
        aiRepairing.value = false
      },
      onReview: (data) => {
        aiReview.value = data
        aiRepairing.value = false
      },
```

`onEnd` 回调开头加 `aiRepairing.value = false`。

- [ ] **Step 6: StudioView 模板展示检查报告**

在 `StudioView.vue:1800`（`<n-alert v-if="aiError" ...>` 行）之后插入：

```html
            <n-alert v-if="aiRepairing" type="info" :bordered="false">正在按审稿意见自动修复…</n-alert>
            <n-alert
              v-else-if="aiReview"
              :type="aiReview.ok ? 'success' : 'warning'"
              :bordered="false"
            >
              <template v-if="aiReview.ok">
                审稿通过{{ aiReview.repaired ? '（已自动修复一轮）' : '' }}
              </template>
              <template v-else>
                {{ aiReview.repaired ? '已自动修复一轮，仍有遗留：' : '审稿发现：' }}
                {{ (aiReview.violations || []).map((v) => v.message).join('；') }}
                <template v-if="(aiReview.toneHits || []).length">
                  AI 腔 {{ aiReview.toneHits.length }} 处（{{ [...new Set(aiReview.toneHits.map((h) => h.excerpt))].slice(0, 4).join('、') }}）
                </template>
                <template v-if="aiReview.contractFailed">输出未按契约解析，请人工整理后再用。</template>
              </template>
            </n-alert>
```

- [ ] **Step 7: 跑测试 + 前端构建**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_main_path_review.py tests/test_ai_fallback.py tests/test_model_selection.py -q`
Expected: PASS
Run: `cd apps/web && npm run build`
Expected: 构建成功

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/modules/models_module.py apps/web/src/api/client.js apps/web/src/views/StudioView.vue apps/api/tests/test_main_path_review.py
git commit -m "feat(studio): 主创作路径生成后审校+AI味+字数检查，违规自动修复一轮"
```

---

### Task 4: 热点素材注入（簇详情 → 写作 prompt）

**Files:**
- Modify: `apps/api/app/services/hot_topics.py:587-623`（get_cluster 加 material；新增 build_material）
- Modify: `apps/api/app/resources/skills/builtin_skills.yaml`（style-news-and-gov 加素材纪律规则，version 1.2.0）
- Modify: `apps/web/src/views/HotTopicsView.vue:230-253`（writeFromCluster 带 material）
- Modify: `apps/web/src/views/StudioView.vue:674-685`（consumeHotDraft）、`1076-1095`（createDraftFromHot）
- Test: `apps/api/tests/test_hot_topics.py`（追加）

**Interfaces:**
- Consumes: `models.HotCluster`（title/track/score）、`models.HotItem`（platform/rank/title/hot/url/first_seen_at/last_seen_at）
- Produces:
  - `hot_topics.build_material(cluster, items) -> str`（纯函数）
  - `GET /hot-topics/clusters/{id}` 响应新增 `material: str`
  - `history.state.hotDraft.material: str`（HotTopicsView → StudioView）

- [ ] **Step 1: 写失败测试**

`apps/api/tests/test_hot_topics.py` 末尾追加：

```python
def test_build_material_lists_members_and_urls():
    from types import SimpleNamespace

    from app.services.hot_topics import build_material

    cluster = SimpleNamespace(title="某事件", track="科技", score=82)
    items = [
        SimpleNamespace(platform="微博", rank=1, title="事件原话A", hot="123万",
                        url="https://example.com/a", first_seen_at="2026-11-19 08:00",
                        last_seen_at="2026-11-19 10:00"),
        SimpleNamespace(platform="知乎", rank=3, title="事件讨论B", hot="",
                        url="", first_seen_at="2026-11-19 09:00",
                        last_seen_at="2026-11-19 09:30"),
    ]
    text = build_material(cluster, items)
    assert "某事件" in text and "科技" in text and "82" in text
    assert "微博" in text and "事件原话A" in text and "https://example.com/a" in text
    assert "知乎" in text and "事件讨论B" in text
    assert "2026-11-19 08:00" in text


def test_build_material_empty_items_still_has_event():
    from types import SimpleNamespace

    from app.services.hot_topics import build_material

    text = build_material(SimpleNamespace(title="空事件", track="", score=0), [])
    assert "空事件" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_hot_topics.py -q`
Expected: 新增两条 FAIL（build_material 不存在），既有用例 PASS

- [ ] **Step 3: 实现 build_material 并挂到 get_cluster**

`hot_topics.py` 在 `get_cluster` 函数前加：

```python
def build_material(cluster, items) -> str:
    """把簇内榜单素材收成一段可注入写作 prompt 的事实清单。

    只含采集到的标题/热度/链接/时间，不含正文——模型只能引用这里出现的事实。
    """
    lines = [
        f"事件：{cluster.title}",
        f"赛道：{cluster.track or '未分类'}｜综合评分：{cluster.score or 0}",
        "各平台榜单素材（只能引用这里出现的事实）：",
    ]
    for item in list(items)[:12]:
        hot = f"，热度 {item.hot}" if item.hot else ""
        url = f"，原文 {item.url}" if item.url else ""
        rank = item.rank if item.rank else "?"
        lines.append(f"- {item.platform} 第{rank}名「{item.title}」{hot}{url}")
    first = min((i.first_seen_at for i in items if i.first_seen_at), default="")
    last = max((i.last_seen_at for i in items if i.last_seen_at), default="")
    if first:
        lines.append(f"上榜时间：{first} 至 {last or first}")
    return "\n".join(lines)
```

`get_cluster` 的 `return out` 前加：

```python
    out["material"] = build_material(cluster, items)
```

- [ ] **Step 4: 资讯风格加素材纪律**

`builtin_skills.yaml` style-news-and-gov：version → `"1.2.0"`；styleRules 末尾加：

```yaml
        - 没有参考素材时只写可核实的公开信息，细节不足就明确说明，不虚构现场与引语
```

- [ ] **Step 5: HotTopicsView 携带素材跳转**

`HotTopicsView.vue` `writeFromCluster` 中 `const item = await api.post(...)` 之后、`router.push` 之前加：

```javascript
    let material = ''
    try {
      const detail = await api.get(`/hot-topics/clusters/${cluster.id}`)
      material = detail.material || ''
    } catch {
      material = ''
    }
```

`hotDraft` 对象加字段 `material,`。

- [ ] **Step 6: StudioView 两处注入**

`consumeHotDraft`（约 674-685 行）在 `aiTask.value = 'article'` 之前加：

```javascript
  if (incoming.material) {
    aiPrompt.value += `\n\n参考素材：\n${incoming.material}\n写作要求：只使用素材中出现的事实与数据；素材没有的细节不要编造，确需提及时用〔待核实〕标出。`
  }
```

`createDraftFromHot`（约 1088 行）把 `aiPrompt.value = ...` 一行替换为：

```javascript
    aiPrompt.value = `围绕「${item.title}」写一篇初稿，语气平实`
    const facts = []
    if (item.hot) facts.push(`热度：${item.hot}`)
    if (item.source) facts.push(`来源平台：${item.source}`)
    if (item.url) facts.push(`原文链接：${item.url}`)
    if (facts.length) {
      aiPrompt.value += `\n\n参考素材（只能引用这里出现的事实，缺失细节用〔待核实〕标出）：\n${facts.join('\n')}`
    }
```

- [ ] **Step 7: 跑测试 + 前端构建**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_hot_topics.py tests/test_skills.py -q`
Expected: PASS
Run: `cd apps/web && npm run build`
Expected: 构建成功

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/hot_topics.py apps/api/app/resources/skills/builtin_skills.yaml apps/web/src/views/HotTopicsView.vue apps/web/src/views/StudioView.vue apps/api/tests/test_hot_topics.py
git commit -m "feat(hot-topics): 一键成稿注入簇内榜单素材，资讯风格补素材纪律"
```

---

### Task 5: AI 味规则扩充 + 流程面板「去 AI 味改写」

**Files:**
- Modify: `apps/api/app/services/article_workflow.py:124-194`（词表/模式扩充）
- Modify: `apps/web/src/views/StudioView.vue:1995-2003`（按钮）、`585-607` 附近（deAiTone 函数）
- Test: `apps/api/tests/test_article_workflow.py`（追加）

**Interfaces:**
- Consumes: 现有 `check_ai_tone(markdown) -> {"hits": [{"ruleId","excerpt","offset"}]}`（签名不变）
- Produces: 扩充后的词表/正则；前端 `deAiTone()` 把改写指令填进 AI 助手（不自动发起生成，由用户确认）

- [ ] **Step 1: 写失败测试**

`tests/test_article_workflow.py` 末尾追加：

```python
def test_ai_tone_expanded_phrases_and_buzzwords():
    text = "总的来说，这件事值得记录。底层逻辑并不复杂，颗粒度也可以很细。"
    hits = check_ai_tone(text)
    excerpts = {h["excerpt"] for h in hits["hits"]}
    assert "总的来说" in excerpts
    assert "底层逻辑" in excerpts
    assert "颗粒度" in excerpts


def test_ai_tone_pattern_rules():
    text = "在这个信息爆炸的时代，随着智能手机的普及，人们变了。"
    hits = check_ai_tone(text)
    rule_ids = {h["ruleId"] for h in hits["hits"]}
    assert "pattern-era" in rule_ids
    assert "pattern-with" in rule_ids


def test_ai_tone_clean_text_no_hits():
    assert check_ai_tone("他把旧手机拆开，换了一块电池。")["hits"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_article_workflow.py -q`
Expected: 新增前两条 FAIL，既有用例 PASS

- [ ] **Step 3: 扩充规则**

`article_workflow.py`：

`_SENTENCE_START_PHRASES` 元组追加 `"总的来说", "不得不说", "众所周知", "由此可见"`。

`_WORD_RULES` 元组追加：

```python
    ("buzzword-granularity", "颗粒度"),
    ("buzzword-combo", "组合拳"),
    ("buzzword-moat", "护城河"),
    ("buzzword-mind", "心智"),
    ("buzzword-logic", "底层逻辑"),
    ("buzzword-topdesign", "顶层设计"),
```

`_WORD_RULES` 之后加：

```python
_PATTERN_RULES = (
    ("pattern-era", re.compile(r"在这个[^，。！？]{2,20}的时代")),
    ("pattern-with", re.compile(r"随着[^，。！？]{2,20}的(?:发展|普及|进步|到来)")),
)
```

`check_ai_tone` 的 `hits.sort(...)` 之前加：

```python
    for rule_id, pattern in _PATTERN_RULES:
        for match in pattern.finditer(text):
            hits.append({"ruleId": rule_id, "excerpt": match.group(0), "offset": match.start()})
```

- [ ] **Step 4: 前端「去 AI 味改写」按钮**

`StudioView.vue` 在 `flowStepSummary` 函数前加：

```javascript
/** 把去 AI 味改写指令填进 AI 助手：不自动烧 token，用户确认后点生成 */
function deAiTone() {
  const hits = flow.value?.steps?.find((s) => s.tool === 'check_ai_tone')?.result?.hits || []
  const excerpts = [...new Set(hits.map((h) => h.excerpt).filter(Boolean))]
  const target = excerpts.length
    ? `重点去掉或改写这些表达：${excerpts.map((e) => `「${e}」`).join('、')}。`
    : ''
  aiPrompt.value = `改写下面这篇文章，消除模板化 AI 腔：少用排比小标题和空洞升华，句式长短交错，保留全部事实与结构。${target}\n\n${draftBody.value}`
  aiTask.value = 'polish'
  rightTab.value = 'ai'
  toast('已填入去 AI 味改写指令，确认后点「生成」')
}
```

模板：写作流程面板头部「重新检查」按钮（约 2000 行）旁加：

```html
            <n-button quaternary size="small" :disabled="!flow?.hasBody || aiRunning" @click="deAiTone">
              去 AI 味改写
            </n-button>
```

- [ ] **Step 5: 跑测试 + 前端构建**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_article_workflow.py tests/test_agent_repair_loop.py -q`
Expected: PASS
Run: `cd apps/web && npm run build`
Expected: 构建成功

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/article_workflow.py apps/web/src/views/StudioView.vue apps/api/tests/test_article_workflow.py
git commit -m "feat(studio): AI 味词表扩充+句式模式检测，流程面板一键填入去 AI 味改写"
```

---

### Task 6: 风格范文（few-shot）+ 风格硬禁与审稿联动

**Files:**
- Modify: `apps/api/app/services/skill_manifest.py`（Exemplar 模型 + exemplars 字段）
- Modify: `apps/api/app/services/ai_article.py:139-163`（build_system_prompt 注入范文）
- Modify: `apps/api/app/resources/skills/builtin_skills.yaml`（article-drafting 1.5.0 / style-tech-article 1.2.0 / style-culture-emotion 1.1.0 加 exemplars 与 reviewRules）
- Test: `apps/api/tests/test_skills.py`（追加）

**Interfaces:**
- Consumes: manifest 校验管道 `validate_manifest`
- Produces:
  - manifest 字段 `exemplars: [{"title": str≤120, "excerpt": str≤1500}]`（最多 3 条，可选）
  - `build_system_prompt(skill)` 在风格规则后附范文段（无 exemplars 时输出与旧版一致）

- [ ] **Step 1: 写失败测试**

`tests/test_skills.py` 末尾追加：

```python
def test_manifest_exemplars_validation():
    from app.services.skill_manifest import ManifestInvalid, validate_manifest

    ok = validate_manifest({"exemplars": [{"title": "范文", "excerpt": "一段文字。"}]})
    assert ok["exemplars"][0]["title"] == "范文"
    with pytest.raises(ManifestInvalid):
        validate_manifest({"exemplars": [{"title": f"t{i}", "excerpt": "x"} for i in range(4)]})
    with pytest.raises(ManifestInvalid):
        validate_manifest({"exemplars": [{"title": "", "excerpt": "x"}]})


def test_build_system_prompt_injects_exemplars():
    from types import SimpleNamespace

    from app.services.ai_article import build_system_prompt

    skill = SimpleNamespace(manifest={
        "tone": "克制",
        "exemplars": [{"title": "旧手机", "excerpt": "他把旧手机拆开，换了一块电池。"}],
    })
    prompt = build_system_prompt(skill)
    assert "风格范文" in prompt
    assert "他把旧手机拆开" in prompt
    # 无范文时不出现范文段
    assert "风格范文" not in build_system_prompt(SimpleNamespace(manifest={}))
```

（若文件顶部未 import pytest，则补 `import pytest`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_skills.py -q`
Expected: 新增两条 FAIL

- [ ] **Step 3: skill_manifest 加 Exemplar**

`skill_manifest.py` 常量区加：

```python
MAX_EXEMPLARS = 3
MAX_EXEMPLAR_TITLE = 120
MAX_EXEMPLAR_EXCERPT = 1500
```

`ReviewRule` 类前加：

```python
class Exemplar(BaseModel):
    """风格范文：只给笔调参照，不是内容素材。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_EXEMPLAR_TITLE)
    excerpt: str = Field(min_length=1, max_length=MAX_EXEMPLAR_EXCERPT)
```

`SkillManifest` 的 `targetLength` 字段后加：

```python
    exemplars: list[Exemplar] | None = Field(default=None, max_length=MAX_EXEMPLARS)
```

- [ ] **Step 4: build_system_prompt 注入范文**

`ai_article.py` 把 `build_system_prompt` 整个函数替换为：

```python
def build_system_prompt(skill: models.SkillRegistryEntry | None) -> str:
    """构建受保护的文章输出契约，并附加 Skill 写作风格、范文与流程约束。

    factPolicy / imagePolicy 只在这份 manifest 自己写了时才追加。
    没有 workflow 的文风 Skill 不注入这两段。
    """
    prompt_template, tone, style_rules, _ = resolve_skill_style(skill)
    style_prompt = prompt_template.replace("{tone}", tone).strip()
    parts = [ARTICLE_OUTPUT_CONTRACT]
    style_requirements = f"语气：{tone}"
    if style_prompt:
        style_requirements += f"\n{style_prompt}"
    if style_requirements:
        parts.append(f"写作风格要求（不得改变以上输出契约）：\n{style_requirements}")
    if style_rules:
        rules_text = "\n".join(f"- {r}" for r in style_rules)
        parts.append(f"必须遵守以下风格规则（不得改变以上输出契约）：\n{rules_text}")
    manifest = (skill.manifest or {}) if skill and isinstance(getattr(skill, "manifest", None), dict) else {}
    exemplars = manifest.get("exemplars")
    if isinstance(exemplars, list) and exemplars:
        exemplar_parts = ["风格范文（模仿其笔调与节奏，不要照抄内容）："]
        for ex in exemplars[:3]:
            if isinstance(ex, dict) and str(ex.get("excerpt") or "").strip():
                exemplar_parts.append(f"【{str(ex.get('title') or '范文')[:120]}】\n{str(ex['excerpt'])[:1500]}")
        if len(exemplar_parts) > 1:
            parts.append("\n\n".join(exemplar_parts))
    fact_text = _fact_policy_text(manifest.get("factPolicy"))
    if fact_text:
        parts.append(fact_text)
    image_text = _image_policy_text(manifest.get("imagePolicy"))
    if image_text:
        parts.append(image_text)
    return "\n\n".join(parts)
```

- [ ] **Step 5: YAML 加范文与硬禁审稿规则**

article-drafting：version → `"1.5.0"`；manifest 末尾（repairPolicy 后）加：

```yaml
      exemplars:
        - title: 平实说理示范
          excerpt: |
            我见过最离谱的时间管理，是把日程表排到分钟，然后第一天就崩了。问题不在表，在于人不是机器。后来我只留三件事：早上先做最难的那件，下午留一整块不被打断的时间，晚上允许自己什么都不干。坚持半年，产出反而比排满的时候多。方法说起来简单，难的是承认自己一天只能做好三件事。
```

style-tech-article：version → `"1.2.0"`；manifest 末尾加：

```yaml
      reviewRules:
        - id: no-breakthrough
          kind: not_contains
          field: markdown
          value: "重大突破"
          message: 含无证据断言「重大突破」
      exemplars:
        - title: 实测派示范
          excerpt: |
            这块硬盘我用了四十天。标称读速 7000MB/s，实际拷 200GB 素材，前半程 6.2GB/s，过半掉到 1.8GB/s——缓存写完了。散热片摸着烫手，加了个小风扇才稳住。结论不复杂：剪短视频够用，长时间大文件写入要降预期。参数表不会告诉你这些，只有拷过才知道。
```

style-culture-emotion：version → `"1.1.0"`；manifest 末尾加：

```yaml
      exemplars:
        - title: 说破派示范
          excerpt: |
            母亲学会发微信语音那年，六十一岁。她每条都按住说满六十秒，前面五十秒是家常，最后一句才是正事。我嫌长，她说不长，怕你听不见。后来我才明白，那五十秒是她给自己壮胆的时间。人到老年，连想念都要先排练一遍。
```

- [ ] **Step 6: 跑测试确认通过 + 回归**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_skills.py tests/test_agent_outline_chain.py tests/test_agent_repair_loop.py tests/test_main_path_review.py -q`
Expected: PASS（注意 test_main_path_review 的干净稿字数仍需在阈值内；范文注入不改变正文）

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/skill_manifest.py apps/api/app/services/ai_article.py apps/api/app/resources/skills/builtin_skills.yaml apps/api/tests/test_skills.py
git commit -m "feat(skills): 风格范文 few-shot 注入，科技/资讯风格硬禁接入审稿规则"
```

---

### Task 7: 原创性比对（工作区历史稿查重）

**Files:**
- Modify: `apps/api/app/services/article_workflow.py`（check_originality 纯函数）
- Modify: `apps/api/app/services/agent_policy.py:15-36`（登记工具）
- Modify: `apps/api/app/services/agent_runtime.py`（_WRITING_CHECK_TOOLS / _ARG_KEYS / _run_writing_check / plan_steps）
- Modify: `apps/api/app/modules/content.py:348-444`（_FLOW_STEP_LABELS + writing-flow checks）
- Modify: `apps/api/app/resources/skills/builtin_skills.yaml`（article-drafting tools/workflow + version 1.6.0）
- Modify: `apps/web/src/views/StudioView.vue:585-607`（flowStepSummary）
- Test: `apps/api/tests/test_article_workflow.py`（追加）

**Interfaces:**
- Consumes: `models.ContentItem` / `models.ContentVersion`
- Produces:
  - `article_workflow.check_originality(markdown, candidates) -> {"matches": [{"contentItemId","title","similarity"}], "skipped"?: "too_short"}`（candidates 为 `[{"id","title","body"}]`；SequenceMatcher 截断 3000 字；阈值 0.55；正文 <50 字跳过）
  - 工具 `check_originality`（READ_ONLY），进 writing-flow 面板与 Agent 工作流

- [ ] **Step 1: 写失败测试**

`tests/test_article_workflow.py` 末尾追加：

```python
def test_originality_identical_text_matches():
    from app.services.article_workflow import check_originality

    body = "这是一篇用来测试原创性比对的文章正文，内容足够长才能参与比对。" * 5
    result = check_originality(body, [{"id": "c1", "title": "旧稿", "body": body}])
    assert result["matches"][0]["contentItemId"] == "c1"
    assert result["matches"][0]["similarity"] >= 0.99


def test_originality_unrelated_and_short():
    from app.services.article_workflow import check_originality

    body = "完全不同的另一篇文章，讲做饭的步骤与火候，和原文没有任何重叠句子。" * 5
    other = "这是一篇用来测试原创性比对的文章正文，内容足够长才能参与比对。" * 5
    assert check_originality(body, [{"id": "c1", "title": "旧稿", "body": other}])["matches"] == []
    assert check_originality("太短", [{"id": "c1", "title": "旧稿", "body": other}])["skipped"] == "too_short"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_article_workflow.py -q`
Expected: 新增两条 FAIL（check_originality 不存在）

- [ ] **Step 3: 实现纯函数**

`article_workflow.py` 顶部 `import re` 旁加 `import difflib`。「稿件检索」小节前加：

```python
# ---------------------------------------------------------------- 原创性比对


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def check_originality(markdown: str, candidates: list[dict]) -> dict:
    """与工作区历史稿做相似度比对；只报候选，不判定抄袭。"""
    text = _norm_text(markdown)
    if len(text) < 50:
        return {"matches": [], "skipped": "too_short"}
    matches = []
    for cand in candidates or []:
        if not isinstance(cand, dict):
            continue
        other = _norm_text(cand.get("body") or "")
        if len(other) < 50:
            continue
        ratio = difflib.SequenceMatcher(None, text[:3000], other[:3000]).ratio()
        if ratio >= 0.55:
            matches.append({
                "contentItemId": cand.get("id"),
                "title": cand.get("title") or "",
                "similarity": round(ratio, 3),
            })
    matches.sort(key=lambda m: -m["similarity"])
    return {"matches": matches[:5]}
```

- [ ] **Step 4: 登记工具 + Agent 接线**

`agent_policy.py` TOOL_CATALOG 的 `"plan_images"` 行后加：

```python
    "check_originality": {"risk": READ_ONLY, "label": "原创性比对"},
```

`agent_runtime.py`：
- `_WRITING_CHECK_TOOLS` 集合加 `"check_originality"`。
- `_ARG_KEYS` 加 `"check_originality": None,`。
- `plan_steps` 的 `if "check_ai_tone" in has:` 块后加：

```python
        if "check_originality" in has:
            steps.append({"tool": "check_originality", "args": {}})
```

- `_run_writing_check` 的 `extract_facts` 分支后加：

```python
    if tool_id == "check_originality":
        rows = db.execute(
            select(models.ContentItem.id, models.ContentItem.title, models.ContentVersion.body)
            .join(models.ContentVersion, models.ContentVersion.content_item_id == models.ContentItem.id)
            .where(
                models.ContentItem.workspace_id == run.workspace_id,
                models.ContentItem.id != version.content_item_id,
                models.ContentItem.archived.is_(False),
            )
            .order_by(models.ContentItem.updated_at.desc())
            .limit(20)
        ).all()
        candidates = [{"id": r.id, "title": r.title, "body": r.body} for r in rows]
        return {**article_workflow.check_originality(markdown, candidates), "versionId": version.id}
```

- [ ] **Step 5: writing-flow 面板接线**

`content.py`：
- `_FLOW_STEP_LABELS` 加 `"check_originality": "原创性比对",`。
- `get_writing_flow` 的 `checks` dict 加：

```python
        "check_originality": article_workflow.check_originality(
            markdown or "",
            [
                {"id": r.id, "title": r.title, "body": r.body}
                for r in db.execute(
                    select(models.ContentItem.id, models.ContentItem.title, models.ContentVersion.body)
                    .join(models.ContentVersion, models.ContentVersion.content_item_id == models.ContentItem.id)
                    .where(
                        models.ContentItem.workspace_id == item.workspace_id,
                        models.ContentItem.id != item.id,
                        models.ContentItem.archived.is_(False),
                    )
                    .order_by(models.ContentItem.updated_at.desc())
                    .limit(20)
                ).all()
            ],
        ),
```

- [ ] **Step 6: YAML + 前端摘要**

`builtin_skills.yaml` article-drafting：version → `"1.6.0"`；tools 列表加 `check_originality`；workflow 的 `- tool: check_ai_tone` 块后加：

```yaml
        - tool: check_originality
          args: {}
```

`StudioView.vue` `flowStepSummary` 的 `plan_images` 分支前加：

```javascript
  if (step.tool === 'check_originality') {
    if (result.skipped === 'too_short') return '正文太短，暂不比对'
    const matches = result.matches || []
    if (!matches.length) return '未发现明显相似稿'
    return `${matches.length} 篇相似稿（最高 ${Math.round(matches[0].similarity * 100)}%）`
  }
```

- [ ] **Step 7: 跑测试 + 前端构建**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_article_workflow.py tests/test_agent_policy.py tests/test_agent_outline_chain.py tests/test_agent_repair_loop.py tests/test_api.py tests/test_content_library.py -q`
Expected: PASS（agent 用例 maxRounds=10 足够容纳 7 步）
Run: `cd apps/web && npm run build`
Expected: 构建成功

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/article_workflow.py apps/api/app/services/agent_policy.py apps/api/app/services/agent_runtime.py apps/api/app/modules/content.py apps/api/app/resources/skills/builtin_skills.yaml apps/web/src/views/StudioView.vue apps/api/tests/test_article_workflow.py
git commit -m "feat(studio): 原创性比对——与工作区历史稿相似度检查进流程面板与 Agent 工作流"
```

---

### Task 8: 封面「无字图 + 代码叠标题」路线（可选开关）

**Files:**
- Modify: `apps/api/app/services/image_service.py`（overlay_cover_title）
- Modify: `apps/api/app/services/job_runner.py:662-708`（_handle_ai_image 叠字钩子 + 结果标记）
- Modify: `apps/api/app/schemas.py:355-368`（ImageGenerationIn.cover_title）
- Modify: `apps/api/app/modules/ai_images.py:48-58`（payload 透传）
- Modify: `apps/web/src/views/StudioView.vue:120`（开关 ref）、`834-858`（封面请求）、`1928`（复选框）
- Test: `apps/api/tests/test_cover_overlay.py`（新建）

**Interfaces:**
- Consumes: Pillow（已在 requirements）；`_handle_ai_image` 的 `images: list[(raw, mime)]`
- Produces:
  - `image_service.overlay_cover_title(raw: bytes, mime: str, title: str) -> (bytes, str, bool)`（无字体/空标题时原样返回 + False，绝不静默声称叠了字）
  - job payload `coverTitle: str`；结果 `coverTitleApplied: bool`
  - 前端开关默认关闭；开启时封面 prompt 追加无字约束（具体约束，不是笼统「不要文字」）

- [ ] **Step 1: 写失败测试**

新建 `apps/api/tests/test_cover_overlay.py`：

```python
"""封面叠字：有字体才叠，无字体/空标题原样返回并如实标记。"""

import io

import pytest
from PIL import Image


def _png_bytes():
    out = io.BytesIO()
    Image.new("RGB", (600, 800), (30, 60, 90)).save(out, format="PNG")
    return out.getvalue()


def test_overlay_empty_title_returns_original():
    from app.services.image_service import overlay_cover_title

    raw = _png_bytes()
    result, mime, applied = overlay_cover_title(raw, "image/png", "  ")
    assert result == raw and applied is False


def test_overlay_no_font_returns_original(monkeypatch):
    from app.services import image_service

    monkeypatch.setattr(image_service, "_FONT_CANDIDATES", ())
    raw = _png_bytes()
    result, mime, applied = image_service.overlay_cover_title(raw, "image/png", "标题文字")
    assert result == raw and applied is False


def test_overlay_with_font_changes_image(monkeypatch):
    from app.services import image_service

    font = next((p for p in image_service._FONT_CANDIDATES if __import__("os").path.exists(p)), None)
    if font is None:
        pytest.skip("本机没有可用 CJK 字体")
    monkeypatch.setattr(image_service, "_FONT_CANDIDATES", (font,))
    raw = _png_bytes()
    result, mime, applied = image_service.overlay_cover_title(raw, "image/png", "时间管理的三个误区")
    assert applied is True
    assert result != raw
    with Image.open(io.BytesIO(result)) as im:
        assert im.size == (600, 800)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_cover_overlay.py -q`
Expected: FAIL（overlay_cover_title 不存在）

- [ ] **Step 3: image_service 实现叠字**

`image_service.py` 顶部 `import io` 旁加 `import os`。文件末尾加：

```python
# ---------------------------------------------------------------- 封面叠标题

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)


def _cover_font(size: int):
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return None


def overlay_cover_title(raw: bytes, mime: str, title: str) -> tuple[bytes, str, bool]:
    """在封面底部叠中文标题（半透明暗带 + 白字，最多两行）。

    生图模型写中文必出错字，标题一律由本机字体渲染。
    找不到 CJK 字体时原样返回并标记 False，不静默声称叠了字。
    """
    title = (title or "").strip()[:40]
    if not title:
        return raw, mime, False
    from PIL import ImageDraw

    font_size_probe = 32
    if _cover_font(font_size_probe) is None:
        return raw, mime, False
    with Image.open(io.BytesIO(raw)) as im:
        image = im.convert("RGBA")
        w, h = image.size
        font_size = max(24, int(w / 12))
        font = _cover_font(font_size)
        if font is None:
            return raw, mime, False
        draw = ImageDraw.Draw(image)
        lines: list[str] = []
        line = ""
        for ch in title:
            if draw.textlength(line + ch, font=font) > w * 0.86 and line:
                lines.append(line)
                line = ch
                if len(lines) == 2:
                    break
            else:
                line += ch
        if line and len(lines) < 2:
            lines.append(line)
        band_h = int(font_size * 1.5 * len(lines) + font_size)
        band = Image.new("RGBA", (w, band_h), (0, 0, 0, 110))
        image.alpha_composite(band, (0, h - band_h))
        y = h - band_h + int(font_size * 0.5)
        for ln in lines:
            draw.text((int(w * 0.07), y), ln, font=font, fill=(255, 255, 255, 255))
            y += int(font_size * 1.5)
        out = io.BytesIO()
        if mime == "image/png":
            image.save(out, format="PNG")
        else:
            image.convert("RGB").save(out, format="JPEG", quality=92)
            mime = "image/jpeg"
        return out.getvalue(), mime, True
```

- [ ] **Step 4: job_runner 钩子**

`job_runner.py` `_handle_ai_image` 中 `asset_ids: list[str] = []` 行前加：

```python
    cover_title_applied = False
    cover_title = str(payload.get("coverTitle") or "").strip()
    if cover_title and payload.get("purpose") == "cover" and images:
        raw, mime, applied = image_service.overlay_cover_title(images[0][0], images[0][1], cover_title)
        images[0] = (raw, mime)
        cover_title_applied = applied
```

返回 dict（`"assetIds": asset_ids,` 旁）加：

```python
        "coverTitleApplied": cover_title_applied,
```

- [ ] **Step 5: schema + 路由透传**

`schemas.py` ImageGenerationIn 末尾加：

```python
    # 封面叠标题：非空时生成无字封面后由服务端用本机 CJK 字体叠字（仅 purpose=cover 生效）
    cover_title: str | None = Field(default=None, max_length=60)
```

`ai_images.py` enqueue payload dict 加 `"coverTitle": payload.cover_title,`。

- [ ] **Step 6: 前端开关与请求**

`StudioView.vue:120`（`const aiGenerateCover = ref(true)` 旁）加：

```javascript
const aiCoverTitle = ref(false)
```

`startAutoImageGeneration` 的封面请求对象（约 836 行）替换为：

```javascript
    settings.generateCover && prompts.coverPrompt
      ? {
          scene: aiCoverTitle.value
            ? `${prompts.coverPrompt}, no text, no letters, no numbers, no words, no glyphs, clean composition`
            : prompts.coverPrompt,
          purpose: 'cover',
          label: '封面图',
          aspectRatio: settings.coverAspectRatio,
          coverTitle: aiCoverTitle.value ? (draftTitle.value || '').trim() : '',
        }
      : null,
```

请求体（`api.post('/ai/image-generations', {...})`）加：

```javascript
      coverTitle: request.coverTitle || null,
```

模板：`aiGenerateCover` 复选框（约 1928 行）后加：

```html
              <n-checkbox v-model:checked="aiCoverTitle" :disabled="aiRunning || !aiGenerateCover">
                封面叠文章标题（本机字体渲染，零错字）
              </n-checkbox>
```

- [ ] **Step 7: 跑测试 + 前端构建**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_cover_overlay.py tests/test_image_service.py tests/test_novel_jobs.py -q`
Expected: PASS（无字体环境第三条 skip 属预期）
Run: `cd apps/web && npm run build`
Expected: 构建成功

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/image_service.py apps/api/app/services/job_runner.py apps/api/app/schemas.py apps/api/app/modules/ai_images.py apps/web/src/views/StudioView.vue apps/api/tests/test_cover_overlay.py
git commit -m "feat(images): 封面无字图+本机字体叠标题路线，可选开关默认关闭"
```

---

### Task 9: 全量验证与收尾

**Files:**
- Modify: 本计划文件（勾选 checkbox）
- 无新增代码（除非回归发现问题）

- [ ] **Step 1: 后端全量测试**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest -q`
Expected: 全部 PASS（基线 232 项 + 本计划新增；skip 仅允许字体缺失项）

- [ ] **Step 2: 编译检查 + 前端构建**

Run: `cd apps/api && .venv/Scripts/python.exe -m compileall -q app`
Run: `cd apps/web && npm run build`
Expected: 均成功

- [ ] **Step 3: 浏览器冒烟（能验多少验多少，如实记录）**

启动后端（`.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000`）与前端（`npm run dev`），用浏览器验证：
- 创作空间打开稿件 → 写作流程面板出现「原创性比对」步与「去 AI 味改写」按钮；
- 配图设置出现「封面叠文章标题」复选框；
- 热点页一键成稿 → AI 助手 prompt 含「参考素材」段；
- AI 生成（若本机已配置可用模型连接）→ 结果区出现审稿 alert；未配置模型则验证错误提示仍为 `provider_unavailable` 诚实降级，并**明确记录「生成后检查/修复链路未经真实模型端到端验证」**。

- [ ] **Step 4: 勾选计划 checkbox + 收尾提交**

把本文件所有已完成步骤 `- [ ]` 改 `- [x]`。

```bash
git add docs/superpowers/plans/2026-11-19-article-quality-optimizations.md
git commit -m "docs(plan): 文章创作质量优化计划执行完毕"
```

- [ ] **Step 5: 汇报**

向用户汇报：每项不足 → 对应提交哈希 → 验证方式（测试/构建/浏览器）；明确列出未端到端验证的部分与多平台适配的遗留决策。