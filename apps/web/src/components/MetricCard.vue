<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: '—' },
  hint: { type: String, default: '' },
  status: { type: String, default: '' },
})

const statusText = computed(() => {
  if (props.status === 'unavailable') return '不可获取'
  if (props.status === 'pending') return '待同步'
  return ''
})
</script>

<template>
  <div class="metric">
    <span class="metric-label">{{ label }}</span>
    <span class="metric-value">
      {{ statusText || value }}
    </span>
    <span v-if="hint" class="metric-hint">{{ hint }}</span>
  </div>
</template>
