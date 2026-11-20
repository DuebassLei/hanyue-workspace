# 文章原创度检测系统 - Phase 1 MVP 实现完成

## 📋 项目概述
文章原创度检测工具 Phase 1 MVP 已全面实现，集成了 TF-IDF 相似度计算与五维度评分系统。

---

## ✅ 后端实现清单

### 1. 数据模型 (`OriginalityAnalysis`)
**文件**: `apps/api/app/models.py` (第 1284 行)

- 表名: `originality_analyses`
- 主要字段:
  - `id`, `content_version_id`, `workspace_id` - 基础关联
  - `similarity_score`, `originality_score`, `structure_score`, `data_accuracy_score`, `style_uniqueness_score` - 五维度评分
  - `total_score`, `level` - 综合评分与等级
  - `similar_articles`, `key_points`, `risk_flags` - JSON 存储详情
  - `recommendations` - 改进建议
  - `compare_scope`, `model_version` - 检测参数

### 2. 相似度计算引擎
**文件**: `apps/api/app/services/similarity_engine.py`

核心功能:
- `calculate_tfidf_similarity()` - TF-IDF + 余弦相似度
- `convert_similarity_to_score()` - 相似度→评分映射
- `extract_similar_segments()` - 段落级相似度提取

技术栈:
- jieba (中文分词)
- scikit-learn (TF-IDF + 余弦相似度)

### 3. 原创度检测服务
**文件**: `apps/api/app/services/originality_service.py`

核心功能:
- `analyze_originality()` - 主检测函数，编排各维度分析
- `_calculate_similarity_score()` - 相似度评分与文章匹配
- `_calculate_*_score()` - 其他维度评分（观点独特性、结构、数据、风格）
- `_generate_recommendations()` - 基于评分生成建议

权重配置:
```
总分 = 相似度×40% + 观点独特性×25% + 结构创新度×15% + 数据真实度×12% + 语言风格×8%
```

### 4. API 路由
**文件**: `apps/api/app/modules/originality.py`

端点:
- `POST /api/v1/content/analyze-originality` - 执行检测
- `GET /api/v1/content/{content_version_id}/originality` - 获取最新结果

### 5. 请求/响应 Schema
**文件**: `apps/api/app/schemas.py` (第 1593-1659 行)

- `OriginalityAnalysisRequest` - 检测请求
- `OriginalityAnalysisOut` - 检测结果
- `ScoreboardOut` - 五维度评分
- `SimilarArticleOut` - 相似文章摘要
- 等支持结构

### 6. 应用注册
**文件**: `apps/api/app/main.py`

- 第 31 行: 导入 originality 模块
- 第 121 行: 在路由中注册

---

## ✅ 前端实现清单

### 1. 右侧面板新增 Tab
**文件**: `apps/web/src/views/StudioView.vue`

位置:
- 第 1798-1804 行: Tab 按钮
- 第 2079-2171 行: Tab 内容面板

功能:
- 四个 Tab 切换（AI 助手 / 配图 / 原创检测 / 灵感）
- 原创检测 Tab 默认展开

### 2. UI 组件
位置: 第 2079-2171 行

组件结构:
```
- 工具头（标题 + 收起按钮）
- 加载态 / 空态 / 结果态
- 总分卡片（彩色等级指示）
- 五维度评分条形图
- 相似文章列表
- 改进建议列表
- 重新分析按钮
```

### 3. 状态管理
位置: 第 1151-1154 行

```javascript
const originalityOpen = ref(true)           // 面板展开状态
const originalityLoading = ref(false)       // 加载状态
const originalityError = ref('')            // 错误信息
const originalityAnalysis = ref(null)       // 分析结果
```

### 4. 分析函数
位置: 第 1156-1182 行

```javascript
async function analyzeOriginality()  // 执行检测
function openOriginalityTab()        // 打开 Tab
```

### 5. 样式定义
位置: 第 3609-3732 行

样式类:
- `.originality-score-card` - 总分卡片
- `.originality-scores` - 五维度评分
- `.originality-section` - 详情分组
- `.recommendations-list` - 建议列表

等级颜色:
- `level-excellent` - 绿色梯度（90+）
- `level-good` - 蓝色梯度（75-89）
- `level-fair` - 橙色梯度（60-74）
- `level-poor` - 红色梯度（0-59）

### 6. Composable 辅助
**文件**: `apps/web/src/composables/useOriginality.js` (新建)

功能:
- 状态管理封装
- 分析函数封装
- 等级标签/样式映射

---

## 🔗 集成验证

### API 路径一致性 ✅
- 前端请求: `/content/analyze-originality` (第 1166 行)
- 后端路由: `/analyze-originality` (modules/originality.py 第 20 行)
- 完整路径: `/api/v1/content/analyze-originality`

### 字段映射 ✅
前端发送 (camelCase):
```javascript
{
  contentVersionId: "...",
  compareScope: "all"
}
```

后端期望 (snake_case):
```python
content_version_id: str
compare_scope: str
```

自动转换由 `CamelModel` 处理（pydantic-settings）

### 响应字段匹配 ✅
前端期望字段 (camelCase):
- `originalityAnalysis.totalScore`
- `originalityAnalysis.level`
- `originalityAnalysis.scoreboard.similarity`
- `originalityAnalysis.details.similarArticles`
- `originalityAnalysis.recommendations`

后端返回字段 (OriginalityAnalysisOut):
- `total_score` → `totalScore`
- `level` → `level`
- `scoreboard.similarity` → `scoreboard.similarity`
- `details.similar_articles` → `details.similarArticles`
- `recommendations` → `recommendations` (列表)

---

## 📦 依赖项

已安装在 `requirements.txt`:
- `scikit-learn==1.4.2` - 向量化 & 余弦相似度
- `jieba==0.42.1` - 中文分词
- `FastAPI==0.162.0` - Web 框架
- `SQLAlchemy==2.1.8` - ORM
- 其他已有依赖

---

## 🎯 Phase 1 MVP 功能清单

✅ 相似度检测
- TF-IDF + jieba 分词
- 句级相似度提取
- 文章级相似度排序

✅ 五维度评分
1. 相似度 (40%)
2. 观点独特性 (25%)
3. 结构创新度 (15%)
4. 数据真实度 (12%)
5. 语言风格 (8%)

✅ 综合评分与等级
- 0-59: 低 (poor)
- 60-74: 中等 (fair)
- 75-89: 良好 (good)
- 90+: 优秀 (excellent)

✅ 结果展示
- 总分卡片（彩色）
- 五维度条形图
- 相似文章列表
- 改进建议

✅ 用户交互
- 一键检测
- 重新分析
- 加载/错误状态

---

## ⚠️ 已知限制（Phase 2 改进）

1. 观点独特性 - 当前固定分数，需 LLM 关键词提取
2. 结构创新度 - 当前固定分数，需要结构对比算法
3. 语言风格独特性 - 当前固定分数
4. 缓存机制 - 尚未实现（影响性能）
5. 向量嵌入 - 当前仅 TF-IDF，可扩展为 NLP 模型

---

## 📝 文件清单

### 后端
- `app/models.py` - OriginalityAnalysis 模型
- `app/schemas.py` - 请求/响应 Schema
- `app/modules/originality.py` - API 路由
- `app/services/originality_service.py` - 检测逻辑
- `app/services/similarity_engine.py` - 相似度计算
- `app/main.py` - 应用注册

### 前端
- `src/views/StudioView.vue` - UI 实现 + 状态管理
- `src/composables/useOriginality.js` - 辅助 composable
- `src/api/client.js` - API 客户端（已有）

### 文档
- `docs/planning/originality-detection-spec.md` - 完整规划文档

---

## 🚀 后续步骤

### Phase 2 (1 周)
- [ ] NLP 模型集成 - 关键观点提取
- [ ] 结构对比算法 - 逻辑框架分析
- [ ] 数据验证 - 引用源检查
- [ ] 缓存层 - 提升性能

### Phase 3 (1 周)
- [ ] 向量模型 - 语义相似度
- [ ] 风险提示 - 高重合段落标记
- [ ] 报告导出 - PDF/Excel
- [ ] 批量检测 - 队列优化

### 优化方向
- 实现相似度缓存表
- 集成向量数据库 (Milvus/Weaviate)
- 异步检测队列
- 检测结果版本管理

---

## 📖 使用示例

### 前端调用
```javascript
const { analyze, analysis, loading, error } = useOriginality()

// 执行检测
await analyze('content_version_id_xxx', 'all')

// 结果访问
console.log(analysis.value.totalScore)      // 85
console.log(analysis.value.level)           // 'good'
console.log(analysis.value.recommendations) // ['改进建议...']
```

### API 调用
```bash
curl -X POST http://localhost:8000/api/v1/content/analyze-originality \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "content_version_id": "xxx",
    "compare_scope": "all"
  }'
```

### 响应示例
```json
{
  "analysisId": "analysis_xxx",
  "contentVersionId": "version_xxx",
  "totalScore": 85,
  "level": "good",
  "scoreboard": {
    "similarity": 85,
    "originality": 80,
    "structure": 78,
    "dataAccuracy": 82,
    "styleUniqueness": 75
  },
  "details": {
    "similarArticles": [
      {
        "id": "article_xxx",
        "title": "相似文章标题",
        "similarity": 0.65,
        "matchingSnippets": [...]
      }
    ],
    "keyPoints": [],
    "riskFlags": []
  },
  "recommendations": [
    "观点独特性有提升空间，建议加入更多原创观点和数据"
  ],
  "processingTimeMs": 245
}
```

---

**项目状态**: ✅ Phase 1 MVP 完成，可进行端到端测试
**最后更新**: 2026-11-19
