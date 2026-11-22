<script setup>
import { computed } from 'vue'
import { NModal } from 'naive-ui'
import HotPlatformTabs from './HotPlatformTabs.vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  summary: { type: Object, default: () => ({}) },
  degraded: { type: Array, default: () => [] },
  platform: { type: String, default: '' },
  platforms: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['update:show', 'update:platform'])

const funnel = computed(() => {
  const s = props.summary || {}
  const items = s.items || 0
  const clusters = s.clusters || 0
  const recommended = s.recommended || 0
  const generated = s.generated || 0
  const base = items || 1
  return [
    { key: 'items', label: '素材', value: items, hint: '各平台榜单原文', width: 100 },
    {
      key: 'clusters',
      label: '事件',
      value: clusters,
      hint: '同一件事合并成一条',
      width: Math.max(18, (clusters / base) * 100),
    },
    {
      key: 'recommended',
      label: '值得写',
      value: recommended,
      hint: '分数过线、今天可以动手',
      width: Math.max(18, (recommended / base) * 100),
      accent: true,
    },
    {
      key: 'generated',
      label: '已出稿',
      value: generated,
      hint: '已创建稿件，可在文章管理查看',
      width: Math.max(18, (generated / base) * 100),
    },
  ]
})

const tracks = computed(() => {
  const entries = Object.entries(props.summary.tracks || {}).sort((a, b) => b[1] - a[1])
  const max = entries.reduce((m, [, n]) => Math.max(m, n), 1)
  return entries.map(([name, count]) => ({
    name,
    count,
    width: Math.max(6, (count / max) * 100),
  }))
})

/** 采集源不再由服务端登记：这里只回答「当天有哪些平台入库了」。 */
const sourceNote = computed(() => {
  const names = Object.keys(props.summary.platforms || {})
  if (!names.length) return '今天还没有平台入库'
  return `${names.length} 个平台已入库：${names.join('、')}`
})

const captured = computed(() => props.summary.capturedAt || '')

function close() {
  emit('update:show', false)
}
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    title="今日概览"
    :bordered="false"
    :segmented="{ content: true, footer: 'soft' }"
    style="width: min(640px, calc(100vw - 32px))"
    @update:show="emit('update:show', $event)"
  >
    <p class="overview-lead">
      素材收进来、合成事件、再决定写哪一条。数字是今天这一轮采集的结果，不是筛选后的列表。
    </p>

    <section class="overview-block" aria-label="采集漏斗">
      <div class="overview-kicker">
        <span>从素材到成稿</span>
        <span v-if="captured" class="overview-muted">最近采集 {{ captured }}</span>
      </div>
      <ol class="funnel">
        <li v-for="step in funnel" :key="step.key" class="funnel-row">
          <span class="funnel-label">{{ step.label }}</span>
          <span class="funnel-track">
            <span
              class="funnel-fill"
              :class="{ 'is-accent': step.accent }"
              :style="{ width: step.width + '%' }"
            />
          </span>
          <span class="funnel-value">{{ step.value }}</span>
          <span class="funnel-hint">{{ step.hint }}</span>
        </li>
      </ol>
    </section>

    <section class="overview-block" aria-label="赛道分布">
      <div class="overview-kicker">
        <span>赛道</span>
        <span class="overview-muted">{{ tracks.length ? `${tracks.length} 个赛道` : '还没有赛道' }}</span>
      </div>
      <p v-if="!tracks.length" class="overview-empty">这一轮还没归出赛道。先刷新并评分。</p>
      <ul v-else class="tracks">
        <li v-for="track in tracks" :key="track.name" class="track-row">
          <span class="track-name">{{ track.name }}</span>
          <span class="track-track">
            <span class="track-fill" :style="{ width: track.width + '%' }" />
          </span>
          <span class="track-count">{{ track.count }}</span>
        </li>
      </ul>
    </section>

    <section class="overview-block" aria-label="按平台筛选">
      <div class="overview-kicker">
        <span>只看一个平台</span>
        <span class="overview-muted">数字是当天事件数，再点一次回到全部</span>
      </div>
      <HotPlatformTabs
        :platforms="platforms"
        :model-value="platform"
        :total="summary.clusters || 0"
        :loading="loading"
        @update:model-value="emit('update:platform', $event)"
      />
    </section>

    <p class="overview-foot">
      {{ sourceNote }}
      <template v-if="degraded.length">
        · {{ degraded.length }} 个维度数据不足
        <span class="overview-muted">（{{ degraded.map((item) => item.reason).filter(Boolean).join('；') || '悬停分数条看原因' }}）</span>
      </template>
    </p>

    <template #footer>
      <div class="overview-actions">
        <span v-if="platform" class="overview-muted">当前只看「{{ platform }}」</span>
        <span v-else class="overview-muted">当前看全部平台</span>
        <button type="button" class="overview-done" @click="close">看列表</button>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.overview-lead {
  margin: 0 0 var(--space-5);
  color: var(--color-text-secondary);
  font-size: var(--font-sm);
  line-height: 1.6;
}

.overview-block + .overview-block {
  margin-top: var(--space-5);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}

.overview-kicker {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
  font-size: var(--font-xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--color-text-secondary);
}

.overview-muted {
  font-weight: 400;
  letter-spacing: 0;
  color: var(--color-text-muted);
}

.funnel,
.tracks {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.funnel-row,
.track-row {
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr) 36px;
  align-items: center;
  gap: 10px;
}

.funnel-row {
  grid-template-columns: 52px minmax(0, 1fr) 36px minmax(0, 140px);
}

.funnel-label,
.track-name {
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}

.funnel-track,
.track-track {
  display: block;
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--color-surface-raised);
}

.funnel-fill,
.track-fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: color-mix(in srgb, var(--color-text-primary) 28%, transparent);
}

.funnel-fill.is-accent,
.track-fill {
  background: var(--color-accent);
}

.funnel-value,
.track-count {
  font-size: var(--font-sm);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  text-align: right;
  color: var(--color-text-primary);
}

.funnel-hint {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.overview-empty,
.overview-foot {
  margin: 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  line-height: 1.6;
}

.overview-foot {
  margin-top: var(--space-5);
}

.overview-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.overview-done {
  height: 34px;
  padding: 0 14px;
  border: 0;
  border-radius: var(--radius-md);
  background: var(--color-accent);
  color: var(--color-on-accent);
  font: inherit;
  font-size: var(--font-sm);
  font-weight: 600;
  cursor: pointer;
}

.overview-done:hover {
  background: var(--color-accent-hover);
}

.overview-done:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}

@media (max-width: 560px) {
  .funnel-row {
    grid-template-columns: 52px minmax(0, 1fr) 36px;
  }
  .funnel-hint {
    display: none;
  }
}
</style>
