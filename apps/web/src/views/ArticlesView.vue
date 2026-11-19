<script setup>
/**
 * 文章管理（我的稿件）：独立的稿件检索/管理页，编辑器逻辑仍留在 StudioView。
 *
 * - 列表走 GET /content?fields=list + api.getPage() 真 keyset 分页，取消创作空间侧栏的 200 篇硬上限。
 * - 搜索/状态/平台/时间/归档/排序全部是服务端筛选，并同步到 URL query（刷新、分享、后退可复现）。
 * - 状态列复用 publish_jobs.status：按 contentItemId 联查，无 publishing.view 时置灰降级，不报错。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCheckbox, NInput, NSelect, NSwitch } from 'naive-ui'
import { api } from '../api/client'
import { confirm, toast } from '../ui/feedback'
import { usePermission } from '../composables/usePermission'
import StatusTag from '../components/StatusTag.vue'

const route = useRoute()
const router = useRouter()
const { disabledReason } = usePermission()

// 状态列跨权限点：进页只要 studio.use，读发布任务另需 publishing.view。
const publishDisabledReason = computed(() => disabledReason('publishing.view', '发布管理查看'))

const PLATFORMS = [{ code: 'wechat_official', label: '公众号' }]
const PAGE_SIZE = 50
const RANGE_OPTIONS = [
  { label: '不限时间', value: '' },
  { label: '近 7 天', value: '7d' },
  { label: '近 30 天', value: '30d' },
  { label: '近 90 天', value: '90d' },
]
const SORT_OPTIONS = [
  { label: '最近更新', value: 'updatedAt' },
  { label: '最近创建', value: 'createdAt' },
  { label: '标题 A-Z', value: 'title' },
]
// 真实发布态在 publish_jobs；chips 经 /content 的 publishStatus 参数做服务端 EXISTS 联表筛选。
const STATUS_CHIPS = [
  { id: 'all', label: '全部', value: '' },
  { id: 'draft', label: '草稿', value: 'none' },
  { id: 'pending', label: '待处理', value: 'pending_review,approved' },
  { id: 'queued', label: '排期中', value: 'queued' },
  { id: 'publishing', label: '进行中', value: 'publishing' },
  { id: 'attention', label: '需处理', value: 'failed,needs_attention' },
  { id: 'succeeded', label: '成功', value: 'succeeded' },
  { id: 'cancelled', label: '已取消', value: 'cancelled' },
]
const STATUS_IDS = new Set(STATUS_CHIPS.map((chip) => chip.id))
const SORT_IDS = new Set(SORT_OPTIONS.map((option) => option.value))
const RANGE_IDS = new Set(RANGE_OPTIONS.map((option) => option.value))

const platformOptions = computed(() => [
  { label: '全部平台', value: '' },
  ...PLATFORMS.map((item) => ({ label: item.label, value: item.code })),
])
const chipValue = (id) => STATUS_CHIPS.find((chip) => chip.id === id)?.value || ''

function readQuery(query) {
  const status = String(query.status || 'all')
  const sort = String(query.sort || 'updatedAt')
  const range = String(query.range || '')
  return {
    q: String(query.q || ''),
    status: STATUS_IDS.has(status) ? status : 'all',
    platform: String(query.platform || ''),
    range: RANGE_IDS.has(range) ? range : '',
    sort: SORT_IDS.has(sort) ? sort : 'updatedAt',
    includeArchived: query.archived === '1' || query.archived === 'true',
  }
}

const filters = reactive(readQuery(route.query))
const rows = ref([])
const cursor = ref('')
const hasMore = ref(false)
const loading = ref(false)
const loadingMore = ref(false)
const errorText = ref('')
const selected = ref([])
const rowBusyId = ref('')
const batchBusy = ref(false)
const creating = ref(false)
// contentItemId → 最新发布状态；status='none' 表示无发布任务（草稿）
const publishStatusById = ref({})

const allSelected = computed(
  () => rows.value.length > 0 && selected.value.length === rows.value.length,
)
const selectedCount = computed(() => selected.value.length)

function isoDaysAgo(days) {
  return new Date(Date.now() - days * 86400000).toISOString()
}

function queryUrl(cursorValue) {
  const params = new URLSearchParams({
    fields: 'list',
    limit: String(PAGE_SIZE),
    sort: filters.sort,
  })
  if (filters.q.trim()) params.set('q', filters.q.trim())
  const publishStatus = chipValue(filters.status)
  if (publishStatus) params.set('publishStatus', publishStatus)
  if (filters.platform) params.set('platform', filters.platform)
  if (filters.range) params.set('updatedFrom', isoDaysAgo(Number(filters.range.replace('d', ''))))
  if (filters.includeArchived) params.set('includeArchived', 'true')
  if (cursorValue) params.set('cursor', cursorValue)
  return `/content?${params.toString()}`
}

async function loadPublishStatuses(list) {
  // 无 publishing.view 时整列置灰，根本不发请求；单条失败也降级为草稿，不影响整页。
  if (publishDisabledReason.value) return
  const missing = list.filter((row) => !(row.id in publishStatusById.value))
  if (!missing.length) return
  await Promise.all(
    missing.map(async (row) => {
      try {
        const jobs = await api.get(
          `/publishing/jobs?contentItemId=${encodeURIComponent(row.id)}&limit=1`,
        )
        publishStatusById.value[row.id] = jobs?.[0]?.status || 'draft'
      } catch {
        publishStatusById.value[row.id] = 'draft'
      }
    }),
  )
}

async function load(reset = true) {
  if (reset) {
    loading.value = true
    errorText.value = ''
  } else {
    loadingMore.value = true
  }
  try {
    const { data, nextCursor } = await api.getPage(queryUrl(reset ? '' : cursor.value))
    rows.value = reset ? data : [...rows.value, ...data]
    cursor.value = nextCursor || ''
    hasMore.value = Boolean(nextCursor)
    if (reset) selected.value = []
    await loadPublishStatuses(data)
  } catch (err) {
    errorText.value = err.message
  } finally {
    loading.value = false
    loadingMore.value = false
  }
}

// 筛选条件变化：同步 URL + 300ms 防抖回第一页。
let filterTimer = null
let urlDriven = false
function scheduleLoad() {
  if (filterTimer) clearTimeout(filterTimer)
  filterTimer = setTimeout(() => load(true), 300)
}

function syncUrl() {
  const query = { ...route.query }
  const put = (key, value) => {
    if (value) query[key] = value
    else delete query[key]
  }
  put('q', filters.q.trim())
  put('status', filters.status === 'all' ? '' : filters.status)
  put('platform', filters.platform)
  put('range', filters.range)
  put('sort', filters.sort === 'updatedAt' ? '' : filters.sort)
  put('archived', filters.includeArchived ? '1' : '')
  if (JSON.stringify(query) === JSON.stringify(route.query)) return
  router.replace({ query })
}

watch(
  filters,
  () => {
    if (urlDriven) {
      urlDriven = false
      return
    }
    syncUrl()
    scheduleLoad()
  },
  { deep: true },
)

// 浏览器前进/后退改 URL 时反向驱动筛选，避免页面与地址栏脱节。
watch(
  () => route.query,
  (query) => {
    const next = readQuery(query)
    if (Object.keys(next).every((key) => next[key] === filters[key])) return
    urlDriven = true
    Object.assign(filters, next)
    load(true)
  },
)

onMounted(() => load(true))

function dateLabel(value) {
  return value ? String(value).slice(0, 16).replace('T', ' ') : '—'
}

function toggleAll(checked) {
  selected.value = checked ? rows.value.map((row) => row.id) : []
}

function toggleSelect(id, checked) {
  selected.value = checked
    ? [...selected.value, id]
    : selected.value.filter((item) => item !== id)
}

function openRow(row) {
  router.push({ name: 'studio', query: { id: row.id } })
}

async function createArticle() {
  creating.value = true
  try {
    const created = await api.post('/content', { title: '未命名稿件' })
    router.push({ name: 'studio', query: { id: created.id } })
  } catch (err) {
    toast(err.message, 'error')
  } finally {
    creating.value = false
  }
}

async function toggleArchive(row) {
  rowBusyId.value = row.id
  try {
    const updated = await api.patch(`/content/${row.id}/archive`, { archived: !row.archived })
    row.archived = updated.archived
    row.archivedAt = updated.archivedAt
    selected.value = selected.value.filter((id) => id !== row.id)
    toast(updated.archived ? `已归档「${row.title}」` : `已恢复「${row.title}」`)
    if (!filters.includeArchived) load(true)
  } catch (err) {
    toast(err.message, 'error')
  } finally {
    rowBusyId.value = ''
  }
}

async function deleteRow(row) {
  if (!(await confirm(`确认删除「${row.title}」？稿件及其版本、发布记录会一并删除，且不可恢复。`, '删除稿件'))) {
    return
  }
  rowBusyId.value = row.id
  try {
    await api.del(`/content/${row.id}`)
    toast(`已删除「${row.title}」`)
    await load(true)
  } catch (err) {
    toast(err.message, 'error')
  } finally {
    rowBusyId.value = ''
  }
}

async function batchArchive(archived) {
  if (!selectedCount.value || batchBusy.value) return
  const action = archived ? '归档' : '恢复'
  if (!(await confirm(`确认${action}选中的 ${selectedCount.value} 篇稿件？`, `批量${action}`))) return
  batchBusy.value = true
  try {
    const result = await api.post('/content/batch-archive', {
      ids: [...selected.value],
      archived,
    })
    toast(`已${action} ${result.updated} 篇${result.skipped ? `，跳过 ${result.skipped} 篇` : ''}`)
    await load(true)
  } catch (err) {
    toast(err.message, 'error')
  } finally {
    batchBusy.value = false
  }
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1 class="page-title">文章管理</h1>
        <p class="page-sub">我的稿件：服务端检索、筛选与分页，打开稿件仍回到创作空间继续编辑。</p>
      </div>
      <n-button type="primary" size="small" :loading="creating" @click="createArticle">
        + 新建稿件
      </n-button>
    </header>

    <section class="card">
      <div class="articles-toolbar">
        <div class="articles-batch">
          <span class="articles-selected">已选 {{ selectedCount }} 篇</span>
          <n-button
            size="small"
            tertiary
            :disabled="!selectedCount"
            :loading="batchBusy"
            @click="batchArchive(true)"
          >
            批量归档
          </n-button>
          <n-button
            size="small"
            tertiary
            :disabled="!selectedCount"
            :loading="batchBusy"
            @click="batchArchive(false)"
          >
            批量恢复
          </n-button>
        </div>
        <div class="articles-sort">
          <span class="articles-sort-label">排序</span>
          <n-select
            v-model:value="filters.sort"
            size="small"
            :options="SORT_OPTIONS"
            aria-label="排序方式"
          />
        </div>
      </div>

      <div class="articles-chips" role="group" aria-label="按发布状态筛选">
        <button
          v-for="chip in STATUS_CHIPS"
          :key="chip.id"
          type="button"
          :class="{ 'is-active': filters.status === chip.id }"
          :aria-pressed="filters.status === chip.id"
          @click="filters.status = chip.id"
        >
          {{ chip.label }}
        </button>
      </div>

      <div class="articles-filters">
        <n-input
          v-model:value="filters.q"
          clearable
          round
          size="small"
          placeholder="搜索稿件标题"
          aria-label="搜索稿件标题"
        />
        <n-select
          v-model:value="filters.platform"
          size="small"
          :options="platformOptions"
          aria-label="按平台筛选"
        />
        <n-select
          v-model:value="filters.range"
          size="small"
          :options="RANGE_OPTIONS"
          aria-label="按更新时间筛选"
        />
        <label class="articles-archived">
          <n-switch v-model:value="filters.includeArchived" size="small" />
          <span>含已归档</span>
        </label>
      </div>

      <p v-if="errorText" class="articles-error">{{ errorText }}</p>

      <div class="articles-table-wrap">
        <table class="articles-table">
          <thead>
            <tr>
              <th class="articles-check">
                <n-checkbox
                  :checked="allSelected"
                  :indeterminate="selectedCount > 0 && !allSelected"
                  aria-label="全选本页"
                  @update:checked="toggleAll"
                />
              </th>
              <th>标题</th>
              <th>状态</th>
              <th>版本</th>
              <th>平台</th>
              <th>更新</th>
              <th class="articles-actions" aria-label="操作" />
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in rows"
              :key="row.id"
              :class="{ 'is-archived': row.archived, 'is-selected': selected.includes(row.id) }"
            >
              <td class="articles-check">
                <n-checkbox
                  :checked="selected.includes(row.id)"
                  aria-label="选择稿件"
                  @update:checked="(checked) => toggleSelect(row.id, checked)"
                />
              </td>
              <td class="articles-title-cell" :title="row.title">
                <button class="articles-open" type="button" @click="openRow(row)">
                  {{ row.title }}
                </button>
                <span v-if="row.archived" class="articles-archived-tag">已归档</span>
              </td>
              <td>
                <StatusTag
                  v-if="!publishDisabledReason"
                  :status="publishStatusById[row.id] || 'draft'"
                />
                <span v-else class="articles-status-muted" :title="publishDisabledReason">—</span>
              </td>
              <td class="articles-num">v{{ row.versionNo || 1 }}</td>
              <td class="articles-num">{{ row.variantCount || 0 }}</td>
              <td class="articles-date">{{ dateLabel(row.updatedAt) }}</td>
              <td class="articles-actions">
                <n-button size="tiny" quaternary :loading="rowBusyId === row.id" @click="openRow(row)">
                  打开
                </n-button>
                <n-button
                  size="tiny"
                  tertiary
                  :disabled="rowBusyId === row.id"
                  @click="toggleArchive(row)"
                >
                  {{ row.archived ? '恢复' : '归档' }}
                </n-button>
                <n-button
                  size="tiny"
                  type="error"
                  quaternary
                  :disabled="rowBusyId === row.id"
                  @click="deleteRow(row)"
                >
                  删除
                </n-button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="loading && !rows.length" class="articles-empty">加载中…</p>
        <p v-else-if="!loading && !rows.length" class="articles-empty">
          没有符合条件的稿件，换个筛选条件或新建一篇试试。
        </p>
      </div>

      <footer class="articles-foot">
        <span class="articles-count">共加载 {{ rows.length }} 篇</span>
        <n-button v-if="hasMore" size="small" tertiary :loading="loadingMore" @click="load(false)">
          加载更多
        </n-button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.articles-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
}
.articles-batch,
.articles-sort {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.articles-selected,
.articles-sort-label {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}
.articles-sort .n-select {
  width: 132px;
}
.articles-chips {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding: 2px 0 8px;
  border-bottom: 1px solid var(--color-border);
}
.articles-chips button {
  display: flex;
  flex: 0 0 auto;
  padding: 6px 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: var(--font-xs);
  cursor: pointer;
}
.articles-chips button.is-active {
  border-color: var(--color-border);
  background: var(--color-surface-raised);
  color: var(--color-text-primary);
  font-weight: 600;
}
.articles-filters {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin: var(--space-3) 0;
}
.articles-filters .n-input {
  flex: 1 1 220px;
  min-width: 180px;
}
.articles-filters .n-select {
  width: 132px;
}
.articles-archived {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
  white-space: nowrap;
}
.articles-error {
  margin: 0 0 var(--space-2);
  font-size: var(--font-xs);
  color: var(--color-danger);
}
.articles-table-wrap {
  max-height: 64vh;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.articles-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-sm);
}
.articles-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 8px 10px;
  background: var(--color-surface-raised);
  color: var(--color-text-muted);
  font-size: var(--font-xs);
  font-weight: 600;
  text-align: left;
}
.articles-table td {
  padding: 7px 10px;
  border-top: 1px solid var(--color-border);
  vertical-align: middle;
}
.articles-table tr.is-selected td {
  background: var(--color-surface-raised);
}
.articles-check {
  width: 36px;
  text-align: center;
}
.articles-title-cell {
  max-width: 360px;
}
.articles-open {
  max-width: 300px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--color-text-primary);
  font: inherit;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  vertical-align: bottom;
}
.articles-open:hover {
  color: var(--color-accent);
}
.is-archived .articles-open {
  color: var(--color-text-muted);
  text-decoration: line-through;
}
.articles-archived-tag {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--color-surface-raised);
  color: var(--color-text-muted);
  font-size: 10px;
}
.articles-status-muted {
  color: var(--color-text-muted);
}
.articles-num {
  font-variant-numeric: tabular-nums;
  color: var(--color-text-secondary);
}
.articles-date {
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.articles-actions {
  text-align: right;
  white-space: nowrap;
}
.articles-empty {
  margin: 0;
  padding: var(--space-6);
  color: var(--color-text-muted);
  font-size: var(--font-sm);
  text-align: center;
}
.articles-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: var(--space-3);
}
.articles-count {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}
</style>
