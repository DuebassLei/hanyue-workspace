# AI 智能助手宠物：完整规划与实施方案

> **版本**：v1.0 | **发布日期**：2026-11-22 | **所有者**：创作平台团队  
> **目标**：将成稿台对话能力独立为全局浮窗，融合 Skill 系统与多源创作入口，打造"创意伙伴"体验。

---

## 一、产品定位与核心价值

### 1.1 是什么

**AI 智能助手宠物** = 悬浮全局对话窗 + Skill 系统中台 + 多源创作编排引擎

- **不是聊天机器人**：每次对话都能直接产出可用的**初稿、修改建议、SVG卡**，不是闲聊。
- **不是成稿台的复制**：成稿台是稿件编辑车间（段落级细节编辑、版本管理），宠物是创意助手（快速启动、风格迁移、快捷工具）。
- **是创作流的上游**：热点 → 资料蒸馏 → 初稿 → 一键投喂成稿台 → 精编 → 发布。

### 1.2 核心场景

| 场景 | 用户操作 | AI 宠物响应 | 成果 |
|------|--------|----------|------|
| 灵感捕捉 | "我想写个关于 [热点] 的文章" | 蒸馏选题、推荐风格档 | 创作方向清晰 |
| 资料积累 | 投喂链接 / 粘贴文档 / 上传 PDF | 提取要点、融合已有风格 | 知识库 + 参考素材 |
| 初稿生成 | "按[风格档]写初稿" | 组织结构、插入要点、风格一致 | 可编辑的 Markdown 初稿 |
| 快捷改稿 | 选中段落 → "去 AI 味" / "润色" | 在线改写、保留原意 | 多版本候选 |
| SVG 卡 | "生成个数据可视化卡" | 分析数据 → SVG 卡 | 插入成稿台 |
| 一键成稿 | "投喂到成稿台" | 创建新稿件 / 追加到现有稿 | 进入成稿台编辑 |

---

## 二、核心功能模块设计

### 2.1 Skill 系统中台

#### 2.1.1 Skill 的定义

```yaml
Skill:
  id: "writing_v1"           # 全局唯一，可版本化
  type: "writing"            # 类型：writing / editor / visual / analytic
  label: "新闻评论风格"       # UI 显示名
  description: "适合时事热点评论，观点锐利、论据充分"
  version: "1.0"
  tags: ["评论", "新闻", "观点"]
  meta:
    source: "uploaded"       # built-in / uploaded / imported
    created_by: "user_id"
    created_at: "2026-11-22T10:00:00Z"

  # 核心能力
  capabilities:
    - draft_writing: "生成初稿"
    - polish: "润色 / 改写"
    - format_check: "AI 味检测"
    - visual_suggest: "SVG 卡建议"

  # 配置
  config:
    system_prompt: "你是一个新闻评论专家..."  # 蒸馏自用户投喂的内容
    tone: "严肃、专业、偏观点"
    target_audience: "财经媒体编辑、投资者"
    forbidden_patterns: ["显而易见", "人工智能"]
    example_articles: ["url_1", "url_2"]  # 参考文章

  # 由系统维护
  stats:
    times_used: 42
    avg_rating: 4.5
    last_used: "2026-11-21T18:30:00Z"
```

#### 2.1.2 Skill 维护界面

**路由**：`/settings/skills` （或独立 `/skills` 页面）

**功能**：
- **列表视图**：所有 Skill 卡片展示（类型筛选、搜索、排序）
- **创建 Skill**：
  - 新建空白 → 手工编写 prompt
  - 从链接蒸馏 → 自动分析内容 → 生成初步 prompt → 人工调优
  - 从上传文档蒸馏 → 同上
  - 从 Git 仓库导入 → 读 `skills/*.json` 或 `skills/*.md`
- **编辑 Skill**：
  - 修改 prompt、tone、forbidden_patterns
  - 添加 / 删除示例文章
  - 预览生成效果（快速测试）
- **导入 / 导出**：
  - 单个导出为 `.json` 或 `.md`
  - 打包导出为 `.zip`（含所有依赖）
  - 导入 GitHub Release / 本地 Zip
  - **预设包**：官方提供的 "新闻评论 v2"、"小红书风格 v1" 等，一键安装

#### 2.1.3 Skill 注册表

**后端存储**：

```
PostgreSQL 表：
- skill_registry
  - id (uuid)
  - owner_id (user_id)  # 用户级隔离
  - type (enum: writing/editor/visual/...)
  - label, description, version
  - config (jsonb)  # system_prompt, tone 等
  - is_public (bool)  # 公开分享
  - created_at, updated_at, last_used_at
  
- skill_imports  # 追踪导入来源
  - id
  - skill_id
  - import_source_url  # GitHub release / 本地上传
  - imported_at
  - version_snapshot  # 防后续更新破坏原用例
```

**API 端点**：

```
GET    /api/v1/skills/registry              # 列表（带搜索、排序、筛选）
POST   /api/v1/skills                       # 创建
PATCH  /api/v1/skills/{id}                  # 编辑
DELETE /api/v1/skills/{id}                  # 删除
POST   /api/v1/skills/{id}/export           # 导出单个为 JSON
POST   /api/v1/skills/bulk-export           # 打包导出（含依赖）
POST   /api/v1/skills/import                # 导入（本地上传 / GitHub 链接）
POST   /api/v1/skills/{id}/preview          # 快速测试（投喂文本 → 看生成效果）
GET    /api/v1/skills/presets               # 官方预设包列表
POST   /api/v1/skills/presets/{id}/install  # 一键安装预设
```

---

### 2.2 写作风格档库

**现有实现**（成稿台已部分支持）：

```
resources/studio_skills/styles/
├── tutorial.md
├── analysis.md
├── opinion.md
├── soft_ad.md
└── ...
```

**扩展设计**：

#### 2.2.1 风格档结构

```markdown
---
label: "新闻评论"
hint: "适合时事热点，观点锐利"
category: "news"
tags: ["评论", "观点", "时效"]
source: "蒸馏自《财经十一人》文章库"
---

# 风格档定义

## 结构要求
- 开头：事件背景 (1-2 段)
- 中间：多维分析 (3-4 段)
- 结尾：观点总结 (1-2 段)

## 语气特征
- 专业权威，不过度友善
- 用数据说话，论据充分
- 避免夸大或绝对化表述

## 禁用词表
- 显而易见、众所周知、人工智能、值得一提、需要注意
```

#### 2.2.2 风格档来源与蒸馏

**来源**：
1. **内置预设** → `resources/studio_skills/styles/*.md`
2. **用户链接投喂** → 从参考文章库自动提取
3. **文档蒸馏** → 上传 PDF / Word / TXT → 分析 → 生成风格档

**蒸馏流程**（后端 `WritingStyleExtractor` 服务）：

```python
# 伪代码
async def extract_style_from_articles(articles: List[str]):
    """
    输入：5-10 篇参考文章 URL / 本地内容
    输出：风格档 Markdown
    """
    # 1. 获取原文
    contents = [fetch_article(url) for url in articles]
    
    # 2. LLM 分析
    prompt = """
    分析这些文章的共同特征：
    1. 段落结构（开头、中间、结尾各占比）
    2. 句式风格（长句/短句比例）
    3. 修饰词偏好
    4. 避讳表述
    5. 论据方式（数据 / 案例 / 理论）
    
    输出 Markdown 格式的风格档。
    """
    
    style_md = await ai_gateway.generate(
        prompt=prompt,
        context=contents,
        model="text-generation"
    )
    
    # 3. 人工确认 → 存入 Skill Registry
    return style_md
```

---

### 2.3 AI 智能助手宠物：对话引擎

#### 2.3.1 对话架构

**宠物 ≠ 通用 ChatGPT**：每轮对话都有**明确的业务目标**，输出是**可以直接用**的产物（初稿、修改建议、SVG 卡）。

#### 2.3.2 对话状态机

```
[初始化] → [信息收集] → [处理选择] → [执行任务] → [成果预览] → [确认提交]

例子流：
用户: "帮我写个关于消费降级的文章"
  ↓ [信息收集]
宠物: "我们来细化一下：
  - 目标平台？(小红书 / 公众号 / 博客)
  - 风格倾向？(观点型 / 分析型 / 故事型)
  - 参考链接有吗？"
  
用户: "公众号，分析型，这三篇有参考价值"
  ↓ [处理选择]
宠物: "👍 分析型偏理性，论据充分。检测到的风格特征：数据驱动、结构清晰。
  推荐使用 'economics_analysis' 风格档。要我从这些链接蒸馏风格吗？"

用户: "直接用 economics_analysis 写初稿吧"
  ↓ [执行任务]
宠物: "正在组织内容..." (流式显示)
  [初稿显示在浮窗中]
  
  ↓ [成果预览]
  "初稿已生成，共 1200 字，3 个段落。满意吗？"
  
用户: "去掉第二段的一些数据，显得太复杂"
  ↓ [循环回 执行任务]
  
用户: "投喂到成稿台"
  ↓ [确认提交]
  宠物: "✓ 已投喂！成稿 ID: draft_xxx"
  [跳转成稿台]
```

#### 2.3.3 核心对话指令

**指令集**（用户可说自然语言，宠物自动识别意图）：

| 意图 | 用户说法 | 宠物行为 |
|------|--------|--------|
| 新建创作 | "帮我写 X" / "创作关于 X" / "分析 X" | 信息收集 → 推荐 Skill / 风格档 → 一键生成 |
| 投喂资料 | "这个链接有参考" / "上传这个 PDF" | 蒸馏内容 → 融入风格 → 预览 |
| 蒸馏风格 | "从这 5 篇文章学风格" / "我的文风是..." | 提取特征 → 生成风格档 → 存入 Skill Registry |
| 改稿 | "这段去 AI 味" / "把这里润色下" / "改写这段" | 识别范围 → 调用对应 Skill → 展示多版本 |
| SVG 卡 | "生成个数据卡" / "把这个数据做成可视化" | 分析数据 → 生成 SVG → 预览 → 插入 |
| 投喂成稿 | "投喂到成稿台" / "填入编辑" | 创建或更新稿件 → 跳转成稿台 |
| Skill 管理 | "创建新 Skill" / "导入预设" | 打开 Skill 管理界面 |

---

### 2.4 多源创作入口

#### 2.4.1 创作流程图

```
┌─ 热点选题库 (/hot-topics)
│  - 多源采集 + 事件合并
│  - 100 分评分
│  - 点击 → 宠物推荐初稿风格 → 生成 → 成稿
│
├─ 宠物对话创作
│  - 文字描述 / 链接投喂
│  - 资料蒸馏 → 初稿 → 修改 → 投喂
│
├─ 一键成稿
│  - 从热点 / 链接 / 描述 → 自动选 Skill
│  - 一次生成可用初稿
│
└─ 成稿台 (/paper) 细编
   - 段落级编辑
   - 对话改稿（复用现有对话面板）
   - 配图、发布
```

#### 2.4.2 一键成稿流程（新 API）

**输入三选一**：
- 热点 ID：`?topic_id=xxx`
- 链接：`?url=https://...`
- 自由描述：`?prompt=...`

**处理**：
1. **信息补全**：如果输入不足，宠物对话进行信息收集
2. **Skill 推荐**：基于内容 + 用户历史偏好推荐
3. **一键初稿**：调用 `ai_article.py` 的 draft 接口 + 指定 Skill
4. **投喂成稿**：创建新 `StudioArticle` + 返回成稿 ID

**API**：
```
POST /api/v1/studio/quick-draft
{
  "topic_id": "xxx",  # 三选一
  "url": "https://...",
  "prompt": "写个关于消费降级的分析",
  
  "skill_id": "writing_analysis_v1",  # 可选，不指定则自动推荐
  "auto_submit": true  # 是否直接投喂成稿台
}

Response:
{
  "article_id": "draft_xxx",
  "content": "Markdown 初稿",
  "skill_used": "writing_analysis_v1",
  "preview_url": "/paper?id=draft_xxx",  # 直接跳转成稿
  "metadata": {
    "word_count": 1200,
    "structure_analysis": "3 段，论据充分"
  }
}
```

---

### 2.5 快捷功能集

**在宠物对话中可用**，也可从成稿台调用（右键菜单或快捷键）。

#### 2.5.1 段落级快捷工具

| 功能 | 触发 | 实现 | 输出 |
|------|------|------|------|
| **去 AI 味** | 选中段落 → 命令 | 调用 `studio_pipeline.fix_aigc_taste()` + 指定 Skill | 多版本改写 |
| **润色** | 选中段落 → 命令 | LLM 改进可读性、流畅度，保留意思 | 改写建议 |
| **改写** | 选中 + 指定风格 | 按新风格档改 | 结构不变，表述改 |
| **扩展** | 选中 + 字数目标 | 保留核心，补充细节 | 更详细版本 |
| **精简** | 选中 + 目标压缩比 | 去冗余，保留精髓 | 简明版本 |
| **论据补充** | 选中观点 | 自动搜索数据 / 案例 | 建议补充项 |

**实现**：复用 `studio_pipeline.py` 的改稿能力 + 新增 `ParagraphToolkit` 服务

#### 2.5.2 SVG 动态卡自动生成

**触发**：
- 用户说"生成个数据卡"
- 或在宠物中选择"可视化建议"

**流程**：
1. **数据识别**：从文本中提取表格、数字、对比
2. **卡类型推荐**：
   - 趋势折线图
   - 对比柱状图
   - 分布饼图
   - 信息卡（带图标 + 数字）
   - 进度条
3. **SVG 生成**：调用现有 `card_service.py`，新增"数据驱动"模板
4. **预览与微调**：用户可在宠物中看预览，说"改成冷色系"→ 重新生成

**API**（新增）：
```
POST /api/v1/studio/svg-card/generate
{
  "content": "2024年用户增长50%，对比2023年的30%",
  "card_type": "auto",  # 或指定 "comparison" / "trend" / "info"
  "style_hint": "现代、蓝色系"  # 可选
}

Response:
{
  "svg": "<svg>...</svg>",
  "type": "comparison",
  "markdown_fence": "```svg\n<svg>...</svg>\n```"  # 插入成稿用
}
```

---

## 三、前端架构

### 3.1 组件设计

#### 3.1.1 PetChat.vue（全局悬浮窗）

**位置**：`apps/web/src/components/global/PetChat.vue`

**属性**：
- 位置：右下角固定（可拖动）
- 大小：初始 400x600px（可调）
- 状态：
  - 最小化（仅显示头像 + 未读计数）
  - 展开（完整对话窗）
  - 全屏模式（大屏互动）

**结构**：
```vue
<template>
  <div class="pet-chat-container" :class="{ expanded, fullscreen }">
    <!-- 头部：宠物名 + 操作按钮 -->
    <div class="pet-header">
      <span class="pet-name">🤖 创意助手</span>
      <button @click="toggleFullscreen">⛶</button>
      <button @click="toggleMinimize">−</button>
    </div>

    <!-- 对话区 -->
    <div class="chat-history">
      <ChatMessage v-for="msg in messages" :key="msg.id" :message="msg" />
    </div>

    <!-- 快捷命令面板 -->
    <QuickCommandBar v-if="showCommandBar" @select="executeCommand" />

    <!-- 输入框 + 建议 -->
    <div class="input-area">
      <textarea v-model="userInput" placeholder="对我说..." @keydown.enter="sendMessage" />
      <SuggestionPills :suggestions="suggestedCommands" @select="applyCommand" />
      <SendButton @click="sendMessage" :loading="isLoading" />
    </div>

    <!-- 侧栏：Skill 切换器 / 风格档选择 -->
    <Drawer v-if="showDrawer" position="left">
      <SkillSelector :skills="userSkills" @select="setActiveSkill" />
      <StylePresetSelector :styles="writerStyles" @select="setActiveStyle" />
    </Drawer>
  </div>
</template>
```

#### 3.1.2 SkillManager.vue（Skill 维护界面）

**路由**：`/settings/skills` 或独立 `/skills`

**页面结构**：
```
┌─────────────────────────────────────────┐
│ Skill 库管理 [搜索] [筛选]               │
├─────────────────────────────────────────┤
│ ┌─ 我的 Skill (3)                       │
│ │ ┌──────────┐ ┌──────────┐             │
│ │ │ writing  │ │ visual   │  ...        │
│ │ │ v2.0     │ │ v1.0     │             │
│ │ └──────────┘ └──────────┘             │
│ └─────────────────────────────────────────│
│                                           │
│ ┌─ 官方预设 (5)                          │
│ │ ┌──────────┐ ┌──────────┐             │
│ │ │ 新闻评论  │ │ 小红书   │  ...       │
│ │ │ 安装      │ │ 安装     │             │
│ │ └──────────┘ └──────────┘             │
│ └─────────────────────────────────────────│
│                                           │
│ [+ 新建 Skill] [导入预设] [批量导出]      │
└─────────────────────────────────────────┘

[点击卡片] → 详情 / 编辑页面
```

**编辑页面**：
```
┌────────────────────────────────────────┐
│ 编辑: Writing Analysis v1              │
├────────────────────────────────────────┤
│ 基本信息                               │
│ ┌──────────────────────────────────┐  │
│ │ 名称: Writing Analysis           │  │
│ │ 类型: Writing ▼                  │  │
│ │ 版本: 1.0                        │  │
│ │ 描述: 适合财经分析...            │  │
│ │ 标签: [财经] [分析] [数据]       │  │
│ └──────────────────────────────────┘  │
│                                        │
│ 核心配置                               │
│ ┌──────────────────────────────────┐  │
│ │ System Prompt:                   │  │
│ │ [长文本编辑器]                    │  │
│ │                                  │  │
│ │ 语气: [严肃] [专业] [观点型]      │  │
│ │ 目标受众: [财经媒体编辑]          │  │
│ └──────────────────────────────────┘  │
│                                        │
│ 禁用词表                               │
│ ┌──────────────────────────────────┐  │
│ │ [显而易见] [众所周知] [人工智能]  │  │
│ │ [+ 添加禁用词]                    │  │
│ └──────────────────────────────────┘  │
│                                        │
│ 参考文章 (示例来源)                    │
│ ┌──────────────────────────────────┐  │
│ │ [+ 添加链接]                     │  │
│ │ https://finance.qq.com/...       │  │
│ │ https://huxiu.com/...            │  │
│ └──────────────────────────────────┘  │
│                                        │
│ 快速测试                               │
│ ┌──────────────────────────────────┐  │
│ │ 投入文本: [textarea]              │  │
│ │ [生成测试] → 预览生成效果          │  │
│ └──────────────────────────────────┘  │
│                                        │
│ [保存] [导出] [删除] [预览]            │
└────────────────────────────────────────┘
```

#### 3.1.3 PaperIntegration（成稿台投喂按钮）

**位置**：PetChat 对话窗底部

**行为**：
- 一键按钮 "投喂到成稿台"
- 对话框选择：创建新稿件 / 追加到现有稿件
- 跳转到成稿台编辑

---

### 3.2 状态管理（Pinia Store）

```js
// stores/petChat.js
export const usePetChatStore = defineStore('petChat', {
  state: () => ({
    // 对话状态
    messages: [],
    userInput: '',
    isLoading: false,
    
    // Skill 状态
    activeSkill: null,  // 当前选用的 Skill
    activeStyle: 'tutorial',  // 当前风格档
    userSkills: [],  // 用户的 Skill 列表
    skillRegistry: {},  // 缓存
    
    // UI 状态
    isPetExpanded: true,
    isPetFullscreen: false,
    showCommandBar: false,
    showSkillDrawer: false,
    
    // 任务追踪
    currentTask: null,
    taskHistory: [],
  }),
  
  actions: {
    async sendMessage(content) {
      // 1. 发送到后端 /api/v1/agent/pet/chat
      // 2. 获取意图识别 + 建议下一步
      // 3. 流式获取响应
      // 4. 更新 UI
    },
    
    async executeCommand(command) {
      // 根据 command 触发对应的 Skill
    },
    
    async loadSkills() {
      // GET /api/v1/skills/registry
    },
    
    async submitToPaper(draft) {
      // POST /api/v1/studio/quick-draft
      // 或 POST /api/v1/studio/articles/{id}/append
    },
  }
})
```

---

## 四、后端架构

### 4.1 新增模块

#### 4.1.1 `modules/skills.py`（Skill 注册表 API）

```python
from fastapi import APIRouter, Depends
from app.core.deps import get_db

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])

@router.get("/registry")
async def list_skills(
    type_: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """列表：按类型、搜索、排序"""

@router.post("")
async def create_skill(skill: SkillCreate, db: Session = Depends(get_db)):
    """创建 Skill"""

@router.patch("/{skill_id}")
async def update_skill(skill_id: str, updates: SkillUpdate, db: Session = Depends(get_db)):
    """编辑 Skill"""

@router.delete("/{skill_id}")
async def delete_skill(skill_id: str, db: Session = Depends(get_db)):
    """删除"""

@router.post("/{skill_id}/export")
async def export_skill(skill_id: str, db: Session = Depends(get_db)):
    """导出单个"""

@router.post("/bulk-export")
async def bulk_export_skills(skill_ids: List[str], db: Session = Depends(get_db)):
    """打包导出"""

@router.post("/import")
async def import_skills(file: UploadFile, db: Session = Depends(get_db)):
    """导入（ZIP / JSON）"""

@router.post("/{skill_id}/preview")
async def preview_skill(skill_id: str, preview: SkillPreview, db: Session = Depends(get_db)):
    """快速测试生成效果"""

@router.get("/presets")
async def list_presets():
    """官方预设列表"""

@router.post("/presets/{preset_id}/install")
async def install_preset(preset_id: str, db: Session = Depends(get_db)):
    """一键安装预设"""
```

#### 4.1.2 `modules/pet_chat.py`（宠物对话 API）

```python
@router.post("/api/v1/agent/pet/chat")
async def pet_chat(
    request: PetChatRequest,  # { message, context, activeSkill }
    db: Session = Depends(get_db),
):
    """
    对话端点：
    1. 意图识别（NLU）
    2. 参数补全（如需信息收集）
    3. 任务执行（调用对应 Skill / 服务）
    4. 返回结果 + 建议下一步
    """

@router.post("/api/v1/studio/quick-draft")
async def quick_draft(
    request: QuickDraftRequest,  # { topic_id / url / prompt, skill_id }
    db: Session = Depends(get_db),
):
    """一键成稿"""

@router.post("/api/v1/studio/svg-card/generate")
async def generate_svg_card(
    request: SVGCardRequest,
    db: Session = Depends(get_db),
):
    """SVG 卡生成"""
```

#### 4.1.3 `services/skill_registry.py`（Skill 注册表服务）

```python
class SkillRegistryService:
    """Skill 生命周期管理"""
    
    async def create_skill(self, user_id: str, skill_data: dict):
        """新建 Skill，自动分配 ID"""
    
    async def extract_style_from_articles(self, articles: List[str]) -> str:
        """从参考文章蒸馏风格档 Markdown"""
    
    async def extract_style_from_document(self, file_path: str) -> str:
        """从文档蒸馏风格档"""
    
    async def export_skill_as_json(self, skill_id: str) -> dict:
        """导出为 JSON"""
    
    async def import_skills_from_zip(self, zip_path: str, user_id: str):
        """导入 ZIP 包"""
    
    async def install_preset(self, preset_id: str, user_id: str):
        """安装官方预设"""
```

#### 4.1.4 `services/pet_chat_engine.py`（宠物对话引擎）

```python
class PetChatEngine:
    """
    对话状态机 + 意图路由
    """
    
    async def process_message(self, user_msg: str, context: dict) -> dict:
        """
        1. NLU 意图识别
        2. 参数补全（信息收集）
        3. 任务路由
        4. 执行 + 返回
        """
    
    async def identify_intent(self, msg: str) -> dict:
        """
        返回: {
          intent: "new_draft" / "polish" / "style_extract" / "svg_card" / ...,
          entities: { topic, url, skill_id, ... },
          confidence: 0.95
        }
        """
    
    async def collect_missing_info(self, intent: str, entities: dict) -> dict:
        """返回需要收集的字段 + 建议值"""
    
    async def execute_task(self, intent: str, entities: dict, skill_id: str) -> dict:
        """根据意图调用相应服务"""
```

#### 4.1.5 `services/paragraph_toolkit.py`（段落快捷工具）

```python
class ParagraphToolkit:
    """段落级改稿快捷工具"""
    
    async def remove_ai_taste(self, text: str, skill_id: str) -> List[str]:
        """去 AI 味，返回 3 个版本"""
    
    async def polish(self, text: str) -> str:
        """润色"""
    
    async def rewrite_by_style(self, text: str, style_name: str) -> str:
        """按风格改写"""
    
    async def expand(self, text: str, target_words: int) -> str:
        """扩展到目标字数"""
    
    async def compress(self, text: str, compression_ratio: float) -> str:
        """精简到指定比例"""
    
    async def suggest_evidence(self, text: str) -> List[str]:
        """论据补充建议"""
```

### 4.2 数据模型

```python
# models.py

class SkillRegistry(Base):
    """Skill 注册表"""
    __tablename__ = "skill_registry"
    
    id = Column(String, primary_key=True, default=uuid4)
    owner_id = Column(String, ForeignKey("users.id"), index=True)
    type = Column(String)  # writing / editor / visual / analytic
    label = Column(String)
    description = Column(String)
    version = Column(String, default="1.0")
    tags = Column(ARRAY(String), default=[])
    
    # 配置
    config = Column(JSON)  # { system_prompt, tone, forbidden_patterns, ... }
    
    # 元数据
    is_public = Column(Boolean, default=False)
    times_used = Column(Integer, default=0)
    avg_rating = Column(Float, default=0)
    last_used_at = Column(DateTime)
    
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    
    # 关系
    owner = relationship("User")
    imports = relationship("SkillImport", back_populates="skill")


class SkillImport(Base):
    """Skill 导入记录"""
    __tablename__ = "skill_imports"
    
    id = Column(String, primary_key=True, default=uuid4)
    skill_id = Column(String, ForeignKey("skill_registry.id"))
    import_source_url = Column(String)
    imported_at = Column(DateTime, default=utcnow)
    version_snapshot = Column(JSON)  # 防后续更新破坏
    
    skill = relationship("SkillRegistry", back_populates="imports")


class PetChatConversation(Base):
    """宠物对话记录"""
    __tablename__ = "pet_chat_conversations"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), index=True)
    
    messages = Column(JSON)  # [{role, content, timestamp}, ...]
    state = Column(String)  # active / archived
    
    # 追踪
    associated_article_id = Column(String, nullable=True)
    associated_skill_ids = Column(ARRAY(String), default=[])
    
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
```

---

## 五、实施阶段规划

### 5.1 Phase 1：Skill 系统中台（2 周）

**目标**：完整的 Skill 生命周期管理

**任务**：
- [ ] **后端**
  - [ ] 数据模型 (`SkillRegistry`, `SkillImport`)
  - [ ] `modules/skills.py` 实现 CRUD + 导入导出
  - [ ] `services/skill_registry.py` 核心逻辑
  - [ ] 单元测试 (100% 覆盖 API 路由)
  
- [ ] **前端**
  - [ ] `SkillManager.vue` 列表 + 创建 / 编辑 / 删除
  - [ ] 快速测试面板（投喂文本 → 预览生成）
  - [ ] 导入 / 导出 UI
  - [ ] 预设包一键安装

- [ ] **测试**
  - [ ] 创建 → 编辑 → 导出 → 导入 → 验证一致性
  - [ ] 权限隔离（用户只能看 / 编辑自己的 Skill）

**验收标准**：
- Skill 管理页面完全可用
- 导入 GitHub Release 预设包成功
- 一键安装后下次选择时出现在下拉列表

---

### 5.2 Phase 2：宠物对话引擎（2 周）

**目标**：对话 → 任务转发 → 结果交付

**任务**：
- [ ] **后端**
  - [ ] `modules/pet_chat.py` 对话端点
  - [ ] `services/pet_chat_engine.py` 意图识别 + 路由
  - [ ] 对话状态机实现（`信息收集` → `处理` → `执行` → `预览` → `确认`）
  - [ ] 测试用例覆盖 5 个核心场景
  
- [ ] **前端**
  - [ ] `PetChat.vue` 悬浮窗组件
  - [ ] 对话输入 + 历史展示
  - [ ] Skill / 风格档选择器
  - [ ] 快捷命令面板
  
- [ ] **集成**
  - [ ] 宠物对话 → 调用 `ai_article.py` draft（已有）
  - [ ] 宠物对话 → 调用 `studio_pipeline.py` 改稿（已有）

**验收标准**：
- 完整对话流"新建创作"可成功走通
- 生成的初稿可投喂到成稿台
- 宠物理解至少 10 种自然语言表述

---

### 5.3 Phase 3：快捷工具 + SVG 卡（2 周）

**目标**：段落级快捷改稿 + 数据可视化

**任务**：
- [ ] **后端**
  - [ ] `services/paragraph_toolkit.py` 实现 6 个工具
  - [ ] 复用 `studio_pipeline.py` 的去 AI 味逻辑
  - [ ] 新增 SVG 卡生成接口（结合现有 `card_service.py`）
  
- [ ] **前端**
  - [ ] 段落选中 → 快捷菜单（右键 / 长按）
  - [ ] "去 AI 味" / "润色" 弹窗选择版本
  - [ ] SVG 卡生成 & 预览
  
- [ ] **集成**
  - [ ] 宠物对话中执行快捷工具
  - [ ] 成稿台中调用快捷工具

**验收标准**：
- 选中段落点"去 AI 味"返回 3 个版本 < 2 秒
- SVG 卡从数据识别到预览 < 3 秒
- 卡片可正确插入成稿

---

### 5.4 Phase 4：多源创作入口打通（1 周）

**目标**：热点 / 链接 / 描述 → 一键成稿

**任务**：
- [ ] **后端**
  - [ ] `POST /api/v1/studio/quick-draft` 实现
  - [ ] 自动 Skill 推荐逻辑
  - [ ] 蒸馏链接 / 文档为初稿输入
  
- [ ] **前端**
  - [ ] 热点列表 → "创意助手生成" 按钮
  - [ ] 创作入口统一跳转宠物对话
  - [ ] 一键成稿 → 成稿台
  
- [ ] **集成**
  - [ ] `/hot-topics` 页面与宠物打通
  - [ ] 创作空间「灵感」面板与宠物打通

**验收标准**：
- 点热点 → 宠物推荐初稿 → 一键填入成稿台
- 粘贴链接 → 自动蒸馏资料 → 生成初稿

---

### 5.5 Phase 5：风格档蒸馏（1 周）

**目标**：从链接 / 文档自动提取风格档

**任务**：
- [ ] **后端**
  - [ ] `WritingStyleExtractor` 服务实现
  - [ ] LLM 调用 + 风格档 Markdown 生成
  - [ ] 自动保存为 Skill
  
- [ ] **前端**
  - [ ] 宠物对话中"从这些文章学风格"
  - [ ] 显示蒸馏进度 + 预览
  - [ ] 一键保存为 Skill

**验收标准**：
- 投喂 5 篇文章 → 自动提取风格档
- 提取的风格档可用于后续创作
- 风格档自动落库可复用

---

### 5.6 Phase 6：测试 & 打磨（1 周）

**目标**：完整回归 + 性能优化 + 文档

**任务**：
- [ ] **功能测试**
  - [ ] 完整业务流回归（AGENT.md 要求的全量 pytest）
  - [ ] E2E 测试：热点 → 创作 → 成稿 → 发布
  
- [ ] **性能**
  - [ ] 对话响应 < 500ms（不含 LLM 生成）
  - [ ] SVG 卡生成 < 3s
  - [ ] 初稿生成 SSE 流式显示
  
- [ ] **文档 & 交付**
  - [ ] 产品 FAQ
  - [ ] Skill 编写指南
  - [ ] API 文档 (Swagger 自动生成)
  - [ ] 更新 AGENT.md

**验收标准**：
- 全量回归通过（`pytest -q` 无新增失败）
- 性能指标达成
- 文档完整

---

## 六、自我验收清单

### 6.1 功能验收

#### Skill 系统
- [ ] 创建新 Skill（手工编写 prompt）
- [ ] 从链接蒸馏 Skill
- [ ] 编辑 Skill（改 prompt、tone、禁用词）
- [ ] 导出单个 Skill 为 JSON
- [ ] 打包导出多个 Skill 为 ZIP
- [ ] 导入 Skill ZIP 包
- [ ] 导入 GitHub Release 预设包
- [ ] 一键安装官方预设
- [ ] 权限隔离（用户只看自己的 Skill）

#### 宠物对话
- [ ] 发送消息 → 获取回复（正常路径）
- [ ] 意图识别（新建创作、投喂资料、改稿、SVG 卡）
- [ ] 信息收集（缺少参数时询问用户）
- [ ] Skill 自动推荐
- [ ] 风格档自动推荐
- [ ] 一键投喂成稿台（创建新 / 追加现有）

#### 快捷工具
- [ ] 段落选中 → 右键菜单显示快捷工具
- [ ] "去 AI 味" 返回多个版本
- [ ] "润色" 改进可读性
- [ ] "按新风格改写" 保留意思改结构
- [ ] "扩展 / 精简" 控制字数

#### SVG 卡
- [ ] 自动识别数据（表格、数字、对比）
- [ ] 生成对应卡类型（趋势 / 对比 / 分布 / 信息）
- [ ] 预览与微调（改色系等）
- [ ] 插入成稿台

#### 创作流集成
- [ ] 热点列表 → "创意助手生成" → 初稿
- [ ] 粘贴链接 → 自动蒸馏资料 → 初稿
- [ ] 一键成稿 → 填入成稿台
- [ ] 热点评分与推荐联动

### 6.2 性能验收

| 指标 | 目标 | 验证方法 |
|------|------|--------|
| 对话响应 | < 500ms（不含 LLM 生成） | 浏览器 DevTools 网络耗时 |
| SVG 卡生成 | < 3s | 从点击到预览显示 |
| 初稿生成 | SSE 流式（总耗时 < 30s） | 检查流式响应 |
| Skill 列表加载 | < 200ms | 页面加载耗时 |
| 导入 ZIP | < 5s（单个 Skill） | 导入 100 个 Skill 包 |

### 6.3 兼容性验收

- [ ] Chrome 最新版
- [ ] Safari 最新版
- [ ] 移动端 (iPhone / Android)
- [ ] 浅色主题（已支持）
- [ ] 深色主题（需验证）

### 6.4 权限与安全

- [ ] Skill 只能由所有者编辑
- [ ] 对话历史不跨用户泄露
- [ ] 投喂链接 SSRF 防护（已有）
- [ ] 生成内容不包含用户隐私
- [ ] API 鉴权完整（Bearer token）

### 6.5 文档验收

- [ ] API 文档完整（Swagger）
- [ ] Skill 编写指南（3+ 示例）
- [ ] 对话意图列表与触发词
- [ ] 快捷键速查表
- [ ] 常见问题 FAQ
- [ ] 更新 AGENT.md 的 Skill 表

### 6.6 回归测试

**执行前置条件**：
```bash
cd apps/api
.venv/Scripts/python.exe -m pytest -q  # 全量回归（AGENT.md 要求）
cd ../web && npm run build  # 前端生产构建
```

**验证项**：
- [ ] 新增测试覆盖 Skill CRUD、对话引擎、快捷工具 (> 80% 覆盖率)
- [ ] 现有测试无新增失败
- [ ] 前端构建无警告
- [ ] 本地启动可用（`npm run dev` / 后端启动）

---

## 七、实用技巧补充

### 7.1 Skill 编写最佳实践

#### 7.1.1 System Prompt 结构化

```markdown
# 角色定义
你是一个[具体角色]，特点是[核心特征]。

# 写作目标
- 受众：[谁会读]
- 目的：[达到什么效果]
- 平台：[发布在哪里]

# 结构要求
1. 开头：[占比与作用]
2. 中间：[段数与内容类型]
3. 结尾：[call-to-action 或总结]

# 语气与禁忌
- 要这样说：[正面例子]
- 不要这样说：[负面例子]
- 禁用词：[列表]

# 论据方式
优先使用：[数据/案例/引用/实验]

# 长度指引
- 总字数：[范围]
- 段落数：[建议]
- 句子平均长：[风格指标]
```

#### 7.1.2 参考文章要求

- **数量**：3-10 篇
- **质量**：风格一致的高质量文章
- **来源多样性**：不要全是某一个作者的文章
- **时间跨度**：不要都是很久以前的

#### 7.1.3 Skill 命名规范

```
<domain>_<sub_type>_v<version>

例：
- writing_news_comment_v1
- writing_xiaohongshu_v2
- visual_data_chart_v1
- editor_polish_v1
```

### 7.2 对话流设计模板

#### 7.2.1 "新建创作"流

```
用户: "帮我写个关于 [X] 的文章"
  ↓
宠物: 自动识别 topic / url / 文本
     - 如果信息完整：直接推荐 Skill
     - 如果不完整：补充信息收集
       "你想发在哪个平台？(小红书/公众号/博客)"
       "风格倾向？(观点/分析/故事)"
  ↓ 用户回答或确认
宠物: "推荐使用 'writing_news_comment_v2'，擅长时事评论。"
     "需要我从这些链接蒸馏风格吗？或直接开始？"
  ↓
用户: "直接开始"
宠物: [流式显示初稿生成]
  ↓ 初稿完成
宠物: "初稿完成！1200 字，3 个段落。满意吗？"
     [三个选择按钮]
     - 投喂到成稿台
     - 继续调整
     - 重新生成
```

#### 7.2.2 "快捷改稿"流

```
用户: 在宠物对话中选中一段文字
     点"去 AI 味"
  ↓
宠物: "检测到 AI 味指标 0.72（偏高）。生成 3 个改写版本..."
  ↓
宠物: 显示 3 个版本，标注改进项
  ↓
用户: 选择一个版本 / 继续调整
  ↓
宠物: 更新显示 / 返回宠物对话中
```

### 7.3 前端交互设计细节

#### 7.3.1 PetChat 窗口拖动

- 头部区域可拖动整个窗口
- 窗口自动贴靠屏幕边缘（右下角默认）
- localStorage 记忆位置

#### 7.3.2 对话历史智能保存

- 自动保存到后端（`PetChatConversation` 表）
- 对话关联稿件 ID，方便溯源
- 对话历史可搜索、导出

#### 7.3.3 快捷命令建议

**基于上下文动态显示**：
- 用户当前激活的 Skill（快捷切换）
- 最常用的 3 个快捷工具（"去 AI 味"、"润色"、"改写"）
- 最近生成失败的操作（快速重试）

#### 7.3.4 Skill 推荐算法

```
推荐得分 = 
  用户历史使用频度 (40%) +
  内容相关度 (30%) +
  最后使用时间近度 (20%) +
  评分 (10%)

例：
- writing_news_comment_v2: 85 分（近 1 周用过 5 次，用户打分 4.8）
- writing_analysis_v1: 72 分（用过 2 次，1 个月没用）
- writing_xiaohongshu_v1: 60 分（新 Skill）
```

### 7.4 后端性能优化

#### 7.4.1 LLM 调用优化

- **并发**：独立的文生文、改稿、生图请求可并行（最多 3 个）
- **流式**：初稿生成用 SSE 流式返回，前端即时显示
- **超时**：生成 30s 超时 → 返回已生成部分 + "继续完成"按钮

#### 7.4.2 缓存策略

- **Skill 列表**：用户端缓存 5 分钟
- **预设包**：CDN 缓存 1 天
- **生成结果**：单个对话会话内缓存

#### 7.4.3 数据库查询优化

```python
# 列出用户的 Skill（常用操作，需要加索引）
# CREATE INDEX idx_skill_registry_owner_id ON skill_registry(owner_id);

# 对话历史分页
# 用 keyset 分页，不要 offset limit
```

### 7.5 测试用例补充

#### 7.5.1 单元测试

```python
# tests/test_pet_chat_engine.py
def test_intent_identification():
    """意图识别"""
    cases = [
        ("帮我写个新闻评论", "new_draft"),
        ("这段去 AI 味", "polish"),
        ("从这 5 篇文章学风格", "style_extract"),
        ("生成个数据卡", "svg_card"),
    ]
    for msg, expected_intent in cases:
        assert identify_intent(msg) == expected_intent

def test_skill_isolation():
    """Skill 权限隔离"""
    # 用户 A 创建 Skill
    # 用户 B 应该看不到（除非公开）
```

#### 7.5.2 E2E 测试

```python
# 完整流：热点 → 宠物创作 → 成稿台 → 发布
def test_e2e_creation_workflow():
    1. 获取热点列表
    2. 点热点 → 宠物推荐初稿
    3. 一键成稿
    4. 成稿台编辑
    5. 发布
```

---

## 八、风险与缓解

| 风险 | 影响 | 缓解方案 |
|------|------|--------|
| **LLM 调用频繁 → 成本 / 延迟** | P2 | 用本地规则优先（去 AI 味检测）；缓存热用例；用户可选"完整模式 / 快速模式" |
| **Skill 版本不兼容** | P2 | 记录 version_snapshot；导入时检查兼容性；提示升级 |
| **对话历史泄露** | P1 | 严格权限检查；对话加密存储；定期删除策略 |
| **SVG 卡 CSS 兼容性** | P2 | 预先测试主流浏览器；提供降级方案（PNG 导出） |
| **初稿质量不稳定** | P2 | 用户可快速重生成；提供多版本选择；用户反馈改进 Skill |

---

## 九、交付物清单

### 代码
- [ ] `modules/skills.py` 路由模块
- [ ] `modules/pet_chat.py` 对话路由
- [ ] `services/skill_registry.py` Skill 生命周期
- [ ] `services/pet_chat_engine.py` 对话引擎
- [ ] `services/paragraph_toolkit.py` 快捷工具
- [ ] 数据模型更新 (`SkillRegistry` 等表)
- [ ] 前端组件 (`PetChat.vue`, `SkillManager.vue` 等)
- [ ] 测试用例 (> 80% 新增代码覆盖)

### 文档
- [ ] API 文档 (Swagger 自动生成)
- [ ] Skill 编写指南
- [ ] 对话意图速查表
- [ ] 常见问题 FAQ
- [ ] 更新 AGENT.md

### 验收
- [ ] 功能验收清单 ✓
- [ ] 性能验收指标 ✓
- [ ] 全量回归测试通过
- [ ] E2E 业务流完整性验证

---

## 十、后续演进方向

### 10.1 短期（下一个迭代）

- [ ] **多 Skill 组合**：在单个创作中使用多个 Skill（初稿 + 改稿风格不同）
- [ ] **用户协作**：分享 Skill / 对话历史与团队
- [ ] **Skill 市场**：社区贡献 Skill，评分排序
- [ ] **自动化流**：宠物自动检测改稿机会，主动推荐

### 10.2 中期（3 个月+）

- [ ] **语音输入**：宠物支持语音对话 & 对话生成语音文案
- [ ] **实时协作**：多人同时在成稿台与宠物交互
- [ ] **Agent 任务**：宠物代理执行复杂任务（批量创作、定时发布）
- [ ] **知识库集成**：宠物可查询内部知识库 / 资料库

---

## 附录 A：API 契约速查

### 创作流 API

```
# 一键成稿
POST /api/v1/studio/quick-draft
{ topic_id / url / prompt, skill_id? } → { article_id, content, preview_url }

# Skill 管理
GET  /api/v1/skills/registry [?type=writing&search=...] → [skills]
POST /api/v1/skills { label, type, config } → { id, ... }
PATCH /api/v1/skills/{id} → { updated }
POST /api/v1/skills/{id}/export → JSON 文件
POST /api/v1/skills/import (file: zip) → [imported_skills]

# 宠物对话
POST /api/v1/agent/pet/chat { message, activeSkill? } → { response, intent, suggestions }

# 快捷工具
POST /api/v1/studio/paragraph/remove-ai-taste { text, skill_id } → [versions]
POST /api/v1/studio/paragraph/polish { text } → { polished_text }
POST /api/v1/studio/svg-card/generate { content, type?, style_hint? } → { svg, markdown }
```

---

**版本历史**：  
- v1.0 (2026-11-22): 初版发布，包含 6 个实施阶段，完整验收清单

