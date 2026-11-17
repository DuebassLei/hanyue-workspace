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
  <section class="novel-continue">
    <h2 class="block-title">续写</h2>
    <NButton type="primary" :disabled="busy" :loading="busy" @click="$emit('continue')">续写下一章</NButton>
    <ul v-if="warnings.length" class="warn-list">
      <li v-for="(item, index) in warnings" :key="`${item.code}-${index}`">
        {{ item.name || '未命名' }}（{{ item.code }}）
      </li>
    </ul>
    <div v-if="impactLines.length" class="impact">
      <h3>这些章可能过时</h3>
      <ul>
        <li v-for="(line, index) in impactLines" :key="`${line}-${index}`">{{ line }}</li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.novel-continue { min-width: 0; }
.block-title { margin: 0 0 10px; font-size: 15px; }
.warn-list, .impact ul { margin: 10px 0 0; padding-left: 18px; color: var(--paper-warning); font-size: 13px; }
.impact h3 { margin: 12px 0 0; font-size: 13px; }
</style>
