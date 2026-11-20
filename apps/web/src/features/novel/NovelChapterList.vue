<script setup>
defineProps({
  chapters: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
})

const emit = defineEmits(['select'])

const STATUS_LABEL = {
  pending: '待改写',
  drafted: '有草稿',
  accepted: '已接受',
}
</script>

<template>
  <section class="rail">
    <header class="rail-head">
      <span class="rail-label">章节</span>
      <span class="rail-count">{{ chapters.length }}</span>
    </header>

    <p v-if="!chapters.length" class="rail-empty">切章后会出现在这里</p>

    <ul v-else class="rail-items">
      <li v-for="chapter in chapters" :key="chapter.id">
        <button
          type="button"
          class="rail-row"
          :class="[`is-${chapter.status}`, { 'is-active': chapter.id === activeId }]"
          :aria-current="chapter.id === activeId ? 'true' : undefined"
          @click="emit('select', chapter.id)"
        >
          <span class="rail-order">{{ String((chapter.sortOrder ?? 0) + 1).padStart(2, '0') }}</span>
          <span class="rail-main">
            <strong>{{ chapter.title || '未命名' }}</strong>
            <span class="rail-meta">
              <i class="rail-dot" aria-hidden="true" />
              {{ STATUS_LABEL[chapter.status] || chapter.status }}
              <span v-if="chapter.source === 'continuation'" class="rail-tag">续写</span>
              <span v-if="chapter.stale" class="rail-tag is-warn">可能过时</span>
            </span>
          </span>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.rail { min-width: 0; display: flex; flex-direction: column; min-height: 0; border-right: 1px solid var(--paper-line); background: var(--paper-surface); }
.rail-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 13px 16px; background: var(--paper-header); border-bottom: 1px solid var(--paper-line); }
.rail-label { font-size: 12px; font-weight: 650; letter-spacing: 0.08em; color: var(--paper-ink-soft); }
.rail-count { min-width: 20px; padding: 1px 6px; border-radius: 999px; background: var(--paper-thumb); color: var(--paper-ink-muted); font-size: 11px; text-align: center; font-variant-numeric: tabular-nums; }
.rail-empty { margin: 0; padding: 22px 16px; color: var(--paper-ink-muted); font-size: 12.5px; line-height: 1.7; }

.rail-items { list-style: none; flex: 1; min-height: 0; margin: 0; padding: 6px 0 10px; overflow-y: auto; }
.rail-row { display: flex; align-items: flex-start; gap: 10px; width: 100%; padding: 9px 14px; border: 0; box-shadow: inset 2px 0 0 transparent; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.rail-row:hover { background: var(--paper-hover); }
.rail-row.is-active { background: var(--paper-accent-tint); box-shadow: inset 2px 0 0 var(--paper-accent); }
.rail-order { flex: 0 0 auto; padding-top: 1px; color: var(--paper-ink-muted); font-size: 10.5px; font-variant-numeric: tabular-nums; letter-spacing: 0.04em; }
.rail-main { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.rail-main strong { font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif; font-size: 13.5px; font-weight: 600; color: var(--paper-ink); line-height: 1.5; overflow-wrap: anywhere; }
.rail-row.is-active .rail-main strong { font-weight: 700; }
.rail-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; color: var(--paper-ink-muted); font-size: 11px; }
.rail-dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.rail-row.is-drafted .rail-meta { color: var(--paper-warning); }
.rail-row.is-accepted .rail-meta { color: var(--paper-success); }
.rail-row.is-pending .rail-meta { color: var(--paper-ink-muted); }
.rail-tag { padding: 0 4px; border: 1px solid currentColor; border-radius: 3px; font-size: 10px; line-height: 1.4; opacity: 0.85; }
.rail-tag.is-warn { color: var(--paper-warning); }

@media (max-width: 980px) {
  .rail { border-right: 0; border-bottom: 1px solid var(--paper-line); }
  .rail-items { display: flex; flex: none; gap: 4px; overflow-x: auto; overflow-y: hidden; padding: 8px 10px; }
  .rail-items li { flex: 0 0 auto; max-width: 190px; }
  .rail-row { width: auto; max-width: 190px; padding: 7px 10px; border: 1px solid var(--paper-line); border-radius: 6px; }
  .rail-row.is-active { border-color: var(--paper-accent-line); box-shadow: none; }
  .rail-order { display: none; }
}
</style>
