<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api, fetchBlobUrl } from '../api/client'

const OPERATIONS = [
  { value: 'thumbnail', label: '生成缩略图', hint: 'maxSize' },
  { value: 'resize', label: '缩放尺寸', hint: 'size' },
  { value: 'crop', label: '按比例裁切', hint: 'ratio' },
  { value: 'compress', label: '压缩体积', hint: 'quality' },
  { value: 'convert', label: '格式转换', hint: 'format' },
  { value: 'watermark', label: '添加水印', hint: 'text' },
  { value: 'split_panels', label: '条漫分格', hint: 'panels' },
]

const assets = ref([])
const loading = ref(true)
const error = ref('')
const uploading = ref(false)
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
const provider = ref({ available: false, note: '', modelCode: null })
const aiForm = reactive({ prompt: '', aspectRatio: '3:4', count: 1 })
const aiBusy = ref(false)
let pollTimer = null
let pollRounds = 0
const MAX_POLL_ROUNDS = 120 // 1.5s × 120 = 3 分钟，避免任务卡住时无限轮询

const selected = computed(() => assets.value.find((a) => a.id === selectedId.value) || null)
const optionHint = computed(() => OPERATIONS.find((o) => o.value === operation.value)?.hint)

function releaseThumbs() {
  for (const url of Object.values(thumbs.value)) URL.revokeObjectURL(url)
  thumbs.value = {}
}

async function loadThumbs() {
  releaseThumbs()
  const map = {}
  for (const asset of assets.value.slice(0, 60)) {
    try {
      map[asset.id] = await fetchBlobUrl(`/assets/${asset.id}/raw`)
    } catch {
      map[asset.id] = ''
    }
  }
  thumbs.value = map
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [list, status, recent] = await Promise.all([
      api.get('/assets'),
      api.get('/ai/image-provider/status'),
      api.get('/jobs?limit=20'),
    ])
    assets.value = list
    provider.value = status
    jobs.value = recent.items || []
    if (!selectedId.value && list.length) selectedId.value = list[0].id
    await loadThumbs()
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

function startPolling() {
  if (pollTimer) return
  pollRounds = 0
  pollTimer = setInterval(async () => {
    pollRounds += 1
    if (pollRounds > MAX_POLL_ROUNDS) {
      stopPolling()
      error.value = '任务轮询超时（3 分钟），请刷新页面或到任务状态页确认结果'
      return
    }
    try {
      const data = await api.get('/jobs?limit=20')
      jobs.value = data.items || []
      const active = jobs.value.some((j) => ['queued', 'running'].includes(j.status))
      if (!active) {
        stopPolling()
        await load()
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
    selectedId.value = created.id
    await load()
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
  if (!selected.value) return
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
    startPolling()
  } catch (err) {
    error.value = err.message
  }
}

async function generate() {
  if (!aiForm.prompt.trim()) return
  aiBusy.value = true
  error.value = ''
  try {
    const res = await api.post('/ai/image-generations', {
      prompt: aiForm.prompt.trim(),
      aspectRatio: aiForm.aspectRatio,
      count: Number(aiForm.count),
    })
    jobs.value = [
      { id: res.jobId, queue: 'ai-image', status: 'queued', progress: 0 },
      ...jobs.value.filter((j) => j.id !== res.jobId),
    ]
    startPolling()
  } catch (err) {
    error.value = err.message
  } finally {
    aiBusy.value = false
  }
}

async function remove(asset) {
  error.value = ''
  try {
    await api.del(`/assets/${asset.id}`)
    if (selectedId.value === asset.id) selectedId.value = ''
    await load()
  } catch (err) {
    error.value = err.message
  }
}

function sizeLabel(bytes) {
  if (!bytes) return '—'
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function jobTone(status) {
  if (status === 'succeeded') return 'tag-success'
  if (status === 'failed' || status === 'cancelled') return 'tag-danger'
  return 'tag-warning'
}

onMounted(() => {
  load()
  startPolling()
})
onBeforeUnmount(() => {
  stopPolling()
  releaseThumbs()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">素材与配图</h1>
        <p class="page-sub">上传原图、基础图像处理与 AI 生成封面；原图永不覆盖，派生图单独保存。</p>
      </div>
      <div class="row row-tight">
        <label class="btn btn-sm" style="cursor: pointer">
          {{ uploading ? '上传中…' : '上传图片' }}
          <input type="file" accept="image/*" hidden :disabled="uploading" @change="onUpload" />
        </label>
        <button class="btn btn-ghost btn-sm" :disabled="loading" @click="load">刷新</button>
      </div>
    </div>

    <div v-if="error" class="banner banner-danger" role="alert">
      {{ error }}
      <button class="btn btn-ghost btn-sm" @click="error = ''">知道了</button>
    </div>

    <div class="stack" style="gap: var(--space-4)">
      <!-- 素材库 -->
      <section class="card">
        <div class="row">
          <h2 class="section-title">素材库</h2>
          <div class="spacer" />
          <span class="field-hint">{{ assets.length }} 个素材</span>
        </div>
        <div v-if="loading" class="skeleton" style="height: 140px; margin-top: var(--space-3)" />
        <div v-else-if="!assets.length" class="empty-state" style="margin-top: var(--space-3)">
          <span class="empty-title">还没有素材</span>
          <span>点击右上角「上传图片」开始，上传后即可在这里处理或用于画布配图节点。</span>
        </div>
        <div v-else class="asset-grid" style="margin-top: var(--space-3)">
          <button
            v-for="asset in assets"
            :key="asset.id"
            class="asset-card"
            :class="{ 'is-selected': selectedId === asset.id }"
            type="button"
            :aria-pressed="selectedId === asset.id"
            @click="selectedId = asset.id"
          >
            <img v-if="thumbs[asset.id]" :src="thumbs[asset.id]" :alt="asset.originalName" />
            <div v-else class="asset-placeholder" aria-hidden="true">▣</div>
            <div class="asset-meta">
              <span class="asset-name" :title="asset.originalName">{{ asset.originalName }}</span>
              <span class="field-hint">
                {{ asset.width }}×{{ asset.height }} · {{ sizeLabel(asset.sizeBytes) }}
              </span>
              <span class="tag tag-sm">{{ asset.sourceType }}</span>
            </div>
          </button>
        </div>
      </section>

      <!-- 图像处理 -->
      <section class="card">
        <h2 class="section-title">基础图像处理</h2>
        <p class="field-hint" style="margin-top: 4px">
          任务走队列异步执行，结果可在下方任务列表查看；大图不会阻塞接口请求。
        </p>
        <div class="row row-tight" style="flex-wrap: wrap; margin-top: var(--space-3)">
          <label class="field" style="min-width: 180px">
            <span class="field-label">操作</span>
            <select v-model="operation" class="select">
              <option v-for="op in OPERATIONS" :key="op.value" :value="op.value">{{ op.label }}</option>
            </select>
          </label>
          <label v-if="optionHint === 'maxSize'" class="field" style="width: 130px">
            <span class="field-label">最大边长</span>
            <input v-model.number="options.maxSize" class="input" type="number" min="16" max="4096" />
          </label>
          <template v-if="optionHint === 'size'">
            <label class="field" style="width: 120px">
              <span class="field-label">宽</span>
              <input v-model.number="options.width" class="input" type="number" min="1" max="8000" />
            </label>
            <label class="field" style="width: 120px">
              <span class="field-label">高</span>
              <input v-model.number="options.height" class="input" type="number" min="1" max="8000" />
            </label>
          </template>
          <label v-if="optionHint === 'ratio'" class="field" style="width: 130px">
            <span class="field-label">比例 W:H</span>
            <input v-model="options.aspectRatio" class="input" placeholder="3:4" />
          </label>
          <template v-if="optionHint === 'quality' || optionHint === 'format'">
            <label class="field" style="width: 130px">
              <span class="field-label">输出格式</span>
              <select v-model="options.format" class="select">
                <option value="webp">WebP</option>
                <option value="jpeg">JPEG</option>
                <option value="png">PNG</option>
              </select>
            </label>
            <label class="field" style="width: 120px">
              <span class="field-label">质量</span>
              <input v-model.number="options.quality" class="input" type="number" min="10" max="100" />
            </label>
          </template>
          <label v-if="optionHint === 'text'" class="field" style="min-width: 200px">
            <span class="field-label">水印文字</span>
            <input v-model="options.text" class="input" placeholder="例如：@瀚月" />
          </label>
          <template v-if="optionHint === 'panels'">
            <label class="field" style="width: 130px">
              <span class="field-label">最小间隔(px)</span>
              <input v-model.number="options.minGap" class="input" type="number" min="2" max="200" />
            </label>
            <label class="field" style="width: 130px">
              <span class="field-label">最多分格</span>
              <input v-model.number="options.maxPanels" class="input" type="number" min="1" max="30" />
            </label>
            <label class="field" style="width: 130px">
              <span class="field-label">输出格式</span>
              <select v-model="options.format" class="select">
                <option value="jpeg">JPEG</option>
                <option value="webp">WebP</option>
                <option value="png">PNG</option>
              </select>
            </label>
          </template>
          <div class="spacer" />
          <button class="btn btn-sm" :disabled="!selected" @click="runOperation">执行处理</button>
          <button class="btn btn-ghost btn-sm" :disabled="!selected" @click="remove(selected)">
            删除原图
          </button>
        </div>
        <p v-if="selected" class="field-hint" style="margin-top: var(--space-2)">
          已选：{{ selected.originalName }}（{{ selected.id }}）
        </p>
      </section>

      <!-- AI 生图 -->
      <section class="card">
        <div class="row">
          <h2 class="section-title">AI 生成封面</h2>
          <div class="spacer" />
          <span class="tag" :class="provider.available ? 'tag-success' : 'tag-warning'">
            {{ provider.available ? `供应商已配置 · ${provider.modelCode}` : '供应商未接入' }}
          </span>
        </div>
        <p class="field-hint" style="margin-top: 4px">{{ provider.note }}</p>
        <div class="row row-tight" style="flex-wrap: wrap; margin-top: var(--space-3)">
          <label class="field" style="flex: 1 1 280px">
            <span class="field-label">画面描述</span>
            <input v-model="aiForm.prompt" class="input" placeholder="例如：清晨书桌上的笔记本与咖啡，柔和自然光" />
          </label>
          <label class="field" style="width: 130px">
            <span class="field-label">比例</span>
            <select v-model="aiForm.aspectRatio" class="select">
              <option value="3:4">3:4</option>
              <option value="1:1">1:1</option>
              <option value="16:9">16:9</option>
              <option value="9:16">9:16</option>
            </select>
          </label>
          <label class="field" style="width: 110px">
            <span class="field-label">数量</span>
            <select v-model.number="aiForm.count" class="select">
              <option :value="1">1</option>
              <option :value="2">2</option>
              <option :value="3">3</option>
              <option :value="4">4</option>
            </select>
          </label>
          <button
            class="btn btn-sm"
            :disabled="!provider.available || !aiForm.prompt.trim() || aiBusy"
            @click="generate"
          >
            {{ aiBusy ? '提交中…' : '生成图片' }}
          </button>
        </div>
        <div v-if="!provider.available" class="banner banner-warning" style="margin-top: var(--space-3)">
          未配置图像生成供应商时不会生成占位图。可在设置中添加支持 image.generate 能力的模型，
          或设置 IMAGE_PROVIDER_BASE_URL / IMAGE_PROVIDER_API_KEY / IMAGE_PROVIDER_MODEL 后重启服务。
        </div>
      </section>

      <!-- 任务列表 -->
      <section class="card">
        <h2 class="section-title">任务状态</h2>
        <div v-if="!jobs.length" class="field-hint" style="margin-top: var(--space-2)">暂无任务</div>
        <div v-else class="stack" style="margin-top: var(--space-2)">
          <div v-for="job in jobs" :key="job.id" class="row row-tight card-flat" style="padding: var(--space-2)">
            <span class="tag" :class="jobTone(job.status)">{{ job.status }}</span>
            <span class="field-hint">{{ job.queue || job.kind }} · {{ job.id }}</span>
            <span v-if="job.errorCode" class="field-hint" style="color: var(--color-danger)">
              {{ job.errorCode }} · {{ job.errorMessage }}
            </span>
            <div class="spacer" />
            <span v-if="job.result?.assetIds?.length" class="field-hint">
              产出 {{ job.result.assetIds.length }} 张
            </span>
            <span v-if="job.progress" class="field-hint">{{ job.progress }}%</span>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.section-title {
  margin: 0;
  font-size: var(--font-section);
  font-weight: 600;
  color: var(--color-text-primary);
}
.asset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(168px, 1fr));
  gap: var(--space-3);
}
.asset-card {
  display: block;
  width: 100%;
  padding: 0;
  text-align: left;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 10px);
  background: var(--color-surface);
  color: var(--color-text-primary);
  overflow: hidden;
  cursor: pointer;
  min-height: 44px;
}
.asset-card.is-selected {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px var(--color-accent-soft);
}
.asset-card img,
.asset-placeholder {
  width: 100%;
  height: 120px;
  object-fit: cover;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg);
  font-size: 28px;
}
.asset-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-2);
}
.asset-name {
  font-size: var(--font-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
.tag-sm {
  align-self: flex-start;
  font-size: var(--font-xs);
}
</style>
