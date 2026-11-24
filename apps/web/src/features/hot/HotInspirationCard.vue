<script setup>
import { NButton, NSpace } from 'naive-ui'
import HotScoreBars from './HotScoreBars.vue'

defineProps({
  cluster: { type: Object, required: true },
  detail: { type: Object, default: null },
  degraded: { type: Array, default: () => [] },
  busy: Boolean,
  detailLoading: Boolean,
  expanded: Boolean,
  writeDenied: { type: String, default: '' },
  stickerDenied: { type: String, default: '' },
  index: { type: Number, required: true },
})
const emit = defineEmits(['write', 'ignore', 'toggle', 'sticker'])
</script>

<template>
  <article class="inspiration-card" :class="{ 'is-muted': cluster.status === 'ignored' }">
    <div class="card-topline">
      <span>{{ cluster.track || '未判赛道' }}</span>
      <span>{{ cluster.grade || '热点' }} · {{ Number(cluster.score) || 0 }} 分</span>
    </div>
    <h3 class="card-title">{{ cluster.title }}</h3>
    <div v-if="cluster.riskFlags?.length" class="card-tags">
      <span v-for="flag in cluster.riskFlags" :key="flag" class="tag tag-danger">{{ flag }}</span>
    </div>
    <div class="card-meta">
      <span class="card-dot" />
      <span>{{ (cluster.platforms || []).join(' · ') || '未知平台' }}</span>
      <span class="card-status">{{ cluster.status === 'generated' ? '已出稿' : cluster.status === 'ignored' ? '已忽略' : cluster.status === 'watching' ? '在观察' : `${cluster.itemCount || 0} 条原文` }}</span>
    </div>
    <div class="card-meta">首见 {{ cluster.firstSeenAt || cluster.capturedAt || '—' }} · 连续 {{ cluster.daysSeen || 1 }} 天 · 最高 {{ cluster.bestRank == null ? '—' : `#${cluster.bestRank}` }}</div>
    <HotScoreBars :detail="cluster.scoreDetail || {}" :degraded="degraded" variant="compact" />
    <n-space class="card-actions" :size="6">
      <n-button type="primary" size="small" :loading="busy" :disabled="cluster.status === 'ignored' || Boolean(writeDenied)" :title="writeDenied || (cluster.status === 'ignored' ? '已忽略，先恢复再写' : '基于这条热点新建稿件')" @click="emit('write', cluster)">{{ cluster.status === 'generated' ? '再写一篇' : '写这篇' }}</n-button>
      <n-button secondary size="small" :disabled="busy" @click="emit('ignore', cluster)">{{ cluster.status === 'ignored' ? '恢复' : '忽略' }}</n-button>
      <n-button secondary size="small" :loading="detailLoading" @click="emit('toggle', cluster)">{{ expanded ? '收起原文' : '展开原文' }}</n-button>
      <n-button secondary size="small" :disabled="Boolean(stickerDenied)" :title="stickerDenied || '基于这条热点做一组贴图'" @click="emit('sticker', cluster)">做贴图</n-button>
    </n-space>
    <a v-if="cluster.contentItemId" class="card-link" :href="`/studio?id=${cluster.contentItemId}`">打开已生成的稿件 →</a>
    <div v-if="expanded" class="card-detail">
      <div v-if="detailLoading" class="skeleton" style="height: 64px" />
      <template v-else-if="detail">
        <p v-if="degraded.length">本簇未计算：{{ degraded.map((x) => x.reason).join('；') }}</p>
        <p v-if="detail.contentItemTitle">已生成稿件：<a class="card-link" :href="`/studio?id=${detail.contentItemId}`">{{ detail.contentItemTitle }}</a></p>
        <div v-for="item in detail.items || []" :key="item.id" class="card-source">
          <span>{{ item.platform }} · {{ item.rank == null ? '—' : `#${item.rank}` }}</span>
          <a v-if="item.url" class="card-link" :href="item.url" target="_blank" rel="noreferrer">{{ item.title }}</a><span v-else>{{ item.title }}</span>
          <a v-if="item.sourceUrl" class="card-link" :href="item.sourceUrl" target="_blank" rel="noreferrer">来源</a>
          <span>{{ item.hot ? item.hot.toLocaleString('zh-CN') : '—' }}</span>
        </div>
      </template>
    </div>
  </article>
</template>

<style scoped>
.inspiration-card { display: flex; flex-direction: column; gap: 9px; min-width: 0; padding: 14px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); box-shadow: var(--shadow-sm); break-inside: avoid; margin-bottom: 14px; transition: transform .2s ease, box-shadow .2s ease; }
.inspiration-card:hover { transform: translateY(-3px); box-shadow: var(--shadow-md); }
.inspiration-card.is-muted { opacity: .65; }
.card-topline, .card-meta { display: flex; align-items: center; gap: 8px; color: var(--color-text-secondary); font-size: 12px; }
.card-topline { justify-content: space-between; }
.card-title { margin: 0; color: var(--color-text); font-family: Georgia, 'Songti SC', serif; font-size: 16px; line-height: 1.5; font-weight: 600; overflow-wrap: anywhere; }
.card-meta span:nth-child(2) { min-width: 0; overflow-wrap: anywhere; }
.card-status { margin-left: auto; white-space: nowrap; }
.card-dot { width: 6px; height: 6px; flex: none; border-radius: 50%; background: var(--color-accent); }
.card-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.card-actions { padding-top: 4px; border-top: 1px solid var(--color-border); }
.card-link { color: var(--color-accent); overflow-wrap: anywhere; }
.card-detail { border-top: 1px solid var(--color-border); padding-top: 10px; color: var(--color-text-secondary); font-size: 12px; }
.card-source { display: flex; flex-wrap: wrap; gap: 6px; padding: 5px 0; overflow-wrap: anywhere; }
@media (prefers-reduced-motion: reduce) { .inspiration-card { transition: none; } }
@media (max-width: 560px) { .inspiration-card { padding: 14px; } }
</style>
