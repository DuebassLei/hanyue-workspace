# 技能中心（独立技能库）

> 状态：已实现（2026-11-22）。本文是技能中心的设计与契约依据。
> 与 Agent 的 `/skills` 注册表**没有任何数据或页面复用**；创作规则模块已下线删除。

---

## 1. 定位

技能中心沉淀三类可复用能力，供成稿台的悬浮助手与快捷改稿工具调用：

| 类型 | 回答的问题 | 典型来源 |
| --- | --- | --- |
| `writing` 写作风格 | 怎么写、用什么语气和结构 | 代表作投喂蒸馏、手动填写 |
| `persona` 人物思维 | 怎么想、依据什么判断 | 人物材料、访谈、文章合集 |
| `knowledge` 领域知识 | 依据什么事实与方法 | 书、课程、长文、播客文字稿 |

一个用户在同一类型下可以有多个技能，一次创作最多同时启用 5 个。

**为什么新建一套模型**：旧的 `skill_registry_entries` + `skill_writing_rules` 是 Agent 工具型 Skill 与创作规则的组合，字段语义是「工具授权 / 规则行」，不适合承载「风格、思维、知识」这种内容型资产；继续在其上加字段会把两套概念绑死，也无法安全删除创作规则。因此新建三张表；创作规则已下线，`skill_writing_rules` 表已随模块删除。

## 2. 数据模型

`apps/api/app/models.py`：

- `author_skills`：技能卡。`owner_user_id` + `name` 唯一；`skill_type`；`config`（JSON）；`source`（manual / distilled / imported）；`version`；`usage_count`。
- `author_skill_revisions`：每次创建、编辑、蒸馏、导入都留一版 `config` 快照，用于回滚与对比。
- `author_skill_distill_runs`：蒸馏记录。材料统计、结果、错误、起止时间；失败的记录也会保留，便于排查。

`pet_conversations` 复用上一轮的宠物会话表，`active_skill_ids` 现在指向 `author_skills.id`。

## 3. config 结构

服务层 `author_skills.normalize_config()` 是唯一校验入口，未知字段直接丢弃，保证编译 prompt 时可预期。

### writing

```json
{
  "tone": "克制、短句",
  "styleRules": ["先给结论", "每段只讲一件事"],
  "openings": ["反常识问题开场"],
  "structures": ["结论-论据-行动"],
  "vocabulary": ["拆解", "落地"],
  "taboos": ["赋能", "闭环"],
  "materialPolicy": "只写可核实的数据",
  "exemplars": [{ "title": "范例标题", "excerpt": "节选" }],
  "expression": { "sentenceLength": "20 字内", "punctuation": "少用感叹号" }
}
```

### persona

```json
{
  "mentalModels": [{ "name": "逆向思考", "description": "先想怎么会失败", "application": "决策前列失败清单" }],
  "heuristics": ["不预测宏观，只找明显错误定价"],
  "values": ["长期主义"],
  "boundaries": ["材料未覆盖的领域不强行给结论"],
  "expression": { "style": "直白", "tone": "冷静", "habits": ["先反问再回答"] }
}
```

### knowledge

```json
{
  "overview": "这套方法解决什么问题",
  "capabilities": [
    { "name": "需求拆解", "cognition": "先分角色再分场景", "examples": ["案例 A"], "application": "写前列三条验收" }
  ],
  "glossary": [{ "term": "RIA", "definition": "认知-实例-应用" }],
  "cases": [{ "title": "案例标题", "summary": "一句话结论" }],
  "counterexamples": ["只堆术语不给例子"]
}
```

## 4. API

前缀 `/api/v1/skill-center`，全部要求 `studio.use` 权限，数据按登录用户隔离。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/skills?type=writing` | 列表，可按类型过滤 |
| POST | `/skills` | 新建（同名 409） |
| GET | `/skills/{id}` | 详情 |
| PUT | `/skills/{id}` | 编辑（`config` 整体替换，版本 +1） |
| DELETE | `/skills/{id}` | 删除（级联版本与蒸馏记录） |
| POST | `/skills/{id}/duplicate` | 复制为「… 副本」 |
| POST | `/skills/{id}/distill` | 资料蒸馏，同步返回 completed |
| GET | `/runs/{id}` | 蒸馏记录 |
| GET | `/skills/{id}/export` | 导出单张（附件下载） |
| GET | `/export` | 导出整包 |
| POST | `/import` | 直接提交导出的 JSON 本体，同名自动改名 |

导出格式：`{"format":"hanyue.author-skill/v1", ...}`；整包额外带 `kind:"bundle"`。

### 蒸馏请求

```json
{
  "materials": ["全文文本", { "title": "可选标题", "content": "全文" }],
  "urls": ["https://example.com/article"],
  "personName": "可选，仅 persona",
  "tier": "quick | standard | deep"
}
```

规则：

- 材料合计少于 200 字直接 422，不猜测。
- URL 走 `core.ssrf.safe_request`（逐跳校验、1 MB / 20 篇上限），只接受 HTML / 纯文本，正文不足 100 字视为失败。
- 模型必须返回约定 JSON；成功写入 `config` 并把版本 +1，失败保留 failed 记录且技能内容不变。
- 蒸馏会覆盖当前内容，历史版本在 `author_skill_revisions` 里。

## 5. 与宠物的关系

`pet_chat.py` → `pet_conversation_service`：

1. 技能 id 先过 `author_skills.get_skill()`（本人 + 存在），越权 404，不静默降级。
2. `build_system_prompt()` 把选中的技能编译成指令块，附在系统提示里；用户本次指令优先级更高。
3. 生成结果只写进对话与 `last_generation`；**不改当前稿件**。用户在气泡里点「新建成稿」才新建一篇并打开。
4. 成稿台的「改稿」模式走原有 `studioApi.chat`，结果直接写入当前稿件，段落锚点由正文选中决定。

## 6. 前端

- `views/SkillCenterView.vue`：技能卡片库、类型筛选、搜索、编辑抽屉、蒸馏抽屉、导入导出。路由 `/skills`，菜单「工作区 → 技能中心」。
- `components/PetChatPanel.vue`：悬浮助手，创作 / 改稿双模式；技能列表来自 `/skill-center/skills`。

## 7. 测试

`apps/api/tests/test_skill_center.py` 覆盖：CRUD 与用户隔离、同名冲突、config 形状校验、蒸馏成功（版本与规则生效）、无模型失败不动内容、材料不足与越权、导入导出 round-trip、宠物按技能生成。

`apps/api/tests/test_pet_chat.py` 覆盖：会话持久化、清空技能、跨用户隔离、无模型时明确失败、意图优先级。

## 8. 已知边界

- 蒸馏是同步请求：材料很大时响应会久（受模型时延影响），当前不做后台队列。
- 人物与知识的编辑表单覆盖了主要字段，但 `exemplars`（范例）暂未提供逐条编辑 UI，可通过导入 JSON 维护。
- 创作规则模块已删除（`/writing-rules` 路由、菜单、权限点与表均已移除）；技能中心不受影响。
