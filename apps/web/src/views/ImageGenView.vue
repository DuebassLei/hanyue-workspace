<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fetchBlobUrl } from '../api/client'

const REFERENCE_LIMIT = 4
const MAX_POLL_ROUNDS = 120 // 1.5s × 120 = 3 分钟

const router = useRouter()
const route = useRoute()
const provider = ref({ available: false, note: '', modelCode: null, providers: [] })
const selectedModelId = ref('')
const form = reactive({ prompt: '', aspectRatio: '3:4', count: 1 })
const referenceFiles = ref([])
const busy = ref(false)
const error = ref('')
const jobs = ref([])
const genJobId = ref('')
const genPrompt = ref('')
const assets = ref([])
const assetsLoading = ref(true)
const previewAsset = ref(null)
const copiedId = ref('')
let copiedTimer = null
let pollTimer = null
let pollRounds = 0

// 提示词库：与图片生成解耦加载，接口异常不影响正常出图
const promptLibrary = ref([])
const promptLibraryLoading = ref(false)
const promptLibraryError = ref('')
const promptPickerOpen = ref(false)
const promptKeyword = ref('')
const promptCategory = ref('')
const appliedPrompt = ref('')

// 手绘风格：从提示词库选好风格后，合成提示词并记录参考图来源
const handdrawStyle = ref(null)
const handdrawCompose = ref(null)

const providers = computed(() => provider.value.providers || [])
const selectedProvider = computed(
  () => providers.value.find((item) => (item.modelId || '') === selectedModelId.value) || null,
)
const canAddReference = computed(() => referenceFiles.value.length < REFERENCE_LIMIT)
const referenceHint = computed(() =>
  referenceFiles.value.length
    ? `已选 ${referenceFiles.value.length}/${REFERENCE_LIMIT} 张`
    : '未选择文件',
)

const currentGenJob = computed(() => {
  if (genJobId.value) return jobs.value.find((job) => job.id === genJobId.value) || null
  return jobs.value.find((job) => job.kind === 'image-generation') || null
})
const genBusy = computed(() =>
  ['queued', 'running', 'retrying'].includes(currentGenJob.value?.status || ''),
)
// 等待动效：按任务创建时间计时，轮询期间每秒刷新一次
const waitNow = ref(Date.now())
let waitTimer = null
const waitAspect = computed(() => (form.aspectRatio || '3:4').replace(':', ' / '))
// 后端 SQLite 返回的是不带时区的 UTC 时间，直接 parse 会被当成本地时间差 8 小时
function parseJobTime(value) {
  if (!value) return NaN
  return Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`)
}
const genElapsedText = computed(() => {
  const start = parseJobTime(currentGenJob.value?.createdAt)
  if (!Number.isFinite(start)) return '00:00'
  const seconds = Math.max(0, Math.floor((waitNow.value - start) / 1000))
  const mm = String(Math.floor(seconds / 60)).padStart(2, '0')
  const ss = String(seconds % 60).padStart(2, '0')
  return `${mm}:${ss}`
})
const waitTitle = computed(() => {
  const status = currentGenJob.value?.status
  if (status === 'queued') return '任务已排队'
  if (status === 'retrying') return '正在重试生成'
  return '正在生成图片'
})

function startWaitTimer() {
  if (waitTimer) return
  waitNow.value = Date.now()
  waitTimer = setInterval(() => {
    waitNow.value = Date.now()
  }, 1000)
}

function stopWaitTimer() {
  if (waitTimer) clearInterval(waitTimer)
  waitTimer = null
}

watch(genBusy, (active) => {
  if (active) startWaitTimer()
  else stopWaitTimer()
})
const genAssets = computed(() => {
  const result = currentGenJob.value?.result || {}
  const urls = result.assetUrls || []
  const ids = result.assetIds || []
  // 任务结果只给签名直链，尺寸/文件名/提示词要回素材库补齐，
  // 预览弹窗与下载都要用这些字段
  return urls.map((url, index) => {
    const id = ids[index] || ''
    const asset = assets.value.find((item) => item.id === id)
    return {
      id,
      url,
      originalName: asset?.originalName || '生成图片',
      width: asset?.width || null,
      height: asset?.height || null,
      sizeBytes: asset?.sizeBytes || 0,
      prompt: asset?.prompt || genPrompt.value,
      sourceType: 'generated',
    }
  })
})
const downloadingId = ref('')
const recentGenerated = computed(() =>
  assets.value.filter((asset) => asset.sourceType === 'generated').slice(0, 12),
)

const promptCategories = computed(() => {
  const set = new Set()
  for (const item of promptLibrary.value) {
    if (item.category) set.add(item.category)
  }
  return [...set]
})

const filteredPrompts = computed(() => {
  const q = promptKeyword.value.trim().toLowerCase()
  return promptLibrary.value.filter((item) => {
    if (promptCategory.value && item.category !== promptCategory.value) return false
    if (!q) return true
    const haystack = [item.title, item.content, item.category, ...(item.tags || [])]
      .join(' ')
      .toLowerCase()
    return haystack.includes(q)
  })
})

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

async function loadAssets() {
  assets.value = await api.get('/assets?limit=60')
}

async function loadPromptLibrary() {
  promptLibraryLoading.value = true
  promptLibraryError.value = ''
  try {
    promptLibrary.value = await api.get('/prompts')
  } catch (err) {
    promptLibraryError.value = err.message
  } finally {
    promptLibraryLoading.value = false
  }
}

function openPromptPicker() {
  promptPickerOpen.value = true
  if (!promptLibrary.value.length) loadPromptLibrary()
}

function applyPrompt(item, mode = 'replace') {
  const text = (item.content || '').trim()
  if (!text) return
  form.prompt =
    mode === 'append' && form.prompt.trim()
      ? `${form.prompt.trim()}\n\n${text}`
      : text
  appliedPrompt.value = item.title
  promptPickerOpen.value = false
}

function goPromptLibrary() {
  promptPickerOpen.value = false
  router.push({ name: 'prompts' })
}

function goHanddrawLibrary() {
  router.push({ name: 'handdraw' })
}

// 就地选风格：避免跳到提示词库页再跳回来
const stylePickerOpen = ref(false)
const stylePickerLoading = ref(false)
const stylePickerError = ref('')
const stylePickerKeyword = ref('')
const stylePickerGroup = ref('')
const stylePickerGroups = ref([])
const stylePickerItems = ref([])
const stylePickerPage = ref(1)
const stylePickerPages = ref(0)
const styleThumbs = ref(new Map())
let styleSearchTimer = null

function styleThumb(number) {
  return styleThumbs.value.get(number) || ''
}

async function loadStyleThumbs() {
  const list = stylePickerItems.value.filter((item) => item.referenceAvailable)
  await Promise.all(
    list.map(async (item) => {
      if (styleThumbs.value.has(item.number)) return
      try {
        const url = await fetchBlobUrl(`/handdraw/styles/${item.number}/reference`)
        const store = new Map(styleThumbs.value)
        store.set(item.number, url)
        styleThumbs.value = store
      } catch {
        // 单张参考图失败不影响列表
      }
    }),
  )
}

async function loadStylePicker() {
  stylePickerLoading.value = true
  stylePickerError.value = ''
  try {
    const params = new URLSearchParams({
      page: String(stylePickerPage.value),
      pageSize: '24',
    })
    if (stylePickerKeyword.value.trim()) params.set('q', stylePickerKeyword.value.trim())
    if (stylePickerGroup.value) params.set('group', stylePickerGroup.value)
    const data = await api.get(`/handdraw/styles?${params.toString()}`)
    stylePickerItems.value = data.items || []
    stylePickerPages.value = data.pages || 0
    if ((data.groups || []).length) stylePickerGroups.value = data.groups
    loadStyleThumbs()
  } catch (err) {
    stylePickerError.value = err.message
  } finally {
    stylePickerLoading.value = false
  }
}

function openStylePicker() {
  stylePickerOpen.value = true
  if (!stylePickerItems.value.length) loadStylePicker()
}

function onStyleSearch() {
  if (styleSearchTimer) clearTimeout(styleSearchTimer)
  styleSearchTimer = setTimeout(() => {
    stylePickerPage.value = 1
    loadStylePicker()
  }, 260)
}

function pickStyleGroup(key) {
  stylePickerGroup.value = stylePickerGroup.value === key ? '' : key
  stylePickerPage.value = 1
  loadStylePicker()
}

function goStylePage(delta) {
  const next = stylePickerPage.value + delta
  if (next < 1 || next > stylePickerPages.value) return
  stylePickerPage.value = next
  loadStylePicker()
}

async function applyPickedStyle(item) {
  error.value = ''
  try {
    const composed = await api.post('/handdraw/compose', {
      styleNumber: item.number,
      theme: form.prompt.trim() || '自由创作',
      modelId: selectedModelId.value || null,
    })
    handdrawStyle.value = composed.style
    handdrawCompose.value = composed
    loadHanddrawThumb(composed.style)
    form.prompt = composed.promptZh
    appliedPrompt.value = `手绘风格 · ${composed.style.generationName}`
    stylePickerOpen.value = false
  } catch (err) {
    error.value = err.message
  }
}

function clearHanddrawStyle() {
  handdrawStyle.value = null
  handdrawCompose.value = null
  revokeHanddrawThumb()
}

// 参考图接口需要 Bearer 令牌，直连 <img src> 会 401
const handdrawThumb = ref('')

function revokeHanddrawThumb() {
  if (handdrawThumb.value) URL.revokeObjectURL(handdrawThumb.value)
  handdrawThumb.value = ''
}

async function loadHanddrawThumb(style) {
  revokeHanddrawThumb()
  if (!style?.referenceAvailable) return
  try {
    handdrawThumb.value = await fetchBlobUrl(`/handdraw/styles/${style.number}/reference`)
  } catch {
    handdrawThumb.value = ''
  }
}

/** 读取提示词库页带来的手绘风格，按当前主题与供应商合成提示词 */
async function applyHanddrawQuery() {
  const query = route.query
  const styleNumber = typeof query.handdrawStyle === 'string' ? query.handdrawStyle : ''
  if (!styleNumber) return
  const theme = typeof query.theme === 'string' ? query.theme : ''
  try {
    const composed = await api.post('/handdraw/compose', {
      styleNumber,
      theme: theme || form.prompt.trim() || '自由创作',
      colorId: typeof query.color === 'string' ? query.color : null,
      accentColorId: typeof query.accent === 'string' ? query.accent : null,
      modelId: selectedModelId.value || null,
    })
    handdrawStyle.value = composed.style
    handdrawCompose.value = composed
    loadHanddrawThumb(composed.style)
    form.prompt = composed.promptZh
    appliedPrompt.value = `手绘风格 · ${composed.style.generationName}`
  } catch (err) {
    error.value = err.message
  }
  // 参数只用一次：清掉后刷新或移除风格都不会被重新套回去
  const rest = { ...route.query }
  for (const key of ['handdrawStyle', 'theme', 'color', 'accent']) delete rest[key]
  router.replace({ name: 'images', query: rest })
}

async function load() {
  assetsLoading.value = true
  error.value = ''
  try {
    const [status, recent, list] = await Promise.all([
      api.get('/ai/image-provider/status'),
      api.get('/jobs?limit=20'),
      api.get('/assets?limit=60'),
    ])
    provider.value = status
    jobs.value = recent.items || []
    assets.value = list
    if (!genPrompt.value) {
      const latestGen = jobs.value.find((job) => job.kind === 'image-generation')
      const firstId = latestGen?.result?.assetIds?.[0]
      genPrompt.value = assets.value.find((asset) => asset.id === firstId)?.prompt || ''
    }
    if (!providers.value.some((item) => (item.modelId || '') === selectedModelId.value)) {
      selectedModelId.value = providers.value[0] ? providers.value[0].modelId || '' : ''
    }
  } catch (err) {
    error.value = err.message
  } finally {
    assetsLoading.value = false
  }
}

function startPolling() {
  if (pollTimer) return
  pollRounds = 0
  pollTimer = setInterval(async () => {
    pollRounds += 1
    if (pollRounds > MAX_POLL_ROUNDS) {
      stopPolling()
      error.value = '任务轮询超时（3 分钟），请稍后刷新页面确认结果'
      return
    }
    try {
      const data = await api.get('/jobs?limit=20')
      jobs.value = data.items || []
      const active = jobs.value.some((job) => ['queued', 'running', 'retrying'].includes(job.status))
      if (!active) {
        stopPolling()
        await loadAssets()
      }
    } catch (err) {
      stopPolling()
      error.value = `任务状态查询中断：${err.message}`
    }
  }, 1500)
}

function onReferencePick(event) {
  const files = Array.from(event.target.files || []).filter((file) =>
    file.type.startsWith('image/'),
  )
  const room = REFERENCE_LIMIT - referenceFiles.value.length
  if (room <= 0) {
    error.value = `参考图最多 ${REFERENCE_LIMIT} 张`
  } else {
    if (files.length > room) error.value = `参考图最多 ${REFERENCE_LIMIT} 张，已忽略多余文件`
    referenceFiles.value = [
      ...referenceFiles.value,
      ...files.slice(0, room).map((file) => ({ file, url: URL.createObjectURL(file) })),
    ]
  }
  event.target.value = ''
}

function removeReference(index) {
  const [removed] = referenceFiles.value.splice(index, 1)
  if (removed?.url) URL.revokeObjectURL(removed.url)
}

function releaseReferences() {
  for (const item of referenceFiles.value) {
    if (item.url) URL.revokeObjectURL(item.url)
  }
  referenceFiles.value = []
}

function resetForm() {
  if (busy.value) return
  form.prompt = ''
  form.count = 1
  appliedPrompt.value = ''
  releaseReferences()
}

async function generate() {
  if (!form.prompt.trim()) return
  busy.value = true
  error.value = ''
  try {
    // 参考图先落到素材库，任务里只传 assetId，队列负载保持轻量
    const referenceAssetIds = []
    for (const item of referenceFiles.value) {
      const session = await api.post('/assets/upload-sessions?maxBytes=15728640')
      const upload = new FormData()
      upload.append('file', item.file)
      const created = await api.upload(
        `/assets?sessionId=${encodeURIComponent(session.sessionId)}`,
        upload,
      )
      referenceAssetIds.push(created.id)
    }
    const res = await api.post('/ai/image-generations', {
      prompt: form.prompt.trim(),
      aspectRatio: form.aspectRatio,
      count: Number(form.count),
      modelId: selectedModelId.value || null,
      referenceAssetIds,
      builtinReferenceStyleNumber: handdrawStyle.value?.number || null,
    })
    genJobId.value = res.jobId
    genPrompt.value = form.prompt.trim()
    jobs.value = [
      {
        id: res.jobId,
        queue: 'ai-image',
        kind: 'image-generation',
        status: 'queued',
        progress: 0,
        result: {},
        createdAt: new Date().toISOString(),
      },
      ...jobs.value.filter((job) => job.id !== res.jobId),
    ]
    startPolling()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

function openPreview(asset) {
  previewAsset.value = asset
}

function openResultPreview(item) {
  previewAsset.value = { ...item }
}

function closePreview() {
  previewAsset.value = null
}

function onPreviewKeydown(event) {
  if (event.key !== 'Escape') return
  if (previewAsset.value) closePreview()
  else if (promptPickerOpen.value) promptPickerOpen.value = false
}

async function copyPrompt(asset) {
  const text = asset?.prompt || ''
  if (!text) return
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(text)
    copiedId.value = asset.id
    if (copiedTimer) clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => {
      copiedId.value = ''
      copiedTimer = null
    }, 1600)
  } catch {
    error.value = '复制失败，请打开大图后手动选中提示词复制'
  }
}

/** 下载走的都是签名直链：同源时 fetch 拿 blob，避免 <a download> 跨域失效 */
function downloadFileName(asset, blobType) {
  const ext = (blobType?.split('/')[1] || 'png').replace('jpeg', 'jpg')
  const base = String(asset?.originalName || 'generated-image')
    .replace(/\.[^.]+$/, '')
    .replace(/[\\/:*?"<>|\s]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
  return `${base || 'generated-image'}.${ext}`
}

async function downloadAsset(asset) {
  if (!asset?.url || downloadingId.value) return
  const key = asset.id || asset.url
  downloadingId.value = key
  try {
    const res = await fetch(asset.url)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const objectUrl = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = downloadFileName(asset, blob.type)
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(objectUrl), 2000)
  } catch (err) {
    error.value = `下载失败：${err.message || '请稍后重试'}`
  } finally {
    downloadingId.value = ''
  }
}

function statusLabel(status) {
  return (
    {
      queued: '排队中',
      running: '生成中',
      retrying: '重试中',
      succeeded: '已完成',
      failed: '失败',
      cancelled: '已取消',
    }[status] || status
  )
}

function jobTone(status) {
  if (status === 'succeeded') return 'tag-success'
  if (status === 'failed' || status === 'cancelled') return 'tag-danger'
  return 'tag-warning'
}

function sizeLabel(bytes) {
  if (!bytes) return '—'
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

onMounted(async () => {
  loadPromptLibrary()
  // 手绘风格要按当前供应商的模型代号判定激活策略，所以先等供应商状态回来
  await load()
  await applyHanddrawQuery()
  document.addEventListener('keydown', onPreviewKeydown)
})
onBeforeUnmount(() => {
  stopPolling()
  stopWaitTimer()
  releaseReferences()
  revokeHanddrawThumb()
  for (const url of styleThumbs.value.values()) URL.revokeObjectURL(url)
  styleThumbs.value = new Map()
  if (styleSearchTimer) clearTimeout(styleSearchTimer)
  document.removeEventListener('keydown', onPreviewKeydown)
  if (copiedTimer) clearTimeout(copiedTimer)
})
</script>

<template>
  <div class="page">
    <div class="gen-head">
      <div class="gen-head-main">
        <h1 class="page-title">图片生成</h1>
        <span class="gen-head-note">文生图与参考图生图；结果自动存入素材库，可用于封面、正文配图或画布节点。</span>
      </div>
      <div class="row row-tight">
        <span class="tag" :class="provider.available ? 'tag-success' : 'tag-warning'">
          {{ provider.available ? `${providers.length} 个供应商可用` : '供应商未接入' }}
        </span>
        <button class="btn btn-ghost btn-sm" @click="load">刷新</button>
      </div>
    </div>

    <div v-if="error" class="banner banner-danger" role="alert">
      {{ error }}
      <button class="btn btn-ghost btn-sm" @click="error = ''">知道了</button>
    </div>

    <div class="gen-layout">
      <!-- 创建任务 -->
      <section class="card gen-form">
        <h2 class="section-title">创建生图任务</h2>
        <p class="field-hint" style="margin-top: 4px">
          可附加最多 {{ REFERENCE_LIMIT }} 张参考图做图生图；生成成功后图片自动存入素材库。
        </p>

        <label v-if="providers.length" class="field" style="margin-top: var(--space-3)">
          <span class="field-label">供应商</span>
          <select v-model="selectedModelId" class="select">
            <option v-for="item in providers" :key="item.modelId || 'env'" :value="item.modelId || ''">
              {{ item.label }}
            </option>
          </select>
        </label>
        <p v-if="providers.length" class="field-hint" style="margin-top: 4px">
          当前使用：{{ selectedProvider?.label || '默认供应商' }}
          <template v-if="selectedProvider?.source === 'env'">（环境变量配置）</template>
        </p>

        <div class="field gen-prompt-field" style="margin-top: var(--space-3)">
          <div class="row row-tight" style="gap: var(--space-2)">
            <label class="field-label" for="gen-prompt">提示词</label>
            <div class="spacer" />
            <button class="btn btn-ghost btn-sm" type="button" @click="openPromptPicker">
              提示词库
            </button>
          </div>
          <textarea
            id="gen-prompt"
            v-model="form.prompt"
            class="textarea gen-prompt"
            placeholder="例如：一只橘猫坐在木质窗台上晒太阳，水彩插画风格，柔和光线"
            @input="appliedPrompt = ''"
          />
        </div>
        <p v-if="appliedPrompt" class="field-hint" style="margin-top: var(--space-2)">
          已套用模板：{{ appliedPrompt }}
        </p>
        <p class="field-hint" style="margin-top: var(--space-2)">
          提示：真实人物写真、性感或身体特写类描述容易被上游安全策略拒绝并返回 400；
          插画、水彩、景物或虚构角色更稳定。
        </p>

        <div class="field" style="margin-top: var(--space-3)">
          <div class="row row-tight">
            <span class="field-label">手绘风格（可选）</span>
            <div class="spacer" />
            <button class="btn btn-ghost btn-sm" type="button" @click="openStylePicker">
              选择风格
            </button>
          </div>
          <div v-if="handdrawStyle" class="handdraw-chip">
            <img v-if="handdrawThumb" :src="handdrawThumb" :alt="handdrawStyle.generationName" />
            <div class="handdraw-chip-main">
              <span class="handdraw-chip-title">{{ handdrawStyle.generationName }}</span>
              <span class="field-hint">
                {{ handdrawStyle.number }} · {{ handdrawStyle.referenceAuthor }}
                <template v-if="handdrawCompose?.needReference"> · 出图时自动附加参考图</template>
              </span>
            </div>
            <button class="btn btn-ghost btn-sm" type="button" @click="clearHanddrawStyle">
              移除
            </button>
          </div>
          <p v-else class="field-hint">
            未选择；点「选择风格」按画风合成提示词，不用离开本页。
          </p>
        </div>

        <div class="field" style="margin-top: var(--space-3)">
          <span class="field-label">参考图（最多 {{ REFERENCE_LIMIT }} 张，可选）</span>
          <div class="row row-tight">
            <label class="btn btn-ghost btn-sm" :class="{ 'is-disabled': !canAddReference }">
              选择文件
              <input
                type="file"
                accept="image/*"
                multiple
                hidden
                :disabled="!canAddReference"
                @change="onReferencePick"
              />
            </label>
            <span class="field-hint">{{ referenceHint }}</span>
          </div>
          <div v-if="referenceFiles.length" class="ref-grid">
            <figure v-for="(item, index) in referenceFiles" :key="item.url" class="ref-item">
              <img :src="item.url" :alt="`参考图 ${index + 1}`" />
              <button
                class="ref-remove"
                type="button"
                aria-label="移除参考图"
                @click="removeReference(index)"
              >
                ✕
              </button>
            </figure>
          </div>
          <p class="field-hint">
            不选参考图走文生图；选参考图走图生图，需要供应商支持 /images/edits。
          </p>
        </div>

        <div class="row row-tight" style="flex-wrap: wrap; margin-top: var(--space-3)">
          <label class="field" style="width: 130px">
            <span class="field-label">比例</span>
            <select v-model="form.aspectRatio" class="select">
              <option value="3:4">3:4</option>
              <option value="1:1">1:1</option>
              <option value="16:9">16:9</option>
              <option value="9:16">9:16</option>
            </select>
          </label>
          <label class="field" style="width: 110px">
            <span class="field-label">数量</span>
            <select v-model.number="form.count" class="select">
              <option :value="1">1</option>
              <option :value="2">2</option>
              <option :value="3">3</option>
              <option :value="4">4</option>
            </select>
          </label>
          <div class="spacer" />
          <button
            class="btn btn-sm"
            :disabled="!provider.available || !form.prompt.trim() || busy"
            @click="generate"
          >
            {{ busy ? '提交中…' : '生成图片' }}
          </button>
          <button class="btn btn-ghost btn-sm" :disabled="busy" @click="resetForm">清空</button>
        </div>

        <div v-if="!provider.available" class="banner banner-warning" style="margin-top: var(--space-3)">
          未配置图像生成供应商时不会生成占位图。可在设置中添加支持 image.generate 能力的模型，
          或设置 IMAGE_PROVIDER_BASE_URL / IMAGE_PROVIDER_API_KEY / IMAGE_PROVIDER_MODEL 后重启服务。
        </div>
      </section>

      <!-- 生成结果 -->
      <section class="card gen-result">
        <div class="row">
          <h2 class="section-title">生成结果</h2>
          <div class="spacer" />
          <span v-if="currentGenJob" class="tag" :class="jobTone(currentGenJob.status)">
            {{ statusLabel(currentGenJob.status) }}
          </span>
        </div>
        <div
          v-if="genAssets.length"
          class="result-stage"
          :class="{ 'is-multi': genAssets.length > 1 }"
          style="margin-top: var(--space-3)"
        >
          <figure v-for="item in genAssets" :key="item.id || item.url" class="result-item">
            <button class="result-open" type="button" @click="openResultPreview(item)">
              <img :src="item.url" alt="生成结果" />
            </button>
            <button
              class="result-dl"
              type="button"
              :disabled="downloadingId === (item.id || item.url)"
              @click="downloadAsset(item)"
            >
              {{ downloadingId === (item.id || item.url) ? '下载中…' : '下载' }}
            </button>
          </figure>
        </div>
        <div v-else-if="genBusy" class="gen-wait" style="margin-top: var(--space-3)" role="status" aria-live="polite">
          <div class="gen-wait-stage" :style="{ aspectRatio: waitAspect }">
            <div class="gen-wait-canvas" aria-hidden="true">
              <span class="gen-wait-scan" />
              <span class="gen-wait-ring" />
              <span class="gen-wait-ring gen-wait-ring--late" />
              <svg
                class="gen-wait-icon"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <rect x="3" y="4.5" width="18" height="15" rx="2.5" />
                <circle cx="8.5" cy="9.8" r="1.4" />
                <path d="M4.5 17.5l4.3-4.3 3.4 3.4 2.6-2.6 4.7 3.5" />
              </svg>
            </div>
          </div>
          <div class="gen-wait-meta">
            <div class="gen-wait-line">
              <span class="gen-wait-title">
                {{ waitTitle }}
                <span class="gen-wait-dots" aria-hidden="true"><i /><i /><i /></span>
              </span>
              <span class="gen-wait-time">已等待 {{ genElapsedText }}</span>
            </div>
            <div class="gen-wait-bar" aria-hidden="true"><span /></div>
            <p class="field-hint">
              任务 {{ currentGenJob.id }} · 完成后自动显示，期间可切换到其他页面。
            </p>
          </div>
        </div>
        <div v-else class="result-empty" style="margin-top: var(--space-3)">
          <template v-if="currentGenJob?.status === 'failed'">
            <span class="empty-title" style="color: var(--color-danger)">生成失败</span>
            <span>{{ currentGenJob.errorCode }} · {{ currentGenJob.errorMessage }}</span>
          </template>
          <template v-else>
            <span>生成完成后图片显示在这里。</span>
          </template>
        </div>
        <div v-if="genAssets.length" class="result-foot">
          <p class="result-prompt">{{ genPrompt || '本次生成的提示词' }}</p>
          <button
            class="btn btn-ghost btn-sm"
            type="button"
            @click="copyPrompt({ id: currentGenJob?.id || 'gen', prompt: genPrompt })"
          >
            {{ copiedId === (currentGenJob?.id || 'gen') ? '已复制' : '复制提示词' }}
          </button>
        </div>
      </section>
    </div>

    <!-- 最近生成 -->
    <section class="card">
      <div class="row">
        <h2 class="section-title">最近生成</h2>
        <div class="spacer" />
        <span class="field-hint">{{ recentGenerated.length }} 张</span>
      </div>
      <div v-if="assetsLoading" class="skeleton" style="height: 120px; margin-top: var(--space-3)" />
      <div v-else-if="!recentGenerated.length" class="empty-state" style="margin-top: var(--space-3)">
        <span class="empty-title">还没有生成记录</span>
        <span>在上方输入提示词生成第一张图片，完成后会出现在这里与素材库。</span>
      </div>
      <div v-else class="recent-grid" style="margin-top: var(--space-3)">
        <button
          v-for="asset in recentGenerated"
          :key="asset.id"
          class="recent-item"
          type="button"
          :title="asset.prompt || asset.originalName"
          @click="openPreview(asset)"
        >
          <img :src="asset.url" :alt="asset.originalName" />
          <span v-if="asset.prompt" class="recent-prompt">{{ asset.prompt }}</span>
        </button>
      </div>
    </section>

    <!-- 大图预览 -->
    <div v-if="previewAsset" class="modal-backdrop" role="presentation" @click.self="closePreview">
      <div
        class="modal preview-modal"
        role="dialog"
        aria-modal="true"
        :aria-label="`图片预览：${previewAsset.originalName}`"
      >
        <div class="row" style="margin-bottom: var(--space-3)">
          <h2 class="modal-title" style="margin: 0; overflow: hidden; text-overflow: ellipsis">
            {{ previewAsset.originalName }}
          </h2>
          <div class="spacer" />
          <button
            v-if="previewAsset.url"
            class="btn btn-ghost btn-sm"
            type="button"
            :disabled="downloadingId === (previewAsset.id || previewAsset.url)"
            @click="downloadAsset(previewAsset)"
          >
            {{ downloadingId === (previewAsset.id || previewAsset.url) ? '下载中…' : '下载图片' }}
          </button>
          <button class="icon-btn" aria-label="关闭" @click="closePreview">✕</button>
        </div>
        <div class="preview-body">
          <div class="preview-figure">
            <img
              v-if="previewAsset.url"
              :src="previewAsset.url"
              :alt="previewAsset.originalName"
              class="preview-image"
            />
            <div v-else class="asset-placeholder" style="height: 240px">▣</div>
          </div>
          <div class="preview-side">
            <p class="field-hint" style="margin: 0">
              {{ previewAsset.width ? `${previewAsset.width}×${previewAsset.height}` : '尺寸未知' }} ·
              {{ sizeLabel(previewAsset.sizeBytes) }} · {{ previewAsset.sourceType }}
            </p>
            <template v-if="previewAsset.prompt">
              <span class="field-label">生成提示词</span>
              <p class="preview-prompt">{{ previewAsset.prompt }}</p>
              <button class="btn btn-sm" type="button" @click="copyPrompt(previewAsset)">
                {{ copiedId === previewAsset.id ? '已复制' : '复制提示词' }}
              </button>
            </template>
            <p v-else class="field-hint" style="margin: 0">该图片没有记录生成提示词。</p>
          </div>
        </div>
      </div>
    </div>

    <!-- 提示词库选择 -->
    <div
      v-if="promptPickerOpen"
      class="modal-backdrop"
      role="presentation"
      @click.self="promptPickerOpen = false"
    >
      <div class="modal prompt-picker" role="dialog" aria-modal="true" aria-label="选择提示词模板">
        <div class="row">
          <h2 class="modal-title" style="margin: 0">提示词库</h2>
          <div class="spacer" />
          <button class="icon-btn" aria-label="关闭" @click="promptPickerOpen = false">✕</button>
        </div>
        <div class="row prompt-picker-filter" style="margin-top: var(--space-3)">
          <input
            v-model="promptKeyword"
            class="input"
            type="search"
            placeholder="搜索名称、内容或标签"
            aria-label="搜索提示词"
          />
          <select v-model="promptCategory" class="select" aria-label="按分类筛选">
            <option value="">全部分类</option>
            <option v-for="item in promptCategories" :key="item" :value="item">
              {{ item }}
            </option>
          </select>
        </div>

        <div v-if="promptLibraryError" class="banner banner-danger" style="margin-top: var(--space-3)">
          {{ promptLibraryError }}
          <button class="btn btn-ghost btn-sm" type="button" @click="loadPromptLibrary">重试</button>
        </div>

        <div v-if="promptLibraryLoading" class="skeleton" style="height: 96px; margin-top: var(--space-3)" />
        <div v-else-if="promptLibraryError" class="picker-empty">
          <span>暂时无法加载提示词，请稍后重试。</span>
        </div>
        <div v-else-if="!promptLibrary.length" class="picker-empty">
          <span>提示词库还是空的，先去维护几条常用模板。</span>
          <button class="btn btn-sm" type="button" @click="goPromptLibrary">去维护提示词</button>
        </div>
        <div v-else-if="!filteredPrompts.length" class="picker-empty">
          <span>没有匹配的提示词，换个关键词试试。</span>
        </div>
        <div v-else class="picker-list">
          <div v-for="item in filteredPrompts" :key="item.id" class="picker-item">
            <div class="picker-item-main">
              <div class="picker-item-head">
                <strong>{{ item.title }}</strong>
                <span class="tag tag-accent">{{ item.category }}</span>
              </div>
              <p class="picker-item-content">{{ item.content }}</p>
            </div>
            <div class="picker-item-actions">
              <button class="btn btn-sm" type="button" @click="applyPrompt(item, 'replace')">
                使用
              </button>
              <button class="btn btn-ghost btn-sm" type="button" @click="applyPrompt(item, 'append')">
                追加
              </button>
            </div>
          </div>
        </div>

        <div class="row" style="margin-top: var(--space-3)">
          <span class="field-hint">使用=替换当前提示词；追加=保留已输入内容并接在后面。</span>
          <div class="spacer" />
          <button class="btn btn-ghost btn-sm" type="button" @click="goPromptLibrary">
            管理提示词
          </button>
        </div>
      </div>
    </div>

    <!-- 手绘风格选择：就地选，不跳页 -->
    <div
      v-if="stylePickerOpen"
      class="modal-backdrop"
      role="presentation"
      @click.self="stylePickerOpen = false"
    >
      <div class="modal style-picker" role="dialog" aria-modal="true" aria-label="选择手绘风格">
        <div class="row">
          <h2 class="modal-title" style="margin: 0">选择手绘风格</h2>
          <div class="spacer" />
          <button class="icon-btn" aria-label="关闭" @click="stylePickerOpen = false">✕</button>
        </div>
        <div class="row prompt-picker-filter" style="margin-top: var(--space-3)">
          <input
            v-model="stylePickerKeyword"
            class="input"
            type="search"
            placeholder="搜索风格名称、作者或编号"
            aria-label="搜索手绘风格"
            @input="onStyleSearch"
          />
        </div>
        <div class="row row-tight style-picker-groups">
          <button
            class="tag"
            :class="{ 'tag-accent': !stylePickerGroup }"
            type="button"
            @click="pickStyleGroup('')"
          >
            全部
          </button>
          <button
            v-for="item in stylePickerGroups"
            :key="item.key"
            class="tag"
            :class="{ 'tag-accent': stylePickerGroup === item.key }"
            type="button"
            @click="pickStyleGroup(item.key)"
          >
            {{ item.key }} · {{ item.count }}
          </button>
        </div>

        <div v-if="stylePickerError" class="banner banner-danger" style="margin-top: var(--space-3)">
          {{ stylePickerError }}
          <button class="btn btn-ghost btn-sm" type="button" @click="loadStylePicker">重试</button>
        </div>

        <div
          v-if="stylePickerLoading && !stylePickerItems.length"
          class="skeleton"
          style="height: 200px; margin-top: var(--space-3)"
        />
        <div v-else-if="!stylePickerItems.length" class="picker-empty">
          <span>没有匹配的风格，换个关键词或分组试试。</span>
        </div>
        <div v-else class="style-picker-grid">
          <button
            v-for="item in stylePickerItems"
            :key="item.number"
            class="style-picker-card"
            :class="{ 'is-active': handdrawStyle?.number === item.number }"
            type="button"
            @click="applyPickedStyle(item)"
          >
            <span class="style-picker-thumb">
              <img v-if="styleThumb(item.number)" :src="styleThumb(item.number)" :alt="item.generationName" />
              <span v-else>{{ item.referenceAvailable ? '加载中' : '无参考图' }}</span>
            </span>
            <span class="style-picker-name">{{ item.generationName }}</span>
          </button>
        </div>

        <div v-if="stylePickerPages > 1" class="row" style="margin-top: var(--space-3)">
          <button
            class="btn btn-ghost btn-sm"
            type="button"
            :disabled="stylePickerPage <= 1"
            @click="goStylePage(-1)"
          >
            上一页
          </button>
          <span class="field-hint">{{ stylePickerPage }} / {{ stylePickerPages }}</span>
          <button
            class="btn btn-ghost btn-sm"
            type="button"
            :disabled="stylePickerPage >= stylePickerPages"
            @click="goStylePage(1)"
          >
            下一页
          </button>
          <div class="spacer" />
          <button class="btn btn-ghost btn-sm" type="button" @click="goHanddrawLibrary">
            打开风格库
          </button>
        </div>
        <p class="field-hint" style="margin-top: var(--space-2)">
          选中后会按当前提示词内容合成画风提示词；需要主题色和参考图说明时去风格库。
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.handdraw-chip {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
}
.handdraw-chip img {
  width: 44px;
  height: 44px;
  flex: none;
  object-fit: cover;
  border-radius: var(--radius-sm);
}
.handdraw-chip-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}
.handdraw-chip-title {
  font-size: var(--font-sm);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 页头压成一行：标题与说明同基线，操作靠右，避免和顶栏标题重复占两行 */
.gen-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.gen-head-main {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
  min-width: 0;
}
.gen-head-note {
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}
.section-title {
  margin: 0;
  font-size: var(--font-section);
  font-weight: 600;
  color: var(--color-text-primary);
}
.gen-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  gap: var(--space-4);
  /* 两栏等高：左栏不会因为右侧结果图更高而留出一大片空白 */
  align-items: stretch;
}
.gen-form {
  display: flex;
  flex-direction: column;
}
.gen-result {
  display: flex;
  flex-direction: column;
}
.gen-prompt-field {
  flex: 1;
  min-height: 0;
}
.gen-prompt {
  flex: 1;
  min-height: 140px;
}
.ref-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-2);
}
.ref-item {
  position: relative;
  width: 72px;
  height: 72px;
  margin: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 10px);
  overflow: hidden;
}
.ref-item img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.ref-remove {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
  display: grid;
  place-items: center;
}
.btn.is-disabled {
  opacity: 0.5;
  pointer-events: none;
}
/* 生成结果：单图铺满结果栏宽度、保持原比例不裁切；多图两列 */
.result-stage {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: var(--space-2);
}
.result-stage.is-multi {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.result-item {
  position: relative;
  margin: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 10px);
  overflow: hidden;
  background: var(--color-bg);
}
.result-open {
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  background: none;
  line-height: 0;
  cursor: zoom-in;
}
.result-open img {
  display: block;
  width: 100%;
  height: auto;
  max-height: 68vh;
  object-fit: contain;
}
.result-dl {
  position: absolute;
  right: var(--space-2);
  bottom: var(--space-2);
  height: 26px;
  padding: 0 12px;
  font-family: inherit;
  font-size: var(--font-xs);
  color: var(--color-text-primary);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  cursor: pointer;
  opacity: 0.92;
  transition: opacity 0.15s ease;
}
.result-dl:hover:not(:disabled) {
  opacity: 1;
}
.result-dl:disabled {
  cursor: default;
  color: var(--color-text-muted);
}
.result-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  flex: 1;
  min-height: 240px;
  padding: var(--space-4);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md, 10px);
  font-size: var(--font-sm);
  color: var(--color-text-muted);
  text-align: center;
}
/* 生成中等待动效：画布底纹 + 扫描光束 + 呼吸光环 */
.gen-wait {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  flex: 1;
  min-height: 0;
}
.gen-wait-stage {
  position: relative;
  width: 100%;
  max-height: 46vh;
  min-height: 220px;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg, 14px);
  background:
    radial-gradient(120% 95% at 50% 108%, color-mix(in srgb, var(--color-accent) 15%, transparent), transparent 62%),
    var(--color-bg);
  isolation: isolate;
}
.gen-wait-stage::before {
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(to right, color-mix(in srgb, var(--color-border-strong) 45%, transparent) 1px, transparent 1px),
    linear-gradient(to bottom, color-mix(in srgb, var(--color-border-strong) 45%, transparent) 1px, transparent 1px);
  background-size: 36px 36px;
  opacity: 0.45;
  mask-image: radial-gradient(78% 78% at 50% 50%, #000 20%, transparent 80%);
}
.gen-wait-stage::after {
  content: '';
  position: absolute;
  inset: -25%;
  background: radial-gradient(
    42% 42% at 50% 50%,
    color-mix(in srgb, var(--color-accent) 18%, transparent),
    transparent 72%
  );
  animation: gen-wait-breathe 4.6s ease-in-out infinite;
}
.gen-wait-canvas {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: var(--color-accent);
}
.gen-wait-scan {
  position: absolute;
  top: -45%;
  left: 0;
  right: 0;
  height: 42%;
  background: linear-gradient(
    to bottom,
    transparent,
    color-mix(in srgb, var(--color-accent) 9%, transparent) 55%,
    color-mix(in srgb, var(--color-accent) 20%, transparent)
  );
  border-bottom: 1px solid color-mix(in srgb, var(--color-accent) 34%, transparent);
  animation: gen-wait-scan 4.4s cubic-bezier(0.45, 0, 0.55, 1) infinite;
}
.gen-wait-ring {
  position: absolute;
  width: 76px;
  height: 76px;
  border: 1px solid color-mix(in srgb, var(--color-accent) 36%, transparent);
  border-radius: 50%;
  animation: gen-wait-ripple 3.2s ease-out infinite;
}
.gen-wait-ring--late {
  animation-delay: 1.6s;
}
.gen-wait-icon {
  position: relative;
  width: 46px;
  height: 46px;
  opacity: 0.92;
  animation: gen-wait-float 3.4s ease-in-out infinite;
}
.gen-wait-meta {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.gen-wait-line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: var(--font-sm);
}
.gen-wait-title {
  font-weight: 600;
  color: var(--color-text-primary);
}
.gen-wait-dots {
  display: inline-flex;
  gap: 3px;
  margin-left: 6px;
}
.gen-wait-dots i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--color-accent);
  animation: gen-wait-blink 1.4s ease-in-out infinite;
}
.gen-wait-dots i:nth-child(2) {
  animation-delay: 0.2s;
}
.gen-wait-dots i:nth-child(3) {
  animation-delay: 0.4s;
}
.gen-wait-time {
  font-size: var(--font-xs);
  font-variant-numeric: tabular-nums;
  color: var(--color-text-muted);
}
.gen-wait-bar {
  position: relative;
  height: 3px;
  overflow: hidden;
  border-radius: 999px;
  background: color-mix(in srgb, var(--color-accent) 12%, transparent);
}
.gen-wait-bar span {
  position: absolute;
  top: 0;
  bottom: 0;
  left: -40%;
  width: 38%;
  border-radius: 999px;
  background: linear-gradient(90deg, transparent, var(--color-accent), transparent);
  animation: gen-wait-slide 1.8s ease-in-out infinite;
}
@keyframes gen-wait-breathe {
  0%,
  100% {
    opacity: 0.55;
    transform: scale(0.94);
  }
  50% {
    opacity: 1;
    transform: scale(1.04);
  }
}
@keyframes gen-wait-scan {
  0% {
    top: -45%;
    opacity: 0;
  }
  12% {
    opacity: 0.9;
  }
  88% {
    opacity: 0.9;
  }
  100% {
    top: 104%;
    opacity: 0;
  }
}
@keyframes gen-wait-ripple {
  0% {
    transform: scale(0.6);
    opacity: 0.7;
  }
  100% {
    transform: scale(1.7);
    opacity: 0;
  }
}
@keyframes gen-wait-float {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-5px);
  }
}
@keyframes gen-wait-blink {
  0%,
  100% {
    opacity: 0.25;
    transform: translateY(0);
  }
  40% {
    opacity: 1;
    transform: translateY(-2px);
  }
}
@keyframes gen-wait-slide {
  0% {
    left: -40%;
  }
  100% {
    left: 100%;
  }
}
.result-foot {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin-top: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
}
.result-prompt {
  flex: 1;
  margin: 0;
  font-size: var(--font-xs);
  line-height: 1.6;
  color: var(--color-text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.recent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: var(--space-3);
}
.recent-item {
  padding: 0;
  text-align: left;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 10px);
  background: var(--color-surface);
  color: var(--color-text-primary);
  overflow: hidden;
  cursor: zoom-in;
}
.recent-item img {
  display: block;
  width: 100%;
  height: 120px;
  object-fit: cover;
  background: var(--color-bg);
}
.recent-prompt {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  padding: var(--space-2);
  font-size: var(--font-xs);
  line-height: 1.5;
  color: var(--color-text-secondary);
}
.empty-state {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--space-4);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md, 10px);
  font-size: var(--font-sm);
}
.empty-title {
  font-weight: 600;
}
.preview-modal {
  width: min(880px, 100%);
}
.preview-body {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
  gap: var(--space-4);
}
.preview-figure {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 10px);
  overflow: hidden;
  min-height: 200px;
}
.preview-image {
  display: block;
  width: auto;
  height: auto;
  max-width: 100%;
  max-height: 62vh;
  margin: 0 auto;
  object-fit: contain;
}
.preview-side {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}
.preview-prompt {
  margin: 0;
  padding: var(--space-3);
  max-height: 240px;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 10px);
  background: var(--color-bg);
  font-size: var(--font-sm);
  line-height: 1.65;
  color: var(--color-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  user-select: text;
}
/* 提示词库选择弹窗 */
.prompt-picker {
  width: min(680px, 100%);
}
.style-picker {
  width: min(760px, 100%);
}
.style-picker-groups {
  margin-top: var(--space-3);
  max-height: 96px;
  overflow-y: auto;
}
.style-picker-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: var(--space-2);
  margin-top: var(--space-3);
  max-height: 48vh;
  overflow-y: auto;
}
.style-picker-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 10px);
  background: var(--color-surface-raised);
  color: inherit;
  font: inherit;
  cursor: pointer;
  text-align: left;
}
.style-picker-card:hover {
  border-color: var(--color-accent);
}
.style-picker-card.is-active {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent) inset;
}
.style-picker-thumb {
  display: flex;
  align-items: center;
  justify-content: center;
  aspect-ratio: 1;
  overflow: hidden;
  border-radius: var(--radius-sm, 6px);
  background: var(--color-surface-muted, var(--color-surface));
  font-size: var(--font-xs, 12px);
  color: var(--color-text-secondary);
}
.style-picker-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.style-picker-name {
  font-size: var(--font-xs, 12px);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.prompt-picker-filter .input {
  flex: 1 1 200px;
  min-width: 0;
}
.prompt-picker-filter .select {
  width: 150px;
}
.picker-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
  max-height: 46vh;
  overflow-y: auto;
}
.picker-item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 10px);
  background: var(--color-surface-raised);
}
.picker-item-main {
  flex: 1;
  min-width: 0;
}
.picker-item-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
.picker-item-head strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.picker-item-content {
  margin: 4px 0 0;
  font-size: var(--font-xs);
  line-height: 1.6;
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.picker-item-actions {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  flex: 0 0 auto;
}
.picker-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-6) var(--space-3);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  text-align: center;
}
@media (max-width: 1023px) {
  .gen-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 767px) {
  .prompt-picker-filter .select {
    width: 100%;
  }
  .picker-item {
    flex-direction: column;
  }
  .picker-item-actions {
    flex-direction: row;
    align-self: flex-end;
  }
  .preview-body {
    grid-template-columns: minmax(0, 1fr);
  }
  .preview-image {
    max-height: 46vh;
  }
  .result-stage.is-multi {
    grid-template-columns: minmax(0, 1fr);
  }
  .result-open img {
    max-height: none;
  }
}
</style>
