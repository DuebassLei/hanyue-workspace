# Skill 维护指南

本文说明 AI 工作台里 Skill 的数据来源、运行时语义和改动方式。目标：**同一份数据只有一条写路径，出厂内容与管理员/用户改动分家**。

## 1. 数据分层

| 层 | 位置 | 谁维护 | 说明 |
|---|---|---|---|
| 出厂数据 | `apps/api/app/resources/writing_rules_factory.json` | 开发 | 唯一权威源：14 个内置 Skill（3 个 flow、11 个 style）、152 条规则行、7 条提示词规则 |
| 派生条目 | `apps/api/app/resources/skills/builtin_skills.json` | 脚本生成 | `export_factory_to_builtin.py` 的产物，`skill_registry` 读取的唯一入口；手改不生效 |
| 规则行 / 提示词规则 | `skill_writing_rules`、`prompt_rules`（数据库） | 管理员 / 用户 | 运行期实际的规则来源；全局行 `owner_user_id=NULL`，个人副本行归属用户 |
| 注册表 | `skill_registry_entries`（数据库） | 服务端 | 内置行 `owner_user_id=NULL` 且只读；自定义行归属用户 |
| 工作区授权 | `workspace_skills` | 用户 | `enabled` + `granted_tool_ids`，按工作区隔离 |

代码入口：

- `app/services/skill_registry.py`：加载、同步、创建、定制、编辑、启停、运行时解析、checksum
- `app/services/writing_rules.py`：规则行与 manifest 规则型 key 的换算（`compose_manifest` / `effective_manifest`）与校验
- `app/services/writing_rules_factory.py`：出厂数据的导入与按出厂重建（`import_factory` / `reset_skill_rules_to_factory`）
- `app/services/prompt_rules.py`：全局提示词与词表的读取（带进程内短缓存）
- `app/services/skill_manifest.py`：manifest 结构校验
- `app/modules/writing_rules.py`：`/writing-rules` 全部路由（创作规则管理）
- `app/modules/skills.py`：`/skills` 全部路由（唯一入口）
- `app/modules/workspaces.py`：`PATCH /workspaces/{id}/skills/{id}`，与 `/skills` 共用同一校验
- `apps/api/scripts/export_factory_to_builtin.py`：把出厂 JSON 派生为 `builtin_skills.json`，支持 `--check`
- `apps/api/scripts/export_builtin_to_factory.py`：一次性迁移脚本（旧 YAML → 出厂 JSON），只在开发期跑，产物入库

## 2. 新增或修改内置 Skill

内置 Skill 的出厂定义只有 `writing_rules_factory.json` 一份，条目元数据与规则行都写在里面；`builtin_skills.yaml` 已删除，历史迁移逻辑保留在上面那个一次性脚本里。修改流程固定为四步，都在 `apps/api` 目录下执行：

1. 改 `app/resources/writing_rules_factory.json`；
2. `python scripts/export_factory_to_builtin.py` 重新生成派生文件（产物要一起提交）；
3. `python scripts/export_factory_to_builtin.py --check` 校验一致，不一致时非零退出，可直接放进 CI；
4. 重启服务。

条目本身只保留元数据，规则一律作为 `rules` 里的规则行存在：

```json
{
  "key": "style-example",
  "version": "1.0.1",
  "name": "示例风格",
  "description": "一句话说明",
  "category": "style",
  "tools": ["generate_article"],
  "rules": [
    {"ruleType": "tone", "ruleKey": "default", "payload": {"text": "语气描述"}, "sortOrder": 0, "enabled": true},
    {"ruleType": "style_rule", "ruleKey": "structure", "payload": {"text": "结构：..."}, "sortOrder": 0, "enabled": true},
    {"ruleType": "theme", "ruleKey": "default", "payload": {"text": "ink-green"}, "sortOrder": 0, "enabled": true}
  ]
}
```

字段约定：

- `key` 全局唯一；`version` 改内容必须抬，checksum 与内置行版本都取自它；
- `category` 只能是 `flow` 或 `style`；
- `tools` 必须已在 `agent_policy.TOOL_CATALOG` 登记且非 forbidden，工作流步骤里的工具也必须在这个列表内；
- `tone`、`styleRules`、`workflow`、`reviewRules`、`targetLength`、`factPolicy`、`imagePolicy`、`repairPolicy`、`exemplars` **都不写在条目上**，它们是规则行，由 `compose_manifest` 在运行期合成；
- 派生文件的 `manifest` 只保留合成后的规则型 key，以及 `_build_builtin_manifest` 注入的 `input` / `output` / `riskLevel`。它是生成逻辑的不动点，不是可手改的源。

规则行与 manifest 字段的对应关系：

| ruleType | 合成到 manifest | 形态 | 说明 |
|---|---|---|---|
| `tone` | `tone` | 单行 | 语气描述 |
| `style_rule` | `styleRules` | 多行 | 顺序按 `sortOrder` |
| `prompt_template` | `promptTemplate` | 单行 | 每个会成文的 Skill 各一行（11 个 style + `article-drafting`），出厂值为 `DEFAULT_ARTICLE_PROMPT` 原文；保留 `{tone}` 占位符 |
| `theme` | `defaultTheme` | 单行 | 取值必须在 `wechat_layout.PRESET_THEMES` 里 |
| `target_length` | `targetLength` | 单行 | `payload.value` 为整数 |
| `workflow_step` | `workflow` | 多行 | 每步 `tool` 必须在 `tools` 内，最多 12 步 |
| `review_rule` | `reviewRules` | 多行 | 成稿检查，最多 20 条，结果只报告 |
| `fact_policy` | `factPolicy` | 单行 | 策略块 |
| `image_policy` | `imagePolicy` | 单行 | 策略块 |
| `repair_policy` | `repairPolicy` | 单行 | 策略块 |
| `exemplar` | `exemplars` | 多行 | 开头范例，`title` + `excerpt` |

单行类每个 Skill 每类只能有一行，`ruleKey` 固定为 `default`；列表类必须给 `ruleKey`（如 `wf-1`、`ex-1`）。

`workflow` 只被 Agent 规划读取，AI 成文不读它。一次运行里带非空 `workflow` 的 Skill 最多一个：有则按清单顺序执行、不再询问规划模型，清单里任一步未授权则整次运行失败；多于一个则运行失败（`workflow_conflict`）。没有 workflow 的文风 Skill 仍走规划模型或关键词降级。

启动顺序固定为：先 `writing_rules_factory.import_factory(session)`，再 `skill_registry.sync_builtin_skills(session)`。

- `import_factory` 只补缺失的 `(skill_key, rule_type, rule_key)` 全局行与提示词行，**不覆盖已有行**；
- `sync_builtin_skills` 读派生 JSON，按 `skill_key` upsert 内置条目、清掉已下线项及其工作区授权，不触碰 `source=custom` 的用户数据。

由此有两条必须记住的行为：管理员改过的全局规则重启后保留；被删掉的规则行重启后会从出厂数据长回来。所以「全部关掉」是写禁用行（`enabled=false`），不是删行。

出厂数据或派生文件写错（重复 key、缺 `rules`、未登记工具、非法主题、规则类型不对、单行类重复）会**启动失败**，日志里有具体 key，而不是静默少一个 Skill。

新增出厂规则行（例如给每个成文 Skill 补 `prompt_template`）只需改 `writing_rules_factory.json` 并重跑派生脚本：已有库在下次启动时由 `import_factory` 自动补齐，无需写迁移。反过来，**删**一条出厂行不会让已有库的旧行消失，需要 `reset` 或手工删。

## 3. 改规则与提示词

日常改规则不需要动代码，两条路径：

**前端**：`/writing-rules`「创作规则」页。读接口要求 `writing_rules.use`，所有登录用户可用；写全局规则、按出厂重建、改提示词规则要求 `writing_rules.manage`，仅管理员。普通用户只能编辑自己的副本。

**API**（前缀 `/api/v1`）：

- `GET /writing-rules/skills`：列表，带全局与个人规则条数；
- `GET /writing-rules/skills/{skillKey}?scope=global|personal`：读取整组规则；
- `PUT /writing-rules/skills/{skillKey}/rules?scope=...`：整组替换（表单保存）；
- `POST|PATCH|DELETE /writing-rules/skills/{skillKey}/rules...`：单条增改删；
- `POST /writing-rules/skills/{skillKey}/reset`：按出厂重建该 Skill 的全局规则；
- `GET|PUT|POST|DELETE /writing-rules/prompts...`：全局提示词与词表；
- `GET /writing-rules/preview?skillKey=&scope=&kind=article|outline|repair`：预览最终提示词。

全局与个人副本的区别：全局行（`owner_user_id=NULL`）对所有人生效，改它影响全站；个人副本行只在 `scope=personal` 下读写，只影响本人。个人行按类型覆盖全局——单行类有个人行就用个人的；列表类只要个人启用行非空就整体替换全局，不再和全局混合。`fork_builtin` 定制时会把当时的全局规则行复制成个人副本行（`source='user'`），此后两者独立演进，全局再改不会同步到已存在的副本。

几条容易踩的规则：

- **禁用不删行**：全局整组保存传空数组时，接口会把现有全局行逐条改成 `enabled=false`，而不是删除；单条关掉同理。真删行会在下次启动被 `import_factory` 从出厂恢复，看起来像「改了没用」。个人副本清空则不同——个人行没了就回到沿用全局。
- **按出厂重建有风险**：`reset` 会删掉该 Skill 的全部全局规则行（包括管理员改过的）再按出厂重建，只影响这一个 Skill，个人副本不受影响。前端必须二次确认。
- **提示词有额外约束**：`category` 只能是 `contract`、`system`、`outline`、`repair`、`tone_lexicon`、`workflow_note`；保存 `contract` 类时 `title`、`markdown`、`summary`、`coverPrompt`、`inlinePrompts` 五个必需字段缺一不可；出厂提示词行不可删除（返回 `factory_rule_locked`），要下线请改成停用。写接口提交后会清 `prompt_rules` 的进程内缓存，下一次生成立即生效。
- **preview 只拼不调**：不请求模型，直接把当前规则表合成的最终提示词与所用规则行的 key / version 返回，用来核对「为什么生成结果和我改的不一样」。
- **内置条目本身仍然只读**：唯一约束与 `fork_builtin` 的只读模型不变，管理员改的是 `skill_writing_rules` 里的全局行，不去改内置 `SkillRegistryEntry`，避免「一个管理员一改影响所有用户」以及和定制模型冲突。

## 4. 两种「启用」语义

| 路径 | 检查什么 | 说明 |
|---|---|---|
| Agent 运行（`agent_runtime`） | Skill 可见 + `trusted` + 工作区 `enabled` + 工具在授权内 + 风险上限 | 自动执行，必须逐层收窄 |
| AI 成文（`ai_article.resolve_skill`） | Skill 可见 + `trusted` | 用户在创作空间手动选用，**不要求启用** |

设置页文案与此一致：启用只影响 Agent 自动调用。写作风格默认未启用是正常状态。规则行的启用/停用只决定规则是否参与合成，与工作区里的 Skill 启用是两件事。

## 5. 信任与归属

- 用户自建 Skill：`owner_user_id=本人`、`trust_status=trusted`，仅本人可见；
- 内置 Skill：`owner=NULL`、`trusted`，全员可见，条目只读；
- 从内置定制的副本：`source=custom`、`owner=本人`，同时持有一份独立的规则行副本；
- 历史遗留的 `source=custom` 且 owner 为空的行：不展示、不解析、无法启停。用
  `python scripts/repair_skills.py` 查看，确认后加 `--delete` 清理。
- 目前没有「导入外部 Skill」流程，因此没有 untrusted → trusted 的审核接口；将来接入导入时再补。

## 6. checksum 与版本

所有写入路径统一调用：

```python
sha256(json.dumps({
    "skill_key": ..., "version": ...,
    "manifest": ..., "allowed_tool_ids": [...],
}, sort_keys=True))
```

- checksum 是内容指纹，用于判断「内容是否被改过」；
- version 由来源声明：内置条目看派生 JSON（最终来自出厂 JSON），自定义由用户填写；改内容时请同步抬版本；
- 内置行的 version/checksum 随派生文件同步，自定义行随 PUT 重新计算；
- checksum 只覆盖 `skill_registry_entries` 的条目内容，不含 `skill_writing_rules` 的行。管理员在创作规则页改全局规则不会改 checksum，也不该改；只有改出厂 JSON 并重新生成派生文件，下一次启动同步条目时才会变。

## 7. 数据库约束与迁移

- 新建库：`skill_registry_entries` 上有 `(skill_key, owner_user_id)` 唯一约束；
- `skill_writing_rules` 上有 `(skill_key, owner_user_id, rule_type, rule_key)` 唯一约束，`owner_user_id` 为空即全局行；
- `prompt_rules.key` 唯一，`category` 走 check 约束；
- 已有库：启动时 `core/db.py` 先按 `(skill_key, COALESCE(owner_user_id,''))` 去重，再创建表达式唯一索引，保证内置行（owner 为 NULL）也不会重复。

## 8. 排查清单

| 现象 | 先看哪里 |
|---|---|
| 设置页 Skill 数量不对 | `agent_status.status_snapshot` 只统计可见行；确认是否登录了预期账号 |
| 新建风格启用报 403 | 该行是否 owner 为空（历史数据）；跑 `repair_skills.py` |
| 风格改了不生效 | 是否只改了派生文件没重跑脚本；`ai_article.resolve_skill` 是否命中了个人副本；该类型是否有个人规则行覆盖全局 |
| 规则改了，重启又变回去 | 是不是删了行而不是禁用；删掉的行会被 `import_factory` 从出厂恢复 |
| 派生文件与出厂不一致 | 在 `apps/api` 下跑 `python scripts/export_factory_to_builtin.py --check`，按提示重跑生成 |
| 启动直接失败 | 出厂 JSON 或派生文件校验错误，日志里有具体 key |
