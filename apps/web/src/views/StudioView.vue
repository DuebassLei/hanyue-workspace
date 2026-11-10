<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, streamGeneration, fetchBlobUrl, apiUrl } from '../api/client'
import AppModal from '../components/AppModal.vue'
import StatusTag from '../components/StatusTag.vue'
import MarkdownEditor from '../components/MarkdownEditor.vue'
import PublishDrawer from '../features/publish/PublishDrawer.vue'

const route = useRoute()
const router = useRouter()

const items = ref([])
const detail = ref(null)
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const notice = ref('')

const draftTitle = ref('')
const draftBody = ref('')
const dirty = computed(() => {
  const latest = detail.value?.versions?.[0]
  if (!latest) return Boolean(draftTitle.value || draftBody.value)
  return latest.title !== draftTitle.value || latest.body !== draftBody.value
})

// 自动保存状态：idle | pending | saving | saved | error
const autoSaveStatus = ref('idle')
let autoSaveTimer = null

async function triggerAutoSave() {
  if (!detail.value) return
  autoSaveStatus.value = 'saving'
  try {
    await api.patch(`/content/${detail.value.item.id}/draft`, {
      title: draftTitle.value,
      body: draftBody.value,
    })
    autoSaveStatus.value = 'saved'
  } catch {
    autoSaveStatus.value = 'error'
  }
}

function scheduleAutoSave() {
  autoSaveStatus.value = 'pending'
  clearTimeout(autoSaveTimer)
  autoSaveTimer = setTimeout(triggerAutoSave, 1500)
}

// 监听正文/标题变化，触发自动保存
watch([draftTitle, draftBody], () => {
  if (!detail.value) return
  scheduleAutoSave()
})

// AI 面板
const aiOpen = ref(true)
const aiTask = ref('article')
const aiPrompt = ref('')
const aiOutput = ref('')
const aiRunning = ref(false)
const aiError = ref('')
const aiTargetLength = ref(600)  // 字数目标
const aiInsertMode = ref('append') // 'append' | 'replace'
const aiAutoImages = ref(false)
const writingStyles = ref([])
const writingStylesLoading = ref(false)
const writingStyleKey = ref('article-drafting')
const STYLE_TASKS = new Set(['article', 'polish', 'rewrite'])
const usesWritingStyle = computed(() => STYLE_TASKS.has(aiTask.value))
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
const variantForm = ref({ platform: 'wechat_official', title: '', body: '' })

// 删除稿件
const deletingItem = ref(null)
const deleting = ref(false)

// 右侧面板 Tab：ai | inspiration
const rightTab = ref('ai')
const draftsCollapsed = ref(false)
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

async function loadList() {
  loading.value = true
  try {
    items.value = await api.get('/content')
    const targetId = route.query.id || items.value[0]?.id
    if (targetId) await openItem(targetId)
    else detail.value = null
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function openItem(id) {
  const data = await api.get(`/content/${id}`)
  detail.value = data
  const latest = data.versions[0]
  draftTitle.value = latest?.title || data.item.title
  draftBody.value = latest?.body || ''
  notice.value = ''
  if (route.query.id !== id) router.replace({ name: 'studio', query: { id } })
}

async function createItem() {
  const created = await api.post('/content', { title: '未命名稿件' })
  items.value.unshift(created)
  await openItem(created.id)
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
  saving.value = true
  notice.value = ''
  try {
    const version = await api.post(`/content/${detail.value.item.id}/versions`, {
      title: draftTitle.value,
      body: draftBody.value,
      sourceType: 'manual',
    })
    detail.value.versions.unshift(version)
    detail.value.item.title = draftTitle.value
    detail.value.item.currentVersionId = version.id
    notice.value = `已保存为 v${version.versionNo}`
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

function restore(version) {
  draftTitle.value = version.title
  draftBody.value = version.body
  notice.value = `已载入 v${version.versionNo}，保存后才会覆盖为最新版本`
}

function parseArticleImagePrompts(rawOutput) {
  const cleaned = String(rawOutput || '').replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, '').trim()
  const start = cleaned.indexOf('{')
  const end = cleaned.lastIndexOf('}')
  if (start < 0 || end <= start) return null
  try {
    const data = JSON.parse(cleaned.slice(start, end + 1))
    return {
      coverPrompt: typeof data.coverPrompt === 'string' ? data.coverPrompt.trim() : '',
      inlinePrompts: Array.isArray(data.inlinePrompts)
        ? data.inlinePrompts.filter((prompt) => typeof prompt === 'string' && prompt.trim()).map((prompt) => prompt.trim())
        : [],
    }
  } catch {
    return null
  }
}

async function startAutoImageGeneration(rawOutput, contentItemId) {
  if (!contentItemId || detail.value?.item.id !== contentItemId) return
  const prompts = parseArticleImagePrompts(rawOutput)
  if (!prompts || (!prompts.coverPrompt && !prompts.inlinePrompts.length)) {
    notice.value = '文章已生成，但未提取到配图提示词'
    return
  }
  const requests = [
    prompts.coverPrompt ? { prompt: prompts.coverPrompt, purpose: 'cover', label: '封面图' } : null,
    ...prompts.inlinePrompts.map((prompt, index) => ({ prompt, purpose: 'inline', label: `正文插图 ${index + 1}` })),
  ].filter(Boolean)
  const results = await Promise.allSettled(requests.map(async (request) => {
    const response = await api.post('/ai/image-generations', {
      prompt: request.prompt,
      purpose: request.purpose,
      aspectRatio: request.purpose === 'cover' ? '3:4' : '1:1',
      count: 1,
    })
    return { id: response.jobId, status: 'queued', prompt: request.prompt, label: request.label, purpose: request.purpose, contentItemId }
  }))
  for (const result of results) {
    if (result.status === 'fulfilled') imgJobs.value.unshift(result.value)
    else imgError.value = result.reason?.message || '自动配图任务创建失败'
  }
  if (results.some((result) => result.status === 'fulfilled')) {
    notice.value = `文章已生成，${results.filter((result) => result.status === 'fulfilled').length} 个配图任务已开始`
    startImgPoll()
  }
}

function runAi() {
  if (!aiPrompt.value.trim()) {
    aiError.value = '请先写清楚这次要做什么'
    return
  }
  const generationItemId = detail.value?.item.id
  const autoGenerateImages = aiAutoImages.value && aiTask.value === 'article'
  let generationSaved = false
  aiOutput.value = ''
  aiError.value = ''
  aiRunning.value = true
  const request = {
    taskType: aiTask.value,
    prompt: aiPrompt.value,
    contentItemId: generationItemId,
    constraints: { targetLength: aiTargetLength.value, platform: 'wechat_official' },
  }
  if (usesWritingStyle.value) {
    request.skillKey = writingStyleKey.value || 'article-drafting'
  }
  abortStream = streamGeneration(
    request,
    {
      onStart: () => {},
      onDelta: (data) => {
        aiOutput.value += data.text || ''
      },
      onError: (data) => {
        aiError.value = data.message || '生成失败'
      },
      onSaved: () => {
        generationSaved = true
        notice.value = 'AI 结果已保存为新版本'
      },
      onEnd: async () => {
        aiRunning.value = false
        if (generationItemId && detail.value?.item.id === generationItemId) {
          const refreshed = await api.get(`/content/${generationItemId}`)
          detail.value = refreshed
          if (autoGenerateImages && generationSaved && !aiError.value) {
            void startAutoImageGeneration(aiOutput.value, generationItemId)
          }
        }
      },
    },
  )
}

function stopAi() {
  abortStream?.()
  aiRunning.value = false
}

function useAiOutput() {
  if (aiInsertMode.value === 'replace') {
    draftBody.value = aiOutput.value
  } else {
    draftBody.value = draftBody.value ? `${draftBody.value}\n\n${aiOutput.value}` : aiOutput.value
  }
  aiOutput.value = ''
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
  notice.value = '平台版本已保存（未提交发布）'
}

watch(
  () => route.query.id,
  (id) => {
    if (id && id !== detail.value?.item.id) openItem(id)
  },
)

// 切换到新稿件时重置状态
watch(
  () => detail.value?.item.id,
  () => {
    autoSaveStatus.value = 'idle'
  },
)

async function openInspirationTab() {
  rightTab.value = 'inspiration'
  if (!hotStatus.value.checked) await loadHotTopics()
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
    notice.value = `已新建稿件「${item.title}」，可在 AI 助手里继续生成`
  } catch (err) {
    hotError.value = err.message
  } finally {
    hotActionId.value = ''
  }
}

// ─── 配图 Tab ────────────────────────────────────────────────
// 提供商状态
const imgProvider = ref({ available: false, note: '', modelCode: null })
const imgProviderLoaded = ref(false)

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

// 已生成图片（含 blob URL，用于展示）
const imgResults = ref([]) // [{assetId, blobUrl, rawUrl, mime, jobId}]

async function openImageTab() {
  rightTab.value = 'image'
  if (!imgProviderLoaded.value) {
    try {
      imgProvider.value = await api.get('/ai/image-provider/status')
    } catch {
      // 静默失败：不影响手动填 prompt 发起生图
    }
    imgProviderLoaded.value = true
  }
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
  imgBusy.value = true
  imgError.value = ''
  try {
    const res = await api.post('/ai/image-generations', {
      prompt: imgPrompt.value.trim(),
      aspectRatio: imgAspectRatio.value,
      count: Number(imgCount.value),
    })
    imgJobs.value.unshift({ id: res.jobId, status: 'queued', prompt: imgPrompt.value.trim(), label: '手动配图' })
    startImgPoll()
  } catch (err) {
    imgError.value = err.message
  } finally {
    imgBusy.value = false
  }
}

async function retryImageJob(job) {
  if (!job.prompt || job.status !== 'failed') return
  job.status = 'retrying'
  job.error = ''
  try {
    const response = await api.post('/ai/image-generations', {
      prompt: job.prompt,
      purpose: job.purpose || 'cover',
      aspectRatio: job.purpose === 'cover' ? '3:4' : '1:1',
      count: 1,
    })
    job.id = response.jobId
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
              imgResults.value.unshift({
                assetId, blobUrl, rawUrl: rawPath ? apiUrl(rawPath) : '', jobId: job.id,
              })
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

/** 把图片以 Markdown 语法插入正文末尾（用后端返回的签名直链，img 标签才能直接取图） */
function insertImageToBody(result) {
  if (!result.rawUrl) {
    notice.value = '该素材缺少可用直链，请稍后重试'
    return
  }
  // 走 v-model 已经能同步到编辑器；这里保持原逻辑（追加到末尾）
  draftBody.value = (draftBody.value || '') + `\n\n![配图](${result.rawUrl})\n`
  notice.value = '图片已插入正文末尾'
}

/** 复制素材直链到剪贴板 */
async function copyImageUrl(result) {
  const url = result.rawUrl || `${location.origin}/api/v1/assets/${result.assetId}/raw`
  try {
    await navigator.clipboard.writeText(url)
    notice.value = '已复制图片链接'
  } catch {
    notice.value = `链接：${url}`
  }
}

function releaseImgResults() {
  for (const r of imgResults.value) {
    if (r.blobUrl) URL.revokeObjectURL(r.blobUrl)
  }
  imgResults.value = []
}

onMounted(() => {
  loadList()
  loadWritingStyles()
})

onBeforeUnmount(() => {
  clearTimeout(autoSaveTimer)
  stopImgPoll()
  releaseImgResults()
  // 组件卸载时若有待保存内容，立即同步
  if (autoSaveStatus.value === 'pending' && detail.value) {
    triggerAutoSave()
  }
})
</script>

<template>
  <div class="page studio-page">
    <div class="page-head studio-page-head">
      <div class="studio-page-heading">
        <h1 class="page-title">创作空间</h1>
        <p class="page-sub">从灵感到成稿，在同一处完成创作与发布</p>
      </div>
      <div class="row row-tight studio-page-actions">
        <button class="btn btn-ghost btn-sm" @click="createItem">新建稿件</button>
        <button class="btn btn-ghost btn-sm" @click="openVariant" :disabled="!detail">
          生成平台版本
        </button>
        <button
          class="btn btn-ghost btn-sm"
          :disabled="!detail || !draftBody"
          title="将当前正文带入图文卡片创作台"
          @click="router.push({ name: 'cards', state: { markdown: draftBody } })"
        >
          转为图文卡片
        </button>
        <button class="btn btn-ghost btn-sm" :disabled="!detail" @click="publishDrawerOpen = true">
          发布
        </button>
        <button class="btn btn-sm" :disabled="saving || !detail" @click="saveVersion">
          {{ saving ? '保存中…' : '存为版本' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="banner banner-danger" role="alert">{{ error }}</div>
    <div v-if="notice" class="banner banner-info" role="status">{{ notice }}</div>

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
        <button class="btn btn-sm" @click="createItem">新建稿件</button>
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
          <button
            class="icon-btn icon-btn-sm"
            type="button"
            aria-expanded="true"
            aria-label="收起稿件列表"
            title="收起稿件列表"
            @click="draftsCollapsed = true"
          >‹</button>
        </div>
        <button class="btn btn-ghost btn-sm studio-new-draft" type="button" @click="createItem">+ 新建稿件</button>
        <div class="list studio-list">
          <div
            v-for="item in items"
            :key="item.id"
            class="list-row item-row"
            :class="{ 'is-active': item.id === detail?.item.id }"
          >
            <button
              class="item-open"
              type="button"
              @click="openItem(item.id)"
            >
              <div class="list-main">
                <div class="list-title">{{ item.title }}</div>
                <div class="list-meta">{{ item.updatedAt.slice(0, 10) }}</div>
              </div>
            </button>
            <button
              class="icon-btn icon-btn-sm item-delete"
              type="button"
              aria-label="删除稿件"
              title="删除稿件"
              @click="askDelete(item)"
            >
              ✕
            </button>
          </div>
        </div>
        <section v-if="detail" class="studio-versions" aria-labelledby="studio-versions-title">
          <h3 id="studio-versions-title" class="studio-section-label">最近版本</h3>
          <div class="list studio-list">
            <div v-for="v in detail.versions.slice(0, 6)" :key="v.id" class="list-row version-row">
              <div class="list-main">
                <div class="list-title studio-version-title">v{{ v.versionNo }}</div>
                <div class="list-meta">{{ v.sourceType }} · {{ v.createdAt.slice(5, 16).replace('T', ' ') }}</div>
              </div>
              <button class="btn btn-ghost btn-sm" @click="restore(v)">载入</button>
            </div>
          </div>
        </section>
        </template>
        <template v-else>
          <div class="studio-drafts-rail">
            <button
              class="icon-btn icon-btn-sm studio-rail-toggle"
              type="button"
              aria-expanded="false"
              aria-label="展开稿件列表"
              title="展开稿件列表"
              @click="draftsCollapsed = false"
            >›</button>
            <span class="studio-rail-count" :title="`共 ${items.length} 篇稿件`">{{ items.length }}</span>
            <span class="studio-rail-label">稿件</span>
          </div>
        </template>
      </aside>

      <section class="card studio-editor" aria-label="稿件编辑器">
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
          <MarkdownEditor
            v-model="draftBody"
            :enable-ai-bubble="true"
            placeholder="在这里写正文，选中文字可用 AI 润色/改写..."
            class="studio-doc-editor"
            @editor-ready="(ed) => (editorRef = ed)"
          />
          <p class="studio-doc-hint">AI 生成结果需手动插入正文，选中段落可直接 inline 润色</p>

          <footer v-if="detail.variants.length" class="studio-doc-variants">
            <h3 class="studio-section-label">平台版本</h3>
            <div class="list studio-list">
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
          </footer>
        </template>
        <div v-else class="empty">
          <span class="empty-title">选择或新建一篇稿件</span>
        </div>
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
            id="studio-tab-inspiration"
            class="segmented-item"
            :class="{ 'is-active': rightTab === 'inspiration' }"
            role="tab"
            aria-controls="studio-panel-inspiration"
            :aria-selected="rightTab === 'inspiration'"
            @click="openInspirationTab"
          >灵感</button>
        </div>

        <!-- AI 助手 Tab -->
        <section
          v-if="rightTab === 'ai'"
          id="studio-panel-ai"
          class="studio-tab-panel"
          role="tabpanel"
          aria-labelledby="studio-tab-ai"
        >
          <div class="row studio-tool-head">
            <div>
              <span class="studio-section-label studio-section-label-flush">AI 写作</span>
              <p class="studio-tool-description">基于当前稿件生成内容，确认后再插入正文</p>
            </div>
            <div class="spacer" />
            <button class="icon-btn icon-btn-sm" :aria-label="aiOpen ? '收起 AI 助手' : '展开 AI 助手'" @click="aiOpen = !aiOpen">
              {{ aiOpen ? '▾' : '▸' }}
            </button>
          </div>
          <div v-if="aiOpen" class="studio-tool-stack">
            <div class="field">
              <label class="field-label" for="ai-task">任务</label>
              <select id="ai-task" v-model="aiTask" class="select">
                <option v-for="task in TASKS" :key="task.value" :value="task.value">{{ task.label }}</option>
              </select>
            </div>
            <div v-if="usesWritingStyle" class="field">
              <label class="field-label" for="ai-writing-style">写作风格</label>
              <select
                id="ai-writing-style"
                v-model="writingStyleKey"
                class="select"
                :disabled="aiRunning || writingStylesLoading"
              >
                <option v-if="!writingStyles.length" value="article-drafting">使用默认风格</option>
                <option
                  v-for="style in writingStyles"
                  :key="style.skillKey"
                  :value="style.skillKey"
                >
                  {{ style.displayName }}
                </option>
              </select>
            </div>
            <div class="field">
              <label class="field-label" for="ai-prompt">要做什么</label>
              <textarea
                id="ai-prompt"
                v-model="aiPrompt"
                class="textarea studio-ai-prompt"
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
            <label class="ai-auto-image-toggle">
              <input v-model="aiAutoImages" type="checkbox" :disabled="aiRunning || aiTask !== 'article'">
              <span class="ai-auto-image-switch" aria-hidden="true"><span /></span>
              <span>
                <strong>自动生成配图</strong>
                <small>成文完成后自动生成封面图和正文插图</small>
              </span>
            </label>
            <div class="row studio-actions">
              <button class="btn btn-sm" :disabled="aiRunning" @click="runAi">
                {{ aiRunning ? '生成中…' : '开始生成' }}
              </button>
              <button v-if="aiRunning" class="btn btn-ghost btn-sm" @click="stopAi">中断</button>
            </div>
            <p v-if="aiError" class="field-error" role="alert">{{ aiError }}</p>
            <section v-if="aiOutput" class="studio-ai-output" aria-label="AI 生成结果">
              <span class="studio-ai-output-label">生成结果</span>
              <!-- 渲染 Markdown 预览 -->
              <div class="studio-ai-output-content">
                <MarkdownEditor
                  :model-value="aiOutput"
                  :readonly="true"
                  :toolbar="{}"
                />
              </div>
              <!-- 插入方式 -->
              <div class="ai-insert-mode">
                <label class="ai-insert-label">插入方式</label>
                <div class="ai-insert-group">
                  <button
                    class="ai-length-btn"
                    :class="{ 'is-active': aiInsertMode === 'append' }"
                    @click="aiInsertMode = 'append'"
                  >追加末尾</button>
                  <button
                    class="ai-length-btn"
                    :class="{ 'is-active': aiInsertMode === 'replace' }"
                    @click="aiInsertMode = 'replace'"
                  >替换全文</button>
                </div>
              </div>
              <div class="row studio-actions">
                <button class="btn btn-sm" @click="useAiOutput">插入正文</button>
                <button class="btn btn-ghost btn-sm" @click="aiOutput = ''">丢弃</button>
              </div>
            </section>
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
            <button class="btn btn-ghost btn-sm" :disabled="hotLoading" @click="loadHotTopics">
              {{ hotLoading ? '刷新中…' : '刷新' }}
            </button>
          </div>

          <div v-if="hotError" class="banner banner-danger" role="alert" style="margin-top: var(--space-3)">{{ hotError }}</div>
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
                <button
                  class="btn btn-sm"
                  :disabled="hotActionId === item.title"
                  @click="createDraftFromHot(item)"
                >用它写稿</button>
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
                  ? `使用 ${imgProvider.modelCode || '已配置模型'} 生成配图`
                  : '未配置生图供应商，请在「设置 → 模型连接」添加支持 image.generate 的模型' }}
              </p>
            </div>
          </div>

          <div class="studio-tool-stack">
            <!-- Prompt 输入 -->
            <div class="field">
              <div class="row" style="justify-content: space-between; align-items: center; margin-bottom: var(--space-1)">
                <label class="field-label" for="img-prompt" style="margin: 0">图像描述</label>
                <button
                  class="btn btn-ghost btn-xs img-suggest-btn"
                  :disabled="imgBusy || !detail"
                  @click="suggestImagePrompt"
                >{{ imgBusy && !imgJobs.length ? '提炼中…' : '✦ 从正文提炼' }}</button>
              </div>
              <textarea
                id="img-prompt"
                v-model="imgPrompt"
                class="textarea"
                style="min-height: 88px; font-size: 12px; line-height: 1.65"
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
                <select id="img-count" v-model="imgCount" class="select" style="min-height: 34px; font-size: var(--font-xs)">
                  <option v-for="n in [1,2,3,4]" :key="n" :value="n">{{ n }} 张</option>
                </select>
              </div>
            </div>

            <!-- 操作按钮 -->
            <div class="row studio-actions">
              <button
                class="btn btn-sm"
                :disabled="imgBusy || !imgPrompt.trim()"
                @click="generateImages"
              >
                {{ imgBusy && imgJobs.some(j => ['queued','running'].includes(j.status)) ? '生成中…' : '生成配图' }}
              </button>
            </div>

            <p v-if="imgError" class="field-error" role="alert">{{ imgError }}</p>

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
                  <div class="row img-card-actions">
                    <button class="btn btn-ghost btn-xs" style="flex:1" @click="insertImageToBody(result)">插入正文</button>
                    <button class="btn btn-ghost btn-xs" @click="copyImageUrl(result)" title="复制链接">🔗</button>
                  </div>
                </div>
              </div>
            </div>

            <!-- 失败任务提示 -->
            <div v-for="job in imgJobs.filter(j => j.status === 'failed')" :key="job.id" class="banner banner-danger img-job-error" style="font-size: 11px">
              <span>{{ job.label || '配图' }}生成失败：{{ job.error || '未知错误' }}</span>
              <button class="btn btn-ghost btn-xs" :disabled="job.status === 'retrying'" @click="retryImageJob(job)">
                {{ job.status === 'retrying' ? '重试中…' : '重试' }}
              </button>
            </div>
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
      @close="publishDrawerOpen = false"
      @published="notice = '草稿已推送到公众号草稿箱，请到后台预览并群发。'"
    />

    <AppModal v-if="showVariant" title="生成平台版本" description="平台版本独立可编辑，提交发布前仍需人工审核。" @close="showVariant = false">      <div class="stack">
        <label class="field">
          <span class="field-label">平台</span>
          <select v-model="variantForm.platform" class="select">
            <option v-for="p in PLATFORMS" :key="p.code" :value="p.code">{{ p.label }}</option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">标题</span>
          <input v-model="variantForm.title" class="input" />
        </label>
        <label class="field">
          <span class="field-label">正文</span>
          <textarea v-model="variantForm.body" class="textarea" style="min-height: 160px" />
        </label>
        <div class="row">
          <div class="spacer" />
          <button class="btn btn-ghost" @click="showVariant = false">取消</button>
          <button class="btn" @click="saveVariant">保存版本</button>
        </div>
      </div>
    </AppModal>

    <AppModal
      v-if="deletingItem"
      title="删除稿件"
      :description="`「${deletingItem.title}」将被彻底删除，且不可恢复。`"
      @close="deletingItem = null"
    >
      <div class="stack">
        <p class="field-hint" style="margin: 0">
          会一并清理：所有版本、平台版本、审阅评论、发布任务与发布记录、对应的数据统计，以及日历里的排期。
        </p>
        <div class="row">
          <div class="spacer" />
          <button class="btn btn-ghost" @click="deletingItem = null">取消</button>
          <button class="btn btn-danger" :disabled="deleting" @click="confirmDelete">
            {{ deleting ? '删除中…' : '确认删除' }}
          </button>
        </div>
      </div>
    </AppModal>
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
  grid-template-columns: minmax(190px, 220px) minmax(0, 1fr) minmax(300px, 340px);
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
  position: sticky;
  top: 80px;
  max-height: calc(100vh - 104px);
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
  overflow-y: auto;
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
  border-style: dashed;
  background: transparent;
  color: var(--color-text-secondary);
}
.studio-new-draft:hover {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  color: var(--color-accent);
}
.studio-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.studio-list .list-row {
  position: relative;
  padding: 10px;
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
  display: -webkit-box;
  overflow: hidden;
  color: var(--color-text-primary);
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.item-open .list-meta {
  margin-top: var(--space-1);
  color: var(--color-text-secondary);
  font-size: 11px;
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
  box-shadow: 0 1px 2px rgba(32, 39, 35, 0.03), var(--shadow-md);
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
  margin-top: auto;
  padding: var(--space-4) 28px var(--space-5);
  border-top: 1px solid var(--color-border);
  background: var(--color-surface-raised);
}
.studio-doc-variants .studio-section-label {
  margin: 0 0 var(--space-2);
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
  overflow-y: auto;
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
.studio-ai-prompt {
  min-height: 108px;
  line-height: 1.7;
}
.studio-actions {
  gap: var(--space-2);
}
.studio-ai-output {
  padding: var(--space-3);
  border-left: 3px solid var(--color-accent);
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
  background: var(--color-accent-soft);
}
.studio-ai-output-label {
  display: block;
  margin-bottom: var(--space-2);
  color: var(--color-accent);
  font-size: 11px;
  font-weight: 600;
}
.studio-ai-output-content {
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
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
    grid-template-columns: minmax(190px, 220px) minmax(0, 1fr);
  }
  .studio-grid.drafts-collapsed {
    grid-template-columns: 56px minmax(0, 1fr);
  }
  .studio-tools,
  .studio-tools-skeleton {
    position: relative;
    top: auto;
    grid-column: 2;
    max-height: none;
  }
  .studio-tools {
    overflow: visible;
  }
  .studio-tools .studio-tab-panel {
    overflow: visible;
  }
  .studio-tools-skeleton {
    height: 320px;
  }
}

@media (max-width: 780px) {
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
    padding: var(--space-4) 20px;
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
</style>
