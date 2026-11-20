<script setup>
import { computed } from 'vue'
import { NButton } from 'naive-ui'

const props = defineProps({
  busy: { type: Boolean, default: false },
  warnings: { type: Array, default: () => [] },
  impactChapters: { type: Array, default: () => [] },
})

defineEmits(['continue'])

const impactLines = computed(() =>
  props.impactChapters.map((item) => (typeof item === 'string' ? item : item?.title || item?.id || '')).filter(Boolean),
)
</script>

<template>
  <section class="panel">
    <header class="panel-head">
      <span>续写</span>
      <em>接着最后一章往下写</em>
    </header>

    <div class="panel-body">
      <NButton type="primary" block :disabled="busy" :loading="busy" @click="$emit('continue')">续写下一章</NButton>
      <p class="hint">参考设定、人物状态与最近章节，结果落成新章节草稿。</p>

      <div v-if="warnings.length" class="block is-warn">
        <h4>需要确认</h4>
        <ul class="warn-list">
          <li v-for="(item, index) in warnings" :key="`${item.code}-${index}`">
            <span class="warn-name">{{ item.name || '未命名' }}</span>
            <span class="warn-code">{{ item.code }}</span>
          </li>
        </ul>
      </div>

      <div v-if="impactLines.length" class="block">
        <h4>这些章可能过时</h4>
        <ul class="chips">
          <li v-for="(line, index) in impactLines" :key="`${line}-${index}`">{{ line }}</li>
        </ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
.panel { min-width: 0; border: 1px solid var(--paper-line); border-radius: 8px; background: var(--paper-surface); overflow: hidden; }
.panel-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; padding: 10px 14px; background: var(--paper-header); border-bottom: 1px solid var(--paper-line); }
.panel-head span { font-size: 12px; font-weight: 650; letter-spacing: 0.08em; color: var(--paper-ink-soft); }
.panel-head em { font-style: normal; font-size: 10.5px; color: var(--paper-ink-muted); }
.panel-body { padding: 12px 14px 14px; }
.hint { margin: 8px 0 0; font-size: 11px; line-height: 1.7; color: var(--paper-ink-muted); }

.block { margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--paper-line-strong); }
.block h4 { margin: 0 0 6px; font-size: 11.5px; font-weight: 600; color: var(--paper-ink-soft); }
.block.is-warn h4 { color: var(--paper-warning); }
.warn-list { list-style: none; display: flex; flex-direction: column; gap: 4px; margin: 0; padding: 0; }
.warn-list li { display: flex; align-items: baseline; gap: 6px; font-size: 11.5px; }
.warn-name { color: var(--paper-ink); }
.warn-code { color: var(--paper-ink-muted); font-size: 10px; }
.chips { list-style: none; display: flex; flex-wrap: wrap; gap: 4px; margin: 0; padding: 0; }
.chips li { padding: 1px 7px; border: 1px solid var(--paper-warning-line); border-radius: 999px; background: var(--paper-warning-soft); color: var(--paper-warning); font-size: 10.5px; }
</style>
