# 文章原创度检测系统设计方案

## 1. 产品定位与价值

### 1.1 需求背景
- **用户痛点**：AI 生成的文章可能与已有内容相似，需要实时检测原创度
- **创作流程缺口**：当前 StudioView 支持「大纲 → 正文 → 润色」，缺少**质量评估**环节
- **发布风险**：无法检测重复内容导致内容平台审核拒绝或处罚

### 1.2 功能定位
**原创度检测** 作为 StudioView 的**右侧面板新 Tab**（与现有「AI 助手」并列）

```
[ AI 助手 ]  [ 原创检测 ]
```

### 1.3 用户旅程
```
用户在 StudioView 完成 AI 创作
    ↓
点击「原创检测」Tab
    ↓
系统自动分析当前稿件
    ↓
显示多维度评分 + 详细报告
    ↓
根据反馈决定：修改稿件、再次润色、或直接发布
```

---

## 2. 多维度评分体系

### 2.1 五大维度（MVP）

| 维度 | 权重 | 说明 | 检测方法 |
|---|---|---|---|
| **文本相似度** | 40% | 与既有文章的重合度 | TF-IDF + 余弦相似度 / 向量相似度 |
| **观点独特性** | 25% | 是否有原创观点或新数据 | NLP 关键概念提取 + 存量对比 |
| **结构创新度** | 15% | 逻辑框架是否新颖 | 大纲树对比 + 段落连接词分析 |
| **数据/引用真实度** | 12% | 引用数据是否可验证 | 正则提取数据格式 + 外链检验（可选） |
| **语言风格独特性** | 8% | 是否有作者特色 | 词汇多样性 + 句式分布 + 特色词频 |

### 2.2 综合评分

**总分 = 100 分**

```
综合得分 = 95 - 相似度匹配值 + Σ(各维度加分)
```

**评级标准**：
- **90-100 分**：✅ 优秀 - 强烈建议发布
- **75-89 分**：⚠️ 良好 - 可发布，建议检查标记相似部分
- **60-74 分**：⚡ 中等 - 建议润色或修改
- **0-59 分**：❌ 低 - 强烈建议重写关键段落

---

## 3. 系统架构

### 3.1 技术栈

| 层 | 技术选型 | 说明 |
|---|---|---|
| **前端** | Vue 3 + Pinia | 集成到 StudioView.vue 右侧面板 |
| **后端** | FastAPI 新路由 | `/api/v1/content/analyze-originality` |
| **对标库** | 本地 PostgreSQL | 已有稿件表 `content_versions` |
| **相似度引擎** | Scikit-learn / faiss | 文本向量化 + 相似度计算 |
| **LLM 辅助** | 现有 AI Gateway | 观点/关键概念提取 |
| **缓存** | Redis（可选）| 加速常见查询 |

### 3.2 数据流

```
StudioView
  ↓
用户点击「原创检测」
  ↓
POST /api/v1/content/analyze-originality
  {
    content_id: "...",
    title: "...",
    body: "...",
    compare_with: "all" | "own" | "public"  // 对标范围
  }
  ↓
后端处理
  1. 加载目标稿件（title + body）
  2. 从库中查询对标稿件（按范围过滤）
  3. 计算 5 个维度指标
  4. 生成详细报告
  ↓
Response:
  {
    totalScore: 88,
    level: "good",
    scoreboard: {
      similarity: 65,        // 相似度得分
      originality: 90,       // 观点独特性
      structure: 80,         // 结构创新度
      dataAccuracy: 75,      // 数据真实度
      styleUniqueness: 70    // 语言风格独特性
    },
    details: {
      similarPieces: [...],  // 相似片段
      keyPoints: [...],      // 关键观点
      highlights: [...]      // 亮点分析
    }
  }
  ↓
前端展示
  - 总分卡片 + 评级标签
  - 5 维度雷达图
  - 相似文章列表
  - 建议行动
```

### 3.3 文件结构

```
apps/api/
├── app/
│   ├── modules/
│   │   └── originality.py          ← 新路由
│   ├── services/
│   │   ├── originality_service.py   ← 核心业务逻辑
│   │   ├── text_analyzer.py         ← NLP 分析
│   │   └── similarity_engine.py     ← 相似度计算
│   ├── models/
│   │   └── (OriginalityAnalysis Schema 在 schemas.py)
│   └── core/
│       └── nlp_utils.py            ← 通用 NLP 工具

apps/web/src/
├── views/
│   └── StudioView.vue               (改造：右侧面板加 Tab)
├── features/
│   └── originality/
│       ├── OriginalityPanel.vue     ← 新面板组件
│       ├── ScoreRadar.vue           ← 雷达图
│       └── SimilarArticlesList.vue  ← 相似文章列表
├── composables/
│   └── useOriginality.js            ← 业务逻辑 composable
└── api/
    └── originality.js               ← API 封装

infra/postgres/
└── schema.sql                       (新增字段见 4.1)
```

---

## 4. 数据库设计

### 4.1 新增表 & 字段

#### 表 A：`originality_analyses`（检测结果历史）

```sql
CREATE TABLE originality_analyses (
    id UUID PRIMARY KEY,
    content_version_id UUID NOT NULL,       -- 关联稿件版本
    workspace_id UUID NOT NULL,
    analyzed_at TIMESTAMP DEFAULT NOW(),
    
    -- 五个维度评分
    similarity_score INT,         -- 0-100，数值越低越好
    originality_score INT,        -- 观点独特性
    structure_score INT,          -- 结构创新度
    data_accuracy_score INT,      -- 数据真实度
    style_uniqueness_score INT,   -- 语言风格独特性
    
    -- 综合评分
    total_score INT,              -- 0-100
    level VARCHAR(20),            -- 'excellent' / 'good' / 'fair' / 'poor'
    
    -- 详情存储（JSONB）
    similar_articles JSONB,       -- [{id, title, similarity_ratio, matching_snippets}, ...]
    key_points JSONB,             -- 提取的关键观点
    risk_flags JSONB,             -- 风险标记 [{type, position, reason}, ...]
    recommendations TEXT,         -- 改进建议
    
    -- 检测参数
    compare_scope VARCHAR(20),    -- 'all' / 'own' / 'public'
    model_version VARCHAR(50),    -- 检测算法版本
    
    FOREIGN KEY (content_version_id) REFERENCES content_versions(id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

CREATE INDEX idx_originality_analyses_content_version 
  ON originality_analyses(content_version_id);
CREATE INDEX idx_originality_analyses_workspace_analyzed 
  ON originality_analyses(workspace_id, analyzed_at DESC);
```

#### 表 B：`similarity_cache`（加速重复查询，可选）

```sql
CREATE TABLE similarity_cache (
    id UUID PRIMARY KEY,
    source_content_version_id UUID NOT NULL,
    target_content_version_id UUID NOT NULL,
    similarity_ratio FLOAT,
    computed_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP,
    
    FOREIGN KEY (source_content_version_id) REFERENCES content_versions(id),
    FOREIGN KEY (target_content_version_id) REFERENCES content_versions(id),
    UNIQUE(source_content_version_id, target_content_version_id)
);
```

#### 表 C：`content_embeddings`（向量存储）

```sql
CREATE TABLE content_embeddings (
    id UUID PRIMARY KEY,
    content_version_id UUID NOT NULL,
    embedding_model VARCHAR(100),     -- 使用的模型（如 bert-base-chinese）
    embedding VECTOR(768),            -- pgvector 扩展，768 维向量
    computed_at TIMESTAMP DEFAULT NOW(),
    
    FOREIGN KEY (content_version_id) REFERENCES content_versions(id),
    UNIQUE(content_version_id, embedding_model)
);
```

### 4.2 迁移脚本

在 `apps/api/app/core/db.py` 的 `_apply_additive_migrations()` 中添加：

```python
_ADDED_COLUMNS = {
    # ...
    "originality_analyses": {
        "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    }
}

_ADDED_TABLES = {
    "originality_analyses": """
        CREATE TABLE originality_analyses (
            id UUID PRIMARY KEY,
            content_version_id UUID NOT NULL,
            workspace_id UUID NOT NULL,
            analyzed_at TIMESTAMP DEFAULT NOW(),
            similarity_score INT,
            originality_score INT,
            structure_score INT,
            data_accuracy_score INT,
            style_uniqueness_score INT,
            total_score INT,
            level VARCHAR(20),
            similar_articles JSONB,
            key_points JSONB,
            risk_flags JSONB,
            recommendations TEXT,
            compare_scope VARCHAR(20),
            model_version VARCHAR(50),
            FOREIGN KEY (content_version_id) REFERENCES content_versions(id),
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
        )
    """
}
```

---

## 5. 算法细节

### 5.1 相似度检测（40%）

**方法**：TF-IDF + 余弦相似度（快速方案）/ 或 向量相似度（精准方案）

```python
# 快速方案：TF-IDF + 余弦相似度
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

def calculate_tfidf_similarity(text1: str, text2: str) -> float:
    """计算两段文本的相似度 [0, 1]"""
    vectorizer = TfidfVectorizer(ngram_range=(1, 3), max_features=1000)
    texts = [text1, text2]
    tfidf_matrix = vectorizer.fit_transform(texts)
    similarity = cosine_similarity(tfidf_matrix)[0][1]
    return min(100, int(similarity * 100))  # 转换为 0-100 分

# 精准方案：使用预训练向量模型
from sentence_transformers import SentenceTransformer

def calculate_semantic_similarity(text1: str, text2: str) -> float:
    """基于语义的相似度"""
    model = SentenceTransformer('distiluse-base-multilingual-cased-v2')
    embedding1 = model.encode(text1)
    embedding2 = model.encode(text2)
    similarity = cosine_similarity([embedding1], [embedding2])[0][0]
    return int(similarity * 100)
```

**评分转换**：
- 相似度 0-30% → 得分 90-100（独特）
- 相似度 30-50% → 得分 70-90（部分重合）
- 相似度 50-70% → 得分 40-70（中度重合）
- 相似度 70-100% → 得分 0-40（高度重合/抄袭）

### 5.2 观点独特性（25%）

**方法**：关键概念提取 + 对标库搜索

```python
def extract_key_concepts(text: str) -> List[str]:
    """提取关键观点和新鲜概念"""
    # 使用现有 AI Gateway 的 LLM 能力
    prompt = f"""
    分析以下文章的 3-5 个核心观点和创新点：
    {text}
    
    返回 JSON: {{"concepts": [...], "novelty_score": 0-100}}
    """
    response = ai_gateway.chat(prompt)
    return response.concepts, response.novelty_score
```

**评分逻辑**：
- 新观点数 / 总观点数 → 独特性比例
- 对标库中出现该观点的稿件数越少 → 得分越高

### 5.3 结构创新度（15%）

**方法**：大纲树对比 + 转移词分析

```python
def analyze_structure_novelty(outline: dict, similar_outlines: List[dict]) -> int:
    """
    分析大纲结构的新颖度
    - 一级标题相似度
    - 段落顺序变化
    - 逻辑连接词使用频率
    """
    # 大纲相似度与平均值对比
    structure_similarity_avg = mean([
        calculate_tree_similarity(outline, other) 
        for other in similar_outlines
    ])
    
    # 转移词丰富度（使用的过渡词数量 / 总段数）
    transition_words = extract_transition_words(content)
    transition_score = min(100, len(set(transition_words)) * 10)
    
    return int((100 - structure_similarity_avg) * 0.7 + transition_score * 0.3)
```

### 5.4 数据真实度（12%）

**方法**：数据提取 + 正则验证

```python
def verify_data_accuracy(text: str) -> int:
    """
    检验文中的数据和引用是否可验证
    - 正则提取数据格式（百分比、年份、数字）
    - 检查引用来源（可选外链验证）
    """
    data_mentions = extract_data_mentions(text)
    verifiable_count = sum(1 for d in data_mentions if has_source(d))
    
    if not data_mentions:
        return 80  # 无数据声明不扣分
    
    return int((verifiable_count / len(data_mentions)) * 100)
```

### 5.5 语言风格独特性（8%）

**方法**：词汇多样性 + 特色词频

```python
def calculate_style_uniqueness(text: str, author_previous_texts: List[str]) -> int:
    """
    分析作者的语言风格独特性
    - 词汇丰富度（类型/令牌比 TTR）
    - 特色词使用频率
    - 与历史稿件的风格一致性
    """
    # 词汇丰富度
    words = jieba.cut(text)
    unique_words = len(set(words))
    ttr = unique_words / len(words)  # 目标 > 0.5
    
    # 特色词（该作者高频但全局低频）
    author_signature_words = extract_signature_words(
        text, 
        author_previous_texts
    )
    signature_count = len(author_signature_words)
    
    return min(100, int(ttr * 40 + min(signature_count, 5) * 12))
```

---

## 6. 前后端设计

### 6.1 后端 API

#### 请求

```http
POST /api/v1/content/analyze-originality HTTP/1.1
Authorization: Bearer <token>
Content-Type: application/json

{
  "content_version_id": "123e4567-e89b-12d3-a456-426614174000",
  "compare_scope": "all",           // 'all' | 'own' | 'public'
  "detailed": true,                 // 是否返回相似片段详情
  "use_cache": true                 // 是否使用缓存
}
```

#### 响应

```json
{
  "analysisId": "uuid",
  "contentVersionId": "uuid",
  "analyzedat": "2026-11-19T18:30:00Z",
  "totalScore": 88,
  "level": "good",
  
  "scoreboard": {
    "similarity": 65,
    "originality": 90,
    "structure": 80,
    "dataAccuracy": 75,
    "styleUniqueness": 70
  },
  
  "details": {
    "similarArticles": [
      {
        "id": "uuid",
        "title": "类似文章标题",
        "similarity": 0.72,
        "matchingSnippets": [
          {
            "position": "段落 2-3",
            "sourceText": "...",
            "targetText": "..."
          }
        ]
      }
    ],
    "keyPoints": [
      {
        "point": "提取的关键观点",
        "novelty": 85,
        "sources": 2  // 已有多少篇稿件提过
      }
    ],
    "riskFlags": [
      {
        "type": "high_similarity_paragraph",
        "position": "第 3 段",
        "severity": "warning",
        "reason": "与文章《标题》相似度 89%"
      }
    ]
  },
  
  "recommendations": [
    "第 3 段与已有文章相似度过高，建议改写",
    "建议补充最新数据支撑观点 1"
  ],
  
  "modelVersion": "v1.0-bert-base-chinese",
  "processingTimeMs": 2340
}
```

### 6.2 前端组件

#### OriginalityPanel.vue

```vue
<template>
  <div class="originality-panel">
    <!-- 加载态 -->
    <div v-if="loading" class="loading">
      <spinner />
      分析中...
    </div>
    
    <!-- 结果展示 -->
    <div v-else-if="analysis" class="result">
      <!-- 总分卡片 -->
      <div :class="['score-card', analysis.level]">
        <div class="score">{{ analysis.totalScore }}</div>
        <div class="label">{{ getLevelLabel(analysis.level) }}</div>
      </div>
      
      <!-- 五维度雷达图 -->
      <ScoreRadar :scores="analysis.scoreboard" />
      
      <!-- 相似文章列表 -->
      <div class="section">
        <h3>相似内容</h3>
        <SimilarArticlesList :articles="analysis.details.similarArticles" />
      </div>
      
      <!-- 关键观点 -->
      <div class="section">
        <h3>关键观点独特性</h3>
        <div v-for="point in analysis.details.keyPoints" :key="point.id" class="point-item">
          <span class="point-text">{{ point.point }}</span>
          <span class="novelty-badge">创新度 {{ point.novelty }}%</span>
        </div>
      </div>
      
      <!-- 风险提示 -->
      <div class="section" v-if="analysis.details.riskFlags.length">
        <h3>⚠️ 风险提示</h3>
        <div v-for="flag in analysis.details.riskFlags" :key="flag.id" class="risk-item">
          <span class="severity" :class="flag.severity">{{ flag.severity }}</span>
          <span class="reason">{{ flag.reason }}</span>
        </div>
      </div>
      
      <!-- 建议 -->
      <div class="section">
        <h3>📋 改进建议</h3>
        <ul>
          <li v-for="(rec, idx) in analysis.recommendations" :key="idx">{{ rec }}</li>
        </ul>
      </div>
      
      <!-- 操作按钮 -->
      <div class="actions">
        <button @click="reanalyze" class="btn-secondary">重新分析</button>
        <button @click="exportReport" class="btn-secondary">导出报告</button>
      </div>
    </div>
    
    <!-- 空态 -->
    <div v-else class="empty-state">
      <p>还未进行原创度检测</p>
      <button @click="analyze" class="btn-primary">开始分析</button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useOriginality } from '@/composables/useOriginality'
import ScoreRadar from './ScoreRadar.vue'
import SimilarArticlesList from './SimilarArticlesList.vue'

const props = defineProps({
  contentVersionId: String,
  title: String,
  body: String
})

const { analysis, loading, analyze, reanalyze } = useOriginality()

const getLevelLabel = (level) => {
  const labels = {
    excellent: '优秀',
    good: '良好',
    fair: '中等',
    poor: '低'
  }
  return labels[level] || level
}

const exportReport = () => {
  // TODO: 导出为 PDF 或 markdown
}
</script>

<style scoped>
.originality-panel {
  padding: 16px;
  background: var(--bg-secondary);
}

.score-card {
  padding: 20px;
  border-radius: 8px;
  text-align: center;
  margin-bottom: 16px;
}

.score-card.excellent {
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
}

.score-card.good {
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: white;
}

.score-card.fair {
  background: linear-gradient(135deg, #f59e0b, #d97706);
  color: white;
}

.score-card.poor {
  background: linear-gradient(135deg, #ef4444, #dc2626);
  color: white;
}

.score {
  font-size: 48px;
  font-weight: bold;
}

.label {
  font-size: 14px;
  margin-top: 8px;
}

.section {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--border-color);
}

.section h3 {
  margin-bottom: 12px;
  font-size: 14px;
  font-weight: 600;
}

.actions {
  margin-top: 20px;
  display: flex;
  gap: 12px;
}

.btn-primary, .btn-secondary {
  flex: 1;
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
}

.btn-primary {
  background: var(--primary-color);
  color: white;
}

.btn-secondary {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--border-color);
}
</style>
```

---

## 7. 实现路线图

### Phase 1: MVP（第 1-2 周）
- [ ] 数据库表创建 + 迁移脚本
- [ ] 后端：`similarity_engine.py` 实现基础 TF-IDF + 余弦相似度
- [ ] 后端：`originality.py` 路由实现
- [ ] 前端：StudioView 右侧面板加 Tab
- [ ] 前端：OriginalityPanel 基础 UI
- [ ] 端到端测试

### Phase 2: 优化（第 3 周）
- [ ] NLP 文本分析（观点提取、关键词）
- [ ] 结构分析 + 数据验证
- [ ] 雷达图和相似文章列表
- [ ] 缓存层（Redis）
- [ ] 性能优化（异步任务）

### Phase 3: 增强（第 4 周）
- [ ] 向量模型（sentence-transformers）集成
- [ ] 风险标记 + 建议优化
- [ ] 报告导出（PDF/Markdown）
- [ ] 与发布流程的集成（发布前校验）
- [ ] 管理后台的检测历史查看

---

## 8. 依赖与集成点

### 8.1 与现有系统的集成

| 集成点 | 方案 |
|---|---|
| **StudioView** | 右侧 Tab 切换，复用 draftTitle/draftBody |
| **AI Gateway** | 用于关键概念提取、建议生成 |
| **content_versions** | 查询历史稿件作为对标库 |
| **发布审核** | 发布前可自动扫描并标记风险内容 |
| **报告导出** | 复用现有 assets 表存储报告 PDF |

### 8.2 第三方库

```
# Python 依赖
scikit-learn>=1.3.0          # TF-IDF + 相似度计算
jieba>=0.42.1                # 中文分词
sentence-transformers>=2.2   # 向量模型（可选，Phase 2）
pandas>=2.0                  # 数据处理
numpy>=1.24                  # 数学运算

# 前端依赖
echarts@5.4.0                # 雷达图
@vueuse/core@10.x            # Vue 工具库
```

---

## 9. 安全与合规

### 9.1 隐私
- 检测结果存储于 workspace 级别，数据隔离
- 对标库仅包含当前用户或公开稿件（按 compare_scope）
- 敏感信息脱敏展示

### 9.2 知识产权
- 相似度检测不用于盗版判定，仅供用户参考
- 平台不保留用户稿件向量/嵌入（可选，取决于法规）
- 用户可选择完全隐私模式（不存储分析结果）

### 9.3 性能与成本
- 单次分析限制在 30 秒内完成
- 缓存 7 天内的重复查询（避免重复计算）
- 异步任务队列处理大型对标库查询

---

## 10. 监控与迭代

### 10.1 关键指标
- 分析成功率（目标 > 99%）
- 平均响应时间（目标 < 5s）
- 用户反馈准确度（与用户对标库的一致性）

### 10.2 A/B 测试
- 不同相似度算法对用户采纳率的影响
- 5 维度权重调优

### 10.3 反馈收集
- 用户对检测结果的满意度评分
- 建议的有效性（用户按建议修改后的发布成功率）

---

## 11. 成本与资源

| 项 | 成本 | 备注 |
|---|---|---|
| **开发** | 3-4 人周 | 后端 1.5 周 + 前端 1 周 + 优化 1 周 |
| **基础设施** | 无额外成本 | 复用现有 PG + Redis |
| **LLM 调用** | 低 | 仅关键概念提取，频率低 |
| **存储** | ~100MB/年 | 假设 1000 篇文章，每篇 100KB 分析结果 |
| **维护** | 每月 1-2 人天 | 监控 + 反馈处理 + 模型优化 |

---

## 附录 A：术语表

| 术语 | 定义 |
|---|---|
| **相似度** | 两文本内容的重合程度，0-100，数值高=重合度高 |
| **原创性** | 文中观点的独特程度，综合 5 个维度后的最终评分 |
| **对标库** | 用于对比的文章集合（所有/用户自有/公开稿件） |
| **风险标记** | 检测出的可能问题（高相似段落、数据缺失等） |
| **embedding** | 文本的向量表示，用于语义相似度计算 |

---

## 附录 B：示例场景

### 场景 1：用户在 Studio 完成 AI 创作

```
用户写了一篇关于"2026年创作者趋势"的文章
点击「原创检测」Tab → 系统分析
结果：总分 82，相似度 68%，观点独特性 85%
风险提示：第 2 段与"创作者年报"一文相似度 75%
建议：改写第 2 段，补充最新数据

用户根据建议修改后，重新分析 → 总分升至 90
决定发布
```

### 场景 2：平台运营人员查看违规稿件

```
一篇被举报的稿件被自动扫描
检测结果显示与已发布文章相似度 95%
系统自动标记为"高风险-可能抄袭"
运营可以一键查看对标文章并作决策
```

