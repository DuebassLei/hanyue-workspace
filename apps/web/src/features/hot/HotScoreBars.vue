<script setup>
import { computed } from 'vue'

const props = defineProps({
  detail: { type: Object, default: () => ({}) },
  degraded: { type: Array, default: () => [] },
  // 'stack'：五维各占一行（详情用）；'compact'：挤进一行（列表卡片用，省纵向空间）
  variant: { type: String, default: 'stack' },
})

const lack = computed(() => {
  const map = {}
  for (const item of props.degraded || []) {
    if (item && item.dim) map[item.dim] = item.reason || '数据不足'
  }
  return map
})

const num = (value) => (value == null ? 0 : Number(value) || 0)

const dims = computed(() => {
  const d = props.detail || {}
  return [
    {
      key: 'resonance',
      label: '共振',
      max: 30,
      value: num(d.resonance),
      tip: (d.platforms || []).join('、') || '平台信息缺失',
    },
    {
      key: 'momentum',
      label: '势能',
      max: 25,
      value: num(d.momentum),
      tip:
        `名次分 ${num(d.rankPoints)} + 升降 ${num(d.risePoints)} + 热度 ${num(d.hotPoints)}` +
        ((d.hotEvidence || []).length ? `（${d.hotEvidence.join('、')}）` : '') +
        (lack.value.hot ? '　· 热度跨平台不可比，已按同平台归一' : ''),
      // 升降缺失只是「低估」，名次分和热度分都是真算的，所以条本身保持实心，
      // 只在旁边加一句注记。整条打斜纹会让人以为这个分是编的。
      note: lack.value.rise ? '升降未算' : '',
    },
    {
      key: 'freshness',
      label: '时效',
      max: 20,
      value: num(d.freshness),
      tip:
        `首见 ${d.firstSeenAt || '—'} · 连续在榜 ${num(d.daysSeen)} 天` +
        (lack.value.fresh ? '　· 只有 1 轮采样，首见时间只能取采集时间，新鲜度会一致偏高' : ''),
      // 时效整维不可信：只有一轮采样时，所有条目首见时间都等于采集时间，
      // 每个簇都拿满新鲜度分。这不是「缺一个子项」，是这一维没有区分度。
      unknown: lack.value.fresh || '',
    },
    {
      key: 'track',
      label: '赛道',
      max: 15,
      value: num(d.trackPoints),
      tip: (d.track || '未判赛道') + ((d.trackHits || []).length ? `：${d.trackHits.join('、')}` : ''),
    },
    {
      key: 'writability',
      label: '可写',
      max: 10,
      value: num(d.writability),
      tip: (d.writabilityEvidence || []).join('、') || '没有可取的可写性证据',
    },
  ].map((dim) => ({
    ...dim,
    pct: Math.max(0, Math.min(100, (dim.value / dim.max) * 100)),
  }))
})
</script>

<template>
  <div class="score-bars" :class="{ 'is-compact': variant === 'compact' }">
    <div v-for="dim in dims" :key="dim.key" class="bar-row" :title="dim.tip">
      <span class="bar-label">{{ dim.label }}</span>
      <span class="bar-wrap" :class="{ 'is-unknown': dim.unknown }">
        <span class="bar-fill" :style="{ width: dim.pct + '%' }" />
      </span>
      <span class="bar-num">{{ dim.value }}</span>
      <span v-if="dim.note" class="bar-note">{{ dim.note }}</span>
    </div>
  </div>
</template>

<style scoped>
.score-bars {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.bar-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-xs);
  line-height: 1.5;
}
.bar-label {
  width: 30px;
  flex: 0 0 auto;
  color: var(--color-text-secondary);
}
.bar-wrap {
  display: block;
  width: 96px;
  height: 6px;
  flex: 0 0 auto;
  border-radius: 3px;
  overflow: hidden;
  background: color-mix(in srgb, var(--color-text-primary) 10%, transparent);
}
/* 数据不足的维度用斜纹：形状上就能看出「这条不是算出来的」 */
.bar-wrap.is-unknown {
  background: repeating-linear-gradient(
    135deg,
    color-mix(in srgb, var(--color-text-primary) 22%, transparent) 0 3px,
    transparent 3px 6px
  );
}
.bar-wrap.is-unknown .bar-fill {
  background: color-mix(in srgb, var(--color-text-primary) 22%, transparent);
}
.bar-fill {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--color-accent);
}
.bar-num {
  width: 28px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--color-text-secondary);
}
.bar-note {
  color: var(--color-text-muted);
  white-space: nowrap;
}

/* 紧凑版：五个维度排成一行，每格「标签 + 短条 + 分数」。
   证据仍靠悬浮提示，点「展开原文」能看到完整明细，信息一点没少。 */
/* 紧凑版可以压缩：事实行放得下就贴右，放不下才整组换行。
   内层行保持 nowrap，否则标签和条会被拆到两行。 */
.score-bars.is-compact {
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px var(--space-3);
  /* 按内容撑开宽度：父级用 :where() 掉进这条规则时（0 特异性），
     不至于被 flex-basis:0 压成一条窄带 */
  width: max-content;
  max-width: 100%;
}
.is-compact .bar-row {
  flex: 0 1 auto;
  gap: 4px;
  white-space: nowrap;
}
.is-compact .bar-label {
  width: auto;
}
.is-compact .bar-wrap {
  width: 40px;
  height: 5px;
}
.is-compact .bar-num {
  width: auto;
  min-width: 14px;
  text-align: left;
}
@media (max-width: 560px) {
  /* 窄屏放不下五格：条缩到最小，仍允许换行（奇数项奇怪时宁可折成两行） */
  .is-compact {
    gap: 2px var(--space-2);
    flex-wrap: wrap;
  }
  .is-compact .bar-wrap {
    width: 28px;
  }
}
</style>
