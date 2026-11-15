<script setup>
// 稿件库：创作空间侧栏「查看全部稿件」打开的全量检索视图。
// 数据走 GET /content?fields=list（瘦身模式，不含正文），游标分页加载更多；
// 归档/恢复在这里闭环——侧栏默认隐藏归档稿，找回入口就在本弹窗。
import { computed, ref, watch } from 'vue'
import { NButton, NModal, NSwitch, NSelect, NInput } from 'naive-ui'
import { api } from '../../api/client'
import { toast } from '../../ui/feedback'

const props = defineProps({
  show: { type: Boolean, default: false },
})
const emit = defineEmits(['update:show', 'open', 'archived'])

// 与创作空间保持同一份平台清单
const PLATFORMS = [{ code: 'wechat_official', label: '公众号' }]
const PAGE_SIZE = 50
const rows = ref([])
const cursor = ref('')
const loading = ref(false)
const loadingMore = ref(false)
const errorText = ref('')
const archivingId = ref('')

const keyword = ref('')
const platform = ref('')
const range = ref('')
const sort = ref('updatedAt')
const includeArchived = ref(false)

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
const platformOptions = computed(() => [
  { label: '全部平台', value: '' },
  ...PLATFORMS.map((p) => ({ label: p.label, value: p.code })),
])

function isoDaysAgo(days) {
  return new Date(Date.now() - days * 86400000).toISOString()
}

function queryUrl(cursorValue) {
  const params = new URLSearchParams({ fields: 'list', limit: String(PAGE_SIZE), sort: sort.value })
  if (keyword.value.trim()) params.set('q', keyword.value.trim())
  if (platform.value) params.set('platform', platform.value)
  if (range.value) params.set('updatedFrom', isoDaysAgo(Number(range.value.replace('d', ''))))
  if (includeArchived.value) params.set('includeArchived', 'true')
  if (cursorValue) params.set('cursor', cursorValue)
  return `/content?${params.toString()}`
}

async function load(reset = true) {
  if (reset) {
    loading.value = true
    errorText.value = ''
  } else {
    loadingMore.value = true
  }
  try {
    // getPage 把 X-Next-Cursor 响应头带出来，真 keyset 分页
    const { data, nextCursor } = await api.getPage(queryUrl(reset ? '' : cursor.value))
    rows.value = reset ? data : [...rows.value, ...data]
    cursor.value = nextCursor || ''
    hasMore.value = Boolean(nextCursor)
  } catch (err) {
    errorText.value = err.message
  } finally {
    loading.value = false
    loadingMore.value = false
  }
}

const hasMore = ref(false)

// 筛选条件变化即回第一页；输入框防抖，避免每敲一个字打一次接口
let filterTimer = null
watch(
  [keyword, platform, range, sort, includeArchived],
  () => {
    if (!props.show) return
    if (filterTimer) clearTimeout(filterTimer)
    filterTimer = setTimeout(() => load(true), 260)
  },
)
watch(
  () => props.show,
  (open) => {
    if (open) load(true)
  },
)

async function toggleArchive(row) {
  archivingId.value = row.id
  try {
    const updated = await api.patch(`/content/${row.id}/archive`, { archived: !row.archived })
    row.archived = updated.archived
    row.archivedAt = updated.archivedAt
    toast(updated.archived ? `已归档「${row.title}」` : `已恢复「${row.title}」`)
    // 通知创作空间刷新侧栏：归档稿要消失、恢复稿要回来
    emit('archived', { id: row.id, archived: updated.archived })
  } catch (err) {
    errorText.value = err.message
  } finally {
    archivingId.value = ''
  }
}

function openRow(row) {
  emit('open', row.id)
  emit('update:show', false)
}

function dateLabel(value) {
  return value ? String(value).slice(0, 16).replace('T', ' ') : '—'
}
</script>

<template>
  <n-modal
    :show="props.show"
    preset="card"
    title="稿件库"
    style="width: min(960px, calc(100vw - 32px))"
    @update:show="(value) => emit('update:show', value)"
  >
    <div class="lib-filters">
      <n-input v-model:value="keyword" clearable round size="small" placeholder="搜索标题" aria-label="搜索标题" />
      <n-select v-model:value="platform" size="small" :options="platformOptions" aria-label="按平台筛选" />
      <n-select v-model:value="range" size="small" :options="RANGE_OPTIONS" aria-label="按更新时间筛选" />
      <n-select v-model:value="sort" size="small" :options="SORT_OPTIONS" aria-label="排序方式" />
      <label class="lib-archived-switch">
        <n-switch v-model:value="includeArchived" size="small" />
        <span>含已归档</span>
      </label>
    </div>
    <p v-if="errorText" class="lib-error">{{ errorText }}</p>
    <div class="lib-table-wrap">
      <table class="lib-table">
        <thead>
          <tr>
            <th>标题</th>
            <th>版本</th>
            <th>平台</th>
            <th>更新</th>
            <th aria-label="操作" />
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id" :class="{ 'is-archived': row.archived }">
            <td class="lib-title-cell" :title="row.title">
              <button class="lib-open" type="button" @click="openRow(row)">{{ row.title }}</button>
              <span v-if="row.archived" class="lib-archived-tag">已归档</span>
            </td>
            <td class="lib-num">v{{ row.versionNo || 1 }}</td>
            <td class="lib-num">{{ row.variantCount || 0 }}</td>
            <td class="lib-date">{{ dateLabel(row.updatedAt) }}</td>
            <td class="lib-actions">
              <n-button size="tiny" tertiary :loading="archivingId === row.id" @click="toggleArchive(row)">
                {{ row.archived ? '恢复' : '归档' }}
              </n-button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="loading && !rows.length" class="lib-empty">加载中…</p>
      <p v-else-if="!loading && !rows.length" class="lib-empty">没有符合条件的稿件</p>
    </div>
    <div class="lib-foot">
      <span class="lib-count">共加载 {{ rows.length }} 篇</span>
      <n-button v-if="hasMore" size="small" tertiary :loading="loadingMore" @click="load(false)">
        加载更多
      </n-button>
    </div>
  </n-modal>
</template>

<style scoped>
.lib-filters {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
}
.lib-filters .n-input {
  flex: 1 1 180px;
  min-width: 160px;
}
.lib-filters .n-select {
  width: 128px;
}
.lib-archived-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
  white-space: nowrap;
}
.lib-error {
  margin: 0 0 var(--space-2);
  font-size: var(--font-xs);
  color: var(--color-danger);
}
.lib-table-wrap {
  max-height: 56vh;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.lib-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-sm);
}
.lib-table th {
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
.lib-table td {
  padding: 7px 10px;
  border-top: 1px solid var(--color-border);
  vertical-align: middle;
}
.lib-title-cell {
  max-width: 320px;
}
.lib-open {
  max-width: 260px;
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
.lib-open:hover {
  color: var(--color-accent);
}
.is-archived .lib-open {
  color: var(--color-text-muted);
  text-decoration: line-through;
}
.lib-archived-tag {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--color-surface-raised);
  color: var(--color-text-muted);
  font-size: 10px;
}
.lib-num {
  font-variant-numeric: tabular-nums;
  color: var(--color-text-secondary);
}
.lib-date {
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.lib-actions {
  text-align: right;
}
.lib-empty {
  margin: 0;
  padding: var(--space-6);
  color: var(--color-text-muted);
  font-size: var(--font-sm);
  text-align: center;
}
.lib-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: var(--space-3);
}
.lib-count {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}
</style>
