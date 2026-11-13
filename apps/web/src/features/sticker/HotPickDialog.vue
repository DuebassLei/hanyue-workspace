<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NInput, NModal, NSpin } from 'naive-ui'
import { api } from '../../api/client'

const props = defineProps({
  show: { type: Boolean, default: false },
})

const emit = defineEmits(['update:show', 'pick'])

// 数据端点与 HotTopicsView 相同：GET /hot-topics/clusters（服务端已按分数排好序）
const clusters = ref([])
const loading = ref(false)
const error = ref('')
const keyword = ref('')
let searchTimer = null

const GRADE_COLOR = {
  强烈推荐: 'var(--color-danger)',
  可做: 'var(--color-warning)',
  备用: 'var(--color-text-secondary)',
  不建议: 'var(--color-text-muted)',
}

const filtered = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return clusters.value
  return clusters.value.filter((row) =>
    [row.title, row.track, ...(row.platforms || [])].join(' ').toLowerCase().includes(q),
  )
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    clusters.value = await api.get('/hot-topics/clusters?sort=score&limit=100')
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

watch(
  () => props.show,
  (open) => {
    if (open && !clusters.value.length && !loading.value) load()
  },
)

watch(keyword, () => {
  clearTimeout(searchTimer)
  // 列表是一次性拉回来的，搜索只做本地过滤，不用每次敲字打接口
})

function pick(cluster) {
  emit('pick', cluster)
  close()
}

function close() {
  emit('update:show', false)
}

onBeforeUnmount(() => clearTimeout(searchTimer))
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    title="选一个热点做贴图"
    :bordered="false"
    :segmented="{ content: true }"
    style="width: min(680px, calc(100vw - 32px))"
    @update:show="emit('update:show', $event)"
  >
    <p class="hot-pick-lead">选中的热点会预填主题与赛道，画风仍可改。</p>
    <n-input v-model:value="keyword" clearable placeholder="搜索事件标题、赛道或平台…" aria-label="搜索热点" />

    <n-alert v-if="error" type="error" :bordered="false" class="hot-pick-alert">
      {{ error }}
      <n-button quaternary size="small" style="margin-left: 8px" @click="load">重试</n-button>
    </n-alert>

    <div v-if="loading" class="hot-pick-state"><n-spin size="small" /><span>正在加载今日热点…</span></div>
    <div v-else-if="!error && !clusters.length" class="hot-pick-state">
      还没有采集过热点。先去「热点选题」页点一次「刷新并评分」。
    </div>
    <div v-else-if="!filtered.length" class="hot-pick-state">没有匹配的事件，换个关键词试试。</div>
    <div v-else class="hot-pick-list">
      <button
        v-for="cluster in filtered"
        :key="cluster.id"
        type="button"
        class="hot-pick-item"
        @click="pick(cluster)"
      >
        <span class="hot-pick-score" :style="{ color: GRADE_COLOR[cluster.grade] || 'var(--color-text-muted)' }">
          {{ cluster.score }}
        </span>
        <span class="hot-pick-main">
          <span class="hot-pick-title" :title="cluster.title">{{ cluster.title }}</span>
          <span class="hot-pick-meta">
            <span>{{ cluster.track || '未判赛道' }}</span>
            <span v-for="platform in cluster.platforms || []" :key="platform" class="tag">{{ platform }}</span>
          </span>
        </span>
        <span class="hot-pick-grade">{{ cluster.grade || '' }}</span>
      </button>
    </div>
  </n-modal>
</template>

<style scoped>
.hot-pick-lead {
  margin: 0 0 var(--space-3);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}
.hot-pick-alert {
  margin-top: var(--space-3);
}
.hot-pick-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-6);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  text-align: center;
}
.hot-pick-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
  max-height: 48vh;
  overflow-y: auto;
}
.hot-pick-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.hot-pick-item:hover {
  border-color: var(--color-accent);
}
.hot-pick-score {
  flex: 0 0 auto;
  width: 34px;
  font-size: var(--font-section);
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  text-align: center;
}
.hot-pick-main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.hot-pick-title {
  font-size: var(--font-body);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hot-pick-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}
.hot-pick-grade {
  flex: 0 0 auto;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
</style>
