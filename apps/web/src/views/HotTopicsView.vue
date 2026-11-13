<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import HotClusterCard from '../features/hot/HotClusterCard.vue'

const router = useRouter()

const clusters = ref([])
const summary = ref({})
const degraded = ref([])
const sources = ref({ platforms: [], enabled: [] })
const loading = ref(true)
const refreshing = ref(false)
const error = ref('')
const notice = ref('')
const busyId = ref('')
const openId = ref('')
const detailCache = reactive({})
const detailLoadingId = ref('')

const filters = reactive({
  date: '',
  track: '',
  platform: '',
  minScore: 0,
  sort: 'score',
  onlyNew: true,
  q: '',
})

// 一次渲染上百张卡片没有意义，先给前 60 个；用户要继续看再加载。
const limit = ref(60)

function showMore() {
  limit.value = Math.min(200, limit.value + 60)
  loadClusters()
}

let searchTimer = null

const trackOptions = computed(() => Object.keys(summary.value.tracks || {}).sort())
const platformOptions = computed(() => sources.value.enabled || [])

const visible = computed(() =>
  clusters.value.filter(
    (row) => !filters.onlyNew || (row.status !== 'ignored' && row.status !== 'generated'),
  ),
)

/** 赛道分布的堆叠条：单一色相的明度阶梯，深色主题自动跟随文字色。 */
const trackSegments = computed(() => {
  const tracks = summary.value.tracks || {}
  const total = Object.values(tracks).reduce((sum, n) => sum + n, 0) || 1
  const tones = [92, 70, 52, 38, 28, 20, 14, 10]
  return Object.entries(tracks)
    .sort((a, b) => b[1] - a[1])
    .map(([name, count], index) => ({
      name,
      count,
      pct: (count / total) * 100,
      color: `color-mix(in srgb, var(--color-text-primary) ${tones[index] || 10}%, transparent)`,
    }))
})

const sourceNote = computed(() => {
  const enabled = sources.value.enabled || []
  if (!enabled.length) return '未启用任何采集源'
  return `采集源 ${enabled.length} 路：${enabled.join('、')}`
})

function queryString() {
  const params = new URLSearchParams()
  if (filters.date) params.set('date', filters.date)
  if (filters.track) params.set('track', filters.track)
  if (filters.platform) params.set('platform', filters.platform)
  if (Number(filters.minScore)) params.set('minScore', String(filters.minScore))
  if (filters.sort) params.set('sort', filters.sort)
  if (filters.q.trim()) params.set('q', filters.q.trim())
  params.set('limit', String(limit.value))
  const text = params.toString()
  return text ? `?${text}` : ''
}

async function loadClusters() {
  error.value = ''
  try {
    clusters.value = await api.get(`/hot-topics/clusters${queryString()}`)
  } catch (err) {
    error.value = err.message
  }
}

async function loadMeta() {
  const [summaryData, degradedData, sourcesData] = await Promise.all([
    api.get('/hot-topics/summary'),
    api.get('/hot-topics/degraded'),
    api.get('/hot-topics/sources'),
  ])
  summary.value = summaryData || {}
  degraded.value = degradedData || []
  sources.value = sourcesData || { platforms: [], enabled: [] }
}

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([loadClusters(), loadMeta()])
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function refreshTopics() {
  refreshing.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await api.post('/hot-topics/refresh', {})
    notice.value =
      `已刷新 ${result.day}：${result.items} 条素材 → ${result.clusters} 个事件` +
      (result.notes && result.notes.length ? `（${result.notes.join('；')}）` : '')
    // 重新采集后筛选条件可能把新数据挡住，重置日期筛选
    filters.date = ''
    await Promise.all([loadClusters(), loadMeta()])
  } catch (err) {
    error.value = err.message
  } finally {
    refreshing.value = false
  }
}

async function toggleDetail(cluster) {
  if (openId.value === cluster.id) {
    openId.value = ''
    return
  }
  openId.value = cluster.id
  if (detailCache[cluster.id]) return
  detailLoadingId.value = cluster.id
  try {
    detailCache[cluster.id] = await api.get(`/hot-topics/clusters/${cluster.id}`)
  } catch (err) {
    error.value = err.message
    openId.value = ''
  } finally {
    detailLoadingId.value = ''
  }
}

async function writeFromCluster(cluster) {
  busyId.value = cluster.id
  error.value = ''
  try {
    const item = await api.post(`/hot-topics/clusters/${cluster.id}/draft`, {})
    notice.value = `已新建稿件「${item.title}」，正在跳转到创作空间`
    router.push({ name: 'studio', query: { id: item.id } })
  } catch (err) {
    error.value = err.message
  } finally {
    busyId.value = ''
  }
}

async function toggleIgnore(cluster) {
  busyId.value = cluster.id
  error.value = ''
  const next = cluster.status === 'ignored' ? 'new' : 'ignored'
  try {
    const updated = await api.patch(`/hot-topics/clusters/${cluster.id}`, { status: next })
    const index = clusters.value.findIndex((row) => row.id === cluster.id)
    if (index >= 0) clusters.value[index] = updated
    if (detailCache[cluster.id]) detailCache[cluster.id] = { ...detailCache[cluster.id], ...updated }
    notice.value = next === 'ignored' ? '已忽略这条热点' : '已恢复到列表'
  } catch (err) {
    error.value = err.message
  } finally {
    busyId.value = ''
  }
}

function resetFilters() {
  filters.track = ''
  filters.platform = ''
  filters.minScore = 0
  filters.sort = 'score'
  filters.q = ''
  filters.date = ''
}

watch(
  () => [filters.date, filters.track, filters.platform, filters.minScore, filters.sort],
  () => loadClusters(),
)

watch(
  () => filters.q,
  () => {
    clearTimeout(searchTimer)
    searchTimer = setTimeout(loadClusters, 250)
  },
)

onMounted(loadAll)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">热点选题</h1>
        <p class="page-sub">
          「每日热榜」回答今天大家搜什么；这里回答我该写哪一个。分数与档位由服务端算好原样展示，前端只排序、筛选、成稿。
        </p>
      </div>
      <div class="row row-tight">
        <button class="btn btn-ghost" :disabled="refreshing || loading" @click="refreshTopics">
          {{ refreshing ? '采集中…' : '刷新并评分' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="banner banner-danger" role="alert">{{ error }}</div>
    <div v-if="notice" class="banner banner-info" role="status">{{ notice }}</div>

    <section class="card hot-stat">
      <div class="hot-stat-line">
        <span class="hot-stat-item"><b>{{ summary.items || 0 }}</b> 条素材</span>
        <span class="hot-stat-arrow">→</span>
        <span class="hot-stat-item"><b>{{ summary.clusters || 0 }}</b> 个事件</span>
        <span class="hot-stat-arrow">→</span>
        <span class="hot-stat-item hot-stat-strong"><b>{{ summary.recommended || 0 }}</b> 个值得写</span>
        <span class="hot-stat-arrow">→</span>
        <span class="hot-stat-item"><b>{{ summary.generated || 0 }}</b> 个已出稿</span>
        <div class="spacer" />
        <span v-if="summary.capturedAt" class="field-hint">最近采集 {{ summary.capturedAt }}</span>
      </div>

      <div v-if="trackSegments.length" class="hot-track-bar" role="img" aria-label="赛道分布">
        <span
          v-for="segment in trackSegments"
          :key="segment.name"
          class="hot-track-seg"
          :style="{ width: segment.pct + '%', background: segment.color }"
          :title="`${segment.name} ${segment.count} 个`"
        />
      </div>
      <div v-if="trackSegments.length" class="hot-track-legend">
        <span v-for="segment in trackSegments" :key="segment.name">
          {{ segment.name }} {{ segment.count }}
        </span>
      </div>

      <p class="hot-stat-note">
        {{ sourceNote }}
        <template v-if="degraded.length">
          · <span :title="degraded.map((x) => x.reason).join('；')">
            {{ degraded.length }} 个维度数据不足（悬停看原因）
          </span>
        </template>
      </p>
    </section>

    <section class="card hot-filters">
      <input v-model="filters.date" class="input hot-date" type="date" aria-label="按日期筛选" />
      <select v-model="filters.track" class="select" aria-label="按赛道筛选">
        <option value="">全部赛道</option>
        <option v-for="track in trackOptions" :key="track" :value="track">{{ track }}</option>
      </select>
      <select v-model="filters.platform" class="select" aria-label="按平台筛选">
        <option value="">全部平台</option>
        <option v-for="platform in platformOptions" :key="platform" :value="platform">{{ platform }}</option>
      </select>
      <select v-model.number="filters.minScore" class="select" aria-label="按分数筛选">
        <option :value="0">全部分数</option>
        <option :value="40">40+ 备用</option>
        <option :value="55">55+ 可做</option>
        <option :value="70">70+ 强烈推荐</option>
      </select>
      <select v-model="filters.sort" class="select" aria-label="排序方式">
        <option value="score">价值优先</option>
        <option value="time">最新优先</option>
        <option value="resonance">共振优先</option>
      </select>
      <label class="hot-check">
        <input v-model="filters.onlyNew" type="checkbox" />
        只看未出稿
      </label>
      <input
        v-model="filters.q"
        class="input hot-search"
        type="search"
        placeholder="搜索事件标题…"
        aria-label="搜索事件"
      />
      <div class="spacer" />
      <span class="field-hint">{{ visible.length }} / {{ clusters.length }} 个事件</span>
      <button class="btn btn-ghost btn-sm" @click="resetFilters">清空</button>
    </section>

    <div v-if="loading" class="stack">
      <div v-for="i in 3" :key="i" class="skeleton" style="height: 150px" />
    </div>

    <div v-else-if="!clusters.length" class="empty">
      <span class="empty-title">还没有热点数据</span>
      <span>点右上角「刷新并评分」，会抓取今日头条、百度、抖音、微博、知乎与 AI 资讯，合并同一事件后打分。</span>
      <button class="btn btn-sm" :disabled="refreshing" @click="refreshTopics">
        {{ refreshing ? '采集中…' : '现在采集' }}
      </button>
    </div>

    <div v-else-if="!visible.length" class="empty">
      <span class="empty-title">没有符合条件的事件</span>
      <span>取消「只看未出稿」，或清空筛选再试试。</span>
      <button class="btn btn-ghost btn-sm" @click="resetFilters">清空筛选</button>
    </div>

    <div v-else class="hot-list">
      <HotClusterCard
        v-for="cluster in visible"
        :key="cluster.id"
        :cluster="cluster"
        :detail="detailCache[cluster.id] || null"
        :degraded="degraded"
        :busy="busyId === cluster.id"
        :detail-loading="detailLoadingId === cluster.id"
        :expanded="openId === cluster.id"
        @write="writeFromCluster"
        @ignore="toggleIgnore"
        @toggle="toggleDetail"
      />
    </div>

    <div v-if="!loading && clusters.length >= limit && limit < 200" class="hot-more">
      <button class="btn btn-ghost btn-sm" @click="showMore">显示更多</button>
    </div>
  </div>
</template>

<style scoped>
.hot-stat {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.hot-stat-line {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}
.hot-stat-item b {
  color: var(--color-text-primary);
  font-size: var(--font-sm);
  font-variant-numeric: tabular-nums;
}
.hot-stat-strong b {
  color: var(--color-danger);
}
.hot-stat-arrow {
  color: var(--color-text-muted);
}
.hot-track-bar {
  display: flex;
  height: 8px;
  width: 100%;
  max-width: 360px;
  border-radius: 4px;
  overflow: hidden;
  gap: 1px;
}
.hot-track-seg {
  display: block;
  height: 100%;
}
.hot-track-legend {
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}
.hot-stat-note {
  margin: 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
.hot-filters {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.hot-date {
  width: 150px;
}
/* .select 基础样式是 width:100%，在 flex-wrap 里会一行一个把筛选栏撑成四行 */
.hot-filters .select {
  width: auto;
  flex: 1 1 128px;
  max-width: 176px;
}
.hot-search {
  flex: 1 1 180px;
  min-width: 140px;
  max-width: 280px;
}
.hot-check {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
  white-space: nowrap;
  cursor: pointer;
}
.hot-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.hot-more {
  display: flex;
  justify-content: center;
  padding: var(--space-2) 0;
}
</style>
