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
  <section class="novel-compare">
    <h2 class="block-title">对照</h2>
    <p v-if="!chapter" class="muted">选中章节后对照原文和改写。</p>
    <div v-else class="compare-grid">
      <div class="compare-col">
        <h3>原文</h3>
        <p v-if="chapter.source === 'continuation' || !chapter.sourceText" class="muted">无原文</p>
        <pre v-else class="plain">{{ chapter.sourceText }}</pre>
      </div>
      <div class="compare-col">
        <h3>改写 <span class="tag">{{ right.label }}</span></h3>
        <p v-if="!right.text" class="muted">还没有正文</p>
        <pre v-else class="plain">{{ right.text }}</pre>
      </div>
    </div>
  </section>
</template>

<style scoped>
.novel-compare { min-width: 0; }
.block-title { margin: 0 0 10px; font-size: 15px; }
.compare-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; min-width: 0; }
.compare-col { min-width: 0; }
.compare-col h3 { margin: 0 0 6px; font-size: 13px; color: #5c564e; }
.tag { font-weight: 500; color: #9a6230; }
.plain { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.6 inherit; background: #f7f4ee; padding: 10px; max-height: 320px; overflow: auto; }
.muted { margin: 0; color: #827a71; font-size: 12px; }
@media (max-width: 800px) {
  .compare-grid { grid-template-columns: 1fr; }
}
</style>
