<script setup>
import { computed } from 'vue'
import { NButton, NSpace } from 'naive-ui'
import HotScoreBars from './HotScoreBars.vue'

const props = defineProps({
  cluster: { type: Object, required: true },
  detail: { type: Object, default: null },
  degraded: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  detailLoading: { type: Boolean, default: false },
  expanded: { type: Boolean, default: false },
  // 空串=有权限。透传而不是在卡片里读 store：卡片只负责展示，
  // 权限判断留在页面，卡片保持可复用（弹窗里也会用）。
  writeDenied: { type: String, default: '' },
  stickerDenied: { type: String, default: '' },
})

const emit = defineEmits(['write', 'ignore', 'toggle', 'sticker'])

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
          <span v-for="flag in cluster.riskFlags || []" :key="flag" class="tag tag-danger">{{ flag }}</span>
        </div>

        <!-- 平台 | 赛道·名次 | 分数条 | 操作 挤成一行；放不下时分数条整组换行，
             而不是把五格条压成窄条（见 .hot-facts 的 flex-basis 说明） -->
        <div class="hot-facts">
          <div class="hot-chips">
            <span v-for="platform in cluster.platforms || []" :key="platform" class="tag">{{ platform }}</span>
          </div>

          <div class="hot-meta">
            <span>{{ cluster.track || '未判赛道' }}</span>
            <span v-if="firstSeen">· 首见 {{ firstSeen }}</span>
            <span>· 连续 {{ cluster.daysSeen || 1 }} 天</span>
            <span>· 最高 {{ bestRank }}</span>
            <span>· {{ cluster.itemCount || 0 }} 条原文</span>
          </div>

          <HotScoreBars
            :detail="cluster.scoreDetail || {}"
            :degraded="degraded"
            variant="compact"
            class="hot-bars"
          />

          <n-space class="hot-actions" :size="8" align="center">
            <n-button
              type="primary"
              size="small"
              :loading="busy"
              :disabled="cluster.status === 'ignored' || Boolean(writeDenied)"
              :title="
                writeDenied ||
                (cluster.status === 'ignored' ? '已忽略，先恢复再写' : '基于这条热点新建稿件')
              "
              @click="emit('write', cluster)"
            >
              {{ cluster.status === 'generated' ? '再写一篇' : '写这篇' }}
            </n-button>
            <n-button secondary size="small" :disabled="busy" @click="emit('ignore', cluster)">
              {{ cluster.status === 'ignored' ? '恢复' : '忽略' }}
            </n-button>
            <n-button secondary size="small" :loading="detailLoading" @click="emit('toggle', cluster)">
              {{ expanded ? '收起原文' : '展开原文' }}
            </n-button>
            <n-button
              secondary
              size="small"
              :disabled="Boolean(stickerDenied)"
              :title="stickerDenied || '基于这条热点做一组贴图'"
              @click="emit('sticker', cluster)"
            >
              做贴图
            </n-button>
            <a
              v-if="cluster.contentItemId"
              class="hot-link"
              :href="`/studio?id=${cluster.contentItemId}`"
            >打开已生成的稿件 →</a>
          </n-space>
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
/* 列表卡片是扫读用的：分数、标题、平台、证据挤在紧凑的两三行里，
   一屏能看十来条；完整证据靠悬浮提示和「展开原文」。 */
.hot-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  padding: var(--space-2) var(--space-3);
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
  justify-content: center;
  align-self: stretch;
  min-width: 48px;
  line-height: 1.1;
}
.hot-score b {
  font-size: 22px;
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
  gap: 4px;
}
.hot-title-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.hot-title {
  margin: 0;
  font-size: var(--font-body);
  font-weight: 600;
  line-height: 1.35;
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
  white-space: nowrap;
}
/* 没判定赛道的条目会显示「未判赛道」再跟「· 首见」，开头那个「·」要藏掉 */
.hot-meta > span:first-child + span::before {
  content: none;
}
/* 操作按钮是卡片里最高的元素，砍掉 4px 每条省一行高度。
   触控目标靠 ::after 撑到 40px：视觉变小，命中区域不变。 */
.hot-actions .btn-sm {
  position: relative;
  height: 28px;
  padding: 0;
  background: none;
  border-color: transparent;
  color: var(--color-text-secondary);
}
.hot-actions .btn-sm::after {
  content: '';
  position: absolute;
  inset: -6px -4px;
}
.hot-actions .btn-sm:hover:not(:disabled) {
  color: var(--color-text-primary);
  text-decoration: underline;
  text-underline-offset: 3px;
}
/* 「写这篇」是主操作，保持实心按钮 */
.hot-actions .btn-sm:first-child {
  padding: 0 var(--space-3);
  background: var(--color-accent);
  border-color: transparent;
  color: var(--color-on-accent);
}
.hot-actions .btn-sm:first-child:hover:not(:disabled) {
  color: var(--color-on-accent);
  text-decoration: none;
}
.hot-actions {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  margin-left: auto;
}
/* 事实行：平台 | 赛道·名次 | 分数条 | 操作。
   每一项都是 flex-basis:auto，放不下时 flex-basis:0 的分数条先让位，
   整组换到第二行，而不是把五格条压成窄条。 */
.hot-facts {
  display: flex;
  align-items: center;
  gap: var(--space-2) var(--space-3);
  flex-wrap: wrap;
  min-width: 0;
}
.hot-facts > * {
  flex: 0 1 auto;
  min-width: 0;
}
@media (max-width: 900px) {
  .hot-meta {
    display: none;
  }
}
@media (max-width: 560px) {
  /* 窄屏：平台一行、五个分数一行、操作一行；分数条独占整行才放得下。
     顺序要按 DOM 顺序写（平台/分数/操作），否则 flex 换行后的位置和读到的不一致。 */
  .hot-facts .hot-bars {
    flex-basis: 100%;
    order: 2;
  }
  .hot-facts .hot-actions {
    flex-basis: 100%;
    order: 3;
  }
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
