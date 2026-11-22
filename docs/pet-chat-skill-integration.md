# AI 宠物创作系统：三层 Skill 整合方案

> 将 女娲、写作蒸馏器、仓颉 三个技能深度融合到成稿台 AI 宠物系统中

---

## 核心设计：三层 Skill 体系

```
┌─────────────────────────────────────────┐
│  L3 应用层：宠物对话、快捷工具、创作流    │
│  （消费者看到的界面与交互）              │
└──────────────┬──────────────────────────┘
               │
┌──────────────┴──────────────────────────┐
│  L2 中台层：Skill 系统中心                │
│  ├─ Skill 中台：创建、编辑、导入、导出    │
│  ├─ Skill 执行引擎：调度 / 链接 / 参数化  │
│  └─ Skill 存储与版本管理                 │
└──────────────┬──────────────────────────┘
               │
┌──────────────┴──────────────────────────┐
│  L1 蒸馏层：三类 Skill 自动生成            │
│  ├─ 写作 Skill：从文章 → Writing-DNA.md  │
│  ├─ 人物 Skill：从人物 → 思维框架 Skill  │
│  └─ 知识 Skill：从资料 → 方法论 Skill    │
└─────────────────────────────────────────┘
```

---

## L1 蒸馏层：三类 Skill 的自动生成

### 1.1 写作 Skill（Writing DNA Skill）

**触发条件**：用户在 Skill 维护界面选"投喂写作示例"

**蒸馏流程**：

```
用户行为：上传 ≥20 篇文章（链接/文件）
  ↓
Step 1：自动下载 & 文本转换（使用 writing-dna-skill）
  ├─ 链接 → 爬取全文
  ├─ PDF/DOCX → 转纯文本
  └─ 存入临时语料库
  ↓
Step 2：六层分析（调用 writing-dna-skill 的核心流程）
  ├─ L1 语言 DNA：词频、句长、标点、修辞
  ├─ L2 文章结构：开头 hook、架构、结尾
  ├─ L3 选题逻辑：发布时机、切入角度、话题优先级
  ├─ L4 素材策略：引用来源、权威对象、数据使用
  ├─ L5 认知框架：世界观、价值判断、核心假设
  └─ L6 视觉风格：配图、排版、字体、色彩
  ↓
Step 3：整合为 Writing-DNA.md
  ├─ 自动生成 `Writing-DNA.md`
  └─ 存入 Skill 配置 JSON
  ↓
输出：一个"写作 Skill"
├─ skill_type: "writing"
├─ config.writing_dna: { 六层分析结果 }
├─ config.example_articles: [ 原始链接 ]
└─ 状态："已蒸馏"
```

**后端实现**（`apps/api/app/services/skill_distiller.py`）：

```python
class WritingSkillDistiller:
    """根据文章列表自动蒸馏写作 Skill"""
    
    async def distill_from_articles(
        self,
        article_sources: List[Union[str, bytes]],  # URL 或文件内容
        skill_id: str,
    ) -> Dict:
        """
        整合 writing-dna-skill 的六层分析
        
        Returns:
            {
                "writing_dna": Writing-DNA.md 的 dict 形式,
                "language_dna": 高频词表等,
                "structure_patterns": 结构模板,
                "cognitive_framework": 核心命题,
                "visual_style": 配图、排版规则,
                "analysis_report": 人可读的分析报告
            }
        """
        # Step 1: 下载 & 转换
        corpus = await self._download_and_normalize(article_sources)
        
        # Step 2: 六层分析（模拟 writing-dna-skill 的流程）
        dna = {
            "language": await self._analyze_language(corpus),
            "structure": await self._analyze_structure(corpus),
            "topic_logic": await self._analyze_topic_selection(corpus),
            "material_strategy": await self._analyze_materials(corpus),
            "cognitive_framework": await self._analyze_cognition(corpus),
            "visual_style": await self._analyze_visual(corpus),
        }
        
        # Step 3: 整合
        writing_dna = self._synthesize_dna(dna, article_sources)
        
        return {
            "writing_dna": writing_dna,
            "analysis_report": self._generate_report(dna),
        }
```

**前端交互**（`apps/web/src/modules/skills/WritingSkillUpload.vue`）：

```vue
<template>
  <div class="skill-upload">
    <!-- 上传示例文章 -->
    <section class="upload-section">
      <h3>投喂写作示例（≥20 篇）</h3>
      <div class="upload-area">
        <input type="file" multiple accept=".md,.txt,.pdf,.docx,*" @change="handleUpload" />
        <p>或粘贴链接（逗号分隔）</p>
        <textarea v-model="urlList" placeholder="https://example.com/article1&#10;https://example.com/article2"></textarea>
      </div>
    </section>

    <!-- 蒸馏进度 -->
    <section class="progress-section" v-if="isDistilling">
      <div class="progress-bar">
        <div :style="{ width: progress + '%' }"></div>
      </div>
      <p>正在分析... {{ currentStep }}</p>
    </section>

    <!-- 蒸馏结果预览 -->
    <section class="preview-section" v-if="dnaPreview">
      <h3>蒸馏完成 ✓</h3>
      <div class="dna-preview">
        <div class="dna-item">
          <h4>语言特征</h4>
          <p>高频词: {{ dnaPreview.language.top_words.join(", ") }}</p>
          <p>平均句长: {{ dnaPreview.language.avg_sentence_length }} 字</p>
        </div>
        <div class="dna-item">
          <h4>文章结构</h4>
          <p v-for="(pattern, idx) in dnaPreview.structure" :key="idx">
            {{ pattern.name }}: {{ pattern.count }} 篇
          </p>
        </div>
        <div class="dna-item">
          <h4>认知框架</h4>
          <p v-for="(belief, idx) in dnaPreview.cognitive_framework" :key="idx">
            • {{ belief }}
          </p>
        </div>
      </div>
      <button @click="confirmAndSaveSkill">保存为写作 Skill</button>
    </section>
  </div>
</template>

<script>
export default {
  data() {
    return {
      urlList: "",
      isDistilling: false,
      progress: 0,
      currentStep: "",
      dnaPreview: null,
    };
  },
  methods: {
    async handleUpload(event) {
      const files = event.target.files;
      await this.distillSkill(files);
    },
    async distillSkill(sources) {
      this.isDistilling = true;
      try {
        const response = await this.$api.post("/api/v1/skills/distill/writing", {
          sources, // 文件或 URL
          skill_id: this.$route.params.skillId,
        });
        this.dnaPreview = response.preview;
      } finally {
        this.isDistilling = false;
      }
    },
    async confirmAndSaveSkill() {
      await this.$api.post(`/api/v1/skills/${this.$route.params.skillId}/save-dna`);
      this.$router.push("/skills/registry");
    },
  },
};
</script>
```

---

### 1.2 人物 Skill（Nuwa Skill）

**触发条件**：用户在 Skill 维护界面选"创建人物思维 Skill"

**蒸馏流程**：

```
用户行为：输入人名或上传一手素材
  ↓
Step 1：确认蒸馏档位（快速/标准/深度）
  ├─ 快速：3 个维度，轻量级，~1 小时
  ├─ 标准：6 个维度，均衡（推荐），~3 小时
  └─ 深度：6 维度 + 一手素材全量分析，~6 小时
  ↓
Step 2：6 个 Agent 并行蒸馏（女娲 skill 的核心流程）
  ├─ Agent 1 著作：书籍、长文、论文 → 核心论点
  ├─ Agent 2 对话：播客、长视频、访谈 → 即兴思维
  ├─ Agent 3 表达：Twitter、微博、即刻 → DNA
  ├─ Agent 4 他者：评价、批评、传记 → 外部视角
  ├─ Agent 5 决策：关键决策、行为记录 → 真实逻辑
  └─ Agent 6 时间线：从出道到最新 → 变化轨迹
  ↓
Step 3：三重验证筛选
  ├─ V1 跨域验证：≥2 个独立段落有佐证？
  ├─ V2 预测力：能回答书里没说过的新问题？
  └─ V3 独特性：不是常识？
  ↓
Step 4：提炼框架
  ├─ 心智模型：3-7 个（每个都独特）
  ├─ 决策启发式：5-10 条
  ├─ 表达 DNA：句式、词汇、幽默方式
  ├─ 价值观：排序的核心信念
  └─ 诚实边界：明确的局限性
  ↓
输出：一个"人物思维 Skill"
├─ skill_type: "persona"
├─ config.persona_name: "芒格"
├─ config.mental_models: [ ... ]
├─ config.decision_heuristics: [ ... ]
├─ config.expression_dna: { ... }
└─ 状态："已蒸馏"
```

**后端实现**（`apps/api/app/services/skill_distiller.py`）：

```python
class PersonaSkillDistiller:
    """根据人物蒸馏思维框架 Skill（女娲 skill 适配）"""
    
    async def distill_from_person(
        self,
        person_name: str,
        tier: Literal["quick", "standard", "deep"] = "standard",
        local_materials: Optional[List[bytes]] = None,
        skill_id: str = None,
    ) -> Dict:
        """
        整合 nuwa-skill 的蒸馏流程
        
        Args:
            person_name: 人物名字
            tier: 蒸馏深度
            local_materials: 用户提供的一手素材（PDF/transcript/文章）
            skill_id: Skill ID（用于保存进度）
        
        Returns:
            {
                "persona_name": "...",
                "mental_models": [ { name, evidence, application, limitation }, ... ],
                "decision_heuristics": [ ... ],
                "expression_dna": { ... },
                "value_system": [ ... ],
                "honest_boundary": [ ... ],
                "research_report": "..."
            }
        """
        # Step 1：确认档位
        tier_config = self._get_tier_config(tier)  # 决定搜索范围、成本等
        
        # Step 2：6 个 Agent 并行蒸馏
        # （如果有本地素材，优先分析，网络搜索变为补充）
        research_results = await self._spawn_6_agents(
            person_name=person_name,
            tier=tier_config,
            local_materials=local_materials,
            skill_id=skill_id,
        )
        
        # Step 3：三重验证
        verified_models = await self._triple_verify(research_results)
        
        # Step 4：提炼框架
        framework = {
            "mental_models": await self._extract_mental_models(verified_models),
            "decision_heuristics": await self._extract_heuristics(verified_models),
            "expression_dna": await self._extract_expression_dna(verified_models),
            "value_system": await self._extract_values(verified_models),
            "honest_boundary": await self._extract_boundaries(verified_models),
        }
        
        return {
            "persona_name": person_name,
            **framework,
            "research_report": self._generate_report(verified_models),
        }
```

---

### 1.3 知识 Skill（Cangjie Skill）

**触发条件**：用户在 Skill 维护界面选"投喂资料蒸馏知识"

**蒸馏流程**：

```
用户行为：上传书籍 / 视频转写 / 播客 / 课程（≥5000 字）
  ↓
Step 1：整书理解（Adler 四步法）
  ├─ 结构：这本书在讲什么？
  ├─ 解释：作者怎么论证的？
  ├─ 批判：哪些假设有问题？
  └─ 应用：我能用来做什么？
  ↓
Step 2：5 个 Agent 并行提取（cangjie-skill）
  ├─ 框架提取器 → 决策框架、思维模型
  ├─ 原则提取器 → 原则、清单、规则
  ├─ 案例提取器 → 实例、应用场景
  ├─ 反例提取器 → 失败模式、警告
  └─ 术语提取器 → 关键概念词典
  ↓
Step 3：三重验证筛选
  ├─ V1 跨域：≥2 个独立段落有佐证？
  ├─ V2 预测力：能推断新情况吗？
  └─ V3 独特性：是独特方法还是常识？
  ↓
Step 4：晋级评估
  ├─ 这个单元值得成为独立 Skill 吗？
  ├─ promoted → 晋升为独立 Skill
  └─ router_input → 保留为来源路由入口内的能力卡
  ↓
Step 5：RIA 能力卡构造（认知/实例/应用）
  ├─ R (Rationale)：为什么这个方法有效？
  ├─ I (Instance)：书中的具体例子
  ├─ A1 (Application 1)：应用场景 1
  ├─ A2 (Application 2)：应用场景 2
  ├─ E (Edge Case)：边界情况和局限
  └─ B (Bibliography)：引用来源
  ↓
输出：一个"知识 Skill"
├─ skill_type: "knowledge"
├─ config.source_title: "《穷查理宝典》"
├─ config.capabilities: [ { id, name, ria_card, ... }, ... ]
├─ config.glossary: { 术语词典 }
└─ 状态："已蒸馏"
```

**后端实现**（`apps/api/app/services/skill_distiller.py`）：

```python
class KnowledgeSkillDistiller:
    """根据资料蒸馏知识 Skill（cangjie-skill 适配）"""
    
    async def distill_from_content(
        self,
        content: Union[str, bytes],  # 书籍、转写稿等纯文本
        source_title: str,
        source_type: Literal["book", "video", "podcast", "course"],
        skill_id: str = None,
    ) -> Dict:
        """
        整合 cangjie-skill 的蒸馏流程
        
        Returns:
            {
                "source_title": "...",
                "book_overview": "...",
                "capabilities": [
                    {
                        "capability_id": "cap-001",
                        "name": "方法名",
                        "ria_card": "RIA 能力卡",
                        "destination": "promoted" | "router",
                    },
                    ...
                ],
                "glossary": { 术语表 },
                "analysis_report": "..."
            }
        """
        # Step 1：整书理解
        overview = await self._adler_analysis(content, source_title)
        
        # Step 2：5 个 Agent 并行提取
        candidates = await self._spawn_5_extractors(
            content=content,
            overview=overview,
            skill_id=skill_id,
        )
        
        # Step 3：三重验证
        verified = await self._triple_verify(candidates)
        
        # Step 4：晋级评估
        promotions = await self._promotion_gate(verified)
        
        # Step 5：RIA 能力卡
        capabilities = []
        for unit in verified:
            ria_card = await self._construct_ria_card(unit, content)
            destination = promotions.get(unit["id"], "router")
            capabilities.append({
                "capability_id": unit["id"],
                "name": unit["name"],
                "ria_card": ria_card,
                "destination": destination,
            })
        
        # 术语表
        glossary = await self._extract_glossary(verified)
        
        return {
            "source_title": source_title,
            "book_overview": overview,
            "capabilities": capabilities,
            "glossary": glossary,
            "analysis_report": self._generate_report(verified, promotions),
        }
```

---

## L2 中台层：Skill 系统中心

### 2.1 数据模型扩展

**新增表结构**：

```python
class SkillRegistry(Base):
    """Skill 注册表"""
    __tablename__ = "skill_registry"
    
    id = Column(String(36), primary_key=True)
    owner_id = Column(String(36), ForeignKey("users.id"))
    
    # 核心属性
    type = Column(String(50), nullable=False)  # writing / persona / knowledge
    label = Column(String(200), nullable=False)  # "我的写作风格 v1"
    description = Column(Text)
    version = Column(String(20), default="1.0")
    
    # 蒸馏来源
    source_type = Column(String(50))  # articles / person_name / content_url
    source_data = Column(JSON)  # {article_urls: [...]} / {person: "..."} / {content: "..."}
    
    # 蒸馏配置与结果
    tier = Column(String(20))  # quick / standard / deep（仅 persona 使用）
    distillation_config = Column(JSON)  # 蒸馏时的参数
    distillation_result = Column(JSON)  # 蒸馏产出（Writing-DNA、mental_models 等）
    
    # 应用参数
    is_public = Column(Boolean, default=False)
    tags = Column(ARRAY(String), default=[])
    
    # 统计
    times_used = Column(Integer, default=0)
    avg_rating = Column(Float, default=0.0)
    last_used_at = Column(DateTime)
    
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class SkillCapability(Base):
    """Skill 能力卡（供 knowledge Skill 使用）"""
    __tablename__ = "skill_capabilities"
    
    id = Column(String(36), primary_key=True)
    skill_id = Column(String(36), ForeignKey("skill_registry.id"))
    
    capability_id = Column(String(100), nullable=False)  # cap-001
    name = Column(String(200))
    ria_card = Column(Text)  # RIA 能力卡的完整文本
    destination = Column(String(50))  # promoted / router
    
    created_at = Column(DateTime, default=utcnow)


class SkillUsageLog(Base):
    """Skill 使用日志（用于后续优化和统计）"""
    __tablename__ = "skill_usage_logs"
    
    id = Column(String(36), primary_key=True)
    skill_id = Column(String(36), ForeignKey("skill_registry.id"))
    user_id = Column(String(36), ForeignKey("users.id"))
    
    context = Column(JSON)  # {article_id, pet_session_id, ...}
    rating = Column(Integer)  # 1-5 or null
    feedback = Column(Text)  # 用户反馈
    
    created_at = Column(DateTime, default=utcnow)
```

### 2.2 Skill 执行引擎

**核心类**（`apps/api/app/services/skill_engine.py`）：

```python
class SkillExecutionEngine:
    """
    Skill 执行引擎：
    1. 加载 Skill 配置
    2. 根据上下文选择合适的 Skill
    3. 参数化调用 LLM
    4. 返回结果并记录日志
    """
    
    async def execute(
        self,
        skill_id: str,
        context: Dict,  # {article_text, pet_message, ...}
        user_id: str,
    ) -> Dict:
        """
        执行 Skill
        
        Args:
            skill_id: Skill ID
            context: 执行上下文
            user_id: 用户 ID
        
        Returns:
            {
                "result": "生成的文本",
                "metadata": {
                    "skill_used": "...",
                    "tokens": 123,
                    "execution_time": 1.23,
                }
            }
        """
        # 加载 Skill
        skill = await self.db.get(SkillRegistry, skill_id)
        if not skill or skill.owner_id != user_id:
            raise PermissionError("Skill not found or access denied")
        
        # 根据类型调用
        if skill.type == "writing":
            return await self._execute_writing_skill(skill, context)
        elif skill.type == "persona":
            return await self._execute_persona_skill(skill, context)
        elif skill.type == "knowledge":
            return await self._execute_knowledge_skill(skill, context)
    
    async def _execute_writing_skill(
        self,
        skill: SkillRegistry,
        context: Dict,
    ) -> Dict:
        """执行写作 Skill：按 Writing-DNA 生成文本"""
        writing_dna = skill.distillation_result["writing_dna"]
        
        # 构造 prompt
        prompt = self._build_writing_prompt(writing_dna, context)
        
        # 调用 LLM
        result = await self.llm_client.generate(
            prompt=prompt,
            temperature=0.7,  # 创意输出
            max_tokens=2000,
        )
        
        # 记录日志
        await self._log_usage(skill.id, context, result)
        
        return {"result": result, "metadata": {...}}
    
    async def _execute_persona_skill(
        self,
        skill: SkillRegistry,
        context: Dict,
    ) -> Dict:
        """执行人物 Skill：按思维框架推理"""
        mental_models = skill.distillation_result["mental_models"]
        expression_dna = skill.distillation_result["expression_dna"]
        
        # 构造 prompt：先研究问题，再按框架回答
        prompt = self._build_persona_prompt(
            mental_models,
            expression_dna,
            context,
        )
        
        result = await self.llm_client.generate(
            prompt=prompt,
            temperature=0.6,  # 更稳定
            max_tokens=1500,
        )
        
        await self._log_usage(skill.id, context, result)
        
        return {"result": result, "metadata": {...}}
    
    async def _execute_knowledge_skill(
        self,
        skill: SkillRegistry,
        context: Dict,
    ) -> Dict:
        """执行知识 Skill：按能力卡路由"""
        capabilities = skill.distillation_result["capabilities"]
        
        # 根据用户意图选择合适的能力卡
        matched_capability = await self._match_capability(
            capabilities,
            context["intent"],
        )
        
        # 执行该能力
        ria_card = matched_capability["ria_card"]
        prompt = self._build_knowledge_prompt(ria_card, context)
        
        result = await self.llm_client.generate(
            prompt=prompt,
            temperature=0.65,
            max_tokens=2000,
        )
        
        await self._log_usage(skill.id, context, result, capability_id=matched_capability["id"])
        
        return {"result": result, "metadata": {...}}
    
    def _build_writing_prompt(self, writing_dna: Dict, context: Dict) -> str:
        """根据 Writing-DNA 和上下文构造 prompt"""
        return f"""
你是一个按特定风格写作的助手。

【写作 DNA】
- 语言特征：{writing_dna['language']}
- 结构模板：{writing_dna['structure']}
- 认知框架：{writing_dna['cognitive_framework']}
- 视觉风格：{writing_dna['visual_style']}

【当前任务】
{context['task_description']}

【原始材料】
{context.get('source_text', '')}

【要求】
1. 严格遵循上述写作 DNA
2. 写出近似原作者的风格
3. 避免 AI 味（过于生硬、空洞）

现在开始写：
"""
    
    def _build_persona_prompt(self, models: List, dna: Dict, context: Dict) -> str:
        """根据人物思维框架构造 prompt"""
        return f"""
你正在扮演一个特定人物的思维方式和表达风格。

【心智模型】
{self._format_models(models)}

【表达特征】
{self._format_dna(dna)}

【用户问题】
{context['question']}

【回答步骤】
1. 先用你掌握的信息做基本研究
2. 应用相关的心智模型分析问题
3. 按这个人的表达风格给出答案
4. 在关键地方说明你的推理依据

现在回答：
"""
```

---

## L3 应用层：宠物对话与创作流

### 3.1 宠物对话集成

**前端组件**（`apps/web/src/modules/studio/PetChatPanel.vue`）：

```vue
<template>
  <div class="pet-chat-panel">
    <!-- 宠物头像与动画 -->
    <div class="pet-avatar">
      <img src="@/assets/pet.png" :style="petAnimationStyle" />
      <div class="pet-status">{{ petStatus }}</div>
    </div>

    <!-- 对话窗口 -->
    <div class="chat-history">
      <div v-for="msg in messages" :key="msg.id" :class="['message', msg.role]">
        <p>{{ msg.content }}</p>
        <small>{{ formatTime(msg.timestamp) }}</small>
      </div>
    </div>

    <!-- 输入框 + 快捷工具 -->
    <div class="chat-input-area">
      <!-- 快捷按钮 -->
      <div class="shortcuts">
        <button @click="showSkillSelector('writing')">📝 选写作 Skill</button>
        <button @click="showSkillSelector('persona')">🧠 选人物思维</button>
        <button @click="showSkillSelector('knowledge')">📚 选知识库</button>
      </div>

      <!-- 输入框 -->
      <div class="input-box">
        <input
          v-model="userInput"
          type="text"
          placeholder="告诉宠物你想要什么..."
          @keyup.enter="sendMessage"
        />
        <button @click="sendMessage">发送</button>
      </div>

      <!-- 显示当前生效的 Skill -->
      <div v-if="activeSkills.length" class="active-skills">
        <span v-for="skill in activeSkills" :key="skill.id" class="skill-tag">
          {{ skill.label }}
          <button @click="removeSkill(skill.id)">✕</button>
        </span>
      </div>
    </div>

    <!-- Skill 选择器（模态框）-->
    <SkillSelector
      v-if="skillSelectorOpen"
      :type="skillSelectorType"
      @select="addSkill"
      @close="skillSelectorOpen = false"
    />
  </div>
</template>

<script>
export default {
  data() {
    return {
      messages: [],
      userInput: "",
      petStatus: "🐾 准备好了",
      petAnimationStyle: {},
      activeSkills: [],  // 当前会话选中的 Skills
      skillSelectorOpen: false,
      skillSelectorType: "",
    };
  },
  methods: {
    async sendMessage() {
      if (!this.userInput.trim()) return;

      // 用户消息
      const userMsg = {
        id: Date.now(),
        role: "user",
        content: this.userInput,
        timestamp: new Date(),
      };
      this.messages.push(userMsg);

      // 宠物回复
      this.petStatus = "🐾 思考中...";
      try {
        const response = await this.$api.post(
          "/api/v1/pet/chat",
          {
            message: this.userInput,
            active_skills: this.activeSkills.map((s) => s.id),
            conversation_id: this.$route.params.conversationId,
          }
        );

        const petMsg = {
          id: Date.now() + 1,
          role: "pet",
          content: response.result,
          timestamp: new Date(),
        };
        this.messages.push(petMsg);
        this.petStatus = "🐾 准备好了";
      } catch (error) {
        this.petStatus = "🐾 出错了";
      }

      this.userInput = "";
    },

    showSkillSelector(type) {
      this.skillSelectorType = type;
      this.skillSelectorOpen = true;
    },

    async addSkill(skillId) {
      const response = await this.$api.get(
        `/api/v1/skills/${skillId}`
      );
      this.activeSkills.push(response);
      this.skillSelectorOpen = false;
    },

    removeSkill(skillId) {
      this.activeSkills = this.activeSkills.filter((s) => s.id !== skillId);
    },

    formatTime(date) {
      return date.toLocaleTimeString();
    },
  },
};
</script>
```

### 3.2 文章创作工作流

**后端路由**（`apps/api/app/modules/pet_chat.py`）：

```python
router = APIRouter(prefix="/api/v1/pet", tags=["pet"])

@router.post("/chat")
async def pet_chat(
    message: str,
    active_skills: List[str],
    conversation_id: str = None,
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """
    宠物对话端点
    
    流程：
    1. 识别用户意图（创作 / 改稿 / 建议）
    2. 加载选中的 Skills
    3. 调用对应的 Skill 执行引擎
    4. 返回结果
    """
    engine = SkillExecutionEngine(db)
    
    # 意图识别
    intent = await _recognize_intent(message)
    
    # 加载 Skills
    skills = []
    for skill_id in active_skills:
        skill = await db.get(SkillRegistry, skill_id)
        if skill.owner_id == current_user.id:
            skills.append(skill)
    
    # 根据意图调用 Skill
    context = {
        "message": message,
        "intent": intent,
        "user_id": current_user.id,
    }
    
    if intent == "create_article":
        # 新创建文章
        result = await engine.execute(
            skill_id=active_skills[0],  # 首选 Skill
            context=context,
        )
        return {"result": result, "action": "create_article"}
    
    elif intent == "refine_paragraph":
        # 改稿
        refine_skills = [s for s in skills if s.type in ["writing", "knowledge"]]
        result = await _refine_text(message, refine_skills, engine, context)
        return {"result": result, "action": "refine_paragraph"}
    
    elif intent == "generate_svg_card":
        # 生成 SVG 卡
        result = await _generate_svg_card(message, engine, context)
        return {"result": result, "action": "generate_svg_card"}


async def _recognize_intent(message: str) -> str:
    """识别用户意图"""
    keywords = {
        "create_article": ["写一篇", "帮我创作", "新文章", "初稿"],
        "refine_paragraph": ["改写", "润色", "去 AI 味", "修改"],
        "generate_svg_card": ["生成卡", "制作卡", "动态卡"],
    }
    
    for intent, kws in keywords.items():
        if any(kw in message for kw in kws):
            return intent
    
    return "chat"  # 默认聊天
```

---

## 整体流程示例：一篇文章的完整创作

```
用户在成稿台：
  "帮我根据这个链接写一篇文章，用我的写作风格，参考芒格的思维方式"
  
  ↓ 宠物理解：
  ├─ Intent: 创建文章
  ├─ 需要的 Skills:
  │  ├─ writing: "我的写作风格"（Writing-DNA）
  │  └─ persona: "芒格思维框架"（mental models）
  └─ 源材料：用户提供的链接
  
  ↓ 宠物行动：
  ├─ 下载 & 分析链接内容
  ├─ 调用 writing Skill 的蒸馏 DNA：
  │  ├─ 确定文章结构（模板匹配）
  │  ├─ 应用语言 DNA（高频词、句长）
  │  └─ 遵循认知框架
  ├─ 调用 persona Skill 的思维框架：
  │  ├─ 用心智模型分析链接内容
  │  ├─ 应用决策启发式
  │  └─ 按表达 DNA 组织语言
  └─ 生成初稿
  
  ↓ 初稿结果：
  {
    "content": "...",
    "structure": { 章节分析 },
    "tone": "芒格风格 + 用户写作风格混合",
  }
  
  ↓ 用户在成稿台点"投喂"
  ├─ 文章自动填入成稿台
  ├─ 显示编辑界面
  └─ 支持进一步改稿（快捷工具）
       └─ "去 AI 味"（调用 knowledge Skill）
       └─ "润色段落"（调用 writing + 快捷工具）
       └─ "生成 SVG 卡"（调用卡生成服务）
```

---

## 下一步：实施规划更新

根据这个整合方案，原先的《分阶段执行指南》需要调整：

| Phase | 原规划 | 升级后 | 新增工作 |
|-------|--------|--------|---------|
| **Phase 1** | Skill 中台 | **L2 中台 + L1 蒸馏（写作 Skill）** | 写作蒸馏引擎 + writing-dna-skill 适配 |
| **Phase 2** | 对话引擎 | **L3 应用 + L1 蒸馏（人物 Skill）** | 人物蒸馏引擎 + nuwa-skill 适配 |
| **Phase 3** | 快捷工具 | **L3 快捷工具 + L1 蒸馏（知识 Skill）** | 知识蒸馏引擎 + cangjie-skill 适配 |
| **Phase 4-5** | 创作流 + 蒸馏 | **集成验证** | 三类 Skill 的协同调度 |
| **Phase 6** | 测试交付 | **全面测试** | - |

---

## 总结：为什么这个设计更强

| 维度 | 优势 |
|------|------|
| **蒸馏自动化** | 写作、人物、知识三类 Skill 自动生成，用户不需要手写配置 |
| **风格沉淀** | Writing-DNA 蒸馏出可复用的语言/结构/认知框架，一次投喂多次使用 |
| **知识复用** | 从各种资料（书、视频、播客）自动提炼方法论，支持持续学习 |
| **思维框架** | 人物思维 Skill 保证宠物不只是"学你写"，还能"像某个聪明人思考" |
| **协同能力** | 一个创作任务可同时调用多个 Skill，形成"三维立体"的创作支撑 |
| **版本管理** | 每个 Skill 有版本号，支持迭代、导入、导出、分享 |

