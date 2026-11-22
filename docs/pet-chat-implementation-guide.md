# AI 智能助手宠物：分阶段执行指南

> 这是《完整规划》的操作版本。按阶段执行、验收、交付。

---

## 执行路线图

```
Week 1-2: Skill 中台          Week 3-4: 对话引擎          Week 5-6: 快捷工具
  ✓ 后端 CRUD API              ✓ 意图识别 NLU               ✓ 段落改稿工具
  ✓ 前端管理界面               ✓ 状态机实现                 ✓ SVG 卡生成
  ✓ 导入/导出                  ✓ 前端悬浮窗                ✓ 成稿投喂
  ✓ 预设包安装                 ✓ 对话历史                   ✓ 界面集成
                 ↓                          ↓                           ↓
            Phase 1 验收              Phase 2 验收                 Phase 3 验收
           
Week 7: 创作流打通            Week 8: 风格蒸馏              Week 9: 测试交付
  ✓ 热点→宠物链接             ✓ LLM 蒸馏风格档             ✓ 回归测试
  ✓ 快速初稿生成              ✓ 自动保存 Skill             ✓ 性能优化
  ✓ 一键成稿                  ✓ 在线编辑调整               ✓ 文档完成
                 ↓                          ↓                           ↓
            Phase 4 验收              Phase 5 验收                 Phase 6 验收
```

---

## Phase 1：Skill 系统中台（Week 1-2）

### 任务 1.1：后端数据模型

**文件**：`apps/api/app/models.py`

```python
# 新增两张表
class SkillRegistry(Base):
    __tablename__ = "skill_registry"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String(50), nullable=False)  # writing / editor / visual
    label = Column(String(200), nullable=False)
    description = Column(Text)
    version = Column(String(20), default="1.0")
    tags = Column(ARRAY(String), default=[])
    
    # 配置 JSON：{system_prompt, tone, forbidden_patterns, example_articles}
    config = Column(JSON, nullable=False)
    
    is_public = Column(Boolean, default=False)
    times_used = Column(Integer, default=0)
    avg_rating = Column(Float, default=0.0)
    last_used_at = Column(DateTime)
    
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    
    # 关系
    owner = relationship("User")
    imports = relationship("SkillImport", back_populates="skill", cascade="all, delete-orphan")


class SkillImport(Base):
    __tablename__ = "skill_imports"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    skill_id = Column(String(36), ForeignKey("skill_registry.id"), nullable=False)
    import_source_url = Column(String(500))
    imported_at = Column(DateTime, default=utcnow)
    version_snapshot = Column(JSON)  # 防导入后更新破坏
    
    skill = relationship("SkillRegistry", back_populates="imports")
```

**验收**：
```bash
# 在 infra/.pgdev/data 中看到新表
psql -U hanyue -d hanyue -c "\dt skill_*"
```

---

### 任务 1.2：后端 API 路由

**文件**：`apps/api/app/modules/skills.py`（新建）

```python
from fastapi import APIRouter, Depends, UploadFile, File, Query
from typing import Optional, List
from app.core.deps import get_db, get_current_user
from app.schemas import SkillCreate, SkillUpdate, SkillResponse
from app.services.skill_registry import SkillRegistryService

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])

@router.get("/registry", response_model=List[SkillResponse])
async def list_skills(
    type_: Optional[str] = Query(None, alias="type"),
    search: Optional[str] = None,
    limit: int = Query(50, le=100),
    offset: int = 0,
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """列表：按类型、搜索、分页"""
    svc = SkillRegistryService(db)
    return await svc.list_skills(
        owner_id=current_user.id,
        type_=type_,
        search=search,
        limit=limit,
        offset=offset,
    )

@router.post("", response_model=SkillResponse)
async def create_skill(
    skill: SkillCreate,
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """创建 Skill"""
    svc = SkillRegistryService(db)
    return await svc.create_skill(owner_id=current_user.id, data=skill.dict())

@router.patch("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: str,
    updates: SkillUpdate,
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """编辑"""
    svc = SkillRegistryService(db)
    return await svc.update_skill(
        skill_id=skill_id,
        owner_id=current_user.id,
        data=updates.dict(exclude_unset=True),
    )

@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str,
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """删除"""
    svc = SkillRegistryService(db)
    await svc.delete_skill(skill_id=skill_id, owner_id=current_user.id)
    return {"message": "Deleted"}

@router.post("/{skill_id}/export")
async def export_skill(
    skill_id: str,
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """导出为 JSON"""
    svc = SkillRegistryService(db)
    skill = await svc.get_skill(skill_id=skill_id, owner_id=current_user.id)
    return skill.to_dict()

@router.post("/import")
async def import_skills(
    file: UploadFile = File(...),
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """导入 ZIP 或 JSON"""
    svc = SkillRegistryService(db)
    return await svc.import_skills(
        file=file,
        owner_id=current_user.id,
    )

@router.post("/{skill_id}/preview")
async def preview_skill(
    skill_id: str,
    preview_text: str,
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """快速测试生成效果"""
    svc = SkillRegistryService(db)
    skill = await svc.get_skill(skill_id, owner_id=current_user.id)
    
    # 调用 ai_gateway 测试生成
    from app.services.ai_gateway import AIGateway
    gateway = AIGateway(db, Settings())
    response = await gateway.generate(
        prompt=preview_text,
        system_prompt=skill.config["system_prompt"],
        model="text-generation",
        max_tokens=200,
    )
    return {"preview": response}

@router.get("/presets")
async def list_presets():
    """官方预设列表"""
    # 返回硬编码的预设列表或从 GitHub Release 拉
    return [
        {
            "id": "writing_news_v2",
            "label": "新闻评论",
            "description": "适合财经时事评论",
            "version": "2.0",
            "url": "https://github.com/xxx/release/writing_news_v2.zip",
        },
        # ... 更多预设
    ]

@router.post("/presets/{preset_id}/install")
async def install_preset(
    preset_id: str,
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """一键安装预设"""
    svc = SkillRegistryService(db)
    return await svc.install_preset(preset_id=preset_id, owner_id=current_user.id)
```

**在 `main.py` 中注册**：
```python
from app.modules import skills
app.include_router(skills.router)
```

**验收**：
```bash
# 启动后端
curl http://127.0.0.1:8000/api/v1/skills/registry
# 返回 [] 或已有 Skill 列表（需认证）
```

---

### 任务 1.3：后端服务层

**文件**：`apps/api/app/services/skill_registry.py`（新建）

```python
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from app.models import SkillRegistry, SkillImport, User
from app.core.db import get_db
import json
import zipfile
from io import BytesIO
from uuid import uuid4
from datetime import datetime

class SkillRegistryService:
    def __init__(self, db: Session):
        self.db = db
    
    async def create_skill(self, owner_id: str, data: dict) -> SkillRegistry:
        """创建 Skill"""
        skill = SkillRegistry(
            id=str(uuid4()),
            owner_id=owner_id,
            type=data["type"],
            label=data["label"],
            description=data.get("description"),
            version=data.get("version", "1.0"),
            tags=data.get("tags", []),
            config=data.get("config", {}),
            is_public=data.get("is_public", False),
        )
        self.db.add(skill)
        self.db.commit()
        self.db.refresh(skill)
        return skill
    
    async def update_skill(self, skill_id: str, owner_id: str, data: dict) -> SkillRegistry:
        """编辑 Skill（仅所有者）"""
        skill = self.db.query(SkillRegistry).filter(
            and_(SkillRegistry.id == skill_id, SkillRegistry.owner_id == owner_id)
        ).first()
        if not skill:
            raise ValueError("Skill not found or permission denied")
        
        for key, value in data.items():
            if hasattr(skill, key) and key != "id":
                setattr(skill, key, value)
        
        skill.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(skill)
        return skill
    
    async def delete_skill(self, skill_id: str, owner_id: str):
        """删除"""
        skill = self.db.query(SkillRegistry).filter(
            and_(SkillRegistry.id == skill_id, SkillRegistry.owner_id == owner_id)
        ).first()
        if not skill:
            raise ValueError("Skill not found or permission denied")
        self.db.delete(skill)
        self.db.commit()
    
    async def list_skills(self, owner_id: str, type_: str = None, search: str = None, limit: int = 50, offset: int = 0):
        """列表"""
        query = self.db.query(SkillRegistry).filter(SkillRegistry.owner_id == owner_id)
        
        if type_:
            query = query.filter(SkillRegistry.type == type_)
        if search:
            query = query.filter(
                or_(
                    SkillRegistry.label.ilike(f"%{search}%"),
                    SkillRegistry.description.ilike(f"%{search}%"),
                )
            )
        
        return query.order_by(SkillRegistry.last_used_at.desc()).limit(limit).offset(offset).all()
    
    async def get_skill(self, skill_id: str, owner_id: str) -> SkillRegistry:
        """取单个"""
        skill = self.db.query(SkillRegistry).filter(
            and_(SkillRegistry.id == skill_id, SkillRegistry.owner_id == owner_id)
        ).first()
        if not skill:
            raise ValueError("Skill not found")
        return skill
    
    async def export_skill_as_json(self, skill_id: str, owner_id: str) -> dict:
        """导出为 JSON"""
        skill = await self.get_skill(skill_id, owner_id)
        return {
            "id": skill.id,
            "label": skill.label,
            "type": skill.type,
            "description": skill.description,
            "version": skill.version,
            "tags": skill.tags,
            "config": skill.config,
        }
    
    async def import_skills(self, file, owner_id: str):
        """导入 ZIP 或 JSON"""
        # 简化版：支持 ZIP 和单个 JSON
        content = await file.read()
        
        if file.filename.endswith(".json"):
            data = json.loads(content)
            skill = await self.create_skill(owner_id, data)
            return [{"id": skill.id, "label": skill.label}]
        
        elif file.filename.endswith(".zip"):
            imported = []
            with zipfile.ZipFile(BytesIO(content)) as zf:
                for name in zf.namelist():
                    if name.endswith(".json"):
                        data = json.loads(zf.read(name))
                        skill = await self.create_skill(owner_id, data)
                        imported.append({"id": skill.id, "label": skill.label})
            return imported
    
    async def install_preset(self, preset_id: str, owner_id: str):
        """安装预设（从 GitHub Release 拉）"""
        # 伪代码：实际需要从 GitHub 拉并存入数据库
        # 这里假设预设已经定义好
        presets = {
            "writing_news_v2": {...},
            "writing_xiaohongshu_v1": {...},
        }
        preset_data = presets.get(preset_id)
        if not preset_data:
            raise ValueError("Preset not found")
        
        skill = await self.create_skill(owner_id, preset_data)
        
        # 记录导入源
        import_record = SkillImport(
            id=str(uuid4()),
            skill_id=skill.id,
            import_source_url=f"preset:{preset_id}",
            version_snapshot=preset_data,
        )
        self.db.add(import_record)
        self.db.commit()
        
        return skill
```

**验收**：
```bash
# 单元测试
cd apps/api
.venv/Scripts/python.exe -m pytest tests/test_skill_registry.py -v
```

---

### 任务 1.4：前端 SkillManager 页面

**文件**：`apps/web/src/views/SkillsView.vue`（新建）

```vue
<template>
  <div class="skills-container">
    <!-- 顶部操作 -->
    <div class="skills-header">
      <h1>Skill 库管理</h1>
      <div class="actions">
        <input v-model="searchQuery" type="text" placeholder="搜索 Skill..." />
        <select v-model="filterType" @change="applyFilters">
          <option value="">全部类型</option>
          <option value="writing">写作</option>
          <option value="editor">编辑</option>
          <option value="visual">可视化</option>
        </select>
        <button @click="showCreateDialog = true" class="btn-primary">
          + 新建 Skill
        </button>
        <button @click="showImportDialog = true" class="btn-secondary">
          导入预设
        </button>
      </div>
    </div>

    <!-- 我的 Skill 列表 -->
    <section class="skills-section">
      <h2>我的 Skill ({{ mySkills.length }})</h2>
      <div class="skills-grid">
        <SkillCard
          v-for="skill in mySkills"
          :key="skill.id"
          :skill="skill"
          @edit="editSkill"
          @delete="deleteSkill"
          @export="exportSkill"
        />
      </div>
    </section>

    <!-- 官方预设 -->
    <section class="skills-section">
      <h2>官方预设 ({{ presets.length }})</h2>
      <div class="skills-grid">
        <SkillPresetCard
          v-for="preset in presets"
          :key="preset.id"
          :preset="preset"
          @install="installPreset"
        />
      </div>
    </section>

    <!-- 创建/编辑对话框 -->
    <SkillEditDialog
      v-if="showCreateDialog || editingSkill"
      :skill="editingSkill"
      @save="saveSkill"
      @close="closeEditDialog"
    />

    <!-- 导入对话框 -->
    <SkillImportDialog
      v-if="showImportDialog"
      @import="importSkills"
      @close="showImportDialog = false"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '@/api'
import SkillCard from '@/components/skill/SkillCard.vue'
import SkillPresetCard from '@/components/skill/SkillPresetCard.vue'
import SkillEditDialog from '@/components/skill/SkillEditDialog.vue'
import SkillImportDialog from '@/components/skill/SkillImportDialog.vue'

const mySkills = ref([])
const presets = ref([])
const searchQuery = ref('')
const filterType = ref('')
const showCreateDialog = ref(false)
const editingSkill = ref(null)
const showImportDialog = ref(false)

onMounted(async () => {
  await loadSkills()
  await loadPresets()
})

async function loadSkills() {
  try {
    mySkills.value = await api.get('/api/v1/skills/registry')
  } catch (err) {
    console.error('Failed to load skills:', err)
  }
}

async function loadPresets() {
  try {
    presets.value = await api.get('/api/v1/skills/presets')
  } catch (err) {
    console.error('Failed to load presets:', err)
  }
}

async function saveSkill(skillData) {
  try {
    if (editingSkill.value) {
      await api.patch(`/api/v1/skills/${editingSkill.value.id}`, skillData)
    } else {
      await api.post('/api/v1/skills', skillData)
    }
    await loadSkills()
    closeEditDialog()
  } catch (err) {
    console.error('Failed to save skill:', err)
  }
}

async function deleteSkill(skillId) {
  if (confirm('确定要删除吗？')) {
    try {
      await api.delete(`/api/v1/skills/${skillId}`)
      await loadSkills()
    } catch (err) {
      console.error('Failed to delete skill:', err)
    }
  }
}

async function exportSkill(skillId) {
  // 实现导出逻辑
}

async function importSkills(files) {
  // 实现导入逻辑
}

async function installPreset(presetId) {
  try {
    await api.post(`/api/v1/skills/presets/${presetId}/install`)
    await loadSkills()
  } catch (err) {
    console.error('Failed to install preset:', err)
  }
}

function editSkill(skill) {
  editingSkill.value = skill
  showCreateDialog.value = true
}

function closeEditDialog() {
  showCreateDialog.value = false
  editingSkill.value = null
}

function applyFilters() {
  // 实现过滤逻辑
}
</script>

<style scoped>
.skills-container {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.skills-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 32px;
}

.actions {
  display: flex;
  gap: 12px;
}

.skills-section {
  margin-bottom: 48px;
}

.skills-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
  margin-top: 16px;
}
</style>
```

**在 `router.js` 中注册**：
```js
{
  path: '/settings/skills',
  component: () => import('@/views/SkillsView.vue'),
  meta: { requiresAuth: true }
}
```

**验收**：
```
✓ 页面加载无报错
✓ 能创建新 Skill
✓ 能编辑现有 Skill
✓ 能导入 Skill ZIP 包
✓ 能安装官方预设
```

---

### Phase 1 验收清单

```
后端：
  ✓ 表创建成功（psql 查询确认）
  ✓ 5 个 API 端点工作正常
  ✓ 单元测试覆盖 > 80%
  ✓ 权限隔离验证（用户只看自己的 Skill）

前端：
  ✓ /settings/skills 页面加载
  ✓ CRUD 操作完整
  ✓ 导入/导出可用
  ✓ 预设安装成功

集成：
  ✓ 创建 → 编辑 → 导出 → 导入 → 验证一致性
  ✓ 团队成员共享预设包成功
```

**时间**：Week 1-2 / 10 个开发日

---

## Phase 2：宠物对话引擎（Week 3-4）

### 任务 2.1：后端对话路由

**文件**：`apps/api/app/modules/pet_chat.py`（新建）

```python
from fastapi import APIRouter, Depends, WebSocketException
from fastapi.responses import StreamingResponse
from app.core.deps import get_db, get_current_user
from app.services.pet_chat_engine import PetChatEngine
from app.schemas import PetChatRequest

router = APIRouter(prefix="/api/v1/agent/pet", tags=["pet-chat"])

@router.post("/chat")
async def pet_chat(
    request: PetChatRequest,  # { message, context, activeSkill? }
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """
    对话端点：
    - 意图识别 + 参数收集 + 任务执行
    - 返回结构化响应
    """
    engine = PetChatEngine(db, current_user)
    
    # 识别意图
    intent_result = await engine.identify_intent(request.message)
    
    # 参数收集（如需补全）
    if intent_result.confidence < 0.8:
        missing_info = await engine.collect_missing_info(
            intent_result.intent,
            intent_result.entities,
        )
        return {
            "intent": intent_result.intent,
            "confidence": intent_result.confidence,
            "needs_info": True,
            "missing_fields": missing_info,
            "prompt": f"我需要更多信息：{missing_info}",
        }
    
    # 执行任务
    task_result = await engine.execute_task(
        intent=intent_result.intent,
        entities=intent_result.entities,
        skill_id=request.activeSkill,
    )
    
    return {
        "intent": intent_result.intent,
        "confidence": intent_result.confidence,
        "result": task_result,
        "suggestions": task_result.get("next_steps", []),
    }
```

---

### 任务 2.2：对话引擎（NLU + 路由）

**文件**：`apps/api/app/services/pet_chat_engine.py`（新建）

```python
from sqlalchemy.orm import Session
from app.models import SkillRegistry, User
from typing import Optional, Dict, Any, List
import json

class PetChatEngine:
    """对话状态机与意图路由"""
    
    # 意图关键词映射
    INTENT_KEYWORDS = {
        "new_draft": ["写", "创作", "帮我写", "新建", "生成", "文章"],
        "polish": ["去AI味", "润色", "改写", "这段"],
        "style_extract": ["学风格", "从", "蒸馏", "风格"],
        "svg_card": ["生成卡", "可视化", "数据卡", "图表"],
        "submit_to_paper": ["投喂", "填入", "成稿台", "编辑"],
    }
    
    def __init__(self, db: Session, current_user: User):
        self.db = db
        self.user_id = current_user.id
    
    async def identify_intent(self, message: str) -> Dict[str, Any]:
        """
        NLU：识别用户意图
        返回：{intent, entities, confidence}
        """
        # 简化版：关键词匹配（实际应该用 LLM）
        message_lower = message.lower()
        
        best_intent = None
        best_score = 0
        
        for intent, keywords in self.INTENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in message_lower) / len(keywords)
            if score > best_score:
                best_score = score
                best_intent = intent
        
        # 提取实体（简化版）
        entities = {
            "text": message,
            "url": self._extract_url(message),
            "topic": self._extract_topic(message),
        }
        
        return {
            "intent": best_intent or "unknown",
            "entities": entities,
            "confidence": best_score if best_intent else 0.3,
        }
    
    async def collect_missing_info(self, intent: str, entities: dict) -> List[str]:
        """
        参数补全：返回需要收集的字段
        """
        missing = []
        
        if intent == "new_draft":
            if not entities.get("url") and not entities.get("topic"):
                missing.append("平台选择（小红书/公众号/博客）")
                missing.append("风格倾向（观点/分析/故事）")
        elif intent == "svg_card":
            if not entities.get("data"):
                missing.append("数据内容")
        
        return missing
    
    async def execute_task(self, intent: str, entities: dict, skill_id: Optional[str]) -> Dict[str, Any]:
        """
        根据意图执行任务
        """
        if intent == "new_draft":
            return await self._handle_new_draft(entities, skill_id)
        elif intent == "polish":
            return await self._handle_polish(entities, skill_id)
        elif intent == "svg_card":
            return await self._handle_svg_card(entities)
        else:
            return {"error": "Intent not recognized"}
    
    async def _handle_new_draft(self, entities: dict, skill_id: Optional[str]) -> Dict[str, Any]:
        """
        场景：用户说"帮我写个关于 X 的文章"
        → 蒸馏资料 → 推荐 Skill → 生成初稿 → 返回预览
        """
        # 1. 推荐 Skill（如未指定）
        if not skill_id:
            skills = self.db.query(SkillRegistry).filter(
                SkillRegistry.owner_id == self.user_id,
                SkillRegistry.type == "writing"
            ).order_by(SkillRegistry.last_used_at.desc()).limit(3).all()
            
            if skills:
                skill_id = skills[0].id  # 用最近使用的
        
        # 2. 调用 ai_article 服务生成初稿
        from app.services.ai_article import AIArticleService
        article_svc = AIArticleService(self.db)
        
        draft = await article_svc.draft_article(
            topic=entities.get("topic"),
            url=entities.get("url"),
            skill_id=skill_id,
        )
        
        return {
            "action": "preview_draft",
            "content": draft["content"],
            "word_count": len(draft["content"]),
            "skill_used": skill_id,
            "next_steps": [
                {"label": "投喂到成稿台", "action": "submit_to_paper"},
                {"label": "继续调整", "action": "continue_chat"},
                {"label": "重新生成", "action": "regenerate"},
            ]
        }
    
    async def _handle_polish(self, entities: dict, skill_id: Optional[str]) -> Dict[str, Any]:
        """去 AI 味、润色等"""
        from app.services.paragraph_toolkit import ParagraphToolkit
        toolkit = ParagraphToolkit(self.db)
        
        # 识别操作类型（去 AI 味/润色/改写）
        text = entities.get("text")
        
        versions = await toolkit.remove_ai_taste(text, skill_id)
        
        return {
            "action": "show_versions",
            "versions": versions,
            "next_steps": [
                {"label": "选择版本 1", "action": "apply_version_1"},
                {"label": "选择版本 2", "action": "apply_version_2"},
                {"label": "选择版本 3", "action": "apply_version_3"},
            ]
        }
    
    async def _handle_svg_card(self, entities: dict) -> Dict[str, Any]:
        """生成 SVG 卡"""
        # 实现在 Phase 3
        return {"action": "generating_card", "status": "pending"}
    
    def _extract_url(self, text: str) -> Optional[str]:
        """从文本提取 URL"""
        import re
        urls = re.findall(r'http[s]?://[^\s]+', text)
        return urls[0] if urls else None
    
    def _extract_topic(self, text: str) -> Optional[str]:
        """从文本提取主题（简化版）"""
        # 实际应该用 NER
        return text.split("关于")[-1].strip() if "关于" in text else None
```

---

### 任务 2.3：前端悬浮窗 PetChat.vue

**文件**：`apps/web/src/components/global/PetChat.vue`（新建）

```vue
<template>
  <div class="pet-chat" :class="{ expanded, fullscreen }">
    <!-- 最小化头部 -->
    <div class="pet-header" @click="toggleExpand" v-if="!expanded">
      <span class="pet-avatar">🤖</span>
      <span class="unread" v-if="unreadCount > 0">{{ unreadCount }}</span>
    </div>

    <!-- 完整窗口 -->
    <div class="pet-window" v-if="expanded">
      <!-- 顶部 -->
      <div class="pet-top-bar">
        <h3>✨ 创意助手</h3>
        <div class="controls">
          <button @click="toggleFullscreen" title="全屏">⛶</button>
          <button @click="toggleExpand" title="最小化">−</button>
        </div>
      </div>

      <!-- 对话历史 -->
      <div class="chat-history" ref="chatHistory">
        <div v-for="msg in messages" :key="msg.id" class="message" :class="msg.role">
          <div class="message-content">
            {{ msg.content }}
          </div>
          <!-- 如果是结构化响应，显示操作按钮 -->
          <div v-if="msg.actions" class="message-actions">
            <button v-for="action in msg.actions" :key="action.id" @click="executeAction(action)">
              {{ action.label }}
            </button>
          </div>
        </div>
      </div>

      <!-- 建议快捷命令 -->
      <div class="suggestions" v-if="suggestedCommands.length > 0">
        <button v-for="cmd in suggestedCommands" :key="cmd.id" @click="executeCommand(cmd)">
          {{ cmd.label }}
        </button>
      </div>

      <!-- 输入框 -->
      <div class="input-area">
        <textarea
          v-model="userInput"
          placeholder="对我说..."
          @keydown.enter.ctrl="sendMessage"
          @keydown.meta.enter="sendMessage"
        />
        <div class="input-controls">
          <button @click="toggleSkillDrawer" title="Skill 选择">🎯</button>
          <button @click="sendMessage" :disabled="!userInput.trim() || isLoading">
            {{ isLoading ? '...发送中' : '发送' }}
          </button>
        </div>
      </div>

      <!-- Skill 选择侧栏 -->
      <SkillDrawer
        v-if="showSkillDrawer"
        :skills="userSkills"
        :active-skill="activeSkill"
        @select="selectSkill"
        @close="showSkillDrawer = false"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { api } from '@/api'
import { useAuthStore } from '@/stores/auth'
import SkillDrawer from '@/components/skill/SkillDrawer.vue'

const authStore = useAuthStore()

// 状态
const messages = ref([])
const userInput = ref('')
const isLoading = ref(false)
const expanded = ref(true)
const fullscreen = ref(false)
const showSkillDrawer = ref(false)
const chatHistory = ref(null)

// Skill
const activeSkill = ref(null)
const userSkills = ref([])

// 建议
const suggestedCommands = ref([
  { id: 'new_draft', label: '📝 新建创作' },
  { id: 'hot_topics', label: '🔥 热点选题' },
  { id: 'style_extract', label: '🎨 蒸馏风格' },
])

const unreadCount = computed(() => messages.value.filter(m => m.role === 'assistant' && !m.read).length)

onMounted(async () => {
  await loadSkills()
  // 添加欢迎消息
  messages.value.push({
    id: 'init',
    role: 'assistant',
    content: '👋 嗨，我是你的创意助手！我可以帮你：\n• 🖊️ 快速生成初稿\n• ✨ 润色与改写\n• 🎨 生成数据卡\n• 📌 一键成稿投喂',
    read: true,
  })
})

async function loadSkills() {
  try {
    userSkills.value = await api.get('/api/v1/skills/registry')
    if (userSkills.value.length > 0) {
      activeSkill.value = userSkills.value[0].id
    }
  } catch (err) {
    console.error('Failed to load skills:', err)
  }
}

async function sendMessage() {
  if (!userInput.value.trim() || isLoading.value) return

  const userMsg = {
    id: `msg_${Date.now()}`,
    role: 'user',
    content: userInput.value,
    read: true,
  }
  messages.value.push(userMsg)
  userInput.value = ''
  isLoading.value = true

  try {
    const response = await api.post('/api/v1/agent/pet/chat', {
      message: userMsg.content,
      context: { messages: messages.value },
      activeSkill: activeSkill.value,
    })

    // 显示 AI 响应
    const assistantMsg = {
      id: `msg_${Date.now()}_ai`,
      role: 'assistant',
      content: response.result?.content || JSON.stringify(response.result),
      actions: response.result?.next_steps,
      read: !expanded.value,  // 未展开时标记为未读
    }
    messages.value.push(assistantMsg)
  } catch (err) {
    messages.value.push({
      id: `msg_${Date.now()}_error`,
      role: 'assistant',
      content: `❌ 出错了：${err.message}`,
      read: true,
    })
  } finally {
    isLoading.value = false
    // 自动滚动到底
    await nextTick()
    if (chatHistory.value) {
      chatHistory.value.scrollTop = chatHistory.value.scrollHeight
    }
  }
}

function executeCommand(cmd) {
  if (cmd.id === 'new_draft') {
    userInput.value = '帮我写一篇关于'
  } else if (cmd.id === 'hot_topics') {
    userInput.value = '推荐今日热点'
  }
  // ... 其他命令
  sendMessage()
}

function executeAction(action) {
  if (action.action === 'submit_to_paper') {
    // 投喂到成稿台
    // TODO: 实现投喂逻辑
  }
}

function toggleExpand() {
  expanded.value = !expanded.value
}

function toggleFullscreen() {
  fullscreen.value = !fullscreen.value
}

function toggleSkillDrawer() {
  showSkillDrawer.value = !showSkillDrawer.value
}

function selectSkill(skillId) {
  activeSkill.value = skillId
  showSkillDrawer.value = false
}
</script>

<style scoped>
.pet-chat {
  position: fixed;
  bottom: 20px;
  right: 20px;
  width: 400px;
  height: 600px;
  background: white;
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
  z-index: 1000;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto;
}

.pet-chat.expanded {
  /* 展开状态 */
}

.pet-header {
  width: 50px;
  height: 50px;
  border-radius: 50%;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  position: relative;
  color: white;
  font-size: 24px;
}

.pet-header .unread {
  position: absolute;
  top: -5px;
  right: -5px;
  background: #ff4757;
  color: white;
  border-radius: 50%;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: bold;
}

.pet-window {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.pet-top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #eee;
  flex-shrink: 0;
}

.pet-top-bar h3 {
  margin: 0;
  font-size: 16px;
  color: #333;
}

.controls {
  display: flex;
  gap: 8px;
}

.controls button {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 16px;
  padding: 4px 8px;
  border-radius: 4px;
  transition: background 0.2s;
}

.controls button:hover {
  background: #f0f0f0;
}

.chat-history {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.message {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.message.user {
  align-items: flex-end;
}

.message.user .message-content {
  background: #667eea;
  color: white;
  border-radius: 8px 8px 0 8px;
  padding: 8px 12px;
  max-width: 80%;
  word-wrap: break-word;
}

.message.assistant .message-content {
  background: #f5f5f5;
  color: #333;
  border-radius: 8px 8px 8px 0;
  padding: 8px 12px;
  max-width: 90%;
}

.message-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.message-actions button {
  background: #667eea;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 6px 12px;
  cursor: pointer;
  font-size: 12px;
  transition: background 0.2s;
}

.message-actions button:hover {
  background: #5568d3;
}

.suggestions {
  display: flex;
  gap: 8px;
  padding: 0 16px 8px;
  flex-wrap: wrap;
  flex-shrink: 0;
}

.suggestions button {
  background: #f0f0f0;
  border: 1px solid #ddd;
  border-radius: 20px;
  padding: 6px 12px;
  cursor: pointer;
  font-size: 12px;
  transition: all 0.2s;
}

.suggestions button:hover {
  background: #e0e0e0;
}

.input-area {
  padding: 12px 16px;
  border-top: 1px solid #eee;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

textarea {
  resize: none;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 8px 12px;
  font-family: inherit;
  font-size: 14px;
  max-height: 80px;
  min-height: 40px;
}

.input-controls {
  display: flex;
  gap: 8px;
}

.input-controls button {
  background: #667eea;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 8px 16px;
  cursor: pointer;
  flex: 1;
  transition: background 0.2s;
}

.input-controls button:hover:not(:disabled) {
  background: #5568d3;
}

.input-controls button:disabled {
  background: #ccc;
  cursor: not-allowed;
}
</style>
```

**验收**：
```
✓ 悬浮窗显示在右下角
✓ 能发送消息
✓ 收到 AI 回复
✓ Skill 选择器工作
✓ 建议命令显示
```

---

### Phase 2 验收清单

```
后端：
  ✓ 意图识别工作（至少 5 种意图）
  ✓ 对话端点返回结构化响应
  ✓ 单元测试覆盖 > 80%

前端：
  ✓ 悬浮窗组件显示
  ✓ 能发送消息并接收回复
  ✓ 建议命令可点击
  ✓ Skill 选择正确

集成：
  ✓ 完整对话流"新建创作" → 生成初稿
  ✓ 对话历史持久化
```

**时间**：Week 3-4 / 10 个开发日

---

## 快速检查清单（全阶段通用）

### 交付前必执行

```bash
# 1. 后端回归测试
cd apps/api
.venv/Scripts/python.exe -m pytest tests/ -q --tb=short
# 预期：所有测试通过，新增测试 > 80% 覆盖

# 2. 前端构建
cd ../web
npm run build
# 预期：无错误，无警告

# 3. 本地启动验证
cd ../api
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
# 新建端口检查日志无错误

cd ../../apps/web
npm run dev
# http://localhost:5173 访问无报错
```

### 权限验证

```
✓ 用户 A 创建的 Skill，用户 B 看不到（除非公开）
✓ 对话历史不跨用户泄露
✓ API 鉴权完整
```

### 性能指标

```
✓ 对话响应 < 500ms（不含 LLM 生成）
✓ Skill 列表加载 < 200ms
✓ 页面首屏 < 1s
```

---

## 附录：技术栈回顾

| 层 | 框架 | 文件位置 |
|----|------|--------|
| 前端 | Vue 3 + Pinia | `apps/web/src/` |
| 后端 | FastAPI + SQLAlchemy | `apps/api/app/` |
| 数据库 | PostgreSQL | `infra/.pgdev/data/` |
| 测试 | pytest | `apps/api/tests/` |
| 认证 | Bearer Token | `app/core/security.py` |

---

**下一步**：确认此方案后，逐个 Phase 推进。每个 Phase 完成后运行验收清单，确认无遗漏。

