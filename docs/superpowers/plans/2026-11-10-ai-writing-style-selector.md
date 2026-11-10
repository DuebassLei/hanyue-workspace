# AI 助手写作风格选择 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让创作空间 AI 助手在文章初稿、整篇润色和整篇改写时选择当前用户可用的写作风格，并由后端安全地读取 Skill manifest 应用到成文提示词。

**Architecture:** 前端 StudioView 加载 `/skills` 并只在文章类任务显示风格选择器，请求通过 `skillKey` 传递选择。后端扩展流式成文请求，在当前用户可见范围内解析 Skill，使用 `promptTemplate`、`tone` 和 `styleRules` 组装任务提示词；解析失败回退 `article-drafting`，不影响原有生成和异步配图。

**Tech Stack:** Vue 3 Composition API、现有 `streamGeneration` SSE 客户端、FastAPI、Pydantic schemas、SQLAlchemy Skill Registry、pytest、Vite。

---

## 文件变更地图

- Modify: `D:\aicode\hanyue-workspace\apps\web\src\views\StudioView.vue` — 加载风格、选择器、按任务发送 `skillKey`。
- Modify: `D:\aicode\hanyue-workspace\apps\api\app\schemas.py` — 为文本生成请求增加可选 `skill_key` 字段，保持 camelCase API 映射。
- Modify: `D:\aicode\hanyue-workspace\apps\api\app\modules\models_module.py` — 从请求接收 `skillKey`，在成文任务中解析当前用户可见 Skill，并将风格上下文交给 prompt 构造。
- Modify: `D:\aicode\hanyue-workspace\apps\api\app\services\ai_article.py` — 复用/扩展现有 Skill 风格解析和 system prompt 组装，支持模板、语气和规则，缺失字段使用默认值。
- Modify or add: `D:\aicode\hanyue-workspace\apps\api\tests\...` — 覆盖 Skill 可见性、定制副本优先级、回退和 prompt 风格上下文。
- Existing reference: `D:\aicode\hanyue-workspace\apps\web\src\api\client.js` — 不改协议封装，只沿用 `streamGeneration`。

## Task 1: 后端请求契约与 Skill 风格解析测试

**Files:**
- Modify: `D:\aicode\hanyue-workspace\apps\api\app\schemas.py`
- Test: 现有 `D:\aicode\hanyue-workspace\apps\api\tests` 中与 models/agent/AI gateway 对应的测试文件；若没有对应文件，新增最小测试文件到该目录。

- [ ] **Step 1: 定位测试 fixture 和现有请求 schema 测试模式**

运行：

```powershell
Get-ChildItem -Path "D:\aicode\hanyue-workspace\apps\api\tests" -Recurse -File
```

确认测试使用的数据库、认证用户 fixture 和 schema 构造方式；沿用现有模式，不新建独立测试基础设施。

- [ ] **Step 2: 写请求契约测试**

构造 `schemas.TextGenerationIn` 时验证：

```python
request = schemas.TextGenerationIn(
    task_type="article",
    prompt="测试主题",
    skill_key="style-food",
)
assert request.skill_key == "style-food"
```

同时验证不传 `skill_key` 仍可构造，确保旧客户端兼容。

- [ ] **Step 3: 扩展 schema**

在现有 `TextGenerationIn` 中增加可选字段：

```python
skill_key: str | None = None
```

保持现有 CamelModel 配置，使 JSON 请求字段为 `skillKey`。

- [ ] **Step 4: 运行契约测试**

运行：

```powershell
Set-Location "D:\aicode\hanyue-workspace\apps\api"
.venv\Scripts\python.exe -m pytest -q tests/<对应测试文件>.py
```

预期：新增测试通过，现有相关测试不回归。

## Task 2: 后端 Skill 解析与成文 prompt 应用

**Files:**
- Modify: `D:\aicode\hanyue-workspace\apps\api\app\services\ai_article.py`
- Modify: `D:\aicode\hanyue-workspace\apps\api\app\modules\models_module.py`
- Test: `D:\aicode\hanyue-workspace\apps\api\tests\...`

- [ ] **Step 1: 写 Skill 解析与 prompt 组装失败测试**

覆盖以下行为：

```python
def test_selected_skill_manifest_adds_tone_and_rules():
    system_prompt = build_system_prompt(
        task_type="article",
        skill={
            "manifest": {
                "promptTemplate": "请完成公众号文章。",
                "tone": "克制、清晰",
                "styleRules": ["不用夸张标题", "段落短一些"],
            }
        },
    )
    assert "克制、清晰" in system_prompt
    assert "不用夸张标题" in system_prompt


def test_missing_skill_falls_back_to_article_drafting():
    resolved = resolve_skill_style(db, user, "not-visible")
    assert resolved.skill_key == "article-drafting"
```

测试中使用项目已有数据库和用户 fixture，不绕过用户隔离逻辑。

- [ ] **Step 2: 实现安全 Skill 解析**

在 `ai_article.py` 或当前已有解析函数内统一实现：

1. 优先按 `skill_key` 查询当前用户可见的 Skill；
2. 内置 Skill 可见；当前用户自定义 Skill 可见；其他用户自定义 Skill 不可见；
3. 用户定制副本优先于内置同 key 版本；
4. 找不到、无权限或 manifest 非字典时返回 `article-drafting`；
5. 默认 Skill 也使用同一套 manifest 解析路径。

不要接受前端传来的完整 prompt/template，只接受 `skillKey`。

- [ ] **Step 3: 实现 manifest 到 system prompt 的最小组装**

保留已有默认任务 prompt。对文章类任务追加或替换以下内容：

```text
写作语气：{tone}
写作规则：
- {style_rule}
```

若 `promptTemplate` 存在，使用它作为文章风格模板基础，并安全处理 `{tone}` 占位符；缺失模板时使用默认模板。`defaultTheme` 不参与文本生成失败判断，也不覆盖已有文章主题逻辑。

`article`、`polish`、`rewrite` 使用风格；`idea_expand`、`outline`、`summarize` 保持原有 prompt，不读取用户风格。

- [ ] **Step 4: 将 models_module 请求字段接入成文流程**

在 `text_generation` 读取 `request.skill_key`。只有文章类任务才调用 Skill 解析，并把解析后的风格上下文传给 `build_system_prompt`/`stream_text`；其他任务传原有参数，避免改变行为。

确保 SSE 的 `onStart`、`onDelta`、`onSaved`、`onEnd` 事件协议不变。

- [ ] **Step 5: 运行后端相关测试**

运行：

```powershell
Set-Location "D:\aicode\hanyue-workspace\apps\api"
.venv\Scripts\python.exe -m pytest -q tests
```

预期：新增 Skill 测试和全部既有测试通过。

## Task 3: StudioView 加载写作风格并接入 AI 请求

**Files:**
- Modify: `D:\aicode\hanyue-workspace\apps\web\src\views\StudioView.vue`

- [ ] **Step 1: 增加前端状态和计算属性**

添加以下状态：

```js
const writingStyles = ref([])
const writingStylesLoading = ref(false)
const writingStyleKey = ref('article-drafting')
const STYLE_TASKS = new Set(['article', 'polish', 'rewrite'])
const usesWritingStyle = computed(() => STYLE_TASKS.has(aiTask.value))
```

增加加载逻辑：调用 `api.get('/skills')`，筛选 `source === 'custom' || manifest?.tone` 的项；成功后优先保留当前 key，否则选择 `article-drafting`，再否则选择第一个可用项。加载失败只清空列表，不设置阻塞性 AI 错误。

- [ ] **Step 2: 在现有初始化流程加载 Skill**

将 Skill 加载与创作列表加载并行或在 `onMounted` 中独立启动，不能让 `/skills` 失败阻断 `loadList()`。保留现有稿件加载、自动保存和图片轮询生命周期。

- [ ] **Step 3: 增加 AI 面板选择器**

在 `StudioView.vue` 现有 AI 任务选择区域附近增加：

```vue
<label v-if="usesWritingStyle" class="ai-field">
  <span>写作风格</span>
  <select v-model="writingStyleKey" :disabled="aiRunning || writingStylesLoading">
    <option v-if="!writingStyles.length" value="article-drafting">使用默认风格</option>
    <option v-for="style in writingStyles" :key="style.skillKey" :value="style.skillKey">
      {{ style.displayName }}
    </option>
  </select>
</label>
```

沿用现有 AI 面板控件样式；仅文章类任务显示。切换任务不清除选择，切回文章类任务继续使用上一次仍可用的风格。

- [ ] **Step 4: 组装 streamGeneration 请求**

在 `runAi()` 中构造请求对象：

```js
const request = {
  taskType: aiTask.value,
  prompt: aiPrompt.value,
  contentItemId: generationItemId,
  constraints: {
    targetLength: aiTargetLength.value,
    platform: 'wechat_official',
  },
}
if (usesWritingStyle.value) {
  request.skillKey = writingStyleKey.value || 'article-drafting'
}
```

把 `request` 传给 `streamGeneration`。自动配图判断、文章保存刷新、`contentItemId` 隔离和失败重试逻辑保持不变。

- [ ] **Step 5: 运行前端构建**

运行：

```powershell
Set-Location "D:\aicode\hanyue-workspace\apps\web"
npm run build
```

预期：Vite 生产构建成功，无 Vue 模板或脚本错误。

## Task 4: 前后端兼容验证与回归

**Files:**
- Modify only if tests expose an actual contract mismatch: files listed above.

- [ ] **Step 1: 检查 API 字段映射**

确认前端发送 `skillKey` 能被 `TextGenerationIn.skill_key` 接收，且旧请求不传该字段仍正常。确认后端输出协议没有增加前端无法解析的必需字段。

- [ ] **Step 2: 验证自动配图兼容**

确认 `runAi()` 在 `article + auto images` 时同时发送 `skillKey` 和原有约束；文章保存成功后才调用 `startAutoImageGeneration`。`polish/rewrite` 即使选择风格也不触发自动配图。

- [ ] **Step 3: 执行完整验证**

运行：

```powershell
Set-Location "D:\aicode\hanyue-workspace\apps\api"
.venv\Scripts\python.exe -m pytest -q
Set-Location "D:\aicode\hanyue-workspace\apps\web"
npm run build
```

预期：后端全量测试通过，前端生产构建通过。

- [ ] **Step 4: 完成前端静态检查**

检查 `StudioView.vue`：

1. 风格请求失败不会覆盖已有 `error` 或阻断稿件加载；
2. 删除或定制风格后，当前 key 能回退；
3. 文章类任务始终能使用默认 `article-drafting`；
4. 非文章任务不发送 `skillKey`；
5. 自动配图相关代码没有被移动到风格选择逻辑中。

## Self-review checklist

- [ ] 设计文档的任务范围、默认风格、用户隔离、回退策略、manifest 字段和自动配图兼容性均有对应实现任务。
- [ ] 计划没有引入新的 Skill 管理页面或无关重构。
- [ ] 每个代码变更点都有明确文件、函数边界和验证命令。
- [ ] 旧客户端不传 `skillKey` 时仍走默认成文行为。
