# Phase 1 MVP 部署指南

文章原创度检测系统 Phase 1 MVP 完整部署与测试指南。

---

## 📋 部署清单

### ✅ 后端部署

#### 1. 数据库初始化
```bash
cd apps/api
.venv/Scripts/python.exe -c "
from app.core.db import init_db
init_db()
print('✓ 数据库初始化完成')
"
```

**验证**:
```bash
# 检查表是否创建
sqlite3 your_database.db ".tables" | grep originality_analyses
```

#### 2. 依赖项验证
```bash
# 检查必需的包
pip show scikit-learn jieba SQLAlchemy FastAPI
```

**必需包**:
- `scikit-learn>=1.4.2` - TF-IDF 向量化
- `jieba>=0.42.1` - 中文分词
- `SQLAlchemy>=2.1.8` - ORM
- `FastAPI>=0.162.0` - Web 框架

#### 3. API 路由注册验证
```bash
.venv/Scripts/python.exe -c "
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
response = client.post('/api/v1/content/analyze-originality', json={
    'contentVersionId': 'test',
    'compareScope': 'all'
})
# 期望状态码: 401 (缺少认证) 或 422 (验证错误)
# 不期望: 404 或 405
print(f'路由状态码: {response.status_code}')
print(f'响应: {response.json()}')
"
```

**预期结果**:
```
路由状态码: 401
响应: {'code': 'unauthorized', 'message': '缺少登录会话', ...}
✓ 路由已正确注册！
```

#### 4. 启动后端服务
```bash
cd apps/api
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**验证服务启动**:
```bash
curl http://localhost:8000/health
# 预期: {"status":"ok"}

curl http://localhost:8000/docs
# 预期: Swagger UI 文档页面
```

---

### ✅ 前端部署

#### 1. 依赖项验证
```bash
cd apps/web
npm ls | grep -E "vue|@vite"
```

#### 2. 前端热编译
```bash
npm run dev
# 通常服务于 http://localhost:5173
```

#### 3. 集成验证

打开浏览器访问: `http://localhost:5173`

1. 登录系统
2. 进入创作空间
3. 打开或新建一篇稿件
4. 右侧面板应显示 4 个 Tab:
   - ✓ AI 助手
   - ✓ 配图
   - ✓ **原创检测** (新增)
   - ✓ 灵感
5. 点击「原创检测」Tab
6. 点击「开始分析」按钮
7. 观察是否成功调用后端 API 并显示结果

---

## 🧪 API 测试

### 使用 cURL 测试

#### 1. 获取认证令牌
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "your@email.com",
    "password": "your_password"
  }'
```

记录返回的 `token` 字段。

#### 2. 测试原创度检测 API
```bash
TOKEN="your_token_here"

curl -X POST http://localhost:8000/api/v1/content/analyze-originality \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "contentVersionId": "your_content_version_id",
    "compareScope": "all"
  }'
```

**预期响应** (200 OK):
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
    "similarArticles": [...],
    "keyPoints": [...],
    "riskFlags": [...]
  },
  "recommendations": [...],
  "modelVersion": "1.0.0",
  "processingTimeMs": 245
}
```

#### 3. 获取最新检测结果
```bash
curl -X GET "http://localhost:8000/api/v1/content/{content_version_id}/originality" \
  -H "Authorization: Bearer $TOKEN"
```

---

## 🔍 故障排查

### 问题 1: 路由返回 404
**症状**: POST `/api/v1/content/analyze-originality` 返回 404

**检查清单**:
1. ✓ originality 模块是否在 `app/main.py` 中导入?
   ```python
   from .modules import originality
   ```

2. ✓ 路由是否正确注册?
   ```python
   app.include_router(originality.router, prefix=f"{settings.api_prefix}/content")
   ```

3. ✓ 后端是否重新启动?
   ```bash
   # 停止并重新启动
   ```

### 问题 2: 路由返回 405 Method Not Allowed
**症状**: 请求方法不对

**解决**:
- 使用 POST 方法调用 `/api/v1/content/analyze-originality`
- 使用 GET 方法调用 `/api/v1/content/{content_version_id}/originality`

### 问题 3: 前端 API 调用返回 401
**症状**: "缺少登录会话"

**解决**:
- 确保已登录系统
- 检查浏览器 localStorage 中的 `hanyue-token`
- 令牌是否过期?

### 问题 4: 数据库表不存在
**症状**: 运行时出现 "table originality_analyses not found"

**解决**:
```bash
# 重新初始化数据库
cd apps/api
.venv/Scripts/python.exe -c "from app.core.db import init_db; init_db()"

# 验证表创建
sqlite3 your_database.db "SELECT name FROM sqlite_master WHERE type='table' AND name='originality_analyses';"
```

### 问题 5: 相似度计算失败
**症状**: 检测返回 NaN 或异常值

**解决**:
```bash
# 检查 jieba 是否正确加载
.venv/Scripts/python.exe -c "import jieba; print('✓ jieba OK')"

# 检查 scikit-learn
.venv/Scripts/python.exe -c "from sklearn.feature_extraction.text import TfidfVectorizer; print('✓ sklearn OK')"
```

---

## 📊 性能基准

| 操作 | 平均时间 | 说明 |
|------|---------|------|
| TF-IDF 相似度计算 | 10-50ms | 取决于文本长度 |
| 五维度评分计算 | 5-10ms | 基础统计 |
| 数据库查询 | 5-20ms | 相似文章检索 |
| 总检测时间 | 50-150ms | 包括 I/O 与网络 |

---

## 🚀 生产部署建议

### 1. 环境变量配置
```bash
# .env.prod
DATABASE_URL=postgresql://user:pass@prod-db:5432/hanyue_prod
CORS_ORIGINS=https://yourdomain.com
LOG_LEVEL=INFO
```

### 2. 数据库连接池
```python
# apps/api/app/core/db.py
engine = create_engine(
    settings.database_url,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    connect_args={"timeout": 10}
)
```

### 3. 缓存策略
在 `originality_service.analyze_originality()` 中添加缓存:
```python
# 检查缓存
cached = db.scalar(
    select(OriginalityAnalysis)
    .where(
        OriginalityAnalysis.content_version_id == content_version_id,
        OriginalityAnalysis.analyzed_at > utcnow() - timedelta(hours=24)
    )
)
if cached:
    return cached
```

### 4. 异步队列（Phase 2）
使用 Celery 或 RabbitMQ 处理长时间检测:
```python
@app.post("/analyze-originality-async")
async def analyze_originality_async(req: OriginalityAnalysisRequest):
    task = originality_service.delay(...)  # Celery task
    return {"task_id": task.id, "status": "queued"}
```

### 5. 监控指标
```python
# 添加到 prometheus metrics
from prometheus_client import Counter, Histogram

analysis_counter = Counter('originality_analysis_total', 'Total analyses')
analysis_duration = Histogram('originality_analysis_duration_seconds', 'Analysis duration')
```

---

## ✨ 完成标志

✓ 所有后端路由正常运行  
✓ 数据库表已创建  
✓ 前端 UI 正确展示  
✓ API 调用成功  
✓ 相似度计算准确  
✓ 多维度评分完整  
✓ 错误处理优雅  
✓ 日志记录清晰  

---

## 📞 后续支持

### Phase 2 计划（1 周）
- [ ] NLP 模型集成 - 关键观点提取
- [ ] 结构对比算法 - 逻辑框架分析
- [ ] 数据验证 - 引用源检查
- [ ] 缓存层 - 性能优化

### Phase 3 计划（1 周）
- [ ] 向量模型 - 语义相似度
- [ ] 风险提示 - 高重合段落标记
- [ ] 报告导出 - PDF/Excel
- [ ] 批量检测 - 队列优化

---

**最后更新**: 2026-11-19  
**状态**: Phase 1 MVP 部署就绪
