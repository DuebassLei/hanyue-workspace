# 写作规则表 + 创作规则管理页 Implementation Plan

> **For agentic workers:** 本计划由专家团执行：Lead 串行分派，实现者不动测试断言，审查者不改码。

**Goal:** 把文章创作链路中所有「提示词 / 规则 / 词表」从代码常量和 `SkillRegistryEntry.manifest` JSON 中迁出，落到独立数据库表，并在新增的「创作规则」管理页上提供增删改查；`builtin_skills.yaml` 退役，内置规则改由库内初始化脚本导入。

**Architecture:** 新增两张表——`skill_writing_rules`（按 Skill + 归属人一行一条规则，规则类型 `rule_type` 决定字段语义）与 `prompt_rules`（全局提示词/词表，管理员维护）。规则读入口统一为 `skill_registry` 的合成函数：把规则表行合成为下游既有的 manifest 形态，`ai_article` / `agent_runtime` / `content` 等改为经合成函数取得**规则型 key**，不再读 `entry.manifest`。`manifest` 仅保留元数据（input/output/riskLevel 等）。

**Tech Stack:** Python 3 + FastAPI + SQLAlchemy 2 + Pydantic v2；Vue 3 + naive-ui；pytest。

## 已确认的产品决策（用户拍板）

| # | 决策 | 落地要点 |
|---|---|---|
| D1 | 维护范围 = 文章写作链路 | Skill 规则 + 输出契约 / 通用系统规则 / 大纲提示词 / AI 味词表与句式 / 修复提示词 / 大纲与素材注入指令 |
| D2 | 管理员全局 + 用户副本 | 新权限点 `writing_rules.manage`；全局规则（owner 为空）仅该权限可写；个人副本仍按人覆盖 |
| D3 | 规则全部拆成独立规则表 | 一行一条规则，可逐条启停/排序/删除 |
| D4 | 新建「创作规则」页 | 新路由 `/writing-rules`、新菜单、权限点 |
| D5 | 规则入表，下游全部改读规则表 | 删掉所有对 `manifest` 规则型 key 的运行期读取 |
| D6 | **移除 YAML**，以配置表 + 固化初始化脚本初始化 | 删除 `builtin_skills.yaml` 与其启动同步；新增 `app/resources/writing_rules_factory.sql`（或 .py 数据文件）+ 幂等导入脚本；提供「按出厂重建」入口 |
| D7 | 契约可改文案，JSON 字段锁定 | 契约规则保存时后端校验 5 个 JSON 字段名仍在；前端字段名只读并高亮 |

## 不在本次范围
- 小说（novel_*）、分镜拆解、角色抽取的系统提示词（D1 已排除）。
- 多平台发布适配。

## Global Constraints
- 测试：`cd apps/api && .venv/Scripts/python.exe -m pytest tests/<file> -q`；全量只在最后一段。
- 测试文件顶层不 import `app.core.db`；用 `tests/conftest.py` 的 `client` fixture 与 `login()`。
- 不新增 alembic：新表靠 `Base.metadata.create_all`；新列必须在模型上带 `server_default`（`core/db.py:527-603` 自动补列）。
- API 响应 camelCase（`CamelModel`）。
- **用户 WIP**：`agent_runtime.py`、`skill_manifest.py` 已有未提交改动；`tests/test_agent_outline_chain.py` 为未跟踪文件。只 `git add` 本计划列出的文件，绝不 `git add -A`；**不自动 commit / push**。
- 文件所有权：同一文件同一时间只归一个实现者。

---

## 设计契约（P1）

### 1. 数据模型

**`apps/api/app/models.py` 新增（放在 `SkillRegistryEntry` 之后）：**

```python
class SkillWritingRule(Base, TimestampMixin):
    """Skill 的写作规则：一行一条，type+key 唯一。

    owner_user_id 为空 = 全局规则（内置 / 管理员维护）；非空 = 该用户的个人副本。
    manifest 里不再保存规则型 key，运行时由 skill_registry.compose_manifest 合成。
    """

    __tablename__ = "skill_writing_rules"
    __table_args__ = (
        UniqueConstraint("skill_key", "owner_user_id", "rule_type", "rule_key", name="uq_swr_rule"),
        CheckConstraint(
            "rule_type IN ('tone','style_rule','prompt_template','theme','target_length',"
            "'workflow_step','review_rule','fact_policy','image_policy','repair_policy','exemplar')",
            name="ck_swr_type",
        ),
        Index("ix_swr_lookup", "skill_key", "owner_user_id", "rule_type", "sort_order"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    skill_key: Mapped[str] = mapped_column(String(120), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    rule_type: Mapped[str] = mapped_column(String(24))
    rule_key: Mapped[str] = mapped_column(String(64))
    # 标量类（tone/prompt_template/theme/target_length 策略块）：存 JSON 值或对象；列表类条目：存单条对象
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # factory = 出厂初始化导入，可被「按出厂重建」覆盖；user = 界面创建
    source: Mapped[str] = mapped_column(String(16), default="user", server_default="user")


class PromptRule(Base, TimestampMixin):
    """全局提示词与词表：一行一个 key，管理员维护。"""

    __tablename__ = "prompt_rules"
    __table_args__ = (
        CheckConstraint(
            "category IN ('contract','system','outline','repair','tone_lexicon','workflow_note')",
            name="ck_prompt_rules_category",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)   # 词表/句式等结构化规则
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    source: Mapped[str] = mapped_column(String(16), default="factory", server_default="factory")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
```

**`SkillRegistryEntry`**：`__table_args__` 中 `uq_skill_owner_key(skill_key, owner_user_id)` **放宽为 `uq_skill_owner_key(skill_key, COALESCE(owner_user_id,''))`** —— 管理员全局编辑仍修改内置行（owner=NULL），避免同键两行。落地方式：保留 ORM 唯一约束但不再依赖它，新增表达式唯一索引（与 `core/db.py:291-307` 的 `uq_skill_owner_key` 同名口径一致，**先核对现有表达式索引名**，冲突则命名为 `uq_skill_owner_key_expr`）。

### 2. 规则类型 → manifest key 映射（`skill_writing_rules`）

| rule_type | 行粒度 | rule_key | payload 示例（存 JSON 对象） | 合成到 manifest |
|---|---|---|---|---|
| `tone` | 单行 | `default` | `{"text": "语气平实有深度…"}` | `tone: str` |
| `style_rule` | 多条 | `sr-<n>` | `{"text": "不用 emoji"}` | `styleRules: [str]` |
| `prompt_template` | 单行 | `default` | `{"text": "你是资深公众号主笔…{tone}…"}` | `promptTemplate: str` |
| `theme` | 单行 | `default` | `{"text": "ink-green"}` | `defaultTheme: str`（校验属于 `PRESET_THEMES`） |
| `target_length` | 单行 | `default` | `{"value": 1200}` | `targetLength: int` |
| `workflow_step` | 多条 | `wf-<n>` | `{"tool": "create_outline", "args": {}}` | `workflow: [{tool,args}]` |
| `review_rule` | 多条 | rule id | `{"id","kind","value","field","message"}` | `reviewRules: [...]` |
| `fact_policy` | 单行 | `default` | `{"requireSources":false,"forbidUnverifiedQuotes":true,"placeholder":"〔待核实〕","extraInstructions":[]}` | `factPolicy: {...}` |
| `image_policy` | 单行 | `default` | `{"coverAspectRatio":"3:4",...}` | `imagePolicy: {...}` |
| `repair_policy` | 单行 | `default` | `{"retries":1,"requireContract":true}` | `repairPolicy: {...}` |
| `exemplar` | 多条 | `ex-<n>` | `{"title": "...", "excerpt": "..."}` | `exemplars: [...]` |

校验：复用 `skill_manifest.py` 的 pydantic 子模型（`ReviewRule` / `FactPolicy` / `ImagePolicy` / `RepairPolicy` / 新增 `Exemplar`），**单行写入时只校验该行**，不再整块覆盖。

### 3. `prompt_rules` 出厂 key 清单（初始化脚本导入，内容取自现有代码常量原文）

| key | category | content 来源（迁移前位置） | 消费方改造后 |
|---|---|---|---|
| `article.output_contract` | contract | `ai_article.ARTICLE_OUTPUT_CONTRACT`（:29-47） | `ai_article.build_system_prompt` |
| `gateway.system_rules` | system | `ai_gateway.SYSTEM_RULES`（:41-44） | `ai_gateway` 生成入口 |
| `agent.outline_system` | outline | `agent_runtime.OUTLINE_SYSTEM_PROMPT`（:28-31） | `create_outline` 工具 |
| `agent.outline_inject` | workflow_note | `agent_runtime` 中「参考大纲（结构可微调…）」字面量 | `generate_article` 拼 prompt |
| `article.repair_prompt` | repair | 新增：现 `build_repair_prompt` 固定前后缀「下面是一篇公众号文章草稿…」「保持原文的立场、素材…」 | `article_workflow.build_repair_prompt` |
| `tone_lexicon.sentence_openers` | tone_lexicon | `article_workflow._SENTENCE_START_PHRASES`；payload `{"window": 12, "phrases": [...]}` | `check_ai_tone` |
| `tone_lexicon.buzzwords` | tone_lexicon | `article_workflow._WORD_RULES`；payload `{"rules": [{"id","word"}]}` | `check_ai_tone` |
| `tone_lexicon.patterns` | tone_lexicon | 新增 `_PATTERN_RULES`（`.superpowers/sdd/.../task-5` 计划中的 `pattern-era` / `pattern-with`）；payload `{"rules":[{"id","pattern"}]}` | `check_ai_tone` |
| `tone_lexicon.first_then` | tone_lexicon | 现内联「首先/其次」规则；payload `{"words": ["首先", "其次"], "ruleId": "first-then"}` | `check_ai_tone` |
| `article.compile_style` | workflow_note | 描述：style 类内置 Skill 缺 promptTemplate 时的默认值来源（原 `DEFAULT_ARTICLE_PROMPT`）。**保留为代码常量工厂默认**，不单独成行——避免与 `prompt_template` 规则重复维护 | — |

> 未列入 `prompt_rules` 的常量保持代码：`PLANNER_INSTRUCTION`（Agent 规划器内部协议，非写作规则）、`_fact_policy_text`/`_image_policy_text` 的固定文案骨架（由 4 条策略字段驱动，文案本身结构化）。

### 4. 服务层接口（`apps/api/app/services/writing_rules.py`，新文件）

```python
WRITING_RULE_TYPES: dict[str, str]  # rule_type -> 中文名（前端/文档共用）

def list_global_rules(db, skill_key: str) -> list[dict]
def list_personal_rules(db, user_id: str, skill_key: str) -> list[dict]
def compose_manifest(db, entry) -> dict
    """返回 entry.manifest（元数据） + 规则表合成的规则型 key。个人规则覆盖全局同名 type 的单行类；
    列表类（style_rule/workflow_step/review_rule/exemplar）个人非空时整体替换全局。"""
def replace_rules(db, *, skill_key, owner_user_id, rules: list[dict], keep_owner_none_as_global: bool)
    """整组写入：先按 (rule_type) 分组校验全部行，再删除同 owner 旧行、插入新行（同一事务）。
    全局写入需调用方已完成权限校验。"""
def upsert_rule(db, *, skill_key, owner_user_id, rule: dict) -> dict   # 单条新增/修改
def delete_rule(db, *, skill_key, owner_user_id, rule_type: str, rule_key: str) -> None
def reset_skill_rules_to_factory(db, skill_key: str) -> dict           # 删全局行后按出厂脚本重建该 skill
```

**错误码**（`ApiError`，与现状一致）：`invalid_rule` 422、`rule_not_found` 404、`permission_denied` 403、`tool_not_in_skill` / `unknown_tool` 409。

### 5. 提示词规则读取（`apps/api/app/services/prompt_rules.py`，新文件）

```python
_DEFAULT_CACHE_TTL = 60  # 秒；避免每次生成都查库
def get_prompt_rule(db, key: str, default: str = "") -> str
def get_prompt_payload(db, key: str) -> dict
def invalidate_cache() -> None   # 管理端写入后调用
```
请求级缓存（按 `db` 对象 id + 键），写接口直接 `invalidate_cache()`。

### 6. HTTP API（`apps/api/app/modules/writing_rules.py`，新文件）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/v1/writing-rules/skills` | 可编辑的 Skill 列表（全局 + 个人副本），含规则计数 | `writing_rules.use` |
| GET | `/api/v1/writing-rules/skills/{skill_key}?scope=global\|personal` | 该范围规则分组返回 + 合成后的有效 manifest（只读预览） | `writing_rules.use` |
| PUT | `/api/v1/writing-rules/skills/{skill_key}/rules?scope=` | 整组替换（表单保存） | global: `writing_rules.manage`；personal: 本人 |
| POST | `/api/v1/writing-rules/skills/{skill_key}/rules?scope=` | 单条新增 | 同上 |
| PATCH | `/api/v1/writing-rules/skills/{skill_key}/rules/{rule_id}` | 单条修改（payload/enabled/sortOrder） | 同上 |
| DELETE | `/api/v1/writing-rules/skills/{skill_key}/rules/{rule_id}` | 单条删除 | 同上 |
| POST | `/api/v1/writing-rules/skills/{skill_key}/reset` | 按出厂重建（仅 global） | `writing_rules.manage` |
| GET | `/api/v1/writing-rules/prompts` | 全局提示词规则列表 | `writing_rules.use` |
| PUT | `/api/v1/writing-rules/prompts/{key}` | 修改 content/payload/enabled | `writing_rules.manage` |
| POST | `/api/v1/writing-rules/prompts` | 新增（自定义 key） | `writing_rules.manage` |
| DELETE | `/api/v1/writing-rules/prompts/{key}` | 删除（仅 source=user） | `writing_rules.manage` |
| GET | `/api/v1/writing-rules/preview?skillKey=&scope=&kind=article\|outline\|repair&sample=...` | 生成「最终 system prompt / 大纲提示词 / 修复提示词」预览（不调模型） | `writing_rules.use` |

契约规则（`category=contract`）保存时校验 5 个必需字段：`title, markdown, summary, coverPrompt, inlinePrompts`，缺任一返回 422 `contract_field_missing`。

### 7. 权限点

- `core/rbac.py` `PERMISSION_CATALOG` 增：`("writing_rules.use", "创作规则查看", "创作", 125)`、`("writing_rules.manage", "创作规则管理", "系统", 340)`。
- `superadmin` 天然全量；`DEFAULT_ROLE_MATRIX` 把 `writing_rules.use` 加入 `_CREATE_BASE`（人人可维护个人副本），`writing_rules.manage` **不加**（仅超管/管理员角色）。
- `rbac_service.MENU_SEED` 增：`system` 组内 `_page("writing-rules", "创作规则", "规则", "settings", "writing_rules.use")`（系统组在「设置」前）。

### 8. 出厂初始化脚本

- 数据文件：`apps/api/app/resources/writing_rules_factory.json`（含 `skills: [{key, version, name, description, category, tools, rules:[...]}, ...]` 与 `prompt_rules: [...]`），由 Task 2 从现有 YAML 与代码常量**逐字**迁移生成，禁止手改写文案。
- 导入：`apps/api/app/services/writing_rules_factory.py::import_factory(db, *, only_missing=True)`；`only_missing=True` 时仅对库中不存在的 (skill_key, owner=NULL) 建行与不存在 key 的 prompt_rules 建行；`False` 用于「按出厂重建」。（重建仅针对单个 skill_key 或单个 prompt key。）
- 启动：`main.py` lifespan 中把 `skill_registry.sync_builtin_skills(session)` 替换为 `writing_rules_factory.import_factory(session)`。
- 删除：`apps/api/app/resources/skills/builtin_skills.yaml`、`skill_registry.get_builtin_specs/_build_builtin_manifest/BuiltinSkillSpec/sync_builtin_skills/_builtin_keys` 对 YAML 的依赖改为查库（`skill_key IN (SELECT DISTINCT skill_key FROM skill_writing_rules WHERE owner_user_id IS NULL)`？**否**——改为 `SkillRegistryEntry.source='builtin'`）。

### 9. 执行期修订（T0 完成后回写，覆盖前文冲突处）

| # | 修订 | 原因 |
|---|---|---|
| R1 | **`SkillRegistryEntry` 唯一约束不改语义**：保留模型内 `UniqueConstraint(skill_key, owner_user_id)`（T0 已改名为 `uq_skill_owner_key_plain` 以避免与 `db.py` 表达式索引同名）；**不**放宽为「管理员全局编辑内置行」。全局规则走 `skill_writing_rules`（owner=NULL），内置条目仍是 `source=builtin` 只读，管理员改的是规则不是条目。 | 内置条目全局可编会让所有用户同时受影响，且与 `fork_builtin`/`resolve_visible` 的「内置只读」模型冲突；本方案不需要它。 |
| R2 | **不删 `builtin_skills.yaml`，改为「生成物」**：目录改名 `builtin_skills.yaml` → `builtin_skills.json`；出厂数据唯一源为 `app/resources/writing_rules_factory.json`，`builtin_skills.json` 只是其**派生物**（条目元数据 + 规则合成后的 manifest），由 `scripts/export_factory_to_builtin.py` 生成、`--check` 校验。`sync_builtin_skills` 的机制（幂等 upsert、只读约束）保留，仅换数据源；`get_builtin_specs()` 仍是对 `skill_registry` 之外代码的稳定接口。 | `get_builtin_specs` 被 `_builtin_keys`/`fork_builtin`/`agent_status`/`validate_workflow` 依赖，硬删代价高；派生文件与「移除 YAML 手改」的目标等价（任何人都不能再手改它生效）。 |
| R3 | **出厂 JSON 是全局规则的权威副本**：`import_factory(only_missing=True)` 只建缺失行；`only_missing=False` 对 `source=factory` 行覆盖、`source=user` 行保留。**不放启动路径**做覆盖式同步。 | 管理员可在「创作规则」页改全局规则；启动覆盖会把改动冲掉。 |
| R4 | **全局规则不可被「清空」**：`replace_rules(scope=global)` 的空数组按「全部禁用」落行处理，而不是删除行（删除行 → 下次启动 `only_missing` 会从出厂恢复）。 | 否则管理员关闭某类规则的操作在重启后复活。 |
| R5 | **`article.compile_style` 不成行**，`DEFAULT_ARTICLE_PROMPT/TONE/STYLE_RULES` 保留为代码兜底常量（规则表为主，缺时才用）。 | 与 §3 原文一致，执行时不再重复讨论。 |
| R6 | **出厂 JSON 覆盖范围 = 迁移前 YAML 实际内容**：3 个 flow（topic-research、article-drafting、publish-prep）+ 全部 `style-*`；`style-*` 的 `promptTemplate` 写 `DEFAULT_ARTICLE_PROMPT` 原文，`input/output/riskLevel` 等元数据留在 `builtin_skills.json` 派生文件，不入规则表。 | 规则表只存规则，不存元数据。 |
| R7 | **`test_api.py` 两条 agent 用例**（`test_agent_generate_article_writes_markdown_not_json`、`test_agent_generate_article_passes_skill_system_prompt`）的红色来自用户 WIP 的「大纲不落稿」改动（`create_outline` 不再返回 `versionId`），不属于本计划；T2 承接该 WIP 时一并修正断言。 | 已核实：测试期望 `create_outline` 结果带 `versionId`，与 WIP 新语义冲突。 |
| R8 | 提交策略（用户 m00079 决定）：**每任务完成后提交**，只 `git add` 本任务文件，中文 conventional commit，不 push；提交信息末尾注明归属任务。 | 阶段划分便于回滚。 |

---

## 分阶段任务

> 阶段 P1 契约已完成（上文）。P2 实现按 T0 → T1…T8 串行/并行；T9 全量验证；T10 独立审查。

### Task T0: 规则表基座（阻塞所有后续任务）

**Files:**
- Modify: `apps/api/app/models.py`
- Modify: `apps/api/app/core/db.py`（表达式唯一索引核对/补充）
- Modify: `apps/api/app/core/rbac.py`（两个权限点 + `_CREATE_BASE`）
- Modify: `apps/api/app/services/rbac_service.py`（菜单种子）
- Modify: `apps/api/app/services/skill_manifest.py`（去 `RepairPolicy` docstring “本批只做校验与存储”；加 `Exemplar` 与 `MAX_EXEMPLARS` 常量）
- New: `apps/api/app/services/writing_rules.py`（`WRITING_RULE_TYPES` + `_validate_rule` + `compose_manifest` + `replace_rules` + `upsert_rule` + `delete_rule` + `reset 占位`，按上文 §4）
- New: `apps/api/app/services/prompt_rules.py`（按上文 §5）
- Test: `apps/api/tests/test_writing_rules.py`（新建）

**Steps:**
1. 写测试：`compose_manifest` 对空规则表返回仅元数据 manifest；tone 单行覆盖；style_rule 多条按 sort_order 合成列表；personal 列表整体替换 global；禁用行被跳过；`replace_rules` 校验失败时库内数据不变（事务回滚）。
2. 跑测试确认失败。
3. 实现模型 + 服务（不含 HTTP/前端/出厂数据）。
4. 跑 `tests/test_writing_rules.py tests/test_db_migrations.py tests/test_rbac.py -q` → PASS。
5. `git add` 本任务文件。

**验收：** 施工单见 `.superpowers/sdd/2026-11-20-writing-rules/task-T0.md`。

---

### Task T1: 出厂数据（JSON）与条目同步换源（按 R2/R3/R6 修订）

**Files:**
- New: `apps/api/app/resources/writing_rules_factory.json`（脚本生成，逐字迁移现有 YAML 的全部 Skill 条目与 manifest 规则键；style 类的 `promptTemplate` 用 `DEFAULT_ARTICLE_PROMPT` 原文）
- New: `apps/api/scripts/export_builtin_to_factory.py`（一次性导出脚本，生成 JSON；**只在开发期运行**，产物入库）
- New: `apps/api/scripts/export_factory_to_builtin.py`（出厂 JSON → `builtin_skills.json` 派生文件，支持 `--check`）
- New: `apps/api/app/services/writing_rules_factory.py`（`load_factory()`、`import_factory(db, only_missing=True)`、`reset_skill_rules_to_factory(db, skill_key)`）
- Modify: `apps/api/app/main.py`（lifespan：`import_factory(session)` 在 `sync_builtin_skills` 之前）
- Modify: `apps/api/app/services/skill_registry.py`（`BUILTIN_RESOURCE` 指向 `builtin_skills.json`；`get_builtin_specs` 读 JSON，数据结构不变；`fork_builtin` 额外把全局规则行复制成个人行；`_builtin_keys` 不变）
- Delete: `apps/api/app/resources/skills/builtin_skills.yaml`
- Test: `apps/api/tests/test_writing_rules_factory.py`（新建）

**Steps:**
1. 写测试：启动后内置条目全部存在；`skill_writing_rules` 全局行数与出厂 JSON 逐 skill 计数一致；二次启动不新增行；管理员改一条全局规则 → 重启仍保留（R3）；删除 → 重启恢复（only_missing 语义印证 R4）；`reset_skill_rules_to_factory` 恢复单 skill；`fork_builtin` 后个人行数 = 全局行数。
2. 跑测试确认失败。
3. 导出脚本生成两个 JSON → 核对条目数与现 YAML 一致（逐 key 计数写入测试）。
4. 实现导入 + 换源。
5. `git rm` YAML。
6. 跑 `tests/test_skills.py tests/test_api.py tests/test_writing_rules_factory.py tests/test_writing_rules.py -q` → PASS（除 R7 两条）。

---

### Task T2: 下游改读规则表（回到原文章质量计划的任务 1、2 基础上）

**Files:**
- Modify: `apps/api/app/services/ai_article.py`（`resolve_skill_style`/`build_system_prompt` 改接 `compose_manifest`；接 `article.output_contract`、`gateway.system_rules`）
- Modify: `apps/api/app/services/agent_runtime.py`（`_workflow_steps` / `generate_article` 的 targetLength / `_run_writing_check` 的 reviewRules 与 imagePolicy 全部经 `compose_manifest`；`OUTLINE_SYSTEM_PROMPT` 改读 `agent.outline_system`）+ 原计划 Task 1 的 outline 联动改动
- Modify: `apps/api/app/services/ai_gateway.py`（`SYSTEM_RULES` 默认改读 `gateway.system_rules`；抬 `PROMPT_TEMPLATE_VERSION` 至 `2026-11-20.v2`，并在 request_summary 记录所用规则 key 与更新时间）
- Modify: `apps/api/app/modules/content.py`（`get_writing_flow` 经 `compose_manifest`）
- Modify: `apps/api/app/modules/models_module.py`（skill 取用不变，`build_system_prompt` 内部已切）
- Test: `tests/test_agent_outline_chain.py`（WIP 已有）、`tests/test_article_workflow.py`、`tests/test_ai_fallback.py`

**要点：** 本任务**不夹带**原计划的质量改进（修复闭环等）——只做读取路径切换，保证行为等价（同一份规则内容产生同一份 prompt）。

---

### Task T3: AI 味词表入表

**Files:**
- Modify: `apps/api/app/services/article_workflow.py`（`check_ai_tone` 增加可选参数 `lexicon: dict | None`，默认值等于现值以保持纯函数可测；新增 `load_lexicon(db)`）
- Modify: 调用点 `agent_runtime._run_writing_check`、`content.get_writing_flow`
- Test: `tests/test_article_workflow.py`

**要点：** 纯函数保留默认词表（单测不依赖 DB）；运行期由 `prompt_rules.get_prompt_payload` 传入。词表为空列表 = 该规则关闭（显式），缺失 = 用默认。

---

### Task T4: 管理 API

**Files:**
- New: `apps/api/app/modules/writing_rules.py`（§6 全部路由）
- Modify: `apps/api/app/main.py`（注册 router）
- Modify: `apps/api/app/schemas.py`（`WritingRuleIn/Out`、`SkillRulesOut`、`PromptRuleIn/Out`、`PreviewIn/Out`，均 `CamelModel`）
- Modify: `apps/api/app/core/deps.py`（若需 `require_permission` 复合用法，复用现有）
- Test: `tests/test_writing_rules_api.py`（新建）

**覆盖矩阵：** 成员可读写本人副本、不能写全局（403）；`writing_rules.manage` 可写全局；契约规则缺字段 422 且库不变；`reset` 需 manage；预览接口对内置与个人副本分别产出预期文本片段。

---

### Task T5: 前端「创作规则」页

**Files:**
- New: `apps/web/src/views/WritingRulesView.vue`
- Modify: `apps/web/src/router.js`（`/writing-rules`，`meta.permission: 'writing_rules.use'`）
- Modify: `apps/web/src/views/SettingsView.vue`（移除写作风格编辑弹窗与保存逻辑，改为指向「创作规则」页的引导卡片；避免两处编辑同一数据）
- Test: `cd apps/web && npm run build`

**页面结构：**
1. 左：Skill 列表（分组：流程型 / 风格型），每项显示「全局 N 条 / 个人 M 条」。
2. 右：作用域切换（全局 / 我的副本）+ 规则分组卡片（语气、风格规则、审稿规则、工作流、字数、事实、配图、修复、范文），每组支持增行/删行/上下移/启停。
3. 底部「提示词与词表」页签：`prompt_rules` 表格编辑（契约类显示只读 JSON 字段名提示）。
4. 「预览最终提示词」抽屉：调 `/writing-rules/preview`，展示实际生效文本与来源（全局/个人）。
5. 危险操作：「按出厂重建」二次确认，提示会覆盖管理员修改。

---

### Task T6: 文档与计划收尾

- `docs/superpowers/plans/2026-11-19-article-quality-optimizations.md` 中所有「改 `builtin_skills.yaml`」的步骤标注「已由规则表方案替代（见 2026-11-20 计划）」。
- 更新 `docs/` 下的架构说明（若有 skill manifest 章节）。

---

### Task T9: 全量验证

```
cd apps/api && .venv/Scripts/python.exe -m pytest -q
cd apps/api && .venv/Scripts/python.exe -m compileall -q app
cd apps/web && npm run build
```
浏览器冒烟：登录 → 打开「创作规则」→ 修改语气 → 个人副本生效；超管修改全局语气 → 另一账号生成 prompt 变化（用预览接口验证）；词表加一个词 → 稿件检查命中；删除 YAML 后冷启动正常。

### Task T10: 独立审查
- 审查者拿 diff + 本计划，不读实现者自述。
- 重点：是否有任何残留的 `manifest.get("styleRules"|"tone"|"workflow"|...)` 读取；权限越权；事务边界；重建覆盖风险；前端两处编辑并存。

---

## 回滚
- 代码回滚到本条分支上一个提交即可；新表 `skill_writing_rules` / `prompt_rules` 留库不影响旧代码（旧代码不读它们）。
- `builtin_skills.yaml` 删除在 T1 提交内，回滚 T1 即恢复 YAML 同步（manifest 已被 T1 写入的规则不冲突，因为旧代码只读 manifest）。
- **注意**：T2 之后 manifest 不再含规则型 key，单独回滚 T2 会让生成路径读不到规则 → 回滚必须 T2+T1 一起。
