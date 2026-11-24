<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { NAlert, NButton, NInput, NInputNumber, NModal, NSelect } from 'naive-ui'
import { api, apiUrl, fetchBlobUrl } from '../api/client'
import { confirm, toast } from '../ui/feedback'

const OPERATIONS = [
  { value: 'thumbnail', label: '生成缩略图', hint: 'maxSize' },
  { value: 'resize', label: '缩放尺寸', hint: 'size' },
  { value: 'crop', label: '按比例裁切', hint: 'ratio' },
  { value: 'compress', label: '压缩体积', hint: 'quality' },
  { value: 'convert', label: '格式转换', hint: 'format' },
  { value: 'watermark', label: '添加水印', hint: 'text' },
  { value: 'split_panels', label: '条漫分格', hint: 'panels' },
]
const operationOptions = OPERATIONS.map((op) => ({ label: op.label, value: op.value }))

const SOURCE_OPTIONS = [
  { value: 'upload', label: '手动上传' },
  { value: 'generated', label: 'AI 生成' },
  { value: 'derived', label: '处理派生' },
]

const MEDIA_OPTIONS = [
  { value: 'image', label: '图片' },
  { value: 'video', label: '视频' },
  { value: 'audio', label: '音频' },
]

const MEDIA_NOUN = { image: '图片', video: '视频', audio: '音频' }

const FORMAT_OPTIONS = [
  { value: 'png', label: 'PNG' },
  { value: 'jpeg', label: 'JPEG' },
  { value: 'webp', label: 'WebP' },
]

const TIME_OPTIONS = [
  { value: 7, label: '近 7 天' },
  { value: 30, label: '近 30 天' },
]

const SOURCE_LABELS = { upload: '上传', generated: 'AI 生成', derived: '派生', agent: 'Agent' }

const assets = ref([])
const loading = ref(true)
const error = ref('')
const uploading = ref(false)
const uploadInput = ref(null)
const FORMAT_SELECT_OPTIONS = [
  { label: 'WebP', value: 'webp' },
  { label: 'JPEG', value: 'jpeg' },
  { label: 'PNG', value: 'png' },
]
const PANEL_FORMAT_OPTIONS = [
  { label: 'JPEG', value: 'jpeg' },
  { label: 'WebP', value: 'webp' },
  { label: 'PNG', value: 'png' },
]
const selectedId = ref('')
const operation = ref('thumbnail')
const options = reactive({
  maxSize: 512,
  width: 1080,
  height: 1440,
  aspectRatio: '3:4',
  quality: 82,
  format: 'webp',
  text: '',
  minGap: 12,
  maxPanels: 12,
})
const thumbs = ref({})
const jobs = ref([])
const jobsCursor = ref('')
const jobsHasMore = ref(false)
const jobsLoadedMore = ref(false)
const jobsLoadingMore = ref(false)
const previewAsset = ref(null)
const copiedId = ref('')
const interrogating = ref(false)
const interrogateResult = ref('')
const interrogateError = ref('')
let copiedTimer = null
let pollTimer = null
let pollRounds = 0
const MAX_POLL_ROUNDS = 120 // 1.5s × 120 = 3 分钟，避免任务卡住时无限轮询

const keyword = ref('')
const searching = ref(false)
const filters = reactive({ mediaType: '', sourceType: '', format: '', days: 0 })
const filterOpen = ref(false) // 窄屏下筛选面板的抽屉开关
const panelOpen = ref(false) // 右侧图像处理 / 任务抽屉
const panelTab = ref('process')
let searchTimer = null
let assetSeq = 0
const THUMB_CONCURRENCY = 6

const selected = computed(() => assets.value.find((a) => a.id === selectedId.value) || null)
const optionHint = computed(() => OPERATIONS.find((o) => o.value === operation.value)?.hint)
const activeFilterCount = computed(
  () => [filters.mediaType, filters.sourceType, filters.format, filters.days].filter(Boolean).length,
)
const hasFilters = computed(() => Boolean(keyword.value.trim()) || activeFilterCount.value > 0)
const resultLabel = computed(() => {
  const noun = MEDIA_NOUN[filters.mediaType] || '素材'
  const narrowed = Boolean(keyword.value.trim() || filters.sourceType || filters.format || filters.days)
  return narrowed ? `匹配 ${assets.value.length} 个${noun}` : `${assets.value.length} 个${noun}`
})

function assetsUrl(q = keyword.value) {
  const params = new URLSearchParams()
  const text = (q || '').trim()
  if (text) params.set('q', text)
  if (filters.mediaType) params.set('mediaType', filters.mediaType)
  if (filters.sourceType) params.set('sourceType', filters.sourceType)
  if (filters.format) params.set('format', filters.format)
  if (filters.days) params.set('days', String(filters.days))
  const qs = params.toString()
  return qs ? `/assets?${qs}` : '/assets'
}

function reconcileSelection(list) {
  if (!list.some((a) => a.id === selectedId.value)) selectedId.value = list[0]?.id || ''
}

function releaseThumbs() {
  for (const url of Object.values(thumbs.value)) URL.revokeObjectURL(url)
  thumbs.value = {}
}

function mediaKind(asset) {
  const mime = asset?.mimeType || ''
  if (mime.startsWith('video/')) return 'video'
  if (mime.startsWith('audio/')) return 'audio'
  if (mime.startsWith('image/')) return 'image'
  return 'file'
}

function mediaGlyph(asset) {
  const kind = mediaKind(asset)
  if (kind === 'video') return '▶'
  if (kind === 'audio') return '♪'
  return '▣'
}

function isImage(asset) {
  return mediaKind(asset) === 'image'
}

async function loadThumbs(requestId) {
  const map = {}
  // 并发拉取缩略图：串行会让首屏随素材数量线性变慢，也是此前加载态的并发窗口来源
  const snapshot = assets.value.filter(isImage)
  let cursor = 0
  const worker = async () => {
    while (cursor < snapshot.length) {
      const asset = snapshot[cursor]
      cursor += 1
      if (requestId !== assetSeq) return
      try {
        map[asset.id] = await fetchBlobUrl(`/assets/${asset.id}/raw`)
      } catch {
        map[asset.id] = ''
      }
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(THUMB_CONCURRENCY, snapshot.length) }, () => worker()),
  )
  if (requestId !== assetSeq) {
    // 已有更新的请求接管，丢弃本次缩略图并释放临时 blob，避免串图
    for (const url of Object.values(map)) if (url) URL.revokeObjectURL(url)
    return
  }
  releaseThumbs()
  thumbs.value = map
}

async function load() {
  const requestId = ++assetSeq
  loading.value = true
  error.value = ''
  try {
    // 任务列表只是附属信息，拉取失败不该让整个图库变成错误页
    const [listResult, recentResult] = await Promise.allSettled([
      api.get(assetsUrl()),
      fetchJobsPage(),
    ])
    if (listResult.status === 'rejected') throw listResult.reason
    if (requestId !== assetSeq) return
    const list = listResult.value
    assets.value = list
    if (recentResult.status === 'fulfilled') {
      const recent = recentResult.value
      jobs.value = recent.items || []
      jobsCursor.value = recent.nextCursor || ''
      jobsHasMore.value = Boolean(recent.nextCursor)
      jobsLoadedMore.value = false
    }
    reconcileSelection(list)
    // 只有确实存在排队/执行中的任务才需要轮询，否则空转会把列表的刷新序号抢走
    if (hasActiveJobs()) startPolling()
    await loadThumbs(requestId)
  } catch (err) {
    if (requestId === assetSeq) error.value = err.message
  } finally {
    // loading 是所有请求共用的骨架屏开关：被新请求接管时也要收尾，否则界面会卡在骨架屏
    if (requestId === assetSeq) loading.value = false
  }
}

async function searchAssets() {
  const requestId = ++assetSeq
  searching.value = true
  error.value = ''
  try {
    const list = await api.get(assetsUrl())
    if (requestId !== assetSeq) return
    assets.value = list
    reconcileSelection(list)
    await loadThumbs(requestId)
  } catch (err) {
    if (requestId === assetSeq) error.value = `搜索素材失败：${err.message}`
  } finally {
    if (requestId === assetSeq) {
      searching.value = false
      loading.value = false
    }
  }
}

function onKeywordInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    searchTimer = null
    searchAssets()
  }, 300)
}

function searchNow() {
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  searchAssets()
}

function setFilter(key, value) {
  filters[key] = filters[key] === value ? '' : value
  if (key === 'mediaType' && filters.mediaType && filters.mediaType !== 'image') filters.format = ''
  searchNow()
}

function setDays(value) {
  filters.days = filters.days === value ? 0 : value
  searchNow()
}

function resetFilters() {
  keyword.value = ''
  filters.mediaType = ''
  filters.sourceType = ''
  filters.format = ''
  filters.days = 0
  searchNow()
}

async function refreshAssets() {
  if (loading.value) return
  const requestId = ++assetSeq
  try {
    const list = await api.get(assetsUrl())
    if (requestId !== assetSeq) return
    assets.value = list
    reconcileSelection(list)
    await loadThumbs(requestId)
  } finally {
    if (requestId === assetSeq) loading.value = false
  }
}

function fetchJobsPage(cursor = '') {
  const suffix = cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''
  return api.get(`/jobs?limit=20${suffix}`)
}

/** 轮询只刷新最新一页，已加载的历史任务保持原样并就地更新状态 */
function mergeJobs(incoming) {
  const previous = new Map(jobs.value.map((job) => [job.id, job]))
  const merged = incoming.map((job) => ({ ...(previous.get(job.id) || {}), ...job }))
  const seen = new Set(merged.map((job) => job.id))
  for (const job of jobs.value) {
    if (!seen.has(job.id)) merged.push(job)
  }
  return merged
}

/** 翻页只追加新任务，保持「新任务在前、历史任务在后」的顺序 */
function appendJobs(incoming) {
  const known = new Set(jobs.value.map((job) => job.id))
  const additions = incoming.filter((job) => !known.has(job.id))
  if (additions.length) jobs.value = [...jobs.value, ...additions]
}

async function loadMoreJobs() {
  if (jobsLoadingMore.value || !jobsHasMore.value) return
  jobsLoadingMore.value = true
  error.value = ''
  try {
    const data = await fetchJobsPage(jobsCursor.value)
    appendJobs(data.items || [])
    jobsCursor.value = data.nextCursor || ''
    jobsHasMore.value = Boolean(data.nextCursor)
    jobsLoadedMore.value = true
  } catch (err) {
    error.value = `加载更多任务失败：${err.message}`
  } finally {
    jobsLoadingMore.value = false
  }
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

function hasActiveJobs() {
  return jobs.value.some((job) => ['queued', 'running'].includes(job.status))
}

function startPolling() {
  if (pollTimer) return
  if (!hasActiveJobs()) return
  pollRounds = 0
  pollTimer = setInterval(async () => {
    pollRounds += 1
    if (pollRounds > MAX_POLL_ROUNDS) {
      stopPolling()
      error.value = '任务轮询超时（3 分钟），请刷新页面或到任务状态页确认结果'
      return
    }
    // 首屏还在加载时不要插一脚刷新，否则会顶掉正在进行的请求
    if (loading.value) return
    try {
      const data = await fetchJobsPage()
      const latest = data.items || []
      jobs.value = mergeJobs(latest)
      // 已经翻过页时保留原有游标，避免新任务插入后重复拉取旧数据
      if (!jobsLoadedMore.value) {
        jobsCursor.value = data.nextCursor || ''
        jobsHasMore.value = Boolean(data.nextCursor)
      }
      const active = latest.some((j) => ['queued', 'running'].includes(j.status))
      if (!active) {
        stopPolling()
        try {
          await refreshAssets()
        } catch (err) {
          error.value = err.message
        }
      }
    } catch (err) {
      stopPolling()
      error.value = `任务状态查询中断：${err.message}`
    }
  }, 1500)
}

async function onUpload(event) {
  const file = event.target.files?.[0]
  if (!file) return
  uploading.value = true
  error.value = ''
  try {
    // 先签发一次性上传会话，把本次限额绑定到当前用户；后端仍保留全局硬上限
    const session = await api.post('/assets/upload-sessions?maxBytes=15728640')
    const form = new FormData()
    form.append('file', file)
    const created = await api.upload(
      `/assets?sessionId=${encodeURIComponent(session.sessionId)}`,
      form,
    )
    // 新素材可能不匹配当前筛选条件，重置筛选确保上传结果可见
    keyword.value = ''
    filters.mediaType = ''
    filters.sourceType = ''
    filters.format = ''
    filters.days = 0
    if (searchTimer) {
      clearTimeout(searchTimer)
      searchTimer = null
    }
    selectedId.value = created.id
    await load()
    // 上传完成后自动打开预览；提示词反推由用户在弹窗内手动触发，避免每次上传都消耗模型调用
    const uploaded = assets.value.find((a) => a.id === created.id) || created
    openPreview(uploaded)
  } catch (err) {
    error.value = err.message
  } finally {
    uploading.value = false
    event.target.value = ''
  }
}

function buildOptions() {
  switch (operation.value) {
    case 'thumbnail':
      return { maxSize: Number(options.maxSize) }
    case 'resize':
      return { width: Number(options.width), height: Number(options.height) }
    case 'crop':
      return { aspectRatio: options.aspectRatio }
    case 'compress':
      return { quality: Number(options.quality), format: options.format }
    case 'convert':
      return { format: options.format, quality: Number(options.quality) }
    case 'watermark':
      return { text: options.text, format: options.format }
    case 'split_panels':
      return {
        minGap: Number(options.minGap),
        maxPanels: Number(options.maxPanels),
        format: options.format,
      }
    default:
      return {}
  }
}

async function runOperation() {
  if (!selected.value || !isImage(selected.value)) return
  error.value = ''
  try {
    const res = await api.post('/image-jobs/async', {
      assetId: selected.value.id,
      operation: operation.value,
      options: buildOptions(),
    })
    jobs.value = [
      { id: res.jobId, queue: 'image-processing', status: 'queued', progress: 0 },
      ...jobs.value.filter((j) => j.id !== res.jobId),
    ]
    panelTab.value = 'jobs'
    startPolling()
  } catch (err) {
    error.value = err.message
  }
}

async function remove(asset) {
  if (!asset) return
  const ok = await confirm(
    `「${asset.originalName}」删除后不可恢复，确定继续吗？`,
    '删除素材',
  )
  if (!ok) return
  error.value = ''
  try {
    await api.del(`/assets/${asset.id}`)
    if (selectedId.value === asset.id) selectedId.value = ''
    if (previewAsset.value?.id === asset.id) previewAsset.value = null
    toast(`已删除「${asset.originalName}」`)
    await load()
  } catch (err) {
    error.value = err.message
  }
}

function openPreview(asset) {
  previewAsset.value = asset
  interrogateResult.value = ''
  interrogateError.value = ''
}

function closePreview() {
  previewAsset.value = null
  interrogateResult.value = ''
  interrogateError.value = ''
}

function openProcess(asset) {
  if (asset) selectedId.value = asset.id
  panelTab.value = 'process'
  panelOpen.value = true
}

function closePanel() {
  panelOpen.value = false
}

function onPreviewKeydown(event) {
  if (event.key !== 'Escape') return
  if (previewAsset.value) closePreview()
  else if (panelOpen.value) closePanel()
}

function selectFromPreview() {
  if (!previewAsset.value) return
  openProcess(previewAsset.value)
  closePreview()
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

async function interrogatePrompt(asset) {
  if (!asset || interrogating.value) return
  interrogating.value = true
  interrogateResult.value = ''
  interrogateError.value = ''
  try {
    const res = await api.post('/ai/image-interrogate', {
      assetId: asset.id,
      saveToAsset: true,
    })
    interrogateResult.value = res.prompt || ''
    // Update the asset in-place so the card's "复制提示词" button appears without reload
    asset.prompt = res.prompt || ''
    // Also sync the previewAsset ref if it's the same object
    if (previewAsset.value?.id === asset.id) {
      previewAsset.value = { ...previewAsset.value, prompt: res.prompt || '' }
    }
  } catch (e) {
    interrogateError.value = e?.message || '反推失败，请检查文本模型是否已配置'
  } finally {
    interrogating.value = false
  }
}

function sizeLabel(bytes) {
  if (!bytes) return '—'
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function dimensionLabel(asset) {
  if (!asset?.width || !asset?.height) return '尺寸未知'
  return `${asset.width}×${asset.height}`
}

function sourceLabel(value) {
  return SOURCE_LABELS[value] || value || '未知'
}

function dateLabel(iso) {
  if (!iso) return ''
  return iso.slice(5, 16).replace('T', ' ')
}

function jobTone(status) {
  if (status === 'succeeded') return 'tag-success'
  if (status === 'failed' || status === 'cancelled') return 'tag-danger'
  return 'tag-warning'
}

onMounted(() => {
  load()
  startPolling()
  document.addEventListener('keydown', onPreviewKeydown)
})
onBeforeUnmount(() => {
  stopPolling()
  releaseThumbs()
  document.removeEventListener('keydown', onPreviewKeydown)
  if (copiedTimer) clearTimeout(copiedTimer)
  if (searchTimer) clearTimeout(searchTimer)
})
</script>

<template>
  <div class="page gallery-page">
    <div class="page-head">
      <div>
        <h1 class="page-title">素材图库</h1>
        <p class="page-sub">集中浏览、搜索和管理创作素材。原图始终保留，图像处理会另存为派生素材。</p>
      </div>
      <div class="row row-tight">
        <n-button type="primary" size="small" :disabled="uploading" @click="uploadInput?.click()">
          {{ uploading ? '上传中…' : '上传图片' }}
        </n-button>
        <input ref="uploadInput" type="file" accept="image/*" hidden :disabled="uploading" @change="onUpload" />
        <n-button quaternary size="small" :disabled="loading" @click="load">刷新</n-button>
      </div>
    </div>

    <n-alert v-if="error" type="error" closable @close="error = ''">{{ error }}</n-alert>

    <div class="gallery-shell">
      <!-- 筛选侧栏 -->
      <aside class="filter-panel" :class="{ 'is-open': filterOpen }" aria-label="素材筛选">
        <div class="filter-head">
          <span class="filter-title">筛选</span>
          <span v-if="activeFilterCount" class="filter-count">{{ activeFilterCount }}</span>
          <div class="spacer" />
          <button
            class="filter-close icon-btn icon-btn-sm"
            type="button"
            aria-label="关闭筛选"
            @click="filterOpen = false"
          >
            ✕
          </button>
        </div>

        <div class="filter-group">
          <span class="filter-group-label">类型</span>
          <button
            v-for="item in MEDIA_OPTIONS"
            :key="item.value"
            type="button"
            class="filter-item"
            :class="{ 'is-active': filters.mediaType === item.value }"
            :aria-pressed="filters.mediaType === item.value"
            @click="setFilter('mediaType', item.value)"
          >
            <span class="filter-dot" :class="`dot-${item.value}`" aria-hidden="true" />
            {{ item.label }}
          </button>
        </div>

        <div class="filter-group">
          <span class="filter-group-label">来源</span>
          <button
            v-for="item in SOURCE_OPTIONS"
            :key="item.value"
            type="button"
            class="filter-item"
            :class="{ 'is-active': filters.sourceType === item.value }"
            :aria-pressed="filters.sourceType === item.value"
            @click="setFilter('sourceType', item.value)"
          >
            <span class="filter-dot" :class="`dot-${item.value}`" aria-hidden="true" />
            {{ item.label }}
          </button>
        </div>

        <div class="filter-group">
          <span class="filter-group-label">格式</span>
          <button
            v-for="item in FORMAT_OPTIONS"
            :key="item.value"
            type="button"
            class="filter-item"
            :class="{ 'is-active': filters.format === item.value }"
            :aria-pressed="filters.format === item.value"
            :disabled="filters.mediaType === 'video' || filters.mediaType === 'audio'"
            @click="setFilter('format', item.value)"
          >
            {{ item.label }}
          </button>
        </div>

        <div class="filter-group">
          <span class="filter-group-label">时间</span>
          <button
            v-for="item in TIME_OPTIONS"
            :key="item.value"
            type="button"
            class="filter-item"
            :class="{ 'is-active': filters.days === item.value }"
            :aria-pressed="filters.days === item.value"
            @click="setDays(item.value)"
          >
            {{ item.label }}
          </button>
        </div>

        <n-button v-if="hasFilters" quaternary size="small" class="filter-reset" @click="resetFilters">
          清除全部筛选
        </n-button>
      </aside>
      <div v-if="filterOpen" class="filter-scrim" @click="filterOpen = false" />

      <!-- 主区 -->
      <section class="gallery-main">
        <div class="gallery-toolbar">
          <n-input
            v-model:value="keyword"
            class="gallery-search-input"
            clearable
            placeholder="搜索名称或提示词"
            aria-label="按关键字搜索素材"
            @update:value="onKeywordInput"
            @keydown.enter.prevent="searchNow"
          />
          <n-button quaternary size="small" class="filter-toggle" @click="filterOpen = true">
            筛选<span v-if="activeFilterCount"> {{ activeFilterCount }}</span>
          </n-button>
          <div class="spacer" />
          <span v-if="searching" class="field-hint">搜索中…</span>
          <span v-else class="field-hint">
            {{ resultLabel }}
          </span>
        </div>

        <div v-if="loading" class="gallery-grid">
          <div v-for="n in 8" :key="n" class="skeleton skeleton-card" />
        </div>

        <div v-else-if="!assets.length && hasFilters" class="empty-state gallery-empty">
          <span class="empty-title">没有匹配的素材</span>
          <span>换个关键字或放宽筛选条件试试。</span>
          <n-button quaternary size="small" style="align-self: flex-start" @click="resetFilters">
            清除筛选
          </n-button>
        </div>

        <div v-else-if="!assets.length" class="empty-state gallery-empty">
          <span class="empty-title">图库还是空的</span>
          <span>点击右上角「上传图片」开始，上传后就能在这里筛选、预览和处理。</span>
        </div>

        <div v-else class="gallery-grid">
          <article
            v-for="asset in assets"
            :key="asset.id"
            class="gallery-card"
            :class="{ 'is-selected': selectedId === asset.id }"
          >
            <button
              class="gallery-thumb"
              type="button"
              :aria-label="`查看大图：${asset.originalName}`"
              @click="openPreview(asset)"
            >
              <img v-if="isImage(asset) && thumbs[asset.id]" :src="thumbs[asset.id]" :alt="asset.originalName" />
              <span v-else class="gallery-placeholder" aria-hidden="true">{{ mediaGlyph(asset) }}</span>
            </button>
            <div class="gallery-actions">
              <button class="gallery-action" type="button" @click.stop="openPreview(asset)">
                预览
              </button>
              <button
                v-if="isImage(asset)"
                class="gallery-action is-primary"
                type="button"
                @click.stop="openProcess(asset)"
              >
                处理
              </button>
            </div>
            <div class="gallery-meta">
              <button
                class="gallery-name"
                type="button"
                :title="asset.originalName"
                @click="openPreview(asset)"
              >
                {{ asset.originalName }}
              </button>
              <div class="gallery-meta-row">
                <span class="field-hint">{{ dimensionLabel(asset) }} · {{ sizeLabel(asset.sizeBytes) }}</span>
                <span class="tag tag-sm">{{ sourceLabel(asset.sourceType) }}</span>
              </div>
              <div class="gallery-meta-row">
                <span class="field-hint">{{ dateLabel(asset.createdAt) }}</span>
                <button
                  v-if="asset.prompt"
                  class="gallery-prompt-btn"
                  type="button"
                  @click.stop="copyPrompt(asset)"
                >
                  {{ copiedId === asset.id ? '已复制' : '复制提示词' }}
                </button>
              </div>
            </div>
          </article>
        </div>
      </section>
    </div>

    <!-- 图像处理 / 任务抽屉 -->
    <div v-if="panelOpen" class="drawer-scrim" @click.self="closePanel">
      <aside class="drawer" role="dialog" aria-modal="true" aria-label="图像处理面板">
        <div class="drawer-head">
          <div class="drawer-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              class="drawer-tab"
              :class="{ 'is-active': panelTab === 'process' }"
              :aria-selected="panelTab === 'process'"
              @click="panelTab = 'process'"
            >
              图像处理
            </button>
            <button
              type="button"
              role="tab"
              class="drawer-tab"
              :class="{ 'is-active': panelTab === 'jobs' }"
              :aria-selected="panelTab === 'jobs'"
              @click="panelTab = 'jobs'"
            >
              任务状态
              <span v-if="jobs.length" class="drawer-tab-count">{{ jobs.length }}</span>
            </button>
          </div>
          <button class="icon-btn icon-btn-sm" aria-label="关闭面板" @click="closePanel">✕</button>
        </div>

        <div v-if="panelTab === 'process'" class="drawer-body">
          <div v-if="selected" class="drawer-target">
            <img v-if="isImage(selected) && thumbs[selected.id]" :src="thumbs[selected.id]" alt="" class="drawer-target-img" />
            <div v-else class="drawer-target-img is-empty" aria-hidden="true">{{ mediaGlyph(selected) }}</div>
            <div class="drawer-target-text">
              <span class="drawer-target-name" :title="selected.originalName">
                {{ selected.originalName }}
              </span>
              <span class="field-hint">{{ dimensionLabel(selected) }} · {{ sizeLabel(selected.sizeBytes) }}</span>
            </div>
          </div>
          <div v-else class="field-hint drawer-hint">在左侧网格中选择一张素材后再执行处理。</div>
          <div v-if="selected && !isImage(selected)" class="field-hint drawer-hint">
            音视频素材不支持图像处理，可在预览里播放。
          </div>

          <label class="field">
            <span class="field-label">操作</span>
            <n-select v-model:value="operation" :options="operationOptions" />
          </label>

          <label v-if="optionHint === 'maxSize'" class="field">
            <span class="field-label">最大边长</span>
            <n-input-number v-model:value="options.maxSize" :min="16" :max="4096" style="width: 100%" />
          </label>
          <div v-if="optionHint === 'size'" class="field-row">
            <label class="field">
              <span class="field-label">宽</span>
              <n-input-number v-model:value="options.width" :min="1" :max="8000" style="width: 100%" />
            </label>
            <label class="field">
              <span class="field-label">高</span>
              <n-input-number v-model:value="options.height" :min="1" :max="8000" style="width: 100%" />
            </label>
          </div>
          <label v-if="optionHint === 'ratio'" class="field">
            <span class="field-label">比例 W:H</span>
            <n-input v-model:value="options.aspectRatio" placeholder="3:4" />
          </label>
          <div v-if="optionHint === 'quality' || optionHint === 'format'" class="field-row">
            <label class="field">
              <span class="field-label">输出格式</span>
              <n-select v-model:value="options.format" :options="FORMAT_SELECT_OPTIONS" />
            </label>
            <label class="field">
              <span class="field-label">质量</span>
              <n-input-number v-model:value="options.quality" :min="10" :max="100" style="width: 100%" />
            </label>
          </div>
          <label v-if="optionHint === 'text'" class="field">
            <span class="field-label">水印文字</span>
            <n-input v-model:value="options.text" placeholder="例如：@瀚月" />
          </label>
          <template v-if="optionHint === 'panels'">
            <div class="field-row">
              <label class="field">
                <span class="field-label">最小间隔(px)</span>
                <n-input-number v-model:value="options.minGap" :min="2" :max="200" style="width: 100%" />
              </label>
              <label class="field">
                <span class="field-label">最多分格</span>
                <n-input-number v-model:value="options.maxPanels" :min="1" :max="30" style="width: 100%" />
              </label>
            </div>
            <label class="field">
              <span class="field-label">输出格式</span>
              <n-select v-model:value="options.format" :options="PANEL_FORMAT_OPTIONS" />
            </label>
          </template>

          <p class="field-hint">任务走队列异步执行，结果在「任务状态」中查看，大图不会阻塞接口请求。</p>
        </div>

        <div v-else class="drawer-body">
          <div v-if="!jobs.length" class="field-hint">暂无任务</div>
          <div v-else class="stack drawer-jobs">
            <div v-for="job in jobs" :key="job.id" class="drawer-job">
              <div class="row row-tight">
                <span class="tag" :class="jobTone(job.status)">{{ job.status }}</span>
                <span class="field-hint">{{ job.queue || job.kind }}</span>
                <div class="spacer" />
                <span v-if="job.progress" class="field-hint">{{ job.progress }}%</span>
              </div>
              <span class="field-hint drawer-job-id">{{ job.id }}</span>
              <span v-if="job.errorCode" class="field-hint" style="color: var(--color-danger)">
                {{ job.errorCode }} · {{ job.errorMessage }}
              </span>
              <span v-if="job.result?.assetIds?.length" class="field-hint">
                产出 {{ job.result.assetIds.length }} 张
              </span>
            </div>
          </div>
          <div v-if="jobs.length" class="drawer-jobs-more">
            <n-button
              v-if="jobsHasMore"
              quaternary
              size="small"
              :disabled="jobsLoadingMore"
              @click="loadMoreJobs"
            >
              {{ jobsLoadingMore ? '加载中…' : '加载更多任务' }}
            </n-button>
            <span v-else class="field-hint">已加载全部 {{ jobs.length }} 条任务</span>
          </div>
        </div>

        <div class="drawer-foot">
          <n-button quaternary size="small" :disabled="!selected" @click="remove(selected)">删除素材</n-button>
          <div class="spacer" />
          <n-button type="primary" size="small" :disabled="!selected || !isImage(selected)" @click="runOperation">执行处理</n-button>
        </div>
      </aside>
    </div>

    <n-modal
      :show="!!previewAsset"
      preset="card"
      :title="previewAsset?.originalName || '素材预览'"
      style="width: min(880px, calc(100vw - 32px))"
      @update:show="(value) => { if (!value) closePreview() }"
    >
      <div class="preview-body">
        <div class="preview-figure">
          <img
            v-if="previewAsset && isImage(previewAsset) && (thumbs[previewAsset.id] || previewAsset.url)"
            :src="thumbs[previewAsset.id] || previewAsset.url"
            :alt="previewAsset.originalName"
            class="preview-image"
          />
          <video
            v-else-if="previewAsset && mediaKind(previewAsset) === 'video' && previewAsset.url"
            class="preview-media"
            controls
            :src="apiUrl(previewAsset.url)"
          />
          <audio
            v-else-if="previewAsset && mediaKind(previewAsset) === 'audio' && previewAsset.url"
            class="preview-media"
            controls
            :src="apiUrl(previewAsset.url)"
          />
          <div v-else class="gallery-placeholder" style="height: 240px">▣</div>
        </div>
        <div v-if="previewAsset" class="preview-side">
          <p class="field-hint" style="margin: 0">
            {{ dimensionLabel(previewAsset) }} · {{ sizeLabel(previewAsset.sizeBytes) }} ·
            {{ sourceLabel(previewAsset.sourceType) }}
          </p>
          <template v-if="previewAsset.prompt">
            <span class="field-label">生成提示词</span>
            <p class="preview-prompt">{{ previewAsset.prompt }}</p>
            <n-button type="primary" size="small" @click="copyPrompt(previewAsset)">
              {{ copiedId === previewAsset.id ? '已复制' : '复制提示词' }}
            </n-button>
          </template>
          <template v-else>
            <span class="field-label">生成提示词</span>
            <template v-if="interrogateResult">
              <p class="preview-prompt">{{ interrogateResult }}</p>
              <n-button type="primary" size="small" @click="copyPrompt(previewAsset)">
                {{ copiedId === previewAsset.id ? '已复制' : '复制提示词' }}
              </n-button>
            </template>
            <template v-else>
              <p class="field-hint" style="margin: 0 0 8px">
                该素材暂无提示词，可用 AI 反推。
              </p>
              <n-button
                type="primary"
                size="small"
                :loading="interrogating"
                :disabled="interrogating"
                @click="interrogatePrompt(previewAsset)"
              >
                {{ interrogating ? '反推中…' : '反推提示词' }}
              </n-button>
              <p v-if="interrogateError" class="field-hint" style="margin: 6px 0 0; color: var(--color-danger)">
                {{ interrogateError }}
              </p>
            </template>
          </template>
          <div class="spacer" />
          <n-button quaternary size="small" @click="selectFromPreview">选为处理对象</n-button>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.gallery-page {
  gap: var(--space-4);
}

/* ---------- 两栏骨架 ---------- */
.gallery-shell {
  display: grid;
  grid-template-columns: 196px minmax(0, 1fr);
  gap: var(--space-3);
  align-items: start;
}

.filter-panel {
  position: sticky;
  top: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}
.filter-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.filter-title {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}
.filter-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-size: var(--font-xs);
  font-weight: 600;
}
.filter-close {
  display: none;
}
.filter-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.filter-group-label {
  margin-bottom: var(--space-2);
  font-size: var(--font-xs);
  font-weight: 600;
  letter-spacing: 0.4px;
  color: var(--color-text-muted);
}
.filter-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: 7px var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-sm);
  text-align: left;
  cursor: pointer;
  transition: background 0.12s ease, color 0.12s ease;
}
.filter-item:hover {
  background: var(--color-surface-raised);
  color: var(--color-text-primary);
}
.filter-item.is-active {
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-weight: 600;
}
.filter-item:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.filter-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--color-border-strong);
}
.dot-upload {
  background: var(--color-success);
}
.dot-generated {
  background: var(--color-accent);
}
.dot-derived {
  background: var(--color-warning);
}
.dot-image {
  background: var(--color-success);
}
.dot-video {
  background: var(--color-info);
}
.dot-audio {
  background: var(--color-warning);
}
.filter-reset {
  align-self: flex-start;
}
.filter-scrim,
.filter-toggle {
  display: none;
}

/* ---------- 主区 ---------- */
.gallery-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.gallery-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.gallery-search-input {
  width: 260px;
  max-width: 100%;
}

/* ---------- 网格 ---------- */
.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(168px, 1fr));
  gap: var(--space-3);
}
.skeleton-card {
  height: 190px;
  border-radius: var(--radius-md);
}
.gallery-card {
  position: relative;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  overflow: hidden;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.gallery-card:hover {
  border-color: var(--color-border-strong);
  box-shadow: var(--shadow-sm);
}
.gallery-card.is-selected {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px var(--color-accent-soft);
}
.gallery-thumb {
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  background: var(--color-bg);
  cursor: zoom-in;
}
.gallery-thumb img,
.gallery-placeholder {
  display: block;
  width: 100%;
  height: 124px;
  object-fit: cover;
  background: var(--color-bg);
}
.gallery-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  color: var(--color-text-muted);
}
.gallery-actions {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  display: flex;
  gap: 4px;
  opacity: 0;
  transform: translateY(-2px);
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.gallery-card:hover .gallery-actions,
.gallery-card:focus-within .gallery-actions {
  opacity: 1;
  transform: translateY(0);
}
.gallery-action {
  padding: 3px 8px;
  border: 1px solid var(--color-inverse-border);
  border-radius: var(--radius-sm);
  background: var(--color-inverse-scrim);
  color: var(--color-inverse-on-scrim);
  font-family: inherit;
  font-size: var(--font-xs);
  cursor: pointer;
}
.gallery-action:hover {
  background: var(--color-inverse-scrim-strong);
}
.gallery-action.is-primary {
  background: var(--color-accent);
  border-color: transparent;
}
.gallery-action.is-primary:hover {
  background: var(--color-accent-hover);
}
.gallery-meta {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: var(--space-2) var(--space-3) var(--space-3);
}
.gallery-name {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-text-primary);
  font: inherit;
  font-size: var(--font-sm);
  font-weight: 500;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}
.gallery-name:hover {
  color: var(--color-accent);
}
.gallery-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  min-width: 0;
}
.gallery-meta-row .field-hint {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gallery-prompt-btn {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-accent);
  font-family: inherit;
  font-size: var(--font-xs);
  cursor: pointer;
  white-space: nowrap;
}
.gallery-prompt-btn:hover {
  text-decoration: underline;
}
.tag-sm {
  flex-shrink: 0;
  font-size: var(--font-xs);
}
.gallery-empty {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--space-6);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--font-sm);
  align-items: flex-start;
}
.empty-title {
  font-weight: 600;
}

/* ---------- 处理抽屉 ---------- */
.drawer-scrim {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  justify-content: flex-end;
  background: var(--color-overlay);
}
.drawer {
  display: flex;
  flex-direction: column;
  width: min(400px, 100%);
  height: 100%;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  box-shadow: var(--shadow-lg);
}
.drawer-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border);
}
.drawer-tabs {
  display: flex;
  gap: var(--space-1);
}
.drawer-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: inherit;
  font-size: var(--font-sm);
  cursor: pointer;
}
.drawer-tab.is-active {
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-weight: 600;
}
.drawer-tab-count {
  padding: 0 5px;
  border-radius: 999px;
  background: var(--color-surface);
  font-size: var(--font-xs);
}
.drawer-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  overflow-y: auto;
}
.drawer-hint {
  margin: 0;
}
.drawer-target {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
}
.drawer-target-img {
  width: 48px;
  height: 48px;
  border-radius: var(--radius-sm);
  object-fit: cover;
  flex-shrink: 0;
}
.drawer-target-img.is-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg);
  color: var(--color-text-muted);
}
.drawer-target-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.drawer-target-name {
  font-size: var(--font-sm);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.field-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2);
}
.drawer-jobs {
  gap: var(--space-2);
}
.drawer-job {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
}
.drawer-job-id {
  word-break: break-all;
}
.drawer-jobs-more {
  display: flex;
  justify-content: center;
  padding-top: var(--space-2);
}
.drawer-foot {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border);
}

/* ---------- 大图预览 ---------- */
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
  border-radius: var(--radius-md);
  overflow: hidden;
  min-height: 200px;
}
.preview-image {
  display: block;
  width: 100%;
  max-height: 62vh;
  object-fit: contain;
}
.preview-media {
  display: block;
  width: 100%;
  max-height: 62vh;
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
  border-radius: var(--radius-md);
  background: var(--color-bg);
  font-size: var(--font-sm);
  line-height: 1.65;
  color: var(--color-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  user-select: text;
}

@media (max-width: 1023px) {
  .gallery-shell {
    grid-template-columns: minmax(0, 1fr);
  }
  .filter-panel {
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 60;
    width: 240px;
    border-radius: 0;
    border-left: 0;
    border-top: 0;
    border-bottom: 0;
    overflow-y: auto;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
  }
  .filter-panel.is-open {
    transform: translateX(0);
  }
  .filter-close,
  .filter-toggle {
    display: inline-flex;
  }
  .filter-scrim {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 55;
    background: var(--color-overlay);
  }
}

@media (max-width: 767px) {
  .gallery-search-input {
    width: 100%;
  }
  .gallery-grid {
    grid-template-columns: repeat(auto-fill, minmax(142px, 1fr));
  }
  .preview-body {
    grid-template-columns: minmax(0, 1fr);
  }
  .preview-image {
    max-height: 46vh;
  }
}
</style>
