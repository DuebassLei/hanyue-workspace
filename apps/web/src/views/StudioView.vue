<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCheckbox,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSelect,
  NSpace,
  NTooltip,
} from 'naive-ui'
import { api, streamGeneration, fetchBlobUrl, apiUrl, downloadAuthenticated } from '../api/client'
import StatusTag from '../components/StatusTag.vue'
import MarkdownEditor from '../components/MarkdownEditor.vue'
import ModelSelector from '../components/ModelSelector.vue'
import { useModelSelectionStore } from '../stores/modelSelection'
import { articleBodyFromOutput, parseArticleOutput, normalizeArticleMarkdown } from '../utils/aiArticle'
import { markdownToHtml } from '../utils/markdownSerializer'
import PublishDrawer from '../features/publish/PublishDrawer.vue'
import DraftLibraryModal from '../features/studio/DraftLibraryModal.vue'
import { providerOptions, resolveSelectedModelId } from '../composables/imageProviders.js'
import { confirm, toast } from '../ui/feedback'
import { usePermission } from '../composables/usePermission'

const route = useRoute()
const router = useRouter()
const modelSelection = useModelSelectionStore()

// 创作空间本身只要 studio.use，但页内的「转为卡片」「发布」各自需要别的权限点。
// 置灰而不是隐藏：让用户知道有这个动作，也知道该找管理员要什么权限。
const { disabledReason } = usePermission()

const items = ref([])
const detail = ref(null)
const loading = ref(true)
const error = ref('')
const saving = ref(false)

const draftTitle = ref('')
const draftBody = ref('')
const hasEscapedArticleBreaks = computed(() => normalizeArticleMarkdown(draftBody.value) !== draftBody.value)
function repairArticleBreaks() {
  draftBody.value = normalizeArticleMarkdown(draftBody.value)
}
const dirty = computed(() => {
  const latest = detail.value?.versions?.[0]
  if (!latest) return Boolean(draftTitle.value || draftBody.value)
  return latest.title !== draftTitle.value || latest.body !== draftBody.value
})

/** 历史稿件里可能有被重复拼接成 /api/v1/api/v1/ 的素材直链，导致正文配图裂图 */
function normalizeAssetUrls(text) {
  return String(text || '').replace(/\/api\/v1\/api\/v1\//g, '/api/v1/')
}

// 自动保存状态：idle | pending | saving | saved | error
const autoSaveStatus = ref('idle')
let autoSaveTimer = null
// 加载中标记：程序赋值 draftTitle/draftBody 时不触发自动保存
let loadingDraft = false
// 串行化保存：保证请求按顺序落库，避免旧内容覆盖新内容
let saveQueue = Promise.resolve()

function flushAutoSave() {
  if (autoSaveTimer) {
    clearTimeout(autoSaveTimer)
    autoSaveTimer = null
  }
  if (autoSaveStatus.value !== 'pending') return Promise.resolve()
  return triggerAutoSave()
}

async function triggerAutoSave() {
  if (!detail.value) return
  const itemId = detail.value.item.id
  const title = draftTitle.value
  const body = draftBody.value
  autoSaveStatus.value = 'saving'
  // 串到上一条保存之后，保证服务端写入顺序与用户输入顺序一致
  const run = () => api.patch(`/content/${itemId}/draft`, { title, body })
  saveQueue = saveQueue.then(run, run)
  try {
    await saveQueue
    // 仅当仍是当前稿件且没有更新的输入时才标记为已保存
    if (detail.value?.item.id === itemId && autoSaveStatus.value === 'saving') {
      autoSaveStatus.value = 'saved'
    }
  } catch {
    if (detail.value?.item.id === itemId) autoSaveStatus.value = 'error'
  }
}

function scheduleAutoSave() {
  autoSaveStatus.value = 'pending'
  clearTimeout(autoSaveTimer)
  autoSaveTimer = setTimeout(triggerAutoSave, 1500)
}

// 监听正文/标题变化，触发自动保存（程序加载赋值时不触发）
watch([draftTitle, draftBody], () => {
  if (loadingDraft || !detail.value) return
  scheduleAutoSave()
})

// AI 面板
const aiOpen = ref(true)
const aiSettingsOpen = ref(false)
const aiTask = ref('article')
const aiPrompt = ref('')
const aiOutput = ref('')
const aiRunning = ref(false)
const aiError = ref('')
const aiPromptError = ref('')
const aiStopped = ref(false)
const aiResultRef = ref(null)
const workspaceView = ref('draft')
const workspaceRef = ref(null)
const aiSourceItemId = ref(null)
const aiApplied = ref(false)
const aiApplying = ref(false)
// 实际使用的模型（降级后由 generation.model 更正），不谎报首选
const aiModelInfo = ref('')
const aiTargetLength = ref(600)  // 字数目标
const aiInsertMode = ref('append') // 'append' | 'replace'
const aiAutoImages = ref(false)
const aiUseLayoutComponents = ref(false)
const aiGenerateCover = ref(true)
const aiCoverAspectRatio = ref('3:4')
const aiInlineAspectRatio = ref('1:1')
const imagePreferencesLoaded = ref(false)
const imagePreferencesLoading = ref(true)
const imagePreferencesSaveStatus = ref('idle') // idle | saving | saved | error
const imagePreferencesError = ref('')
let imagePreferencesTimer = null
let imagePreferencesRequestId = 0
let hydratingImagePreferences = false
let skipImagePreferencesSave = false
const AI_IMAGE_ASPECT_OPTIONS = [
  { value: '3:4', label: '3:4 竖版' },
  { value: '1:1', label: '1:1 方形' },
  { value: '4:3', label: '4:3 横版' },
  { value: '16:9', label: '16:9 宽屏' },
]
const aiImagePreferences = computed(() => ({
  autoImages: aiAutoImages.value,
  generateCover: aiGenerateCover.value,
  coverAspectRatio: aiCoverAspectRatio.value,
  inlineAspectRatio: aiInlineAspectRatio.value,
}))

function scheduleImagePreferencesSave() {
  if (hydratingImagePreferences || skipImagePreferencesSave || !imagePreferencesLoaded.value) return
  imagePreferencesSaveStatus.value = 'saving'
  imagePreferencesError.value = ''
  clearTimeout(imagePreferencesTimer)
  imagePreferencesTimer = setTimeout(saveImagePreferences, 400)
}

async function saveImagePreferences() {
  imagePreferencesTimer = null
  const requestId = ++imagePreferencesRequestId
  imagePreferencesSaveStatus.value = 'saving'
  try {
    await api.patch('/preferences', { studioImagePreferences: aiImagePreferences.value })
    if (requestId === imagePreferencesRequestId) imagePreferencesSaveStatus.value = 'saved'
  } catch (err) {
    if (requestId === imagePreferencesRequestId) {
      imagePreferencesSaveStatus.value = 'error'
      imagePreferencesError.value = `配图设置保存失败：${err.message}`
    }
  }
}

async function loadImagePreferences() {
  imagePreferencesLoading.value = true
  imagePreferencesError.value = ''
  try {
    const preferences = await api.get('/preferences')
    const settings = preferences.studioImagePreferences || {}
    hydratingImagePreferences = true
    aiAutoImages.value = settings.autoImages === true
    aiGenerateCover.value = settings.generateCover !== false
    aiCoverAspectRatio.value = settings.coverAspectRatio || '3:4'
    aiInlineAspectRatio.value = settings.inlineAspectRatio || '1:1'
    skipImagePreferencesSave = true
    imagePreferencesLoaded.value = true
    imagePreferencesSaveStatus.value = preferences.source === 'server' ? 'saved' : 'idle'
    queueMicrotask(() => { skipImagePreferencesSave = false })
  } catch (err) {
    imagePreferencesError.value = `配图设置加载失败，将使用默认设置：${err.message}`
    imagePreferencesLoaded.value = true
    imagePreferencesSaveStatus.value = 'error'
  } finally {
    hydratingImagePreferences = false
    imagePreferencesLoading.value = false
  }
}

watch(
  [aiAutoImages, aiGenerateCover, aiCoverAspectRatio, aiInlineAspectRatio],
  scheduleImagePreferencesSave,
)
watch(aiAutoImages, (enabled) => {
  if (enabled) loadImageProviderStatus()
})

const baseStyles = ref([])
const baseStylesLoaded = ref(false)
const coverStyles = ref([])
const inlineStyles = ref([])
const styleLocking = ref(false)
const sceneDrafts = ref([])
const sceneSubmitting = ref(false)

const baseStylePrompt = computed(() => (detail.value?.item.baseStylePrompt || '').trim())
const baseStyleLocked = computed(() => Boolean(baseStylePrompt.value))
const baseStyleHint = computed(() => (
  baseStylesLoaded.value && !baseStyles.value.length
    ? '还没有可用的基础风格，请管理员到提示词库添加'
    : '先选一个基础风格'
))
const baseStyleSelectValue = computed(() => {
  const item = detail.value?.item
  if (!item) return null
  if (item.baseStyleDeleted) return '__deleted__'
  return item.baseStyleId || null
})
const baseStyleOptions = computed(() => {
  const item = detail.value?.item
  const options = baseStyles.value.map((style) => ({
    value: style.id,
    label: style.summary ? `${style.name} · ${style.summary}` : style.name,
  }))
  if (item?.baseStyleDeleted) {
    options.unshift({
      value: '__deleted__',
      label: `${item.baseStyleName || '基础风格'}（已删除）`,
      disabled: true,
    })
  }
  return options
})

function composeImagePrompt(scene) {
  const description = String(scene || '').trim()
  const style = baseStylePrompt.value
  if (!style) return description
  return description ? `${style}, ${description}` : style
}

// 负向词走独立字段；供应商不支持时由服务端并入正向文本并在结果中标注。
const activeTemplateNegative = ref('')
function composeNegativePrompt() {
  return activeTemplateNegative.value.trim()
}

// 风格统一来自提示词库；这里做一层字段归一化，保持下面组件继续使用 name/prompt。
async function loadStyles(purpose) {
  const promptType = { base: 'base', cover: 'cover', inline: 'illustration' }[purpose] || purpose
  try {
    const rows = await api.get(`/prompts?promptType=${promptType}&enabled=true`)
    return rows.map((row) => ({
      id: row.id,
      name: row.title,
      prompt: row.content,
      negativeContent: row.negativeContent || '',
      summary: row.summary || '',
      sortOrder: row.sortOrder || 0,
      enabled: row.enabled !== false,
    }))
  } catch (err) {
    imgError.value = err.message
    return []
  }
}

async function loadBaseStyles() {
  baseStyles.value = await loadStyles('base')
  baseStylesLoaded.value = true
}

async function loadCoverStyles() {
  if (coverStyles.value.length) return
  coverStyles.value = await loadStyles('cover')
}

async function loadInlineStyles() {
  if (inlineStyles.value.length) return
  inlineStyles.value = await loadStyles('inline')
}

function captureSceneDrafts(rawOutput) {
  const prompts = parseArticleOutput(rawOutput)
  if (!prompts) {
    sceneDrafts.value = []
    return
  }
  const rows = []
  if (prompts.coverPrompt) {
    rows.push({ key: 'cover', label: '封面', purpose: 'cover', scene: prompts.coverPrompt })
  }
  prompts.inlinePrompts.forEach((scene, index) => {
    rows.push({
      key: `inline-${index + 1}`,
      label: `插图 ${index + 1}`,
      purpose: 'inline',
      scene,
      inlineIndex: index + 1,
      inlineTotal: prompts.inlinePrompts.length,
      articleMarkdown: prompts.markdown,
    })
  })
  sceneDrafts.value = rows
}

function applyCoverTemplate(id) {
  const style = coverStyles.value.find((item) => item.id === id)
  const row = sceneDrafts.value.find((item) => item.purpose === 'cover')
  if (style && row) row.scene = style.prompt
  if (style) activeTemplateNegative.value = style.negativeContent
}

function applyInlineTemplate(id) {
  const style = inlineStyles.value.find((item) => item.id === id)
  if (style) imgPrompt.value = style.prompt
  if (style) activeTemplateNegative.value = style.negativeContent
}

// AI 成文配图提示词沉淀：默认不自动入库，由用户手动点击触发（source=ai）。
const promptDepositBusy = ref(false)
const promptManageDenied = computed(() => disabledReason('image_styles.manage', '管理提示词库'))
async function depositAiPrompts() {
  if (promptManageDenied.value || !sceneDrafts.value.length) return
  const cover = sceneDrafts.value.find((row) => row.purpose === 'cover')?.scene || ''
  const inline = sceneDrafts.value
    .filter((row) => row.purpose === 'inline')
    .map((row) => row.scene)
    .filter(Boolean)
  if (!cover && !inline.length) {
    toast('当前没有可存入的配图提示词', 'error')
    return
  }
  promptDepositBusy.value = true
  try {
    const rows = await api.post('/prompts/from-draft', {
      title: detail.value?.item?.title || 'AI 成文',
      coverPrompt: cover,
      inlinePrompts: inline,
    })
    toast(rows.length ? `已存入 ${rows.length} 条提示词` : '这些提示词已经在库里了')
  } catch (err) {
    toast(err.message, 'error')
  } finally {
    promptDepositBusy.value = false
  }
}

async function changeBaseStyle(styleId) {
  const item = detail.value?.item
  if (!item || styleLocking.value) return
  if (styleId === '__deleted__' || styleId === item.baseStyleId) return
  const hasResults = imgResults.value.length > 0 || imgJobs.value.some((job) => job.status === 'succeeded')
  if (hasResults) {
    const ok = await confirm('已生成的图片不会变化', '更换基础风格')
    if (!ok) return
  }
  styleLocking.value = true
  try {
    const updated = await api.patch(`/content/${item.id}/image-style`, { styleId: styleId || null })
    if (detail.value?.item.id === item.id) detail.value = { ...detail.value, item: updated }
  } catch (err) {
    imgError.value = err.message
    toast(err.message, 'error')
  } finally {
    styleLocking.value = false
  }
}

async function submitSceneDrafts() {
  const item = detail.value?.item
  if (!item) return
  if (!baseStyleLocked.value) {
    toast(baseStyleHint.value)
    return
  }
  const rows = sceneDrafts.value.filter((row) => row.scene.trim())
  if (!rows.length) {
    toast('请至少保留一条画面描述')
    return
  }
  sceneSubmitting.value = true
  imgError.value = ''
  const contentItemId = item.id
  try {
    const results = await Promise.allSettled(rows.map(async (row) => {
      const prompt = composeImagePrompt(row.scene)
      const response = await api.post('/ai/image-generations', {
        prompt,
        negativePrompt: composeNegativePrompt() || null,
        purpose: row.purpose,
        aspectRatio: row.purpose === 'cover' ? aiCoverAspectRatio.value : aiInlineAspectRatio.value,
        count: 1,
        modelId: imgModelId.value || null,
      })
      return {
        id: response.jobId,
        status: 'queued',
        prompt,
        scenePrompt: row.scene.trim(),
        aspectRatio: row.purpose === 'cover' ? aiCoverAspectRatio.value : aiInlineAspectRatio.value,
        label: row.label,
        purpose: row.purpose,
        inlineIndex: row.inlineIndex,
        inlineTotal: row.inlineTotal,
        articleMarkdown: row.articleMarkdown,
        contentItemId,
      }
    }))
    for (const result of results) {
      if (result.status === 'fulfilled') imgJobs.value.unshift(result.value)
      else imgError.value = result.reason?.message || '配图任务创建失败'
    }
    const ok = results.filter((result) => result.status === 'fulfilled').length
    if (ok) {
      toast(`${ok} 个配图任务已开始，完成后自动插入正文`)
      startImgPoll()
    }
  } finally {
    sceneSubmitting.value = false
  }
}

const writingStyles = ref([])
const writingStylesLoading = ref(false)
const writingStyleKey = ref('article-drafting')
const STYLE_TASKS = new Set(['article', 'polish', 'rewrite'])
const usesWritingStyle = computed(() => STYLE_TASKS.has(aiTask.value))
const writingStyleOptions = computed(() => {
  if (!writingStyles.value.length) return [{ value: 'article-drafting', label: '使用默认风格' }]
  return writingStyles.value.map((style) => ({ value: style.skillKey, label: style.displayName }))
})

// 写作风格切换后刷新右侧流程（监听必须放在 writingStyleKey 声明之后，否则 setup 期 TDZ 报错）
watch(writingStyleKey, () => {
  if (rightTab.value === 'flow' && detail.value) loadWritingFlow()
})
const editorRef = ref(null)  // MarkdownEditor 暴露的 editor 实例
let abortStream = null

const TARGET_LENGTH_OPTIONS = [
  { value: 300, label: '300字' },
  { value: 600, label: '600字' },
  { value: 1000, label: '1000字' },
  { value: 2000, label: '2000字' },
]

// 发布抽屉
const publishDrawerOpen = ref(false)

// 平台版本
const showVariant = ref(false)
const variantsCollapsed = ref(false)
const variantForm = ref({ platform: 'wechat_official', title: '', body: '' })

// 删除稿件
const deletingItem = ref(null)
const deleting = ref(false)

// 右侧面板 Tab：ai | image | originality | inspiration | flow
const rightTab = ref('ai')
const flow = ref(null)
const flowLoading = ref(false)
const flowError = ref('')
const draftsCollapsed = ref(false)
// 稿件多了以后靠搜索定位；有分组兜底后搜索框常驻
const draftKeyword = ref('')
const filteredItems = computed(() => {
  const q = draftKeyword.value.trim().toLowerCase()
  if (!q) return items.value
  return items.value.filter((item) => (item.title || '').toLowerCase().includes(q))
})

// 后端 SQLite 返回的是不带时区的 UTC 时间，直接 parse 会被当成本地时间差 8 小时
function itemTime(value) {
  return Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`)
}

// 列表与分组共用同一口径：按自然日切"今天 / 近 7 天 / 更早"。
// 当前量级（≤200 篇）DOM 全量渲染足够；将来涨到几百篇再考虑虚拟滚动。
function itemBucket(value) {
  const time = itemTime(value)
  if (!Number.isFinite(time)) return 'older'
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const d = new Date(time)
  const dayStart = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const days = Math.round((today - dayStart) / 86400000)
  if (days <= 0) return 'today'
  if (days < 7) return 'week'
  return 'older'
}

const DRAFT_GROUPS = [
  { key: 'today', label: '今天' },
  { key: 'week', label: '近 7 天' },
  { key: 'older', label: '更早' },
]

// 搜索结果拍平不分组；无搜索时按日期分组（空组隐藏）
const groupedItems = computed(() => {
  if (draftKeyword.value.trim()) return null
  const buckets = { today: [], week: [], older: [] }
  for (const item of items.value) buckets[itemBucket(item.updatedAt)].push(item)
  return DRAFT_GROUPS.map((group) => ({ ...group, list: buckets[group.key] })).filter(
    (group) => group.list.length,
  )
})

// "更早"最容易被翻长，折叠状态跨会话记住；其余组始终展开
const OLDER_COLLAPSE_KEY = 'studio.drafts.groupCollapsed.v1'
const olderCollapsed = ref(false)
try {
  olderCollapsed.value = localStorage.getItem(OLDER_COLLAPSE_KEY) === '1'
} catch {
  // 隐私模式下 localStorage 可能抛错，忽略即可
}
function toggleOlderGroup() {
  olderCollapsed.value = !olderCollapsed.value
  try {
    localStorage.setItem(OLDER_COLLAPSE_KEY, olderCollapsed.value ? '1' : '0')
  } catch {
    // 存不上不影响本次会话内折叠
  }
}

// 列表里只留相对日期（今天/昨天/月-日），完整时间放 tooltip
function relativeDay(value) {
  const time = itemTime(value)
  if (!Number.isFinite(time)) return String(value).slice(0, 10)
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const d = new Date(time)
  const dayStart = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const days = Math.round((today - dayStart) / 86400000)
  if (days <= 0) return '今天'
  if (days === 1) return '昨天'
  if (days < 7) return `${days} 天前`
  return String(value).slice(5, 10)
}
const hotTopics = ref([])
const hotStatus = ref({ configured: false, source: '', note: '' })
const hotLoading = ref(false)
const hotError = ref('')
const hotActionId = ref('')

const TASKS = [
  { value: 'idea_expand', label: '扩展角度' },
  { value: 'outline', label: '生成大纲' },
  { value: 'article', label: '写初稿' },
  { value: 'polish', label: '润色（整篇）' },
  { value: 'rewrite', label: '改写（整篇）' },
  { value: 'summarize', label: '摘要' },
]

const PLATFORMS = [
  { code: 'wechat_official', label: '公众号' },
]

// 流程检查始终跑编辑器当前内容（含未保存草稿），POST 同名接口；
// seq 防竞态：连续输入触发多次时，只认最后一次请求的结果。
let flowSeq = 0
let flowPending = 0
async function loadWritingFlow({ silent = false } = {}) {
  const itemId = detail.value?.item.id
  if (!itemId) {
    flow.value = null
    return
  }
  const seq = ++flowSeq
  flowPending += 1
  if (!silent) flowLoading.value = true
  flowError.value = ''
  try {
    const report = await api.post(`/content/${itemId}/writing-flow`, {
      skillKey: writingStyleKey.value || 'article-drafting',
      title: draftTitle.value,
      body: draftBody.value,
    })
    if (seq === flowSeq && detail.value?.item.id === itemId) flow.value = report
  } catch (err) {
    if (seq === flowSeq && detail.value?.item.id === itemId) {
      flow.value = null
      flowError.value = err.message
    }
  } finally {
    flowPending -= 1
    if (seq === flowSeq && flowPending === 0 && detail.value?.item.id === itemId) {
      flowLoading.value = false
    }
  }
}

// 编辑器输入停顿后静默重检：检查是本地正则，成本极低，不值得让用户手动点
let flowDebounce = null
watch([draftTitle, draftBody], () => {
  if (rightTab.value !== 'flow' || !detail.value) return
  clearTimeout(flowDebounce)
  flowDebounce = setTimeout(() => {
    loadWritingFlow({ silent: true })
  }, 600)
})

// 各检查步骤对应的规则配置位置：跳转创作规则页 deep-link
// tab=rules 是 Skill 规则表（focus 为 ruleType），tab=prompts 是词表类提示词规则
const FLOW_RULE_FOCUS = {
  review_article: { tab: 'rules', focus: 'review_rule' },
  check_ai_tone: { tab: 'prompts', focus: 'tone_lexicon' },
  extract_facts: { tab: 'rules', focus: 'fact_policy' },
  plan_images: { tab: 'rules', focus: 'image_policy' },
}

function goFlowRules(tool) {
  const target = FLOW_RULE_FOCUS[tool]
  if (!target || !flow.value) return
  router.push({
    name: 'writing-rules',
    query: {
      skill: flow.value.skillKey,
      tab: target.tab,
      focus: target.focus,
      from: 'studio',
      item: detail.value?.item.id || '',
    },
  })
}

function flowRuleLabel(rule) {
  if (rule.kind === 'builtin') return '图片占位连续且不重复'
  const where = rule.field === 'title' ? '标题' : rule.field === 'summary' ? '摘要' : '正文'
  if (rule.kind === 'contains') return `${where}必含「${rule.value}」`
  if (rule.kind === 'not_contains') return `${where}禁含「${rule.value}」`
  if (rule.kind === 'min_chars') return `${where}不少于 ${rule.value} 字`
  if (rule.kind === 'max_chars') return `${where}不超过 ${rule.value} 字`
  return `${rule.kind} 规则`
}

function flowLexiconChips(lexicon) {
  const defs = [
    ['sentenceOpeners', '段首套话'],
    ['buzzwords', 'AI 黑话'],
    ['patterns', '套路句式'],
    ['firstThen', '首先其次'],
  ]
  return defs
    .filter(([key]) => (lexicon?.[key]?.count || 0) > 0)
    .map(([key, label]) => ({
      key,
      text: `${label} ${lexicon[key].count} 条${lexicon[key].custom ? '·规则表' : '·默认'}`,
    }))
}

function flowStepSummary(step) {
  const result = step.result
  if (step.kind !== 'check') return '生成时执行，不在这里重跑'
  if (!flow.value?.hasBody) return '正文为空，写入后再检查'
  if (!result) return ''
  if (step.tool === 'review_article') {
    const count = result.violations?.length || 0
    return count ? `${count} 处未通过` : '审稿通过'
  }
  if (step.tool === 'check_ai_tone') {
    const count = result.hits?.length || 0
    return count ? `${count} 处 AI 腔` : '没有明显 AI 腔'
  }
  if (step.tool === 'extract_facts') {
    return `${result.numbers?.length || 0} 个数字 · ${result.quotes?.length || 0} 处引语`
  }
  if (step.tool === 'plan_images') {
    if (result.issue === 'placeholder_mismatch') return '配图占位和提示词对不上'
    const inline = result.inline?.length || 0
    return `${result.cover ? '有封面' : '无封面'} · ${inline} 张正文图`
  }
  return ''
}

function flowStepTone(step) {
  const result = step.result
  if (step.kind !== 'check' || !flow.value?.hasBody || !result) return ''
  if (step.tool === 'review_article') return result.ok ? 'is-ok' : 'is-warn'
  if (step.tool === 'check_ai_tone') return result.hits?.length ? 'is-warn' : 'is-ok'
  if (step.tool === 'plan_images') return result.issue ? 'is-warn' : 'is-ok'
  return ''
}

async function loadWritingStyles() {
  writingStylesLoading.value = true
  try {
    const skills = await api.get('/skills')
    writingStyles.value = (skills || []).filter(
      (skill) => skill.source === 'custom' || skill.manifest?.tone,
    )
    const current = writingStyles.value.find(
      (skill) => skill.skillKey === writingStyleKey.value,
    )
    if (!current) {
      writingStyleKey.value =
        writingStyles.value.find((skill) => skill.skillKey === 'article-drafting')?.skillKey
        || writingStyles.value[0]?.skillKey
        || 'article-drafting'
    }
  } catch {
    writingStyles.value = []
  } finally {
    writingStylesLoading.value = false
  }
}

// 稿件库弹窗：全量检索 + 归档管理，选中后回填编辑器
const libraryOpen = ref(false)

// 归档即从侧栏移除；恢复后重新拉列表才能按更新时间落回正确分组
function onLibraryArchived({ id, archived }) {
  if (archived) {
    items.value = items.value.filter((row) => row.id !== id)
    if (detail.value?.item.id === id) {
      detail.value = null
      draftTitle.value = ''
      draftBody.value = ''
      router.replace({ name: 'studio' })
    }
  } else {
    loadList()
  }
}

async function loadList() {
  loading.value = true
  try {
    // fields=list：不拉正文、后端聚合版本数，几百篇时首屏不再拖泥带水
    items.value = await api.get('/content?fields=list&limit=200')
    // 从创作规则页「返回创作空间」带回 ?tab=flow：先置 tab 再开稿，
    // detail 的 watch 会自动触发一次流程检查
    if (route.query.tab === 'flow') rightTab.value = 'flow'
    const targetId = route.query.id || items.value[0]?.id
    if (targetId) await openItem(targetId)
    else detail.value = null
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function consumeHotDraft(id) {
  const incoming = history.state?.hotDraft
  if (!incoming || incoming.itemId !== id || !incoming.title) return
  // 一次性消费，避免刷新或浏览器前进时覆盖用户已经修改的提示词。
  history.replaceState({ ...history.state, hotDraft: null }, '')
  aiPrompt.value = `围绕「${incoming.title}」写一篇初稿，语气平实`
  if (incoming.track) aiPrompt.value += `\n赛道：${incoming.track}`
  if (incoming.platforms?.length) aiPrompt.value += `\n热点来源：${incoming.platforms.join('、')}`
  aiTask.value = 'article'
  aiOpen.value = true
  rightTab.value = 'ai'
}

async function openItem(id) {
  if (detail.value?.item.id === id) {
    consumeHotDraft(id)
    return
  }
  // 切换前先把上一篇待保存的草稿落库，避免 debounce 期间的内容丢失
  await flushAutoSave()
  let data
  try {
    data = await api.get(`/content/${id}`)
  } catch (err) {
    error.value = err.message
    return
  }
  // 自动保存的草稿优先于最新版本快照，否则刷新后未提交内容会丢失
  const latest = data.versions[0]
  const draft = data.item
  const hasDraft = draft.draftBody != null || draft.draftTitle != null
  loadingDraft = true
  detail.value = data
  draftTitle.value = hasDraft
    ? (draft.draftTitle ?? latest?.title ?? data.item.title)
    : (latest?.title || data.item.title)
  draftBody.value = normalizeAssetUrls(
    hasDraft
      ? (draft.draftBody ?? latest?.body ?? '')
      : (latest?.body || ''),
  )
  await nextTick()
  loadingDraft = false
  autoSaveStatus.value = hasDraft && dirty.value ? 'saved' : 'idle'
  consumeHotDraft(id)
  // 稿件库可能打开侧栏前 200 篇之外的稿子：补进行列，保证激活态有落点；
  // 归档稿按约定不进侧栏，即使正打开着也不补
  if (!data.item.archived && !items.value.some((row) => row.id === id)) {
    items.value.push({
      id: data.item.id,
      title: data.item.title,
      status: data.item.status,
      type: data.item.type,
      updatedAt: data.item.updatedAt,
      archived: data.item.archived,
      versionNo: latest?.versionNo || null,
      variantCount: data.variants?.length || 0,
    })
  }
  if (route.query.id !== id) router.replace({ name: 'studio', query: { id } })
  sceneDrafts.value = []
  loadBaseStyles()
}

async function createItem() {
  try {
    const created = await api.post('/content', { title: '未命名稿件' })
    items.value.unshift(created)
    await openItem(created.id)
  } catch (err) {
    error.value = err.message
  }
}

function askDelete(item) {
  deletingItem.value = item
}

async function confirmDelete() {
  const item = deletingItem.value
  if (!item) return
  deleting.value = true
  try {
    await api.del(`/content/${item.id}`)
    items.value = items.value.filter((row) => row.id !== item.id)
    if (detail.value?.item.id === item.id) {
      detail.value = null
      draftTitle.value = ''
      draftBody.value = ''
      const next = items.value[0]
      if (next) {
        await openItem(next.id)
      } else {
        router.replace({ name: 'studio' })
      }
    }
    deletingItem.value = null
  } catch (err) {
    error.value = err.message
  } finally {
    deleting.value = false
  }
}

async function saveVersion() {
  if (!detail.value) return
  const itemId = detail.value.item.id
  saving.value = true
  // 先把待保存草稿落库，保证草稿与版本内容一致
  if (autoSaveTimer) {
    clearTimeout(autoSaveTimer)
    autoSaveTimer = null
  }
  try {
    await api.patch(`/content/${itemId}/draft`, {
      title: draftTitle.value,
      body: draftBody.value,
    })
    const version = await api.post(`/content/${itemId}/versions`, {
      title: draftTitle.value,
      body: draftBody.value,
      sourceType: 'manual',
    })
    if (detail.value?.item.id !== itemId) return
    detail.value.versions.unshift(version)
    detail.value.item.title = draftTitle.value
    detail.value.item.currentVersionId = version.id
    detail.value.item.draftTitle = draftTitle.value
    detail.value.item.draftBody = draftBody.value
    autoSaveStatus.value = 'idle'
    toast(`已保存为 v${version.versionNo}`)
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

function restore(version) {
  draftTitle.value = version.title
  draftBody.value = normalizeAssetUrls(version.body)
  toast(`已载入 v${version.versionNo}，保存后才会覆盖为最新版本`)
}

async function startAutoImageGeneration(rawOutput, contentItemId) {
  if (!contentItemId || detail.value?.item.id !== contentItemId) return
  const prompts = parseArticleOutput(rawOutput)
  if (!prompts || (!prompts.coverPrompt && !prompts.inlinePrompts.length)) {
    toast('文章已生成，但未提取到配图提示词')
    return
  }
  if (!baseStylePrompt.value) {
    toast(baseStyleHint.value)
    return
  }
  const settings = {
    coverAspectRatio: aiCoverAspectRatio.value,
    inlineAspectRatio: aiInlineAspectRatio.value,
    generateCover: aiGenerateCover.value,
  }
  const requests = [
    settings.generateCover && prompts.coverPrompt
      ? { scene: prompts.coverPrompt, purpose: 'cover', label: '封面图', aspectRatio: settings.coverAspectRatio }
      : null,
    ...prompts.inlinePrompts.map((prompt, index) => ({
      scene: prompt,
      purpose: 'inline',
      label: `正文插图 ${index + 1}`,
      aspectRatio: settings.inlineAspectRatio,
      inlineIndex: index + 1,
      inlineTotal: prompts.inlinePrompts.length,
      // 用于判断用户是否已把这篇 AI 文章应用到编辑区，避免插图落到旧草稿里
      articleMarkdown: prompts.markdown,
    })),
  ].filter(Boolean)
  const results = await Promise.allSettled(requests.map(async (request) => {
    const prompt = composeImagePrompt(request.scene)
    const response = await api.post('/ai/image-generations', {
      prompt,
      negativePrompt: composeNegativePrompt() || null,
      purpose: request.purpose,
      aspectRatio: request.aspectRatio,
      count: 1,
      modelId: imgModelId.value || null,
    })
    return {
      id: response.jobId,
      status: 'queued',
      prompt,
      scenePrompt: request.scene,
      aspectRatio: request.aspectRatio,
      label: request.label,
      purpose: request.purpose,
      inlineIndex: request.inlineIndex,
      inlineTotal: request.inlineTotal,
      articleMarkdown: request.articleMarkdown,
      contentItemId,
    }
  }))
  for (const result of results) {
    if (result.status === 'fulfilled') imgJobs.value.unshift(result.value)
    else imgError.value = result.reason?.message || '自动配图任务创建失败'
  }
  if (results.some((result) => result.status === 'fulfilled')) {
    toast(`文章已生成，${results.filter((result) => result.status === 'fulfilled').length} 个配图任务已开始，完成后自动插入正文`)
    startImgPoll()
  }
}

function runAi() {
  if (aiRunning.value || aiApplying.value || !detail.value) return
  if (!aiPrompt.value.trim()) {
    aiPromptError.value = '请先写清楚这次要做什么'
    return
  }
  aiPromptError.value = ''
  aiStopped.value = false
  const generationItemId = detail.value?.item.id
  aiSourceItemId.value = generationItemId
  aiApplied.value = false
  workspaceView.value = 'result'
  const autoGenerateImages = aiAutoImages.value && aiTask.value === 'article'
  let generationSaved = false
  aiOutput.value = ''
  aiError.value = ''
  aiModelInfo.value = ''
  aiRunning.value = true
  nextTick(() => aiResultRef.value?.scrollIntoView({ block: 'start', behavior: 'auto' }))
  const request = {
    taskType: aiTask.value,
    prompt: aiPrompt.value,
    contentItemId: generationItemId,
    constraints: {
      targetLength: aiTargetLength.value,
      platform: 'wechat_official',
      ...(usesWritingStyle.value ? { useLayoutComponents: aiUseLayoutComponents.value } : {}),
    },
    // 不选择时省略 modelId，由服务端按「默认模型优先 + 自动降级」解析
    ...(modelSelection.requestModelId ? { modelId: modelSelection.requestModelId } : {}),
  }
  if (usesWritingStyle.value) {
    request.skillKey = writingStyleKey.value || 'article-drafting'
  }
  abortStream = streamGeneration(
    request,
    {
      onStart: (data) => {
        aiModelInfo.value = data.modelCode ? `首选 ${data.modelCode}` : ''
      },
      onModel: (data) => {
        aiModelInfo.value = data.fallbackUsed
          ? `已自动降级到 ${data.modelCode}`
          : `正在使用 ${data.modelCode}`
      },
      onDelta: (data) => {
        aiOutput.value += data.text || ''
      },
      onError: (data) => {
        aiError.value = data.message || '生成失败'
      },
      onSaved: () => {
        generationSaved = true
        toast('AI 结果已保存为新版本')
      },
      onEnd: async () => {
        aiRunning.value = false
        if (generationItemId && detail.value?.item.id === generationItemId) {
          const refreshed = await api.get(`/content/${generationItemId}`)
          detail.value = refreshed
          if (aiTask.value === 'article' && generationSaved && !aiError.value) {
            captureSceneDrafts(aiOutput.value)
          }
          if (autoGenerateImages && generationSaved && !aiError.value) {
            void startAutoImageGeneration(aiOutput.value, generationItemId)
          }
        }
      },
    },
  )
}

function stopAi() {
  aiStopped.value = true
  abortStream?.()
  aiRunning.value = false
}

// AI 生成结果预览：结构化文章只展示正文，不让 JSON 字段糊在预览里
const aiPreview = computed(() => articleBodyFromOutput(aiOutput.value))
const aiPreviewHtml = computed(() => markdownToHtml(aiPreview.value))

async function useAiOutput() {
  if (aiRunning.value || aiApplying.value || aiApplied.value || !detail.value) return
  if (aiSourceItemId.value !== detail.value.item.id) {
    toast('请回到生成时的稿件使用此结果', 'error')
    return
  }
  const output = articleBodyFromOutput(aiOutput.value)
  if (!output.trim()) return
  aiApplying.value = true
  try {
    if (aiInsertMode.value === 'replace' && draftBody.value.trim()) {
      if (!await confirm('此操作会用 AI 结果替换当前正文，请确认已保留需要的内容。', '替换全文')) return
      // 确认对话框打开期间可能切换稿件，不得覆盖另一篇正文。
      if (aiSourceItemId.value !== detail.value?.item.id) return
    }
    if (aiInsertMode.value === 'replace') {
      draftBody.value = output
    } else {
      draftBody.value = draftBody.value ? `${draftBody.value}\n\n${output}` : output
    }
    aiApplied.value = true
    applyGeneratedInlineImages()
    workspaceView.value = 'draft'
    await nextTick()
    workspaceRef.value?.scrollIntoView({ block: 'start', behavior: 'auto' })
    // 立即排入现有串行保存队列，不再等待 1.5 秒防抖。
    await flushAutoSave()
    toast(autoSaveStatus.value === 'error' ? '已加入正文，但保存失败，请重试保存' : '已加入正文，AI 结果仍保留，可随时对照')
  } finally {
    aiApplying.value = false
  }
}

/** 把已生成、尚未进正文的自动配图补插到当前文章的 {{imageN}} 位置 */
function applyGeneratedInlineImages() {
  const pending = imgResults.value
    .filter((result) => result.inlineIndex && result.rawUrl)
    .sort((a, b) => a.inlineIndex - b.inlineIndex)
  for (const result of pending) {
    autoInsertInlineImage(result, {
      inlineIndex: result.inlineIndex,
      inlineTotal: result.inlineTotal,
    })
  }
}

// 用户正在编辑时先挂起自动插图，避免重建文档把光标打断
let pendingInlineInsertions = []

function editorHasFocus() {
  return Boolean(document.activeElement?.closest?.('.md-editor'))
}

/** focusout 早于下一个元素获得焦点，等一帧再判断是否真的离开了编辑器 */
function scheduleFlushPendingInlineImages() {
  setTimeout(flushPendingInlineImages, 0)
}

/** 编辑器失焦后把挂起的自动插图落位 */
function flushPendingInlineImages() {
  if (!pendingInlineInsertions.length || editorHasFocus()) return
  const pending = pendingInlineInsertions
  pendingInlineInsertions = []
  for (const { result, job } of pending) {
    if (job.contentItemId && detail.value?.item.id !== job.contentItemId) continue
    autoInsertInlineImage(result, job)
  }
}

function openVariant() {
  variantForm.value = {
    platform: 'wechat_official',
    title: draftTitle.value,
    body: draftBody.value,
  }
  showVariant.value = true
}

async function saveVariant() {
  if (!detail.value) return
  const created = await api.post(`/content/${detail.value.item.id}/platform-variants`, {
    platform: variantForm.value.platform,
    title: variantForm.value.title,
    body: variantForm.value.body,
  })
  detail.value.variants.unshift(created)
  showVariant.value = false
  toast('平台版本已保存（未提交发布）')
}

watch(
  () => route.query.id,
  (id) => {
    if (id) {
      openItem(id).catch((err) => {
        error.value = err.message
      })
    }
  },
)

// 切换到新稿件时重置自动保存状态（openItem 里已处理，这里只兜底）
watch(
  () => detail.value?.item.id,
  () => {
    clearTimeout(autoSaveTimer)
    autoSaveTimer = null
    flow.value = null
    if (rightTab.value === 'flow') loadWritingFlow()
  },
)

async function openInspirationTab() {
  rightTab.value = 'inspiration'
  if (!hotStatus.value.checked) await loadHotTopics()
}

function openFlowTab() {
  rightTab.value = 'flow'
  loadWritingFlow()
}

async function loadHotTopics() {
  hotLoading.value = true
  hotError.value = ''
  try {
    hotStatus.value = { ...(await api.get('/hot-topics/status')), checked: true }
    hotTopics.value = hotStatus.value.configured
      ? await api.get('/hot-topics?limit=15')
      : []
  } catch (err) {
    hotError.value = err.message
  } finally {
    hotStatus.value.checked = true
    hotLoading.value = false
  }
}

/** 基于热点新建一篇稿件，并切回 AI 助手继续写 */
async function createDraftFromHot(item) {
  hotActionId.value = item.title
  hotError.value = ''
  try {
    const created = await api.post('/content', {
      title: item.title,
      type: 'article',
    })
    items.value.unshift(created)
    await openItem(created.id)
    rightTab.value = 'ai'
    aiPrompt.value = `围绕「${item.title}」写一篇初稿，语气平实`
    toast(`已新建稿件「${item.title}」，可在 AI 助手里继续生成`)
  } catch (err) {
    hotError.value = err.message
  } finally {
    hotActionId.value = ''
  }
}

// ─── 配图 Tab ────────────────────────────────────────────────
// 提供商状态
const imgProvider = ref({ available: false, note: '', modelCode: null, providers: [] })
const imgProviderLoaded = ref(false)
const imgModelId = ref('')
const imgModelOptions = computed(() => providerOptions(imgProvider.value.providers))
const selectedImgProvider = computed(
  () => (imgProvider.value.providers || []).find((item) => (item.modelId || '') === imgModelId.value) || null,
)

async function loadImageProviderStatus() {
  if (imgProviderLoaded.value) return
  try {
    const status = await api.get('/ai/image-provider/status')
    imgProvider.value = { ...status, providers: status.providers || [] }
    imgModelId.value = resolveSelectedModelId(imgProvider.value.providers, imgModelId.value)
  } catch (err) {
    imgProvider.value = { available: false, note: err.message, modelCode: null, providers: [] }
  } finally {
    imgProviderLoaded.value = true
  }
}

// 生图表单
const imgPrompt = ref('')
const imgAspectRatio = ref('3:4')
const imgCount = ref(1)
const imgBusy = ref(false)
const imgError = ref('')

// 轮询中的任务列表（只存本次 tab 会话发起的）
const imgJobs = ref([]) // [{id, status, assetIds?, error?}]
let imgPollTimer = null
let imgPollRounds = 0
const IMG_MAX_POLL = 120 // 1.5s × 120 ≈ 3 分钟

const ASPECT_OPTIONS = [
  { value: '3:4', label: '3:4 竖版' },
  { value: '1:1', label: '1:1 方形' },
  { value: '16:9', label: '16:9 横版' },
]
const countOptions = [1, 2, 3, 4].map((n) => ({ value: n, label: `${n} 张` }))
const platformOptions = PLATFORMS.map((p) => ({ value: p.code, label: p.label }))

// 已生成图片（含 blob URL，用于展示）
const imgResults = ref([]) // [{assetId, blobUrl, rawUrl, mime, jobId}]

async function openImageTab() {
  rightTab.value = 'image'
  await Promise.all([loadImageProviderStatus(), loadInlineStyles(), loadBaseStyles()])
}

// ─── 原创检测 Tab ────────────────────────────────────────────────
const originalityOpen = ref(true)
const originalityLoading = ref(false)
const originalityError = ref('')
const originalityAnalysis = ref(null)

// 比对范围与历史
const compareScope = ref('all')
const showAnalysisHistory = ref(false)
const analysisList = ref([])
const analysisHistoryCount = ref(0)

// 报告导出：后端直接出文件，前端带鉴权下载（<a href> 带不了 Authorization）
const reportExportOptions = [
  { label: 'TXT（.txt）', key: 'txt' },
  { label: 'Markdown（.md，可贴进文档）', key: 'markdown' },
  { label: '图片（.png）', key: 'png' }
]

const LEVEL_LABEL = { excellent: '优秀', good: '良好', fair: '中等', poor: '偏低' }

const originalityLevelLabel = computed(
  () => LEVEL_LABEL[originalityAnalysis.value?.level] || '未知'
)

/**
 * 五维评分视图模型。
 * `similarity` 是「越高越像」，跟其余四维方向相反：高相似度是坏消息，
 * 所以配色要反过来，语料为空时显示「样本不足」而不是 0 分。
 */
const originalityDimensions = computed(() => {
  const data = originalityAnalysis.value
  if (!data) return []
  const scores = data.scoreboard || {}
  const corpusSize = data.corpusSize ?? 0
  const dimensions = [
    { key: 'similarity', label: '相似度', weight: 40, inverted: true, hint: '越低越好' },
    { key: 'originality', label: '观点独特性', weight: 25, hint: '越高越好' },
    { key: 'structure', label: '结构层次', weight: 15, hint: '越高越好' },
    { key: 'dataAccuracy', label: '数据支撑度', weight: 12, hint: '越高越好' },
    { key: 'styleUniqueness', label: '文风辨识度', weight: 8, hint: '越高越好' }
  ]
  return dimensions.map((dim) => {
    const value = scores[dim.key] ?? 0
    const unknown = dim.key === 'similarity' && corpusSize === 0
    const tone = unknown
      ? 'muted'
      : dim.inverted
        ? value <= 30
          ? 'good'
          : value <= 55
            ? 'warn'
            : 'bad'
        : value >= 75
          ? 'good'
          : value >= 60
            ? 'warn'
            : 'bad'
    return {
      ...dim,
      value,
      display: unknown ? '样本不足' : String(value),
      percent: unknown ? 0 : Math.max(3, Math.min(100, value)),
      tone
    }
  })
})

const originalitySegments = computed(() => {
  const articles = originalityAnalysis.value?.details?.similarArticles || []
  return articles.flatMap((article) =>
    (article.matchingSnippets || []).map((seg) => ({ ...seg, articleTitle: article.title }))
  )
})

async function analyzeOriginality() {
  const version = detail.value?.versions?.[0]
  if (!version) {
    originalityError.value = '请先保存稿件'
    return
  }

  originalityLoading.value = true
  originalityError.value = ''

  try {
    const response = await api.post('/content/analyze-originality', {
      contentVersionId: version.id,
      compareScope: compareScope.value,
      useCache: true
    })
    originalityAnalysis.value = response
    await loadAnalysisHistory(version.id)
  } catch (err) {
    originalityError.value = err.message || '检测失败'
  } finally {
    originalityLoading.value = false
  }
}

// 历史记录：与检测结果同一个数据源，改了正文再测会多一条
async function loadAnalysisHistory(contentVersionId) {
  try {
    const response = await api.get(`/originality/${contentVersionId}/history?limit=10`)
    analysisList.value = response.items || []
    analysisHistoryCount.value = analysisList.value.length
  } catch (err) {
    console.error('加载历史失败:', err)
  }
}

async function handleReportExport(key) {
  const analysisId = originalityAnalysis.value?.analysisId
  if (!analysisId) return
  const endpoint = { txt: 'report/txt', markdown: 'report/markdown', png: 'report/png' }[key]
  if (!endpoint) return
  try {
    await downloadAuthenticated(`/originality/${analysisId}/${endpoint}`, `原创度报告.${key}`)
    toast('报告已开始下载')
  } catch (err) {
    toast(err.message || '导出失败', 'error')
  }
}

function formatDate(dateString) {
  const date = new Date(dateString)
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

function formatDateTime(dateString) {
  if (!dateString) return ''
  const date = new Date(dateString)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  })
}

function openOriginalityTab() {
  rightTab.value = 'originality'
}

/** 从当前文章标题 + 正文前 300 字提炼图像 prompt（调用 SSE 流） */
async function suggestImagePrompt() {
  if (!detail.value) return
  const title = draftTitle.value || ''
  const excerpt = (draftBody.value || '').slice(0, 300)
  if (!title && !excerpt) {
    imgError.value = '稿件还没有内容，请先写点正文再提炼'
    return
  }
  imgBusy.value = true
  imgError.value = ''
  imgPrompt.value = ''
  const userPrompt = [
    `文章标题：${title}`,
    excerpt ? `正文节选：${excerpt}` : '',
    '请为这篇文章生成 1 条英文图像生成提示词（image generation prompt），',
    '要求：描述具体画面，包含场景、色调、风格，不超过 120 个单词，直接输出提示词文本，不要任何解释。',
  ]
    .filter(Boolean)
    .join('\n')

  let collected = ''
  try {
    await new Promise((resolve, reject) => {
      const stop = streamGeneration(
        { taskType: 'image_prompt', prompt: userPrompt },
        {
          onDelta(data) { collected += (data.text || ''); imgPrompt.value = collected },
          onError(data) { reject(new Error(data.message || data.code || '生成失败')) },
          onDone() { resolve() },
          onEnd() { resolve() },
        },
      )
      // 超时保护
      setTimeout(() => { stop(); resolve() }, 30000)
    })
    imgPrompt.value = collected.trim()
  } catch (err) {
    imgError.value = `提炼失败：${err.message}`
  } finally {
    imgBusy.value = false
  }
}

/** 发起 AI 生图任务 */
async function generateImages() {
  if (!imgPrompt.value.trim()) {
    imgError.value = '请填写图像描述'
    return
  }
  if (!baseStyleLocked.value) {
    imgError.value = baseStyleHint.value
    return
  }
  const scene = imgPrompt.value.trim()
  imgBusy.value = true
  imgError.value = ''
  try {
    const prompt = composeImagePrompt(scene)
    const res = await api.post('/ai/image-generations', {
      prompt,
      negativePrompt: composeNegativePrompt() || null,
      aspectRatio: imgAspectRatio.value,
      count: Number(imgCount.value),
      modelId: imgModelId.value || null,
    })
    imgJobs.value.unshift({
      id: res.jobId,
      status: 'queued',
      prompt,
      scenePrompt: scene,
      aspectRatio: imgAspectRatio.value,
      count: Number(imgCount.value),
      label: '手动配图',
    })
    startImgPoll()
  } catch (err) {
    imgError.value = err.message
  } finally {
    imgBusy.value = false
  }
}

async function retryImageJob(job) {
  const scene = (job.scenePrompt || '').trim()
  if (!scene || job.status !== 'failed') return
  if (!baseStyleLocked.value) {
    job.error = baseStyleHint.value
    return
  }
  job.status = 'retrying'
  job.error = ''
  try {
    const prompt = composeImagePrompt(scene)
    const response = await api.post('/ai/image-generations', {
      prompt,
      negativePrompt: job.negativePrompt || null,
      purpose: job.purpose || 'cover',
      aspectRatio: job.aspectRatio || (job.purpose === 'cover' ? '3:4' : '1:1'),
      count: job.count || 1,
      modelId: imgModelId.value || null,
    })
    job.id = response.jobId
    job.prompt = prompt
    job.status = 'queued'
    startImgPoll()
  } catch (err) {
    job.status = 'failed'
    job.error = err.message
  }
}

function stopImgPoll() {
  if (imgPollTimer) clearInterval(imgPollTimer)
  imgPollTimer = null
}

function startImgPoll() {
  if (imgPollTimer) return
  imgPollRounds = 0
  imgPollTimer = setInterval(async () => {
    imgPollRounds += 1
    if (imgPollRounds > IMG_MAX_POLL) {
      stopImgPoll()
      imgError.value = '生图超时（3 分钟），请刷新或到素材页确认结果'
      return
    }
    const pending = imgJobs.value.filter((j) => ['queued', 'running'].includes(j.status))
    if (!pending.length) { stopImgPoll(); return }
    for (const job of pending) {
      try {
        const data = await api.get(`/jobs/${job.id}`)
        job.status = data.status
        job.progress = data.progress
        if (data.status === 'succeeded') {
          const assetIds = data.result?.assetIds || []
          job.assetIds = assetIds
          for (const assetId of assetIds) {
            if (!imgResults.value.find((r) => r.assetId === assetId)) {
              let blobUrl = ''
              try {
                blobUrl = await fetchBlobUrl(`/assets/${assetId}/raw`)
              } catch {
                // 图片 URL 获取失败不影响整体流程
              }
              // 后端下发的签名直链：可直接放进 Markdown，预览与外部发布都能取图
              const rawPath = (data.result?.assetUrls || [])[assetIds.indexOf(assetId)]
              const result = {
                assetId,
                blobUrl,
                rawUrl: rawPath ? apiUrl(rawPath) : '',
                jobId: job.id,
                label: job.label,
                purpose: job.purpose,
                inlineIndex: job.inlineIndex,
                inlineTotal: job.inlineTotal || imgJobs.value.filter((j) => j.inlineIndex).length,
              }
              imgResults.value.unshift(result)
              // 自动配图：任务完成后直接落到正文预留的 {{imageN}} 位置；
              // 已切换稿件时不回填，避免把图片插进别人的正文
              if (
                job.inlineIndex
                && assetId === assetIds[0]
                && job.contentItemId
                && detail.value?.item.id === job.contentItemId
              ) {
                if (isArticleInEditor(job)) {
                  autoInsertInlineImage(result, job)
                } else {
                  toast(`配图 ${job.inlineIndex} 已生成，点击配图卡片即可插入正文`)
                }
              }
            }
          }
        } else if (data.status === 'failed') {
          job.error = data.errorMessage || '生图失败'
        }
      } catch {
        // 单次轮询失败不中断，继续下轮
      }
    }
    const stillPending = imgJobs.value.some((j) => ['queued', 'running'].includes(j.status))
    if (!stillPending) stopImgPoll()
  }, 1500)
}

/** 当前编辑区里是否是这篇 AI 文章（已点「使用结果」），否则不能自动插图 */
function isArticleInEditor(job) {
  const body = draftBody.value || ''
  if (job.articleMarkdown && body.includes(job.articleMarkdown)) return true
  // 用户可能改过正文，仍留有图片占位符时也认为文章已在编辑区
  return /\{\{\s*image\d+/i.test(body)
}

/**
 * 自动生成的正文插图落到 AI 预留的 {{imageN}} 位置。
 * 模型漏写占位符时按小节均分兜底，都没有才追加到文末；同一张图只插一次。
 * 返回是否真的插入了正文。
 */
function autoInsertInlineImage(result, job) {
  if (!result.rawUrl) return false
  const body = draftBody.value || ''
  const index = job.inlineIndex
  const total = job.inlineTotal
  if (body.includes(result.rawUrl)) return false
  if (editorHasFocus()) {
    if (!pendingInlineInsertions.some((item) => item.result === result)) {
      pendingInlineInsertions.push({ result, job })
      toast(`配图 ${index} 已生成，退出编辑后自动插入`)
    }
    return false
  }
  const placeholder = new RegExp(
    `\\{\\{\\s*image\\s*${index}\\s*(?:\\|\\s*([^}]*?)\\s*)?\\}\\}`,
    'i',
  )
  const match = body.match(placeholder)
  // 占位符里的图注（{{image1|说明}}）保留为图片 alt
  const alt = (match?.[1] || `配图${index}`).replace(/[[\]]/g, '')
  const markdown = `![${alt}](${result.rawUrl})`
  if (match) {
    draftBody.value = body.replace(placeholder, () => markdown)
    toast(`配图 ${index} 已自动插入正文对应位置`)
    return true
  }
  // 没有占位符：按小节位置兜底，避免所有插图都堆到文末
  const lines = body.split('\n')
  const headings = []
  lines.forEach((line, i) => {
    if (/^##\s+/.test(line.trim())) headings.push(i)
  })
  const count = Math.max(1, Number(total) || 1)
  if (headings.length) {
    const slot = Math.min(
      headings.length - 1,
      Math.floor((index * headings.length) / (count + 1)),
    )
    const at = headings[slot]
    draftBody.value = [...lines.slice(0, at), '', markdown, '', ...lines.slice(at)].join('\n')
    toast(`配图 ${index} 未找到占位符，已插入第 ${slot + 1} 个小节处`)
    return true
  }
  draftBody.value = `${body}\n\n${markdown}\n`
  toast(`配图 ${index} 未找到占位符，已插入正文末尾`)
  return true
}

/** 图片链接是否已经在正文里（自动插图后按钮置灰，避免重复插入） */
function isImageInBody(result) {
  return Boolean(result?.rawUrl && (draftBody.value || '').includes(result.rawUrl))
}

/** 把图片以 Markdown 语法插入正文末尾（用后端返回的签名直链，img 标签才能直接取图） */
function insertImageToBody(result) {
  if (!result.rawUrl) {
    toast('该素材缺少可用直链，请稍后重试')
    return
  }
  if (isImageInBody(result)) {
    toast('该图片已在正文中')
    return
  }
  // 走 v-model 已经能同步到编辑器；这里保持原逻辑（追加到末尾）
  draftBody.value = (draftBody.value || '') + `\n\n![配图](${result.rawUrl})\n`
  toast('图片已插入正文末尾')
}

/** 复制素材直链到剪贴板 */
async function copyImageUrl(result) {
  const url = result.rawUrl || `${location.origin}/api/v1/assets/${result.assetId}/raw`
  try {
    await navigator.clipboard.writeText(url)
    toast('已复制图片链接')
  } catch {
    toast(`链接：${url}`)
  }
}

// 发布抽屉默认素材：封面取自动配图，正文按插图编号排序
const publishCoverAssetId = computed(
  () => imgResults.value.find((result) => result.purpose === 'cover')?.assetId || '',
)
const publishInlineAssetIds = computed(() =>
  imgResults.value
    .filter((result) => result.inlineIndex && result.assetId)
    .sort((a, b) => a.inlineIndex - b.inlineIndex)
    .map((result) => result.assetId),
)

function releaseImgResults() {
  for (const r of imgResults.value) {
    if (r.blobUrl) URL.revokeObjectURL(r.blobUrl)
  }
  imgResults.value = []
}

onMounted(() => {
  loadList()
  loadWritingStyles()
  loadImagePreferences()
})

onBeforeUnmount(() => {
  stopImgPoll()
  releaseImgResults()
  clearTimeout(imagePreferencesTimer)
  clearTimeout(flowDebounce)
  // 组件卸载时若有待保存内容，立即同步
  flushAutoSave()
})
</script>

<template>
  <div class="page studio-page">
    <div class="page-head studio-page-head">
      <div class="studio-page-heading">
        <h1 class="page-title">创作空间</h1>
        <p class="page-sub">从灵感到成稿，在同一处完成创作与发布</p>
      </div>
      <n-space :size="8" class="studio-page-actions">
        <n-button quaternary size="small" @click="createItem">新建稿件</n-button>
        <n-button quaternary size="small" :disabled="!detail" @click="openVariant">
          生成平台版本
        </n-button>
        <n-button
          quaternary
          size="small"
          :disabled="!detail || !draftBody || Boolean(disabledReason('cards.use', '图文卡片'))"
          :title="disabledReason('cards.use', '图文卡片') || '将当前正文带入图文卡片创作台'"
          @click="router.push({ name: 'cards', state: { markdown: draftBody } })"
        >
          转为图文卡片
        </n-button>
        <n-button
          quaternary
          size="small"
          :disabled="!detail || Boolean(disabledReason('publishing.use', '发布管理'))"
          :title="disabledReason('publishing.use', '发布管理') || '推送到公众号草稿箱'"
          @click="publishDrawerOpen = true"
        >
          发布
        </n-button>
        <n-button type="primary" size="small" :loading="saving" :disabled="!detail" @click="saveVersion">
          存为版本
        </n-button>
      </n-space>
    </div>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <div v-if="loading" class="studio-grid" :class="{ 'drafts-collapsed': draftsCollapsed }">
      <div class="skeleton studio-drafts-skeleton" />
      <div class="skeleton studio-editor-skeleton" />
      <div class="skeleton studio-tools-skeleton" />
    </div>

    <div v-else-if="!items.length" class="card studio-empty-state">
      <div class="empty">
        <span class="studio-empty-mark" aria-hidden="true">✎</span>
        <span class="empty-title">从第一篇稿件开始</span>
        <span>写下你的想法，或从右侧「灵感」里挑一个热点方向。</span>
        <n-button size="small" @click="createItem">新建稿件</n-button>
      </div>
    </div>

    <div v-else class="studio-grid" :class="{ 'drafts-collapsed': draftsCollapsed }">
      <aside class="card studio-drafts" :class="{ 'is-collapsed': draftsCollapsed }">
        <template v-if="!draftsCollapsed">
        <div class="studio-drafts-head">
          <div class="studio-drafts-heading">
            <h2 class="studio-panel-title">稿件与版本</h2>
            <span class="studio-panel-count">{{ items.length }}</span>
          </div>
          <n-button
            quaternary
            size="small"
            aria-expanded="true"
            aria-label="收起稿件列表"
            title="收起稿件列表"
            @click="draftsCollapsed = true"
          >‹</n-button>
        </div>
        <n-button quaternary size="small" class="studio-new-draft" @click="createItem">+ 新建稿件</n-button>
        <button class="studio-library-link" type="button" @click="libraryOpen = true">
          查看全部稿件（{{ items.length }}）
        </button>
        <n-input
          v-model:value="draftKeyword"
          class="studio-draft-search"
          size="small"
          round
          clearable
          placeholder="搜索稿件"
          aria-label="搜索稿件"
        />
        <!-- 搜索时拍平；平时按 今天/近7天/更早 分组，组头滚动吸顶 -->
        <template v-if="groupedItems">
          <section
            v-for="group in groupedItems"
            :key="group.key"
            class="studio-draft-group"
            :class="{ 'is-collapsed': group.key === 'older' && olderCollapsed }"
          >
            <button
              class="studio-draft-group-head"
              type="button"
              :aria-expanded="!(group.key === 'older' && olderCollapsed)"
              @click="group.key === 'older' && toggleOlderGroup()"
            >
              <span>{{ group.label }}</span>
              <span class="studio-draft-group-count">{{ group.list.length }}</span>
              <span
                v-if="group.key === 'older'"
                class="studio-draft-group-caret"
                aria-hidden="true"
              >{{ olderCollapsed ? '▸' : '▾' }}</span>
            </button>
            <div v-show="!(group.key === 'older' && olderCollapsed)" class="list studio-list">
              <div
                v-for="item in group.list"
                :key="item.id"
                class="list-row item-row"
                :class="{ 'is-active': item.id === detail?.item.id }"
              >
                <n-tooltip trigger="hover" placement="top-start">
                  <template #trigger>
                    <button
                      class="item-open"
                      type="button"
                      @click="openItem(item.id)"
                    >
                      <div class="list-main">
                        <div class="list-title">{{ item.title }}</div>
                        <div class="list-meta">{{ relativeDay(item.updatedAt) }}</div>
                      </div>
                    </button>
                  </template>
                  {{ item.title }}
                </n-tooltip>
                <n-button
                  quaternary
                  size="small"
                  class="item-delete"
                  aria-label="删除稿件"
                  title="删除稿件"
                  @click="askDelete(item)"
                >
                  ✕
                </n-button>
              </div>
            </div>
          </section>
        </template>
        <div v-else class="list studio-list">
          <div
            v-for="item in filteredItems"
            :key="item.id"
            class="list-row item-row"
            :class="{ 'is-active': item.id === detail?.item.id }"
          >
            <n-tooltip trigger="hover" placement="top-start">
              <template #trigger>
                <button
                  class="item-open"
                  type="button"
                  @click="openItem(item.id)"
                >
                  <div class="list-main">
                    <div class="list-title">{{ item.title }}</div>
                    <div class="list-meta">{{ relativeDay(item.updatedAt) }}</div>
                  </div>
                </button>
              </template>
              {{ item.title }}
            </n-tooltip>
            <n-button
              quaternary
              size="small"
              class="item-delete"
              aria-label="删除稿件"
              title="删除稿件"
              @click="askDelete(item)"
            >
              ✕
            </n-button>
          </div>
          <p v-if="!filteredItems.length" class="studio-draft-search-empty">
            没有匹配「{{ draftKeyword.trim() }}」的稿件
          </p>
        </div>
        <section v-if="detail" class="studio-versions" aria-labelledby="studio-versions-title">
          <h3 id="studio-versions-title" class="studio-section-label">最近版本</h3>
          <div class="list studio-list">
            <div v-for="v in detail.versions.slice(0, 6)" :key="v.id" class="list-row version-row">
              <div class="list-main">
                <div class="list-title studio-version-title">v{{ v.versionNo }}</div>
                <div class="list-meta">{{ v.sourceType }} · {{ v.createdAt.slice(5, 16).replace('T', ' ') }}</div>
              </div>
              <n-button quaternary size="small" @click="restore(v)">载入</n-button>
            </div>
          </div>
        </section>
        <section
          v-if="detail?.variants.length"
          class="studio-doc-variants"
          :class="{ 'is-collapsed': variantsCollapsed }"
          aria-labelledby="studio-variants-title"
        >
          <div class="studio-variants-head">
            <h3 id="studio-variants-title" class="studio-section-label">平台版本</h3>
            <n-button
              quaternary
              size="small"
              :aria-expanded="!variantsCollapsed"
              @click="variantsCollapsed = !variantsCollapsed"
            >
              {{ variantsCollapsed ? `展开 ${detail.variants.length}` : '收起' }}
            </n-button>
          </div>
          <div v-if="!variantsCollapsed" class="list studio-list">
            <div v-for="variant in detail.variants" :key="variant.id" class="list-row">
              <div class="list-main">
                <div class="list-title">{{ variant.title }}</div>
                <div class="list-meta">
                  {{ PLATFORMS.find((p) => p.code === variant.platform)?.label || variant.platform }}
                  · 第 {{ variant.revisionNo }} 版
                </div>
              </div>
              <StatusTag :status="variant.status" />
            </div>
          </div>
        </section>
        </template>
        <template v-else>
          <div class="studio-drafts-rail">
            <n-button
              quaternary
              size="small"
              class="studio-rail-toggle"
              aria-expanded="false"
              aria-label="展开稿件列表"
              title="展开稿件列表"
              @click="draftsCollapsed = false"
            >›</n-button>
            <span class="studio-rail-count" :title="`共 ${items.length} 篇稿件`">{{ items.length }}</span>
            <span class="studio-rail-label">稿件</span>
          </div>
        </template>
      </aside>

      <section ref="workspaceRef" class="card studio-editor" aria-label="创作工作区">
        <nav class="studio-workspace-nav" aria-label="写作视图">
          <div class="studio-workspace-tabs" role="tablist" aria-label="正文与 AI 结果">
            <button id="workspace-draft-tab" role="tab" :aria-selected="workspaceView === 'draft'" aria-controls="workspace-draft" :class="{ 'is-active': workspaceView === 'draft' }" @click="workspaceView = 'draft'">正文编辑</button>
            <button id="workspace-result-tab" role="tab" :aria-selected="workspaceView === 'result'" aria-controls="workspace-result" :class="{ 'is-active': workspaceView === 'result' }" @click="workspaceView = 'result'">AI 生成结果 <span v-if="aiOutput || aiRunning" class="studio-result-dot" aria-hidden="true"></span></button>
          </div>
          <span class="studio-workspace-caption">{{ workspaceView === 'draft' ? '你的正式稿件' : '独立预览 · 不会自动覆盖正文' }}</span>
        </nav>
        <div id="workspace-draft" v-show="workspaceView === 'draft'" role="tabpanel" aria-labelledby="workspace-draft-tab" class="studio-draft-surface">
        <template v-if="detail">
          <header class="studio-doc-head">
            <input
              id="doc-title"
              v-model="draftTitle"
              class="studio-title-input"
              aria-label="稿件标题"
              placeholder="未命名稿件"
            />
            <div class="studio-doc-meta">
              <span
                class="studio-chip"
                :class="{
                  'is-dirty': autoSaveStatus === 'pending' || autoSaveStatus === 'saving',
                  'is-ok': autoSaveStatus === 'saved' || autoSaveStatus === 'idle',
                  'is-error': autoSaveStatus === 'error',
                }"
              >
                <template v-if="autoSaveStatus === 'saving'">保存中…</template>
                <template v-else-if="autoSaveStatus === 'pending'">未保存</template>
                <template v-else-if="autoSaveStatus === 'error'">保存失败</template>
                <template v-else-if="dirty">有未提交版本</template>
                <template v-else>已自动保存</template>
              </span>
              <span class="studio-chip">{{ draftBody.length }} 字</span>
            </div>
          </header>
          <n-alert v-if="hasEscapedArticleBreaks" type="warning" :bordered="false">
            检测到正文中的换行被转义，导致标题、列表和段落无法排版。
            <n-button size="small" @click="repairArticleBreaks">修复换行与排版</n-button>
          </n-alert>
          <MarkdownEditor
            v-model="draftBody"
            :enable-ai-bubble="true"
            placeholder="在这里写正文，选中文字可用 AI 润色/改写..."
            class="studio-doc-editor"
            @editor-ready="(ed) => (editorRef = ed)"
            @focusout="scheduleFlushPendingInlineImages"
          />
          <p class="studio-doc-hint">正文自动保存 · AI 结果保留在独立标签中，确认采用后才进入正文</p>
        </template>
        <div v-else class="empty">
          <span class="empty-title">选择或新建一篇稿件</span>
        </div>
        </div>
        <section id="workspace-result" v-show="workspaceView === 'result'" ref="aiResultRef" role="tabpanel" aria-labelledby="workspace-result-tab" class="studio-result-surface">
          <header class="studio-result-header">
            <div><span class="studio-section-label">AI 写作 / 生成结果</span><h2>{{ aiRunning ? '正在把想法写成文章' : aiApplied ? '已采用，结果为你保留' : aiOutput ? '先阅读，再决定采用' : '从一个想法开始' }}</h2></div>
            <n-button v-if="aiRunning" size="small" @click="stopAi">停止生成</n-button>
          </header>
          <p class="studio-ai-status" role="status">{{ aiRunning ? (aiOutput ? '内容持续生成中，完成或停止后可采用' : '正在等待模型响应…') : aiStopped ? '已停止，可阅读并采用已有内容' : aiApplied ? '已加入正文；切换「正文编辑」继续修改。此处保留生成时的内容。' : '生成结果不会自动修改正文。追加保留原文，替换需再次确认。' }}</p>
          <p v-if="aiModelInfo" class="studio-model-usage">{{ aiModelInfo }}</p>
          <n-alert v-if="aiError" type="error" :bordered="false">{{ aiError }}</n-alert>
          <template v-if="aiOutput">
            <div class="studio-result-paper studio-ai-output-markdown" v-html="aiPreviewHtml"></div>
            <footer class="studio-result-actions">
              <div class="ai-insert-group" role="group" aria-label="采用方式">
                <button class="ai-length-btn" :disabled="aiRunning || aiApplying || aiApplied" :class="{ 'is-active': aiInsertMode === 'append' }" :aria-pressed="aiInsertMode === 'append'" @click="aiInsertMode = 'append'">追加到正文末尾</button>
                <button class="ai-length-btn" :disabled="aiRunning || aiApplying || aiApplied" :class="{ 'is-active': aiInsertMode === 'replace' }" :aria-pressed="aiInsertMode === 'replace'" @click="aiInsertMode = 'replace'">替换全文</button>
              </div>
              <n-button type="primary" :loading="aiApplying" :disabled="aiRunning || aiApplied || !detail || aiSourceItemId !== detail.item.id" @click="useAiOutput">{{ aiApplied ? '已加入正文' : '采用到正文' }}</n-button>
              <n-button quaternary @click="workspaceView = 'draft'">返回正文</n-button>
            </footer>
          </template>
          <div v-else-if="!aiRunning" class="studio-result-empty"><span aria-hidden="true">✎</span><h3>写下需求，让 AI 帮你起笔</h3><p>在右侧选择任务并描述主题，生成的文章会显示在这里。</p></div>
        </section>
      </section>

      <aside class="card studio-tools" aria-label="创作工具">
        <!-- 面板 Tab 切换 -->
        <div class="segmented studio-panel-tabs" role="tablist" aria-label="工作面板">
          <button
            id="studio-tab-ai"
            class="segmented-item"
            :class="{ 'is-active': rightTab === 'ai' }"
            role="tab"
            aria-controls="studio-panel-ai"
            :aria-selected="rightTab === 'ai'"
            @click="rightTab = 'ai'"
          >AI 助手</button>
          <button
            id="studio-tab-image"
            class="segmented-item"
            :class="{ 'is-active': rightTab === 'image' }"
            role="tab"
            aria-controls="studio-panel-image"
            :aria-selected="rightTab === 'image'"
            @click="openImageTab"
          >配图</button>
          <button
            id="studio-tab-originality"
            class="segmented-item"
            :class="{ 'is-active': rightTab === 'originality' }"
            role="tab"
            aria-controls="studio-panel-originality"
            :aria-selected="rightTab === 'originality'"
            @click="openOriginalityTab"
          >原创检测</button>
          <button
            id="studio-tab-inspiration"
            class="segmented-item"
            :class="{ 'is-active': rightTab === 'inspiration' }"
            role="tab"
            aria-controls="studio-panel-inspiration"
            :aria-selected="rightTab === 'inspiration'"
            @click="openInspirationTab"
          >灵感</button>
          <button
            id="studio-tab-flow"
            class="segmented-item"
            :class="{ 'is-active': rightTab === 'flow' }"
            role="tab"
            aria-controls="studio-panel-flow"
            :aria-selected="rightTab === 'flow'"
            @click="openFlowTab"
          >流程</button>
        </div>

        <!-- AI 助手 Tab -->
        <section
          v-if="rightTab === 'ai'"
          id="studio-panel-ai"
          class="studio-tab-panel"
          role="tabpanel"
          aria-labelledby="studio-tab-ai"
        >
          <div class="studio-brief-label">创作助手</div>
          <div v-if="aiOutput || aiRunning" class="studio-result-notice" role="status">
            <span>{{ aiRunning ? '文章正在主工作区生成' : aiApplied ? '结果已采用，仍可查看原稿' : '新结果已就绪，等待你确认' }}</span>
            <n-button text size="small" @click="workspaceView = 'result'">查看结果 →</n-button>
          </div>
          <div class="row studio-tool-head">
            <div>
              <span class="studio-section-label studio-section-label-flush">AI 写作</span>
              <p class="studio-tool-description">基于当前稿件生成内容，确认后再插入正文</p>
            </div>
            <div class="spacer" />
            <n-button quaternary size="small" :aria-label="aiOpen ? '收起 AI 助手' : '展开 AI 助手'" @click="aiOpen = !aiOpen">
              {{ aiOpen ? '▾' : '▸' }}
            </n-button>
          </div>
          <div v-if="aiOpen" class="studio-tool-stack">
            <div class="field">
              <label class="field-label" for="ai-task">任务</label>
              <n-select id="ai-task" v-model:value="aiTask" :options="TASKS" />
            </div>
            <div class="field">
              <ModelSelector :disabled="aiRunning" label="模型" />
            </div>
            <div v-if="usesWritingStyle" class="field">
              <label class="field-label" for="ai-writing-style">写作风格</label>
              <n-select
                id="ai-writing-style"
                v-model:value="writingStyleKey"
                :disabled="aiRunning || writingStylesLoading"
                :options="writingStyleOptions"
              />
            </div>
            <div class="field">
              <label class="field-label" for="ai-prompt">要做什么</label>
              <n-input
                id="ai-prompt"
                v-model:value="aiPrompt"
                type="textarea"
                class="studio-ai-prompt"
                :autosize="{ minRows: 3, maxRows: 5 }"
                placeholder="例如：围绕「一周三篇图文」写一篇初稿，语气平实"
              />
            </div>
            <!-- 字数目标 -->
            <div class="field">
              <label class="field-label">目标字数</label>
              <div class="ai-length-group">
                <button
                  v-for="opt in TARGET_LENGTH_OPTIONS"
                  :key="opt.value"
                  class="ai-length-btn"
                  :class="{ 'is-active': aiTargetLength === opt.value }"
                  @click="aiTargetLength = opt.value"
                >{{ opt.label }}</button>
              </div>
            </div>
            <div class="studio-ai-quick-actions">
              <n-button block type="primary" :loading="aiRunning" :disabled="!detail || aiApplying || imagePreferencesLoading" @click="runAi">开始生成</n-button>
              <n-button block secondary @click="aiSettingsOpen = true">配图与高级设置</n-button>
              <n-button v-if="aiRunning" block @click="stopAi">停止生成</n-button>
            </div>
            <n-alert v-if="aiPromptError" type="error" :bordered="false">{{ aiPromptError }}</n-alert>
            <n-modal v-model:show="aiSettingsOpen" preset="card" title="AI 写作设置" class="studio-ai-settings-modal" :style="{ width: 'min(640px, calc(100vw - 32px))' }" :bordered="false">
              <div class="studio-ai-settings-body">
            <n-checkbox
              v-if="usesWritingStyle"
              v-model:checked="aiUseLayoutComponents"
              :disabled="aiRunning"
            >
              <span>
                <strong>使用排版小组件</strong>
                <small>默认关闭；开启后 AI 可按需使用导语、数据、步骤等小组件</small>
              </span>
            </n-checkbox>
            <n-checkbox
              v-model:checked="aiAutoImages"
              :disabled="aiRunning || aiTask !== 'article' || imagePreferencesLoading"
            >
              <span>
                <strong>自动生成配图</strong>
                <small>成文完成后自动生成封面图和正文插图</small>
              </span>
            </n-checkbox>
            <section class="ai-image-settings" aria-label="基础风格与配图">
              <div class="field">
                <label class="field-label">基础风格</label>
                <n-select
                  :value="baseStyleSelectValue"
                  :options="baseStyleOptions"
                  :loading="styleLocking || !baseStylesLoaded"
                  :disabled="!detail || styleLocking"
                  placeholder="选择这篇稿的基础风格"
                  aria-label="选择这篇稿的基础风格"
                  @update:value="changeBaseStyle"
                />
                <p v-if="!baseStyleLocked" class="ai-image-settings-error">{{ baseStyleHint }}</p>
                <p v-else class="ai-image-settings-status">已锁定在这篇稿上，之后新生成的图会带上这个风格。</p>
              </div>
              <div class="field">
                <label class="field-label">封面模板</label>
                <n-select
                  :options="coverStyles.map((style) => ({ value: style.id, label: style.name }))"
                  :disabled="!coverStyles.length"
                  placeholder="选一个封面模板填入画面描述"
                  aria-label="选择封面模板"
                  @update:show="(open) => open && loadCoverStyles()"
                  @update:value="applyCoverTemplate"
                />
              </div>
            </section>
            <section v-if="aiAutoImages" class="ai-image-settings" aria-label="自动配图设置">
              <div class="field">
                <label class="field-label">生图模型</label>
                <n-select
                  v-model:value="imgModelId"
                  :options="imgModelOptions"
                  :loading="!imgProviderLoaded"
                  :disabled="aiRunning || !imgModelOptions.length"
                  :consistent-menu-width="false"
                  placeholder="没有可用的生图模型"
                  aria-label="选择自动配图的生图模型"
                />
              </div>
              <div class="ai-image-settings-grid">
                <div class="field">
                  <label class="field-label">封面比例</label>
                  <n-select v-model:value="aiCoverAspectRatio" :options="AI_IMAGE_ASPECT_OPTIONS" />
                </div>
                <div class="field">
                  <label class="field-label">正文插图比例</label>
                  <n-select v-model:value="aiInlineAspectRatio" :options="AI_IMAGE_ASPECT_OPTIONS" />
                </div>
              </div>
              <n-checkbox v-model:checked="aiGenerateCover" :disabled="aiRunning">
                同时生成封面图
              </n-checkbox>
              <p v-if="!imgProvider.available && imgProviderLoaded" class="ai-image-settings-error">
                当前未配置生图供应商，文章仍可生成，但自动配图任务会失败。
              </p>
              <p v-if="imagePreferencesSaveStatus === 'saving'" class="ai-image-settings-status">正在保存配图设置…</p>
              <p v-else-if="imagePreferencesSaveStatus === 'saved'" class="ai-image-settings-status">配图设置已保存</p>
              <p v-if="imagePreferencesError" class="ai-image-settings-error">{{ imagePreferencesError }}</p>
            </section>
            <p v-else-if="imagePreferencesError" class="ai-image-settings-error">{{ imagePreferencesError }}</p>
            <section v-if="sceneDrafts.length" class="ai-image-settings" aria-label="手动配图">
              <span class="studio-section-label studio-section-label-flush">画面描述</span>
              <p class="ai-image-settings-status">只含画面，不含基础风格。改完再按此生成。</p>
              <div v-for="row in sceneDrafts" :key="row.key" class="field">
                <label class="field-label">{{ row.label }}</label>
                <n-input
                  v-model:value="row.scene"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 6 }"
                  placeholder="画面描述"
                />
              </div>
              <n-space :size="8">
                <n-button
                  size="small"
                  type="primary"
                  :loading="sceneSubmitting"
                  :disabled="!baseStyleLocked || sceneSubmitting"
                  @click="submitSceneDrafts"
                >按此生成</n-button>
                <n-button
                  size="small"
                  :loading="promptDepositBusy"
                  :disabled="Boolean(promptManageDenied) || !sceneDrafts.length"
                  :title="promptManageDenied || '把当前封面/配图提示词存入统一提示词库'"
                  @click="depositAiPrompts"
                >存入提示词库</n-button>
              </n-space>
              <p v-if="!baseStyleLocked" class="ai-image-settings-error">{{ baseStyleHint }}</p>
              <p class="ai-image-settings-status">
                若所选供应商不支持独立负向字段，负向词会以文本并入正向提示词并在生成结果中标注。
              </p>
            </section>
              </div>
              <template #footer>
                <div class="studio-ai-settings-footer">
                  <span>设置会用于下一次生成，关闭不会丢失已填写的内容</span>
                  <n-button type="primary" @click="aiSettingsOpen = false">完成设置</n-button>
                </div>
              </template>
            </n-modal>
          </div>
        </section>

        <!-- 写作流程 Tab -->
        <section
          v-else-if="rightTab === 'flow'"
          id="studio-panel-flow"
          class="studio-tab-panel"
          role="tabpanel"
          aria-labelledby="studio-tab-flow"
        >
          <div class="row row-tight studio-tool-head">
            <div>
              <span class="studio-section-label studio-section-label-flush">写作流程</span>
              <p class="studio-tool-description">{{ flow?.skillName || '按当前写作风格的步骤检查这篇稿' }}</p>
              <p class="flow-source-hint">按编辑器当前内容实时检查（含未保存草稿）</p>
            </div>
            <n-button quaternary size="small" :loading="flowLoading" :disabled="!detail" @click="loadWritingFlow">
              重新检查
            </n-button>
          </div>
          <n-alert v-if="flowError" type="error" :bordered="false" style="margin-top: var(--space-3)">{{ flowError }}</n-alert>
          <div v-else-if="!detail" class="empty" style="padding: var(--space-5) var(--space-2)">
            <span class="empty-title">先打开一篇稿件</span>
          </div>
          <div v-else-if="flowLoading && !flow" class="stack" style="margin-top: var(--space-3)">
            <div v-for="i in 4" :key="i" class="skeleton" style="height: 52px" />
          </div>
          <ol v-else-if="flow" class="flow-steps">
            <li v-for="step in flow.steps" :key="step.tool" class="flow-step" :class="flowStepTone(step)">
              <span class="flow-index">{{ String(step.index).padStart(2, '0') }}</span>
              <div class="flow-main">
                <div class="flow-title-row">
                  <div class="flow-title">{{ step.label }}</div>
                  <button
                    v-if="FLOW_RULE_FOCUS[step.tool]"
                    type="button"
                    class="flow-config-link"
                    @click="goFlowRules(step.tool)"
                  >配置规则</button>
                </div>
                <div class="flow-meta">{{ flowStepSummary(step) }}</div>

                <!-- 审稿：逐条规则给出通过情况，而不是只列未通过项 -->
                <ul v-if="step.tool === 'review_article' && step.result?.rules?.length" class="flow-rule-list">
                  <li
                    v-for="rule in step.result.rules"
                    :key="rule.id"
                    class="flow-rule"
                    :class="rule.ok === true ? 'is-pass' : rule.ok === false ? 'is-fail' : 'is-skip'"
                  >
                    <span class="flow-rule-icon">{{ rule.ok === true ? '✓' : rule.ok === false ? '✗' : '—' }}</span>
                    <span class="flow-rule-text">{{ flowRuleLabel(rule) }}</span>
                    <span v-if="rule.ok === null" class="flow-rule-msg">配置无效，已跳过</span>
                    <span v-else-if="rule.message" class="flow-rule-msg">{{ rule.message }}</span>
                  </li>
                </ul>

                <template v-else-if="step.tool === 'check_ai_tone'">
                  <!-- 词表概览：说明这一项检查依据的是哪些词条 -->
                  <p v-if="step.result?.lexicon" class="flow-lexicon">
                    <span
                      v-for="chip in flowLexiconChips(step.result.lexicon)"
                      :key="chip.key"
                      class="flow-lexicon-chip"
                    >{{ chip.text }}</span>
                  </p>
                  <ul v-if="step.result?.hits?.length" class="flow-list">
                    <li v-for="(hit, index) in step.result.hits.slice(0, 6)" :key="`${hit.offset}-${index}`">
                      「{{ hit.excerpt }}」
                    </li>
                  </ul>
                </template>

                <p
                  v-else-if="step.tool === 'extract_facts' && step.result?.numbers?.length"
                  class="flow-meta"
                >
                  {{ step.result.numbers.slice(0, 6).join('、') }}
                </p>
                <p
                  v-else-if="step.tool === 'plan_images' && step.result?.policy"
                  class="flow-meta"
                >
                  正文图上限 {{ step.result.policy.maxInlineImages ?? 3 }} 张 ·
                  封面 {{ step.result.policy.coverAspectRatio || '3:4' }} ·
                  正文 {{ step.result.policy.inlineAspectRatio || '1:1' }}
                </p>
              </div>
            </li>
          </ol>
        </section>

        <!-- 原创检测 Tab -->
        <section
          v-else-if="rightTab === 'originality'"
          id="studio-panel-originality"
          class="studio-tab-panel"
          role="tabpanel"
          aria-labelledby="studio-tab-originality"
        >
          <div class="row studio-tool-head">
            <div>
              <span class="studio-section-label studio-section-label-flush">原创度检测</span>
              <p class="studio-tool-description">检测文章与已有稿件的重合度及多维度评分</p>
            </div>
            <div class="spacer" />
            <n-button quaternary size="small" :aria-label="originalityOpen ? '收起' : '展开'" @click="originalityOpen = !originalityOpen">
              {{ originalityOpen ? '▾' : '▸' }}
            </n-button>
          </div>

          <div v-if="originalityOpen" class="studio-tool-stack">
            <n-alert v-if="originalityError" type="error" :bordered="false">{{ originalityError }}</n-alert>
            
            <!-- 比对范围 -->
            <div class="originality-options">
              <n-select
                v-model:value="compareScope"
                :options="[
                  { label: '与全部稿件比对', value: 'all' },
                  { label: '只与我的稿件比对', value: 'own' }
                ]"
                size="small"
                style="flex: 1"
              />
            </div>
            
            <div v-if="originalityLoading" class="stack" style="margin-top: var(--space-3)">
              <div class="skeleton" style="height: 200px" />
              <p style="text-align: center; margin-top: var(--space-2); font-size: 12px; color: var(--text-secondary)">
                正在分词比对，长文需要几秒…
              </p>
            </div>
            
            <div v-else-if="!originalityAnalysis" style="text-align: center; padding: var(--space-5) var(--space-2)">
              <p style="margin-bottom: var(--space-3); color: var(--text-secondary)">点击下方按钮开始检测</p>
              <n-button type="primary" size="small" :loading="originalityLoading" @click="analyzeOriginality">
                开始分析
              </n-button>
            </div>
            
            <div v-else class="originality-result">
              <!-- 结论卡片 -->
              <div class="orig-summary" :class="[`level-${originalityAnalysis.level}`]">
                <div class="orig-summary-top">
                  <span class="orig-total">{{ originalityAnalysis.totalScore }}</span>
                  <span class="orig-level">{{ originalityLevelLabel }}</span>
                  <span v-if="originalityAnalysis.cached" class="orig-badge">复用上次结果</span>
                  <span v-else-if="originalityAnalysis.processingTimeMs" class="orig-cost">
                    {{ originalityAnalysis.processingTimeMs }}ms
                  </span>
                </div>
                <p class="orig-summary-text">{{ originalityAnalysis.summary }}</p>
              </div>

              <!-- 五维度评分 -->
              <div class="originality-scores">
                <div v-for="dim in originalityDimensions" :key="dim.key" class="score-item">
                  <div class="score-item-head">
                    <span class="score-label">{{ dim.label }}</span>
                    <span class="score-value" :class="[`tone-${dim.tone}`]">{{ dim.display }}</span>
                  </div>
                  <div class="score-bar">
                    <div
                      class="score-fill"
                      :class="[`tone-${dim.tone}`]"
                      :style="{ width: dim.percent + '%' }"
                    />
                  </div>
                  <span class="score-hint">权重 {{ dim.weight }}% · {{ dim.hint }}</span>
                </div>
              </div>

              <p v-if="originalityAnalysis.corpusSize === 0" class="orig-note">
                库里还没有其它稿件可供比对，相似度与观点独特性不参与计分。
              </p>
              <p v-else class="orig-note">
                与库内 {{ originalityAnalysis.corpusSize }} 篇稿件比对<template
                  v-if="originalityAnalysis.selfVersionCount"
                >，同稿的 {{ originalityAnalysis.selfVersionCount }} 个历史版本未计入</template>。
              </p>

              <!-- 风险提示 -->
              <div v-if="originalityAnalysis.details.riskFlags.length" class="originality-section">
                <h4 class="originality-section-title">风险提示</h4>
                <div class="risk-list">
                  <div
                    v-for="(risk, idx) in originalityAnalysis.details.riskFlags"
                    :key="idx"
                    class="risk-item"
                    :class="[`severity-${risk.severity}`]"
                  >
                    <span class="risk-dot" />
                    <div class="risk-body">
                      <div class="risk-title">{{ risk.position }}</div>
                      <div class="risk-reason">{{ risk.reason }}</div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- 相似文章 -->
              <div v-if="originalityAnalysis.details.similarArticles.length" class="originality-section">
                <h4 class="originality-section-title">最相似的稿件</h4>
                <div
                  v-for="article in originalityAnalysis.details.similarArticles"
                  :key="article.id"
                  class="similar-article"
                >
                  <div class="similar-article-title">{{ article.title }}</div>
                  <div class="similar-article-meta">{{ (article.similarity * 100).toFixed(0) }}%</div>
                </div>
              </div>

              <!-- 重合片段 -->
              <div v-if="originalitySegments.length" class="originality-section">
                <h4 class="originality-section-title">重合片段</h4>
                <div v-for="(seg, idx) in originalitySegments" :key="idx" class="segment-item">
                  <div class="segment-head">
                    <span>{{ seg.position }}</span>
                    <span class="segment-ratio">{{ Math.round(seg.similarity * 100) }}%</span>
                  </div>
                  <p class="segment-line"><span class="segment-tag">本文</span>{{ seg.targetText }}</p>
                  <p class="segment-line segment-line-muted">
                    <span class="segment-tag">对方</span>{{ seg.sourceText }}
                  </p>
                </div>
              </div>

              <!-- 关键观点 -->
              <div v-if="originalityAnalysis.details.keyPoints.length" class="originality-section">
                <h4 class="originality-section-title">关键观点</h4>
                <div class="point-chips">
                  <span
                    v-for="point in originalityAnalysis.details.keyPoints"
                    :key="point.point"
                    class="point-chip"
                    :class="[point.sources ? 'is-shared' : 'is-new']"
                    :title="`库内 ${point.sources} 篇提过，新颖度 ${point.novelty}`"
                  >
                    {{ point.point }}
                    <em v-if="!point.sources">新</em>
                  </span>
                </div>
              </div>

              <!-- 改进建议 -->
              <div v-if="originalityAnalysis.recommendations.length" class="originality-section">
                <h4 class="originality-section-title">改进建议</h4>
                <ul class="recommendations-list">
                  <li v-for="(rec, idx) in originalityAnalysis.recommendations" :key="idx">{{ rec }}</li>
                </ul>
              </div>

              <!-- 操作 -->
              <div class="originality-actions">
                <n-space :size="8" style="margin-bottom: var(--space-3)">
                  <n-button size="small" type="primary" :loading="originalityLoading" @click="analyzeOriginality">
                    重新分析
                  </n-button>
                  <n-dropdown :options="reportExportOptions" @select="handleReportExport">
                    <n-button size="small">导出报告</n-button>
                  </n-dropdown>
                  <n-button
                    v-if="analysisHistoryCount > 0"
                    size="small"
                    text
                    @click="showAnalysisHistory = !showAnalysisHistory"
                  >
                    {{ showAnalysisHistory ? '隐藏' : '显示' }}历史 ({{ analysisHistoryCount }})
                  </n-button>
                </n-space>
              </div>

              <!-- 检测历史 -->
              <div v-if="showAnalysisHistory && analysisList.length > 0" class="originality-section">
                <h4 class="originality-section-title">检测历史</h4>
                <div class="history-timeline">
                  <div v-for="item in analysisList" :key="item.analysisId" class="history-item">
                    <div class="history-marker" :class="[`level-${item.level}`]" />
                    <div class="history-content">
                      <div class="history-score">
                        <span class="history-value">{{ item.totalScore }}</span>
                        <span class="history-level">{{ LEVEL_LABEL[item.level] || item.level }}</span>
                        <span class="history-time">{{ formatDateTime(item.analyzedAt) }}</span>
                      </div>
                      <div class="history-meta">
                        相似度 {{ item.corpusSize ? item.similarity + '%' : '样本不足' }} · 比对
                        {{ item.corpusSize }} 篇
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- 灵感 Tab -->
        <section
          v-else-if="rightTab === 'inspiration'"
          id="studio-panel-inspiration"
          class="studio-tab-panel"
          role="tabpanel"
          aria-labelledby="studio-tab-inspiration"
        >
          <div class="row row-tight studio-tool-head">
            <div>
              <span class="studio-section-label studio-section-label-flush">热点灵感</span>
              <p class="studio-tool-description">挑一个热点，直接开写</p>
            </div>
            <n-button quaternary size="small" :loading="hotLoading" @click="loadHotTopics">
              刷新
            </n-button>
          </div>

          <n-alert v-if="hotError" type="error" :bordered="false" style="margin-top: var(--space-3)">{{ hotError }}</n-alert>
          <div v-if="hotLoading && !hotTopics.length" class="stack" style="margin-top: var(--space-3)">
            <div v-for="i in 4" :key="i" class="skeleton" style="height: 58px" />
          </div>
          <div v-else-if="!hotStatus.configured" class="empty" style="padding: var(--space-5) var(--space-2)">
            <span class="empty-title">热点源未配置</span>
            <span>{{ hotStatus.note || '配置热点源后，这里会显示最新热点。' }}</span>
          </div>
          <div v-else-if="!hotTopics.length" class="empty" style="padding: var(--space-5) var(--space-2)">
            <span class="empty-title">暂时没有热点</span>
            <span>热点源当前没有返回内容，请稍后刷新。</span>
          </div>
          <div v-else class="inspiration-list">
            <div v-for="(item, index) in hotTopics" :key="`${item.title}-${index}`" class="inspiration-item">
              <div class="inspiration-title">
                <span class="hot-rank">{{ index + 1 }}</span>
                <span>{{ item.title }}</span>
              </div>
              <div class="row row-tight inspiration-actions">
                <span v-if="item.hot" class="hot-value">{{ item.hot }}</span>
                <n-button
                  size="small"
                  type="primary"
                  :loading="hotActionId === item.title"
                  @click="createDraftFromHot(item)"
                >用它写稿</n-button>
              </div>
            </div>
          </div>
        </section>

        <!-- 配图 Tab -->
        <section
          v-else-if="rightTab === 'image'"
          id="studio-panel-image"
          class="studio-tab-panel"
          role="tabpanel"
          aria-labelledby="studio-tab-image"
        >
          <div class="row studio-tool-head">
            <div>
              <span class="studio-section-label studio-section-label-flush">AI 配图</span>
              <p class="studio-tool-description">
                {{ imgProvider.available
                  ? `使用 ${selectedImgProvider?.label || imgProvider.modelCode || '已配置模型'} 生成配图`
                  : '未配置生图供应商，请在「模型管理」添加支持 image.generate 的模型' }}
              </p>
            </div>
          </div>

          <div class="studio-tool-stack">
            <div class="field">
              <label class="field-label">基础风格</label>
              <p class="ai-image-settings-status">
                {{ detail?.item.baseStyleDeleted
                  ? `${detail.item.baseStyleName || '基础风格'}（已删除）`
                  : (detail?.item.baseStyleName || '这篇稿还没有基础风格') }}
              </p>
              <n-button quaternary size="tiny" @click="rightTab = 'ai'">到 AI 面板修改</n-button>
              <p v-if="!baseStyleLocked" class="ai-image-settings-error">{{ baseStyleHint }}</p>
            </div>
            <div class="field">
              <label class="field-label">生图模型</label>
              <n-select
                v-model:value="imgModelId"
                :options="imgModelOptions"
                :loading="!imgProviderLoaded"
                :disabled="imgBusy || !imgModelOptions.length"
                :consistent-menu-width="false"
                placeholder="没有可用的生图模型"
                aria-label="选择配图的生图模型"
              />
            </div>
            <!-- Prompt 输入 -->
            <div class="field">
              <div class="row" style="justify-content: space-between; align-items: center; margin-bottom: var(--space-1)">
                <label class="field-label" for="img-prompt" style="margin: 0">图像描述</label>
                <n-button
                  quaternary
                  size="tiny"
                  class="img-suggest-btn"
                  :loading="imgBusy && !imgJobs.length"
                  :disabled="imgBusy || !detail"
                  @click="suggestImagePrompt"
                >✦ 从正文提炼</n-button>
              </div>
              <n-select
                :options="inlineStyles.map((style) => ({ value: style.id, label: style.name }))"
                :disabled="!inlineStyles.length"
                placeholder="配图模板（选中后填入下方描述）"
                aria-label="选择配图模板"
                style="margin-bottom: var(--space-2)"
                @update:value="applyInlineTemplate"
              />
              <n-input
                id="img-prompt"
                v-model:value="imgPrompt"
                type="textarea"
                :autosize="{ minRows: 4, maxRows: 8 }"
                style="font-size: 12px"
                placeholder="描述你想要的画面，例如：A serene mountain landscape at golden hour, soft mist, cinematic lighting"
              />
            </div>

            <!-- 比例 + 数量 -->
            <div class="row" style="gap: var(--space-3)">
              <div class="field" style="flex: 1">
                <label class="field-label" for="img-ratio">比例</label>
                <div class="ai-length-group">
                  <button
                    v-for="opt in ASPECT_OPTIONS"
                    :key="opt.value"
                    class="ai-length-btn"
                    :class="{ 'is-active': imgAspectRatio === opt.value }"
                    @click="imgAspectRatio = opt.value"
                  >{{ opt.label }}</button>
                </div>
              </div>
              <div class="field" style="flex-shrink: 0; min-width: 72px">
                <label class="field-label" for="img-count">数量</label>
                <n-select id="img-count" v-model:value="imgCount" :options="countOptions" style="min-width: 88px" />
              </div>
            </div>

            <!-- 操作按钮 -->
            <n-space :size="8" class="studio-actions">
              <n-button
                size="small"
                type="primary"
                :loading="imgBusy && imgJobs.some(j => ['queued','running'].includes(j.status))"
                :disabled="imgBusy || !imgPrompt.trim() || !baseStyleLocked"
                @click="generateImages"
              >
                生成配图
              </n-button>
            </n-space>

            <n-alert v-if="imgError" type="error" :bordered="false">{{ imgError }}</n-alert>

            <!-- 任务进度 -->
            <div
              v-if="imgJobs.some(j => ['queued','running'].includes(j.status))"
              class="img-job-progress"
            >
              <span class="img-job-label">生成中，请稍候…</span>
              <div class="img-job-bar">
                <div
                  class="img-job-fill"
                  :style="{ width: `${imgJobs.find(j => ['queued','running'].includes(j.status))?.progress || 10}%` }"
                />
              </div>
            </div>

            <!-- 生成结果 -->
            <div v-if="imgResults.length" class="img-results-section">
              <span class="studio-section-label" style="margin: 0 0 var(--space-2)">生成结果</span>
              <div class="img-grid">
                <div v-for="result in imgResults" :key="result.assetId" class="img-card">
                  <div class="img-card-preview">
                    <img
                      v-if="result.blobUrl"
                      :src="result.blobUrl"
                      :alt="`生成配图 ${result.assetId}`"
                      class="img-card-img"
                    />
                    <div v-else class="img-card-placeholder">加载中…</div>
                  </div>
                  <n-space :size="4" class="img-card-actions" style="width: 100%">
                    <n-button
                      quaternary
                      size="tiny"
                      style="flex:1"
                      :disabled="isImageInBody(result)"
                      @click="insertImageToBody(result)"
                    >{{ isImageInBody(result) ? '已在正文' : '插入正文' }}</n-button>
                    <n-button quaternary size="tiny" title="复制链接" @click="copyImageUrl(result)">🔗</n-button>
                  </n-space>
                </div>
              </div>
            </div>

            <!-- 失败任务提示 -->
            <n-alert
              v-for="job in imgJobs.filter(j => j.status === 'failed' || j.status === 'retrying')"
              :key="job.id"
              type="error"
              :bordered="false"
              class="img-job-error"
            >
              <n-space align="center" justify="space-between" style="width: 100%">
                <span>{{ job.label || '配图' }}生成失败：{{ job.error || '未知错误' }}</span>
                <n-button quaternary size="tiny" :loading="job.status === 'retrying'" @click="retryImageJob(job)">
                  重试
                </n-button>
              </n-space>
            </n-alert>
          </div>
        </section>
      </aside>
    </div>

    <!-- 发布抽屉 -->
    <PublishDrawer
      :open="publishDrawerOpen"
      :title="draftTitle"
      :body="draftBody"
      :content-id="detail?.item.id || ''"
      :cover-asset-id="publishCoverAssetId"
      :inline-asset-ids="publishInlineAssetIds"
      @close="publishDrawerOpen = false"
      @published="() => toast('草稿已推送到公众号草稿箱，请到后台预览并群发。')"
    />

    <!-- 稿件库：全量检索 + 归档管理；归档/恢复后刷新侧栏 -->
    <DraftLibraryModal v-model:show="libraryOpen" @open="openItem" @archived="onLibraryArchived" />

    <n-modal
      v-model:show="showVariant"
      preset="card"
      title="生成平台版本"
      style="width: 560px"
      :bordered="false"
    >
      <p class="field-hint" style="margin-top: 0">平台版本独立可编辑，提交发布前仍需人工审核。</p>
      <n-form label-placement="top">
        <n-form-item label="平台">
          <n-select v-model:value="variantForm.platform" :options="platformOptions" />
        </n-form-item>
        <n-form-item label="标题">
          <n-input v-model:value="variantForm.title" />
        </n-form-item>
        <n-form-item label="正文">
          <n-input v-model:value="variantForm.body" type="textarea" :autosize="{ minRows: 6, maxRows: 14 }" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary @click="showVariant = false">取消</n-button>
          <n-button type="primary" @click="saveVariant">保存版本</n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal
      :show="!!deletingItem"
      preset="card"
      title="删除稿件"
      style="width: 480px"
      :bordered="false"
      @update:show="(v) => { if (!v) deletingItem = null }"
    >
      <p class="field-hint" style="margin-top: 0">
        「{{ deletingItem?.title }}」将被彻底删除，且不可恢复。
      </p>
      <p class="field-hint" style="margin: 0">
        会一并清理：所有版本、平台版本、审阅评论、发布任务与发布记录、对应的数据统计，以及日历里的排期。
      </p>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary @click="deletingItem = null">取消</n-button>
          <n-button type="error" :loading="deleting" @click="confirmDelete">确认删除</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.studio-page {
  gap: var(--space-4);
}
.studio-page-head {
  align-items: center;
  min-height: 58px;
  padding: 0 var(--space-1) var(--space-2);
}
.studio-page-heading {
  min-width: 0;
}
.studio-page-heading .page-title {
  letter-spacing: -0.025em;
}
.studio-page-heading .page-sub {
  margin-top: var(--space-1);
}
.studio-page-actions {
  flex-shrink: 0;
  padding: var(--space-1);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--color-surface) 76%, transparent);
}
.studio-page-actions .btn {
  border-color: transparent;
}
.studio-page-actions .btn-ghost:hover {
  border-color: var(--color-border);
  background: var(--color-surface);
}
.studio-grid {
  display: grid;
  grid-template-columns: minmax(240px, 260px) minmax(0, 1fr) minmax(300px, 340px);
  gap: var(--space-4);
  align-items: start;
  /* 编辑器列撑满：设最小行高，让中间列有足够空间 */
  min-height: calc(100vh - 130px);
}
.studio-grid.drafts-collapsed {
  grid-template-columns: 56px minmax(0, 1fr) minmax(300px, 340px);
}
.studio-drafts,
.studio-editor,
.studio-tools {
  min-width: 0;
}
.studio-drafts,
.studio-tools {
  position: relative;
  top: auto;
  max-height: none;
  border-color: color-mix(in srgb, var(--color-border) 78%, transparent);
  background: color-mix(in srgb, var(--color-surface) 82%, var(--color-bg));
  box-shadow: none;
}
.studio-empty-state {
  display: grid;
  min-height: min(620px, calc(100vh - 190px));
  place-items: center;
  border-style: dashed;
  background: color-mix(in srgb, var(--color-surface) 72%, var(--color-bg));
  box-shadow: none;
}
.studio-empty-state .empty {
  max-width: 360px;
  text-align: center;
}
.studio-empty-mark {
  display: grid;
  width: 48px;
  height: 48px;
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--color-accent) 24%, var(--color-border));
  border-radius: 15px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-size: 22px;
}

/* ---------- 左侧稿件栏 ---------- */
.studio-drafts {
  padding: var(--space-3);
  overflow: visible;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border-strong) transparent;
}
.studio-drafts-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  min-height: 36px;
  margin-bottom: var(--space-3);
  padding: 0 var(--space-1);
}
.studio-drafts-heading {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: var(--space-2);
}
.studio-panel-title {
  color: var(--color-text-primary);
  font-size: var(--font-sm);
  font-weight: 600;
  letter-spacing: 0.01em;
}
.studio-panel-count {
  display: grid;
  min-width: 20px;
  height: 20px;
  place-items: center;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.studio-new-draft {
  width: 100%;
  margin-bottom: var(--space-3);
  border: 1px solid var(--color-accent);
  border-radius: 8px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-weight: 500;
  transition: background-color 0.2s ease;
}
.studio-new-draft:hover {
  background: var(--color-accent);
  color: var(--color-on-accent);
}
.studio-library-link {
  display: block;
  width: 100%;
  margin: calc(-1 * var(--space-1)) 0 var(--space-3);
  padding: 2px var(--space-1);
  border: 0;
  background: none;
  color: var(--color-text-muted);
  font: inherit;
  font-size: var(--font-xs);
  text-align: center;
  cursor: pointer;
  transition: color 150ms ease;
}
.studio-library-link:hover {
  color: var(--color-accent);
}
.studio-draft-search {
  margin-bottom: var(--space-2);
}
/* 稿件分组：组头在面板内滚动时吸顶；"更早"可折叠 */
.studio-draft-group + .studio-draft-group {
  margin-top: var(--space-2);
}
.studio-draft-group-head {
  position: sticky;
  /* 负 top + 等量 padding：吸顶时贴住面板边缘，不留透缝 */
  top: calc(-1 * var(--space-3));
  z-index: 1;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  margin: 0 calc(-1 * var(--space-1));
  padding: calc(4px + var(--space-3)) var(--space-1) 4px;
  border: 0;
  background: color-mix(in srgb, var(--color-surface) 90%, var(--color-bg));
  backdrop-filter: blur(4px);
  color: var(--color-text-muted);
  font-family: inherit;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  cursor: pointer;
}
.studio-draft-group-count {
  font-variant-numeric: tabular-nums;
}
.studio-draft-group-caret {
  margin-left: auto;
  font-size: 10px;
}
.studio-draft-group.is-collapsed .studio-draft-group-count {
  margin-left: auto;
}
.studio-draft-search-empty {
  margin: var(--space-2) var(--space-1) 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  text-align: center;
}
.studio-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.studio-list .list-row {
  position: relative;
  padding: 7px 8px;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  transition: border-color 150ms ease, background 150ms ease, box-shadow 150ms ease;
}
.studio-list .list-row:hover {
  background: var(--color-surface-raised);
}
.item-row.is-active {
  border-color: color-mix(in srgb, var(--color-accent) 16%, transparent);
  background: var(--color-accent-soft);
  box-shadow: inset 3px 0 0 var(--color-accent);
}
.item-row.is-active .list-title {
  color: var(--color-text-primary);
  font-weight: 600;
}
.item-open {
  flex: 1;
  min-width: 0;
  padding: 0;
  border: none;
  background: none;
  text-align: left;
  cursor: pointer;
}
.item-open .list-title {
  overflow: hidden;
  color: var(--color-text-primary);
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-open .list-meta {
  margin-top: 2px;
  color: var(--color-text-muted);
  font-size: 11px;
}
.item-row.is-active .item-open .list-meta {
  color: var(--color-text-secondary);
}
.item-delete {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--color-text-secondary);
  opacity: 0;
  transition: opacity 150ms ease, color 150ms ease, background 150ms ease;
}
.item-row:hover .item-delete,
.item-row:focus-within .item-delete,
.item-row.is-active .item-delete,
.item-delete:focus-visible {
  opacity: 1;
}
.item-delete:hover {
  background: color-mix(in srgb, var(--color-danger) 10%, transparent);
  color: var(--color-danger);
}
.studio-versions {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}
.studio-versions .studio-section-label {
  margin: 0 var(--space-1) var(--space-2);
}
.studio-versions .version-row {
  padding: var(--space-2);
}
.studio-versions .version-row .btn {
  min-height: 30px;
  padding: 0 var(--space-2);
}
.studio-version-title {
  color: var(--color-text-secondary);
  font-size: var(--font-sm);
  font-weight: 600;
}
.studio-section-label {
  margin: var(--space-5) 0 var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--font-xs);
  font-weight: 600;
  letter-spacing: 0.04em;
}
.studio-section-label-flush {
  margin: 0;
}
.flow-steps {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: var(--space-3) 0 0;
  padding: 0;
  list-style: none;
}
.flow-step {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.flow-step.is-ok {
  border-color: color-mix(in srgb, var(--color-success) 45%, var(--color-border));
}
.flow-step.is-warn {
  border-color: color-mix(in srgb, var(--color-warning, var(--color-danger)) 45%, var(--color-border));
}
.flow-index {
  color: var(--color-text-muted);
  font-size: var(--font-xs);
  font-variant-numeric: tabular-nums;
}
.flow-main {
  min-width: 0;
}
.flow-title {
  font-size: var(--font-sm);
  font-weight: 600;
}
.flow-meta,
.flow-list {
  margin: var(--space-1) 0 0;
  color: var(--color-text-muted);
  font-size: var(--font-xs);
}
.flow-list {
  padding-left: 1.1em;
}
.flow-source-hint {
  margin: 2px 0 0;
  color: var(--color-text-muted);
  font-size: var(--font-xs);
}
.flow-title-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}
.flow-config-link {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--color-primary, #6a5cff);
  font-size: var(--font-xs);
  cursor: pointer;
  white-space: nowrap;
}
.flow-config-link:hover {
  text-decoration: underline;
}
.flow-rule-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: var(--space-2) 0 0;
  padding: 0;
  list-style: none;
  font-size: var(--font-xs);
}
.flow-rule {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  min-width: 0;
}
.flow-rule-icon {
  flex: none;
  width: 1.2em;
  text-align: center;
  font-weight: 700;
}
.flow-rule.is-pass .flow-rule-icon {
  color: var(--color-success);
}
.flow-rule.is-fail .flow-rule-icon,
.flow-rule.is-fail .flow-rule-msg {
  color: var(--color-danger);
}
.flow-rule.is-skip .flow-rule-icon {
  color: var(--color-text-muted);
}
.flow-rule-text {
  color: var(--color-text-secondary);
  overflow-wrap: anywhere;
}
.flow-rule.is-skip .flow-rule-text {
  color: var(--color-text-muted);
}
.flow-rule-msg {
  color: var(--color-text-muted);
  overflow-wrap: anywhere;
}
.flow-lexicon {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin: var(--space-2) 0 0;
}
.flow-lexicon-chip {
  padding: 1px 6px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-text-muted);
  font-size: var(--font-xs);
  white-space: nowrap;
}

/* 折叠态：窄轨道，仅保留把手与计数 */
.studio-drafts.is-collapsed {
  display: grid;
  place-items: start center;
  padding: var(--space-3) 0;
  border-color: transparent;
  background: transparent;
  box-shadow: none;
}
.studio-drafts-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
}
.studio-rail-toggle {
  color: var(--color-text-secondary);
}
.studio-rail-toggle:hover {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}
.studio-rail-count {
  display: grid;
  min-width: 24px;
  height: 24px;
  place-items: center;
  padding: 0 5px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
  font-size: var(--font-xs);
  font-weight: 600;
}
.studio-rail-label {
  color: var(--color-text-secondary);
  font-size: var(--font-xs);
  letter-spacing: 0.18em;
  writing-mode: vertical-rl;
}

/* ---------- 中间编辑器 ---------- */
.studio-editor {
  display: flex;
  min-height: calc(100vh - 160px);
  flex-direction: column;
  gap: 0;
  overflow: hidden;
  border-color: color-mix(in srgb, var(--color-border) 88%, transparent);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  box-shadow: var(--shadow-md);
}
.studio-doc-head {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  padding: 24px 28px 14px;
}
.studio-title-input {
  flex: 1;
  min-width: 0;
  padding: var(--space-1) var(--space-2);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-primary);
  font-family: inherit;
  font-size: clamp(17px, 1.4vw, 20px);
  font-weight: 650;
  letter-spacing: -0.02em;
  line-height: 1.4;
}
.studio-title-input:hover {
  background: var(--color-surface-raised);
}
.studio-title-input::placeholder {
  color: var(--color-text-muted);
  font-weight: 500;
}
.studio-doc-meta {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: var(--space-2);
  padding-top: var(--space-1);
}
.studio-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px var(--space-2);
  border: 0;
  border-radius: 999px;
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
  font-size: 11px;
  white-space: nowrap;
}
.studio-chip.is-ok,
.studio-chip.is-dirty {
  background: transparent;
}
.studio-chip.is-ok {
  color: var(--color-success);
}
.studio-chip.is-dirty {
  color: var(--color-warning);
}
.studio-chip.is-ok::before,
.studio-chip.is-dirty::before {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  content: '';
}
.studio-chip.is-error {
  color: var(--color-danger);
  background: transparent;
}
.studio-chip.is-error::before {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  content: '';
}
.studio-doc-body {
  width: 100%;
  min-height: min(62vh, 720px);
  flex: 1;
  padding: var(--space-3) 30px var(--space-5);
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--color-text-primary);
  font-size: 14px;
  line-height: 1.85;
  resize: vertical;
}
.studio-doc-body:hover {
  background: transparent;
}
.studio-doc-body:focus {
  background: color-mix(in srgb, var(--color-accent-soft) 20%, transparent);
  outline-offset: -4px;
}
.studio-doc-body::placeholder {
  color: var(--color-text-muted);
}
.studio-doc-hint {
  margin: 0;
  padding: 0 30px var(--space-5);
  color: var(--color-text-secondary);
  font-size: 11px;
}
.studio-doc-variants {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}
.studio-doc-variants.is-collapsed {
  padding-bottom: var(--space-2);
}
.studio-variants-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
.studio-variants-head .studio-section-label {
  margin: 0 var(--space-1) var(--space-2);
}
.studio-doc-variants .studio-list {
  margin-top: var(--space-1);
}
.studio-doc-variants .studio-list .list-row {
  background: var(--color-surface);
}

.preview-frame {
  margin-top: var(--space-2);
  padding: 6px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface-raised);
}
.preview-frame-inner {
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: #fff;
}
.preview-frame-iframe {
  display: block;
  width: 100%;
  height: 340px;
  border: 0;
}

/* ---------- 右侧工具面板 ---------- */
.studio-tools {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}
.studio-panel-tabs {
  position: relative;
  z-index: 2;
  display: flex;
  width: 100%;
  flex-shrink: 0;
  gap: var(--space-1);
  margin: 0;
  padding: 6px;
  border: 0;
  border-bottom: 1px solid var(--color-border);
  border-radius: 0;
  background: var(--color-surface);
}
.studio-panel-tabs .segmented-item {
  flex: 1;
  min-height: 34px;
  padding: 0 var(--space-2);
  border-radius: 7px;
  color: var(--color-text-secondary);
  font-size: var(--font-xs);
}
.studio-panel-tabs .segmented-item:hover:not(:disabled):not(.is-active) {
  background: var(--color-surface-raised);
  color: var(--color-text-primary);
}
.studio-panel-tabs .segmented-item:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
.studio-panel-tabs .segmented-item.is-active {
  background: var(--color-accent-soft);
  color: var(--color-accent);
  box-shadow: none;
  font-weight: 600;
}
.studio-tab-panel {
  min-height: 0;
  padding: var(--space-4);
  overflow: visible;
  scrollbar-width: thin;
  scrollbar-color: var(--color-border-strong) transparent;
}
.studio-tool-head {
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}
.studio-tool-description {
  margin: var(--space-1) 0 0;
  color: var(--color-text-secondary);
  font-size: 11px;
  line-height: 1.55;
}
.studio-tool-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.studio-tool-stack .field-error {
  margin: 0;
}
.studio-ai-quick-actions {
  position: sticky;
  bottom: 0;
  z-index: 2;
  display: grid;
  gap: 10px;
  margin: 0 -4px -4px;
  padding: 12px 4px 4px;
  background: linear-gradient(180deg, transparent, var(--color-surface) 24%);
}
.studio-ai-settings-body { display: flex; flex-direction: column; gap: 16px; max-height: min(60vh, 600px); overflow-y: auto; padding-right: 8px; }
.studio-ai-settings-body .ai-image-settings { margin: 0; }
.studio-ai-settings-footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.studio-ai-settings-footer span { color: var(--color-text-muted); font-size: 12px; }

.studio-ai-prompt {
  min-height: 108px;
  line-height: 1.7;
}
.studio-actions {
  gap: var(--space-2);
}
.studio-model-usage {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 11px;
}
.ai-image-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0 0 var(--space-3) 28px;
  padding: var(--space-3);
  border-left: 2px solid var(--color-border);
  background: var(--color-surface-muted, var(--color-surface));
}
.ai-image-settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3);
}
.ai-image-settings-status,
.ai-image-settings-error {
  margin: 0;
  font-size: 11px;
}
.ai-image-settings-status { color: var(--color-text-secondary); }
.ai-image-settings-error { color: var(--color-danger); }
.studio-ai-output {
  min-width: 0;
  margin-bottom: var(--space-4);
  overflow-wrap: anywhere;
  padding: var(--space-3);
  border-left: 3px solid var(--color-accent);
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
  background: var(--color-accent-soft);
}
.studio-ai-output-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-2);
}
.studio-ai-status {
  margin: var(--space-2) 0;
  color: var(--color-text-secondary);
  font-size: var(--font-xs);
}
.studio-ai-output-label {
  display: block;
  margin-bottom: var(--space-2);
  color: var(--color-accent);
  font-size: 11px;
  font-weight: 600;
}
.studio-ai-output-content {
  max-height: min(300px, 40vh);
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
}
.studio-ai-output-markdown {
  padding: var(--space-3);
  color: var(--color-text-primary);
  font-size: var(--font-sm);
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.studio-ai-output-markdown :deep(h1),
.studio-ai-output-markdown :deep(h2),
.studio-ai-output-markdown :deep(h3) {
  margin: 0.6em 0 0.3em;
  font-size: 1em;
  line-height: 1.4;
}
.studio-ai-output-markdown :deep(p),
.studio-ai-output-markdown :deep(ul),
.studio-ai-output-markdown :deep(ol),
.studio-ai-output-markdown :deep(blockquote),
.studio-ai-output-markdown :deep(pre) {
  margin: 0.4em 0;
}
.studio-ai-output-markdown :deep(img) {
  max-width: 100%;
  height: auto;
}
.studio-ai-output .studio-actions {
  margin-top: var(--space-3);
}
/* AI 字数目标按钮组 */
.ai-length-group,
.ai-insert-group {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.ai-length-btn {
  padding: 3px 8px;
  font-size: var(--font-xs);
  font-weight: 500;
  font-family: inherit;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-secondary);
  cursor: pointer;
  white-space: nowrap;
}
.ai-length-btn.is-active {
  background: var(--color-text-primary);
  border-color: var(--color-text-primary);
  color: var(--color-surface);
}
.ai-auto-image-toggle {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  cursor: pointer;
}
.ai-auto-image-toggle input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}
.ai-auto-image-switch {
  display: inline-flex;
  align-items: center;
  width: 34px;
  height: 20px;
  padding: 2px;
  border-radius: 99px;
  background: var(--color-border);
  transition: background 0.2s ease;
}
.ai-auto-image-switch span {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--color-surface);
  transition: transform 0.2s ease;
}
.ai-auto-image-toggle input:checked + .ai-auto-image-switch {
  background: var(--color-accent);
}
.ai-auto-image-toggle input:checked + .ai-auto-image-switch span {
  transform: translateX(14px);
}
.ai-auto-image-toggle input:focus-visible + .ai-auto-image-switch {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}
.ai-auto-image-toggle > span:last-child {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ai-auto-image-toggle strong {
  font-size: var(--font-xs);
  color: var(--color-text-primary);
}
.ai-auto-image-toggle small {
  font-size: 11px;
  color: var(--color-text-secondary);
}
.ai-insert-mode {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
}
.ai-insert-label {
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
  white-space: nowrap;
}
/* ---------- 配图 Tab ---------- */
.img-suggest-btn {
  padding: 2px 8px;
  min-height: 26px;
  font-size: 11px;
  color: var(--color-accent);
  border-color: color-mix(in srgb, var(--color-accent) 30%, transparent);
  background: var(--color-accent-soft);
}
.img-suggest-btn:hover:not(:disabled) {
  background: color-mix(in srgb, var(--color-accent-soft) 80%, var(--color-accent));
}
.img-suggest-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.img-job-progress {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.img-job-label {
  font-size: 11px;
  color: var(--color-text-secondary);
}
.img-job-bar {
  height: 4px;
  border-radius: 99px;
  background: var(--color-surface-raised);
  overflow: hidden;
}
.img-job-fill {
  height: 100%;
  border-radius: 99px;
  background: var(--color-accent);
  transition: width 0.4s ease;
}
.img-results-section {
  display: flex;
  flex-direction: column;
}
.img-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-2);
}
.img-card {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--color-surface-raised);
}
.img-card-preview {
  aspect-ratio: 3/4;
  overflow: hidden;
  background: var(--color-surface);
  position: relative;
}
.img-card-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.img-card-placeholder {
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
  color: var(--color-text-muted);
  font-size: 11px;
}
.img-card-actions {
  gap: var(--space-1);
  padding: var(--space-1);
  border-top: 1px solid var(--color-border);
}
.btn-xs {
  min-height: 26px;
  padding: 0 var(--space-2);
  font-size: 11px;
}


.studio-doc-editor {
  flex: 1;
  min-height: calc(100vh - 320px);  /* 确保编辑器至少占满大部分视口 */
}
.studio-publish-head {
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.studio-tool-heading {
  margin: var(--space-5) 0 var(--space-3);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}
.studio-tab-panel .card-flat {
  border-color: var(--color-border);
  background: var(--color-surface-raised);
}
.studio-tab-panel details {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
}
.studio-tab-panel summary::marker {
  color: var(--color-accent);
}
.studio-drafts-skeleton {
  grid-column: 1;
  height: 440px;
  border-radius: var(--radius-lg);
}
.studio-editor-skeleton {
  grid-column: 2;
  height: calc(100vh - 160px);
  min-height: 560px;
  border-radius: var(--radius-lg);
}
.studio-tools-skeleton {
  grid-column: 3;
  height: 440px;
  border-radius: var(--radius-lg);
}

/* 工作台：稿件导航 / 主画布 / 创作设置 */
.studio-grid { grid-template-columns: 210px minmax(0, 1fr) 310px; gap: 16px; }
.studio-grid.drafts-collapsed { grid-template-columns: 48px minmax(0, 1fr) 310px; }
.studio-editor { overflow: hidden; border-radius: var(--radius-lg); }
.studio-workspace-nav { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 20px; border-bottom: 1px solid var(--color-border); background: var(--color-surface-raised); }
.studio-workspace-tabs { display: flex; gap: 6px; }
.studio-workspace-tabs button { display: flex; align-items: center; gap: 8px; min-height: 38px; padding: 0 12px; border: 0; border-radius: var(--radius-md); background: transparent; color: var(--color-text-secondary); font: inherit; font-size: 13px; cursor: pointer; }
.studio-workspace-tabs button.is-active { background: var(--color-surface); color: var(--color-accent); box-shadow: inset 0 -2px var(--color-accent); font-weight: 600; }
.studio-workspace-tabs button:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px; }
.studio-workspace-caption { font-size: 11px; color: var(--color-text-muted); }
.studio-result-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-accent); }
.studio-draft-surface { display: flex; flex: 1; flex-direction: column; min-width: 0; }
.studio-draft-surface[style*="display: none"], .studio-result-surface[style*="display: none"] { display: none; }
.studio-result-surface { min-height: 65vh; padding: 28px; scroll-margin-top: 90px; }
.studio-result-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.studio-result-header h2 { margin: 8px 0 0; color: var(--color-text-primary); font-size: 22px; font-weight: 600; letter-spacing: -0.025em; }
.studio-result-paper { margin-top: 24px; padding: 24px 0; min-height: 260px; border-top: 1px solid var(--color-border); font-size: 15px; line-height: 1.95; }
.studio-result-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; position: sticky; bottom: 12px; padding: 14px; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); box-shadow: 0 6px 24px color-mix(in srgb, var(--color-text-primary) 8%, transparent); }
.studio-result-actions .ai-insert-group { margin-right: auto; flex-wrap: wrap; }
.studio-result-empty { display: grid; justify-items: center; padding: 80px 16px; text-align: center; color: var(--color-text-secondary); }
.studio-result-empty > span { font-size: 36px; color: var(--color-accent); }
.studio-result-empty h3 { margin: 20px 0 8px; color: var(--color-text-primary); }
.studio-result-empty p { margin: 0; font-size: 13px; line-height: 1.8; }
.studio-brief-label { margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid var(--color-border); color: var(--color-text-muted); font-size: 11px; letter-spacing: 0.12em; }
.studio-result-notice { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; margin-bottom: 20px; padding: 12px; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-accent-soft); color: var(--color-text-secondary); font-size: 12px; }
@media (max-width: 1180px) and (min-width: 781px) {
  .studio-grid { grid-template-columns: 170px minmax(0, 1fr); }
  .studio-grid.drafts-collapsed { grid-template-columns: 48px minmax(0, 1fr); }
  .studio-workspace-caption { display: none; }
}
@media (max-width: 780px) {
  .studio-workspace-nav { padding: 10px; }
  .studio-workspace-caption { display: none; }
  .studio-result-surface { padding: 16px; }
  .studio-result-header h2 { font-size: 19px; }
  .studio-result-actions { bottom: 0; }
  .studio-result-paper { font-size: 14px; }
}

/* ---------- 灵感列表 ---------- */
.inspiration-list {
  display: flex;
  flex-direction: column;
  margin: 0 calc(var(--space-4) * -1) calc(var(--space-4) * -1);
}
.inspiration-item {
  padding: var(--space-4);
  border-top: 1px solid var(--color-border);
  transition: background 150ms ease;
}
.inspiration-item:hover {
  background: var(--color-surface-raised);
}
.inspiration-title {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  color: var(--color-text-primary);
  font-size: var(--font-sm);
  line-height: 1.6;
}
.inspiration-title > span:last-child {
  min-width: 0;
  overflow-wrap: anywhere;
}
.inspiration-actions {
  justify-content: flex-end;
  flex-wrap: wrap;
  margin-top: var(--space-3);
  padding-left: 34px;
}
.inspiration-actions .btn {
  min-height: 32px;
}
.hot-rank {
  display: grid;
  width: 24px;
  height: 24px;
  flex-shrink: 0;
  place-items: center;
  border-radius: 7px;
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
  font-size: 11px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}
.inspiration-item:first-child .hot-rank {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}
.hot-value {
  margin-right: auto;
  color: var(--color-text-secondary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

@media (max-width: 1180px) {
  .studio-grid {
    grid-template-columns: minmax(240px, 260px) minmax(0, 1fr);
  }
  .studio-grid.drafts-collapsed {
    grid-template-columns: 56px minmax(0, 1fr);
  }
  /* 中等宽度下编辑器和助手并排，避免助手被排到正文之后 */
  .studio-tools {
    position: sticky;
    top: 12px;
    grid-column: 2;
    grid-row: 1;
    align-self: start;
    max-height: calc(100vh - 124px);
    overflow-y: auto;
  }
  .studio-tools,
  .studio-tools-skeleton {
    position: sticky;
    top: 12px;
    grid-column: 2;
    grid-row: 1;
    align-self: start;
    max-height: calc(100vh - 124px);
    overflow-y: auto;
  }
  .studio-tools .studio-tab-panel {
    overflow: visible;
  }
}

@media (max-width: 780px) {
  .ai-image-settings-grid { grid-template-columns: 1fr; }
  .studio-page {
    gap: var(--space-3);
  }
  .studio-page-head {
    align-items: flex-start;
    flex-wrap: wrap;
    gap: var(--space-3);
  }
  .studio-page-actions {
    width: 100%;
    flex-wrap: wrap;
  }
  .studio-page-actions .btn {
    flex: 1;
    min-height: 40px;
    white-space: nowrap;
  }
  .studio-grid,
  .studio-grid.drafts-collapsed {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .studio-editor {
    order: 1;
    width: 100%;
    min-height: 600px;
  }
  .studio-drafts {
    position: relative;
    top: auto;
    order: 2;
    width: 100%;
    max-height: none;
    overflow: visible;
  }
  .studio-drafts.is-collapsed {
    display: block;
    padding: var(--space-2) var(--space-3);
    border-color: var(--color-border);
    background: var(--color-surface);
  }
  .studio-drafts-rail {
    flex-direction: row;
    justify-content: flex-start;
    padding: 0;
    border: 0;
    background: transparent;
  }
  .studio-rail-label {
    letter-spacing: 0.04em;
    writing-mode: horizontal-tb;
  }
  .item-delete {
    opacity: 1;
  }
  .item-open .list-title {
    white-space: normal;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }
  /* 整页滚动的布局里组头不吸顶，避免乱吸 */
  .studio-draft-group-head {
    position: static;
    margin: 0 calc(-1 * var(--space-1));
    padding: 4px var(--space-1);
    background: transparent;
    backdrop-filter: none;
  }
  .studio-tools {
    position: relative;
    top: auto;
    order: 3;
    width: 100%;
    max-height: none;
    overflow: visible;
  }
  .studio-panel-tabs {
    position: sticky;
    top: 56px;
    border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  }
  .studio-panel-tabs .segmented-item {
    min-height: 40px;
  }
  .studio-tab-panel {
    overflow: visible;
  }
  .studio-drafts-skeleton,
  .studio-editor-skeleton,
  .studio-tools-skeleton {
    width: 100%;
    height: 360px;
    min-height: 0;
  }
  .studio-doc-head {
    flex-wrap: wrap;
    gap: var(--space-2);
    padding: 22px 18px 14px;
  }
  .studio-title-input {
    font-size: 18px;
  }
  .studio-doc-meta {
    width: 100%;
    padding: 0 var(--space-2);
  }
  .studio-doc-body {
    min-height: 52vh;
    padding: var(--space-3) 22px var(--space-5);
    font-size: 14px;
    line-height: 1.85;
  }
  .studio-doc-hint {
    padding: 0 22px var(--space-5);
  }
  .studio-doc-variants {
    margin-top: var(--space-3);
  }
}

@media (max-width: 420px) {
  .studio-page-actions .btn {
    padding: 0 var(--space-2);
    font-size: var(--font-xs);
  }
  .studio-tab-panel {
    padding: var(--space-3);
  }
  .inspiration-list {
    margin-right: calc(var(--space-3) * -1);
    margin-bottom: calc(var(--space-3) * -1);
    margin-left: calc(var(--space-3) * -1);
  }
  .inspiration-item {
    padding: var(--space-3);
  }
}

/* 原创检测样式 */
.orig-summary {
  padding: var(--space-3) var(--space-4);
  border-radius: 10px;
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-primary);
  background: var(--color-fill);
  margin-bottom: var(--space-4);
}

.orig-summary.level-excellent { border-left-color: #10b981; }
.orig-summary.level-good { border-left-color: #3b82f6; }
.orig-summary.level-fair { border-left-color: #f59e0b; }
.orig-summary.level-poor { border-left-color: #ef4444; }

.orig-summary-top {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}

.orig-total {
  font-size: 34px;
  font-weight: 700;
  line-height: 1;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.orig-level {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}

.orig-badge {
  margin-left: auto;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  color: var(--text-secondary);
  border: 1px solid var(--color-border);
}

.orig-cost {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-tertiary, var(--text-secondary));
}

.orig-summary-text {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-secondary);
}

.originality-scores {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.score-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.score-item-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}

.score-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.score-hint {
  font-size: 11px;
  color: var(--text-secondary);
  opacity: 0.75;
}

.score-bar {
  flex: 1;
  height: 6px;
  background: var(--color-border);
  border-radius: 3px;
  overflow: hidden;
}

.score-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #10b981);
  transition: width 0.3s ease;
}

.score-fill.tone-good { background: linear-gradient(90deg, #34d399, #10b981); }
.score-fill.tone-warn { background: linear-gradient(90deg, #fbbf24, #f59e0b); }
.score-fill.tone-bad { background: linear-gradient(90deg, #f87171, #ef4444); }
.score-fill.tone-muted { background: var(--color-border); }

.score-value {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
}

.score-value.tone-good { color: #10b981; }
.score-value.tone-warn { color: #f59e0b; }
.score-value.tone-bad { color: #ef4444; }
.score-value.tone-muted { color: var(--text-secondary); font-weight: 400; }

.orig-note {
  margin: calc(var(--space-2) * -1) 0 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-secondary);
}

/* 风险提示 */
.risk-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.risk-item {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-2);
  border-radius: 4px;
  background: var(--color-fill-secondary);
  border-left: 2px solid #f59e0b;
}

.risk-item.severity-error {
  border-left-color: #ef4444;
}

.risk-dot {
  width: 6px;
  height: 6px;
  margin-top: 6px;
  border-radius: 50%;
  background: #f59e0b;
  flex: none;
}

.risk-item.severity-error .risk-dot {
  background: #ef4444;
}

.risk-body { min-width: 0; }

.risk-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.risk-reason {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
}

/* 重合片段 */
.segment-item {
  padding: var(--space-2);
  margin-bottom: var(--space-2);
  background: var(--color-fill-secondary);
  border-radius: 4px;
}

.segment-head {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.segment-ratio {
  font-weight: 600;
  color: #f59e0b;
}

.segment-line {
  margin: 0;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-primary);
}

.segment-line-muted {
  color: var(--text-secondary);
}

.segment-tag {
  display: inline-block;
  min-width: 32px;
  margin-right: 4px;
  font-size: 10px;
  padding: 0 4px;
  border-radius: 2px;
  border: 1px solid var(--color-border);
  color: var(--text-secondary);
}

/* 关键观点 */
.point-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.point-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid var(--color-border);
  color: var(--text-secondary);
  background: var(--color-fill-secondary);
}

.point-chip.is-new {
  border-color: rgba(16, 185, 129, 0.45);
  color: #10b981;
}

.point-chip em {
  font-style: normal;
  font-size: 10px;
  opacity: 0.8;
}

.originality-section {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}

.originality-section-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: var(--space-2);
  color: var(--text-primary);
}

.similar-article {
  padding: var(--space-2);
  margin-bottom: var(--space-2);
  background: var(--color-fill-secondary);
  border-radius: 4px;
}

.similar-article-title {
  font-size: 13px;
  color: var(--text-primary);
  margin-bottom: var(--space-1);
}

.similar-article-meta {
  font-size: 12px;
  color: var(--text-secondary);
}

.recommendations-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.recommendations-list li {
  padding: var(--space-2);
  margin-bottom: var(--space-1);
  font-size: 13px;
  color: var(--text-primary);
  background: var(--color-fill-secondary);
  border-left: 2px solid var(--color-primary);
  border-radius: 2px;
}

/* ═══════════════════════════════════════════════════════════════ */
/* Phase 2 & 3：高级功能样式 */
/* ═══════════════════════════════════════════════════════════════ */

.originality-options {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  margin-bottom: var(--space-3);
  padding: var(--space-2);
  background: var(--color-fill);
  border-radius: 4px;
  font-size: 12px;
}

.score-time {
  font-size: 11px;
  color: var(--text-secondary);
  opacity: 0.7;
}

.originality-actions {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}

/* 检测历史样式 */
.history-timeline {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-2);
  position: relative;
  padding-left: var(--space-4);
}

.history-timeline::before {
  content: '';
  position: absolute;
  left: 8px;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--color-border);
}

.history-item {
  display: flex;
  gap: var(--space-2);
  position: relative;
}

.history-marker {
  position: absolute;
  left: -12px;
  top: 4px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--color-fill-secondary);
  border: 2px solid var(--color-border);
}

.history-marker.level-excellent {
  background: #10b981;
  border-color: #059669;
}

.history-marker.level-good {
  background: #3b82f6;
  border-color: #2563eb;
}

.history-marker.level-fair {
  background: #f59e0b;
  border-color: #d97706;
}

.history-marker.level-poor {
  background: #ef4444;
  border-color: #dc2626;
}

.history-content {
  flex: 1;
  padding: var(--space-2);
  background: var(--color-fill-secondary);
  border-radius: 4px;
  font-size: 12px;
}

.history-score {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-1);
}

.history-value {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.history-time {
  color: var(--text-secondary);
  font-size: 11px;
}

.history-change {
  color: #10b981;
  font-size: 11px;
}

.history-change:has(+ .negative) {
  color: #ef4444;
}

.history-score {
  justify-content: flex-start;
  align-items: baseline;
  gap: 6px;
}

.history-level {
  font-size: 11px;
  color: var(--text-secondary);
}

.history-time {
  margin-left: auto;
}

.history-meta {
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-secondary);
}
</style>
