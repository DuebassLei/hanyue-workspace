<script setup>
import { computed } from 'vue'
import HotScoreBars from './HotScoreBars.vue'

const props = defineProps({
  cluster: { type: Object, required: true },
  detail: { type: Object, default: null },
  degraded: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  detailLoading: { type: Boolean, default: false },
  expanded: { type: Boolean, default: false },
})

const emit = defineEmits(['write', 'ignore', 'toggle'])

const GRADE_COLOR = {
  强烈推荐: 'var(--color-danger)',
  可做: 'var(--color-warning)',
  备用: 'var(--color-text-secondary)',
  不建议: 'var(--color-text-muted)',
}

const gradeColor = computed(() => GRADE_COLOR[props.cluster.grade] || 'var(--color-text-muted)')

/** 'YYYY-MM-DD HH:MM' → '2 小时前'。算不出来就返回空串，不编「未知时间」。 */
function agoText(stamp) {
  if (!stamp) return ''
  const time = Date.parse(String(stamp).replace(' ', 'T'))
  if (!Number.isFinite(time)) return ''
  const diff = Date.now() - time
  if (diff < 60000) return '刚刚'
  const minutes = Math.floor(diff / 60000)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`
  return `${Math.floor(days / 30)} 个月前`
}

const firstSeen = computed(() => agoText(props.cluster.firstSeenAt || props.cluster.capturedAt))

const bestRank = computed(() =>
  props.cluster.bestRank == null ? '—' : `#${props.cluster.bestRank}`,
)

const items = computed(() => (props.detail && props.detail.items) || [])

const statusLabel = computed(() => {
  if (props.cluster.status === 'generated') return '已出稿'
  if (props.cluster.status === 'ignored') return '已忽略'
  if (props.cluster.status === 'watching') return '在观察'
  return ''
})

const score = computed(() => Number(props.cluster.score) || 0)
</script>

<template>
  <article class="hot-card" :class="{ 'is-muted': cluster.status === 'ignored' }">
    <div class="hot-card-main">
      <div class="hot-score" :style="{ color: gradeColor }" :title="`总分 ${score} / 100`">
        <b>{{ score }}</b>
        <span class="hot-grade">{{ cluster.grade || '—' }}</span>
      </div>

      <div class="hot-body">
        <div class="hot-title-row">
          <h3 class="hot-title" :title="cluster.title">{{ cluster.title }}</h3>
          <span v-if="statusLabel" class="tag" :class="{ 'tag-success': cluster.status === 'generated' }">
            {{ statusLabel }}
          </span>
        </div>

        <div class="hot-chips">
          <span v-for="platform in cluster.platforms || []" :key="platform" class="tag">{{ platform }}</span>
          <span v-for="flag in cluster.riskFlags || []" :key="flag" class="tag tag-danger">{{ flag }}</span>
        </div>

        <div class="hot-meta">
          <span>{{ cluster.track || '未判赛道' }}</span>
          <span v-if="firstSeen">· 首见 {{ firstSeen }}</span>
          <span>· 连续 {{ cluster.daysSeen || 1 }} 天</span>
          <span>· 最高 {{ bestRank }}</span>
          <span>· {{ cluster.itemCount || 0 }} 条原文</span>
        </div>

        <HotScoreBars :detail="cluster.scoreDetail || {}" :degraded="degraded" />

        <div class="hot-actions">
          <button
            class="btn btn-sm"
            :disabled="busy || cluster.status === 'ignored'"
            :title="cluster.status === 'ignored' ? '已忽略，先恢复再写' : '基于这条热点新建稿件'"
            @click="emit('write', cluster)"
          >
            {{ busy ? '创建中…' : cluster.status === 'generated' ? '再写一篇' : '写这篇' }}
          </button>
          <button
            class="btn btn-ghost btn-sm"
            :disabled="busy"
            @click="emit('ignore', cluster)"
          >
            {{ cluster.status === 'ignored' ? '恢复' : '忽略' }}
          </button>
          <button class="btn btn-ghost btn-sm" :disabled="detailLoading" @click="emit('toggle', cluster)">
            {{ expanded ? '收起原文' : '展开原文' }}
          </button>
          <a
            v-if="cluster.contentItemId"
            class="hot-link"
            :href="`/studio?id=${cluster.contentItemId}`"
          >打开已生成的稿件 →</a>
        </div>
      </div>
    </div>

    <div v-if="expanded" class="hot-detail">
      <div v-if="detailLoading" class="skeleton" style="height: 64px" />
      <template v-else-if="detail">
        <p v-if="degraded.length" class="hot-degraded">
          <b>本簇未计算：</b>{{ degraded.map((x) => x.reason).join('；') }}
        </p>
        <p v-if="detail.contentItemTitle" class="hot-linked">
          已生成稿件：<a class="hot-link" :href="`/studio?id=${detail.contentItemId}`">{{ detail.contentItemTitle }}</a>
        </p>
        <div class="hot-items">
          <div v-for="item in items" :key="item.id" class="hot-item">
            <span class="tag hot-item-platform">{{ item.platform }}</span>
            <span class="hot-item-rank">{{ item.rank == null ? '—' : `#${item.rank}` }}</span>
            <a
              v-if="item.url"
              class="hot-link hot-item-title"
              :href="item.url"
              target="_blank"
              rel="noreferrer"
              :title="item.title"
            >{{ item.title }}</a>
            <span v-else class="hot-item-title" :title="item.title">{{ item.title }}</span>
            <span class="hot-item-hot">{{ item.hot ? item.hot.toLocaleString('zh-CN') : '—' }}</span>
          </div>
        </div>
      </template>
    </div>
  </article>
</template>

<style scoped>
.hot-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.hot-card.is-muted {
  opacity: 0.62;
}
.hot-card-main {
  display: flex;
  gap: var(--space-4);
  align-items: flex-start;
}
.hot-score {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 54px;
  line-height: 1.1;
}
.hot-score b {
  font-size: 24px;
  font-variant-numeric: tabular-nums;
}
.hot-grade {
  font-size: 10.5px;
  white-space: nowrap;
}
.hot-body {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.hot-title-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.hot-title {
  margin: 0;
  font-size: var(--font-section);
  font-weight: 600;
  line-height: 1.4;
}
.hot-chips {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}
.hot-meta {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}
.hot-actions {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
  margin-top: 2px;
}
.hot-detail {
  border-top: 1px dashed var(--color-border);
  padding-top: var(--space-2);
}
.hot-degraded,
.hot-linked {
  margin: 0 0 var(--space-2);
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
  line-height: 1.6;
}
.hot-items {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.hot-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-sm);
  min-width: 0;
}
.hot-item-platform {
  min-width: 48px;
  text-align: center;
  flex: 0 0 auto;
}
.hot-item-rank {
  width: 34px;
  flex: 0 0 auto;
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
}
.hot-item-title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hot-item-hot {
  flex: 0 0 auto;
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
}
.hot-link {
  color: var(--color-accent);
  text-decoration: none;
  font-size: var(--font-sm);
}
.hot-link:hover {
  text-decoration: underline;
}
</style>
