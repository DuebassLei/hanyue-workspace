<script setup>
const props = defineProps({
  summary: { type: Object, required: true },
  modelValue: { type: String, default: 'all' },
})
defineEmits(['update:modelValue'])

const chips = [
  { id: 'all', label: '全部', statuses: [] },
  { id: 'pending', label: '待处理', statuses: ['pending_review', 'approved'] },
  { id: 'queued', label: '排期中', statuses: ['queued'] },
  { id: 'publishing', label: '进行中', statuses: ['publishing'] },
  { id: 'attention', label: '需处理', statuses: ['failed', 'needs_attention'] },
  { id: 'succeeded', label: '成功', statuses: ['succeeded'] },
  { id: 'cancelled', label: '已取消', statuses: ['cancelled'] },
]

function countFor(chip) {
  if (chip.id === 'all') return props.summary.total || 0
  return chip.statuses.reduce((total, status) => total + (props.summary.byStatus?.[status] || 0), 0)
}
</script>

<template>
  <div class="queue-chips" role="group" aria-label="按任务状态筛选">
    <button
      v-for="chip in chips"
      :key="chip.id"
      type="button"
      :aria-pressed="modelValue === chip.id"
      :class="{ 'is-active': modelValue === chip.id, 'is-warning': chip.id === 'attention' && countFor(chip) > 0 }"
      @click="$emit('update:modelValue', chip.id)"
    >
      {{ chip.label }}<span>{{ countFor(chip) }}</span>
    </button>
  </div>
</template>

<style scoped>
.queue-chips { display:flex; gap:6px; overflow-x:auto; padding:2px 0 8px; }
.queue-chips button { display:flex; flex:0 0 auto; align-items:center; gap:7px; padding:7px 10px; border:1px solid transparent; border-radius:var(--radius-sm); background:transparent; color:var(--color-text-secondary); font:inherit; font-size:var(--font-xs); cursor:pointer; }
.queue-chips button span { color:var(--color-text-muted); font-variant-numeric:tabular-nums; }
.queue-chips button.is-active { border-color:var(--color-border); background:var(--color-surface-raised); color:var(--color-text-primary); font-weight:600; }
.queue-chips button.is-warning { color:var(--color-danger); }
</style>
