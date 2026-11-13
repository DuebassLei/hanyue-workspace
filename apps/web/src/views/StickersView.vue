<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton } from 'naive-ui'
import { api, apiUrl } from '../api/client'
import StickerWizard from '../features/sticker/StickerWizard.vue'
import HotPickDialog from '../features/sticker/HotPickDialog.vue'

const route = useRoute()
const router = useRouter()

const sets = ref([])
const nextCursor = ref('')
const loading = ref(true)
const loadingMore = ref(false)
const error = ref('')
const statusFilter = ref('')

// 向导：mode = create（preset）| edit（sticker）。wizardKey 保证每次打开都是全新实例
const wizard = ref(null)
const wizardKey = ref('')
const hotPickOpen = ref(false)
const preset = ref(null)

const STATUS_LABEL = {
  draft: ['草稿', ''],
  outlined: ['待生成', 'tag-warning'],
  generating: ['生成中', 'tag-warning'],
  review: ['检查中', 'tag-info'],
  done: ['已归档', 'tag-success'],
}

const statusOptions = [
  { value: '', label: '全部状态' },
  { value: 'draft', label: '草稿' },
  { value: 'generating', label: '生成中' },
  { value: 'review', label: '检查中' },
  { value: 'done', label: '已归档' },
]

function queryString() {
  const params = new URLSearchParams()
  if (statusFilter.value) params.set('status', statusFilter.value)
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    // 后端返回裸数组，游标走 X-Next-Cursor 头（getPage 已解好）
    const page = await api.getPage(`/stickers${queryString()}`)
    sets.value = Array.isArray(page.data) ? page.data : []
    nextCursor.value = page.nextCursor || ''
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function loadMore() {
  if (loadingMore.value || !nextCursor.value) return
  loadingMore.value = true
  error.value = ''
  try {
    const sep = queryString() ? '&' : '?'
    const page = await api.getPage(
      `/stickers${queryString()}${sep}cursor=${encodeURIComponent(nextCursor.value)}`,
    )
    sets.value = [...sets.value, ...(Array.isArray(page.data) ? page.data : [])]
    nextCursor.value = page.nextCursor || ''
  } catch (err) {
    error.value = `加载更多失败：${err.message}`
  } finally {
    loadingMore.value = false
  }
}

/** 列表里某一行被向导更新后原位替换 */
function onSetSaved(fresh) {
  const index = sets.value.findIndex((item) => item.id === fresh.id)
  if (index >= 0) sets.value[index] = fresh
}

function openCreate(withPreset = null) {
  preset.value = withPreset
  wizard.value = { mode: 'create' }
  wizardKey.value = `new-${Date.now()}`
}

function openEdit(item) {
  preset.value = null
  wizard.value = { mode: 'edit', sticker: item }
  wizardKey.value = `set-${item.id}`
}

function closeWizard() {
  wizard.value = null
  load()
}

function coverSrc(item) {
  return item.coverUrl ? apiUrl(item.coverUrl) : ''
}

function timeLabel(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

// —— 从热点进入：?new=1&clusterId=xxx ——
async function applyNewQuery() {
  const { new: isNew, clusterId, id } = route.query
  // 入口参数只用一次：清掉后刷新或移除风格都不会被重新套回去
  const rest = { ...route.query }
  for (const key of ['new', 'clusterId', 'id']) delete rest[key]
  router.replace({ query: rest })
  if (id) {
    openEdit({ id })
    return
  }
  if (isNew !== '1') return
  if (clusterId) {
    try {
      // 簇详情端点与 HotTopicsView 相同
      const cluster = await api.get(`/hot-topics/clusters/${clusterId}`)
      preset.value = { theme: cluster.title || '', track: cluster.track || '', hotClusterId: cluster.id }
    } catch (err) {
      error.value = `读取热点失败：${err.message}`
    }
  }
  wizard.value = { mode: 'create' }
}

onMounted(async () => {
  await load()
  applyNewQuery()
})

const filteredNote = computed(() => (statusFilter.value ? ` · 筛选：${statusOptions.find((o) => o.value === statusFilter.value)?.label}` : ''))
</script>

<template>
  <div class="page">
    <template v-if="!wizard">
      <div class="page-head">
        <div>
          <h1 class="page-title">贴图创作</h1>
          <p class="page-sub">一组风格统一的图，适合九宫格多图推送。锚点图定画风，其余跟着走。</p>
        </div>
        <n-button type="primary" @click="openCreate()">+ 新建贴图集</n-button>
      </div>

      <n-alert v-if="error" type="error" closable @close="error = ''">{{ error }}</n-alert>

      <div class="row sticker-filters">
        <button
          v-for="option in statusOptions"
          :key="option.value"
          type="button"
          class="tag"
          :class="{ 'tag-accent': statusFilter === option.value }"
          @click="statusFilter = option.value; load()"
        >
          {{ option.label }}
        </button>
        <div class="spacer" />
        <span class="field-hint">{{ sets.length }} 组{{ filteredNote }}</span>
      </div>

      <div v-if="loading" class="sticker-grid">
        <div v-for="i in 4" :key="i" class="skeleton" style="height: 210px" />
      </div>

      <div v-else-if="!sets.length" class="empty-state">
        <span class="empty-title">还没有贴图集</span>
        <span>贴图 = 一组风格统一的图，适合九宫格多图推送。从今天的热点开始，或空白起一组。</span>
        <div class="row" style="justify-content: center; margin-top: var(--space-2)">
          <n-button type="primary" @click="hotPickOpen = true">从热点做一组</n-button>
          <n-button secondary @click="openCreate()">空白开始</n-button>
        </div>
      </div>

      <div v-else class="sticker-grid">
        <article
          v-for="item in sets"
          :key="item.id"
          class="sticker-card"
          @click="openEdit(item)"
        >
          <div class="sticker-cover">
            <img v-if="coverSrc(item)" :src="coverSrc(item)" :alt="item.title" loading="lazy" />
            <span v-else aria-hidden="true">❖</span>
            <span class="tag sticker-progress" :class="item.failed ? 'tag-danger' : item.succeeded === item.total && item.total ? 'tag-success' : ''">
              {{ item.succeeded }}/{{ item.total }}
            </span>
          </div>
          <div class="sticker-info">
            <h3 class="sticker-name" :title="item.title">{{ item.title || '未命名贴图' }}</h3>
            <div class="sticker-meta">
              <span class="tag" :class="STATUS_LABEL[item.status]?.[1] || ''">{{ STATUS_LABEL[item.status]?.[0] || item.status }}</span>
              <span>{{ timeLabel(item.updatedAt) }}</span>
            </div>
          </div>
        </article>
      </div>

      <div v-if="!loading && nextCursor" class="sticker-more">
        <n-button secondary size="small" :loading="loadingMore" @click="loadMore">加载更多</n-button>
      </div>

      <HotPickDialog v-model:show="hotPickOpen" @pick="(c) => openCreate({ theme: c.title, track: c.track, hotClusterId: c.id })" />
    </template>

    <StickerWizard
      v-else
      :key="wizardKey"
      :preset="preset"
      :sticker="wizard.sticker || null"
      @close="closeWizard"
      @saved="onSetSaved"
    />
  </div>
</template>

<style scoped>
.sticker-filters {
  gap: var(--space-2);
}
.sticker-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--space-4);
}
.sticker-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  overflow: hidden;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.sticker-card:hover {
  border-color: var(--color-accent);
  box-shadow: var(--shadow-md);
}
.sticker-cover {
  position: relative;
  aspect-ratio: 3 / 2;
  background: var(--color-surface-raised);
  display: grid;
  place-items: center;
  font-size: 34px;
  color: var(--color-text-muted);
}
.sticker-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.sticker-progress {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  background: color-mix(in srgb, var(--color-surface) 88%, transparent);
}
.sticker-info {
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.sticker-name {
  margin: 0;
  font-size: var(--font-body);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sticker-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
.sticker-more {
  display: flex;
  justify-content: center;
  padding: var(--space-2) 0;
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: var(--space-8) var(--space-4);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  text-align: center;
}
.empty-title {
  font-weight: 600;
  color: var(--color-text-primary);
}
</style>
