# Skill 维护指南

本文说明 AI 工作台里 Skill 的数据来源、运行时语义和改动方式。目标：**同一份 Skill 只有一条写路径，出厂数据与用户数据分家**。

## 1. 三层模型

| 层 | 位置 | 谁维护 | 说明 |
|---|---|---|---|
| 出厂定义 | `apps/api/app/resources/skills/builtin_skills.yaml` | 开发 | 唯一来源，启动时同步进库 |
| 注册表 | `skill_registry_entries`（数据库） | 服务端 | 内置行 `owner_user_id=NULL`；自定义行归属用户 |
| 工作区授权 | `workspace_skills` | 用户 | `enabled` + `granted_tool_ids`，按工作区隔离 |

代码入口：

- `app/services/skill_registry.py`：加载、同步、创建、定制、编辑、启停、运行时解析、checksum
- `app/services/skill_manifest.py`：manifest 结构校验
- `app/modules/skills.py`：`/skills` 全部路由（唯一入口）
- `app/modules/workspaces.py`：`PATCH /workspaces/{id}/skills/{id}`，与 `/skills` 共用同一校验

## 2. 新增或修改内置 Skill

只改 YAML，然后重启服务：

```yaml
  - key: style-example
    version: "1.0.1"          # 改内容必须抬版本
    name: 示例风格
    description: 一句话说明
    category: style            # flow | style
    tools: [generate_article]  # 必须已在 agent_policy.TOOL_CATALOG 登记且非 forbidden
    manifest:
      tone: 语气描述
      styleRules:
        - 结构：...
        - 规则...
      defaultTheme: ink-green  # 必须是 wechat_layout.PRESET_THEMES 的 id
```

manifest 还支持五个可选策略字段：`workflow`（最多 12 步，每步 `tool` 必须在该 Skill 的 `tools` 里）、
`reviewRules`（成稿检查，最多 20 条）、`factPolicy`、`imagePolicy`、`repairPolicy`，结构见
`skill_manifest.py`。不写这五项等于不启用，落库内容与 checksum 与旧版一致。
`workflow` 只被 Agent 规划读取，AI 成文不读它。一次运行里带非空 `workflow` 的 Skill 最多一个：
有则按清单顺序执行、不再询问规划模型，清单里任一步未授权则整次运行失败；
多于一个则运行失败（`workflow_conflict`）。没有 workflow 的文风 Skill 仍走规划模型或关键词降级。
`factPolicy` 与 `imagePolicy` 写在这份 manifest 上时，会追加进成文系统提示词；没写就不注入。
检查结果只报告，不改稿、不落库。定制内置风格时 manifest 按顶层字段合并，
只提交 `tone` 等字段不会丢掉未改的策略字段。要换文风，从带 workflow 的 Skill fork 出个人副本，
不要再同时勾选一个文风 Skill。

启动时 `sync_builtin_skills` 会：

- 按 `skill_key` upsert 内置行（owner 为 NULL）；
- 删除 YAML 中已移除的内置行，并清理对应工作区授权；
- 不触碰任何 `source=custom` 的用户数据。

YAML 写错（重复 key、未登记工具、非法主题、规则类型不对）会**启动失败**，而不是静默少一个 Skill。

`category: style` 不需要写 `promptTemplate`，服务端注入 `skill_registry.DEFAULT_ARTICLE_PROMPT`，保证输出契约只有一份。

## 3. 两种「启用」语义

| 路径 | 检查什么 | 说明 |
|---|---|---|
| Agent 运行（`agent_runtime`） | Skill 可见 + `trusted` + 工作区 `enabled` + 工具在授权内 + 风险上限 | 自动执行，必须逐层收窄 |
| AI 成文（`ai_article.resolve_skill`） | Skill 可见 + `trusted` | 用户在创作空间手动选用，**不要求启用** |

设置页文案与此一致：启用只影响 Agent 自动调用。写作风格默认未启用是正常状态。

## 4. 信任与归属

- 用户自建 Skill：`owner_user_id=本人`、`trust_status=trusted`，仅本人可见；
- 内置 Skill：`owner=NULL`、`trusted`，全员可见；
- 历史遗留的 `source=custom` 且 owner 为空的行：不展示、不解析、无法启停。用
  `python scripts/repair_skills.py` 查看，确认后加 `--delete` 清理。
- 目前没有「导入外部 Skill」流程，因此没有 untrusted → trusted 的审核接口；将来接入导入时再补。

## 5. checksum 与版本

所有写入路径统一调用：

```python
sha256(json.dumps({
    "skill_key": ..., "version": ...,
    "manifest": ..., "allowed_tool_ids": [...],
}, sort_keys=True))
```

- checksum 是内容指纹，用于判断「内容是否被改过」；
- version 由来源声明（内置看 YAML，自定义由用户填写），改内容时请同步抬版本；
- 内置行的 version/checksum 随 YAML 同步，自定义行随 PUT 重新计算。

## 6. 数据库约束与迁移

- 新建库：`skill_registry_entries` 上有 `(skill_key, owner_user_id)` 唯一约束；
- 已有库：启动时 `core/db.py` 先按 `(skill_key, COALESCE(owner_user_id,''))` 去重，再创建表达式唯一索引，保证内置行（owner 为 NULL）也不会重复。

## 7. 排查清单

| 现象 | 先看哪里 |
|---|---|
| 设置页 Skill 数量不对 | `agent_status.status_snapshot` 只统计可见行；确认是否登录了预期账号 |
| 新建风格启用报 403 | 该行是否 owner 为空（历史数据）；跑 `repair_skills.py` |
| 风格改了不生效 | checksum 是否变化；`ai_article.resolve_skill` 是否命中了个人副本 |
| 启动直接失败 | YAML 校验错误，日志里有具体 key |
