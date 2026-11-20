<script setup>
import { computed } from 'vue'

const props = defineProps({
  chapter: { type: Object, default: null },
})

const right = computed(() => {
  if (!props.chapter) return { text: '', label: '' }
  if (props.chapter.acceptedText) return { text: props.chapter.acceptedText, label: '已接受' }
  return { text: props.chapter.draftText || '', label: '未接受' }
})
</script>

<template>
  <div class="compare">
    <div class="col">
      <div class="col-head"><span>原文</span></div>
      <p v-if="!chapter || chapter.source === 'continuation' || !chapter.sourceText" class="empty">无原文</p>
      <pre v-else class="text">{{ chapter.sourceText }}</pre>
    </div>
    <div class="col">
      <div class="col-head">
        <span>改写</span>
        <em :class="{ 'is-accepted': chapter?.acceptedText }">{{ right.label }}</em>
      </div>
      <p v-if="!right.text" class="empty">还没有正文</p>
      <pre v-else class="text">{{ right.text }}</pre>
    </div>
  </div>
</template>

<style scoped>
.compare { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 0; border: 1px solid var(--paper-line-strong); border-radius: 8px; background: var(--paper-surface); overflow: hidden; }
.col { min-width: 0; padding: 14px 18px 18px; }
.col + .col { border-left: 1px solid var(--paper-line); background: var(--paper-accent-tint); }
.col-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px dashed var(--paper-line-strong); }
.col-head span { font-size: 11px; font-weight: 650; letter-spacing: 0.1em; color: var(--paper-ink-soft); }
.col-head em { font-size: 10.5px; font-style: normal; color: var(--paper-warning); }
.col-head em.is-accepted { color: var(--paper-success); }
.text {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif;
  font-size: 13.5px;
  line-height: 1.95;
  color: var(--paper-ink-soft);
  max-height: 460px;
  overflow: auto;
}
.empty { margin: 0; color: var(--paper-ink-muted); font-size: 12.5px; }

@media (max-width: 800px) {
  .compare { grid-template-columns: 1fr; }
  .col + .col { border-left: 0; border-top: 1px solid var(--paper-line); }
}
</style>
