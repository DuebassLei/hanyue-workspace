<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCheckbox,
  NDatePicker,
  NEmpty,
  NInput,
  NSelect,
  NSpace,
} from 'naive-ui'
import { api } from '../api/client'
import { toast } from '../ui/feedback'
import HotClusterCard from '../features/hot/HotClusterCard.vue'
import HotOverviewDialog from '../features/hot/HotOverviewDialog.vue'
import HotPlatformTabs from '../features/hot/HotPlatformTabs.vue'

const router = useRouter()
const route = useRoute()

const clusters = ref([])
const summary = ref({})
const degraded = ref([])
const sources = ref({ platforms: [], enabled: [] })
const loading = ref(true)
const refreshing = ref(false)
const error = ref('')
const busyId = ref('')
const overviewOpen = ref(false)
const openId = ref('')
const detailCache = reactive({})
const detailLoadingId = ref('')

const filters = reactive({
  date: '',
  track: '',
  // 平台写进 URL（/hot-topics?platform=微博）：刷新、分享、后退都能回到同一视图
  platform: String(route.query.platform || ''),
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
const scoreOptions = [
  { value: 0, label: '全部分数' },
  { value: 40, label: '40+ 备用' },
  { value: 55, label: '55+ 可做' },
  { value: 70, label: '70+ 强烈推荐' },
]
const sortOptions = [
  { value: 'score', label: '价值优先' },
  { value: 'time', label: '最新优先' },
  { value: 'resonance', label: '共振优先' },
]
const trackSelectOptions = computed(() => [
  { value: '', label: '全部赛道' },
  ...trackOptions.value.map((track) => ({ value: track, label: track })),
])
const dateTs = computed({
  get: () => (filters.date ? Date.parse(`${filters.date}T00:00:00`) : null),
  set: (value) => {
    if (!value) {
      filters.date = ''
      return
    }
    const d = new Date(value)
    const month = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    filters.date = `${d.getFullYear()}-${month}-${day}`
  },
})

// 平台切换条：条数来自服务端 summary（当天该平台的事件数），
// 切过去之前就知道会看到多少条，不用点进去撞空列表。
// 顺序跟随采集源配置固定，不按条数排序——每次刷新都换位置会找不到按钮。
const platformTabs = computed(() => {
  const counts = summary.value.platforms || {}
  const enabled = sources.value.enabled || []
  const known = enabled.length ? enabled : Object.keys(counts)
  return known.map((name) => ({ name, count: counts[name] || 0 }))
})

const platformTotal = computed(() =>
  Object.values(summary.value.platforms || {}).reduce((sum, count) => sum + (count || 0), 0),
)

const visible = computed(() =>
  clusters.value.filter(
    (row) => !filters.onlyNew || (row.status !== 'ignored' && row.status !== 'generated'),
  ),
)

/** 计数带上前缀，切了平台也能一眼看出「现在这些数字是哪个渠道的」。 */
const resultHint = computed(() => {
  const scope = filters.platform ? `${filters.platform} · ` : ''
  // 列表被 limit 截断时要说清楚「下面还有」，别让人以为全站就这么几个事件
  const truncated = clusters.value.length >= limit.value && limit.value < 200
  return truncated
    ? `${scope}显示 ${visible.value.length} 个事件（已加载前 ${clusters.value.length} 个）`
    : `${scope}显示 ${visible.value.length} / ${clusters.value.length} 个事件`
})

/** 平台筛选写进 URL：刷新、分享、浏览器后退都还能回到同一个平台视图。 */
function syncPlatformToUrl() {
  const query = { ...route.query }
  if (filters.platform) query.platform = filters.platform
  else delete query.platform
  if (JSON.stringify(query) === JSON.stringify(route.query)) return
  router.replace({ query })
}

// 平台是「看哪个渠道」的主维度，单独一条：切平台时立刻请求，不等防抖
watch(
  () => filters.platform,
  () => {
    syncPlatformToUrl()
    limit.value = 60
    loadClusters()
  },
)

// 浏览器前进/后退改的是 URL，得让它反过来驱动筛选
watch(
  () => route.query.platform,
  (value) => {
    filters.platform = String(value || '')
  },
)

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
  try {
    const result = await api.post('/hot-topics/refresh', {})
    toast(
      `已刷新 ${result.day}：${result.items} 条素材 → ${result.clusters} 个事件` +
        (result.notes && result.notes.length ? `（${result.notes.join('；')}）` : ''),
    )
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
    toast(`已新建稿件「${item.title}」，正在跳转到创作空间`)
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
    toast(next === 'ignored' ? '已忽略这条热点' : '已恢复到列表')
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
  () => [filters.date, filters.track, filters.minScore, filters.sort],
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
        <!-- 定位说明只说一遍：topbar 和标题重复，会白占一屏里最贵的两行 -->
        <p class="page-sub">
          今天大家搜什么 → 我该写哪一个。分数由服务端算好，悬停分数条看证据。
        </p>
      </div>
      <n-space :size="8">
        <n-button secondary :disabled="loading" @click="overviewOpen = true">
          今日概览
          <template v-if="filters.platform"> · {{ filters.platform }}</template>
        </n-button>
        <n-button secondary :loading="refreshing" :disabled="loading" @click="refreshTopics">
          刷新并评分
        </n-button>
      </n-space>
    </div>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <HotPlatformTabs
      v-model="filters.platform"
      :platforms="platformTabs"
      :total="platformTotal"
      :loading="loading"
    />

    <HotOverviewDialog
      :show="overviewOpen"
      :summary="summary"
      :sources="sources"
      :degraded="degraded"
      :platform="filters.platform"
      :platforms="platformTabs"
      :loading="loading"
      @update:show="overviewOpen = $event"
      @update:platform="filters.platform = $event"
    />

    <section class="card hot-filters">
      <n-date-picker v-model:value="dateTs" type="date" clearable class="hot-date" />
      <n-select
        v-model:value="filters.track"
        :options="trackSelectOptions"
        class="hot-select"
        aria-label="按赛道筛选"
      />
      <n-select
        v-model:value="filters.minScore"
        :options="scoreOptions"
        class="hot-select"
        aria-label="按分数筛选"
      />
      <n-select v-model:value="filters.sort" :options="sortOptions" class="hot-select" aria-label="排序方式" />
      <n-checkbox v-model:checked="filters.onlyNew">只看未出稿</n-checkbox>
      <div class="spacer" />
      <span class="field-hint">{{ resultHint }}</span>
      <n-button quaternary size="small" @click="resetFilters">清空</n-button>
      <n-input
        v-model:value="filters.q"
        class="hot-search"
        clearable
        placeholder="搜索事件标题…"
        aria-label="搜索事件"
      />
    </section>

    <div v-if="loading" class="stack">
      <div v-for="i in 3" :key="i" class="skeleton" style="height: 150px" />
    </div>

    <n-empty v-else-if="!summary.clusters" description="点右上角「刷新并评分」，会抓取今日头条、百度、抖音、微博、知乎与 AI 资讯，合并同一事件后打分。">
      <template #extra>
        <n-button type="primary" size="small" :loading="refreshing" @click="refreshTopics">现在采集</n-button>
      </template>
    </n-empty>

    <n-empty v-else-if="!visible.length">
      <template #default>
        <template v-if="filters.platform">
          「{{ filters.platform }}」下没有符合条件的事件。
          <span v-if="summary.platforms?.[filters.platform]">
            这个平台今天有 {{ summary.platforms[filters.platform] }} 个事件，可能都被筛选条件挡住了。
          </span>
          <span v-else>今天这一路没采到事件（可能采集失败），换一个平台看看。</span>
        </template>
        <template v-else>没有符合条件的事件。取消「只看未出稿」，或清空筛选再试试。</template>
      </template>
      <template #extra>
        <n-space v-if="filters.platform">
          <n-button type="primary" size="small" @click="filters.platform = ''">看全部平台</n-button>
          <n-button secondary size="small" @click="resetFilters">清空筛选</n-button>
        </n-space>
        <n-button v-else secondary size="small" @click="resetFilters">清空筛选</n-button>
      </template>
    </n-empty>

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
        @sticker="(c) => router.push(`/stickers?new=1&clusterId=${c.id}`)"
      />
    </div>

    <div v-if="!loading && clusters.length >= limit && limit < 200" class="hot-more">
      <n-button secondary size="small" @click="showMore">显示更多</n-button>
    </div>
  </div>
</template>

<style scoped>
.hot-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  /* 往下滚时筛选贴顶，换平台/改分数不用先滚回顶部 */
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--color-surface);
}
/* 全部快速筛选排一行（桌面），搜索独占第二行；结果计数和清空在右侧。
   以前七件东西挤一行，窄一点就折成三四行，粘顶后白占半屏。 */
.hot-date {
  flex: 0 0 auto;
  width: 160px;
}
.hot-filters :deep(.hot-select) {
  width: auto;
  flex: 1 1 140px;
  max-width: 180px;
}
.hot-search {
  flex: 1 1 100%;
  max-width: 360px;
}
@media (max-width: 560px) {
  .hot-search {
    order: -1;
    flex-basis: 100%;
  }
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
  gap: var(--space-2);
}
.hot-more {
  display: flex;
  justify-content: center;
  padding: var(--space-2) 0;
}
</style>
