<script setup>
import { computed, ref, watch } from 'vue'
import JobRow from './JobRow.vue'

const props = defineProps({
  jobs: { type: Array, required: true },
  accountLabel: { type: Function, required: true },
  deletingJobId: { type: String, default: '' },
})
defineEmits(['record', 'retry', 'cancel', 'delete', 'open'])

const collapsed = ref({})
const terminal = new Set(['succeeded', 'cancelled'])

watch(() => props.jobs, (items) => {
  const grouped = new Map()
  for (const job of items) {
    if (!job.contentItemId) continue
    if (!grouped.has(job.contentItemId)) grouped.set(job.contentItemId, [])
    grouped.get(job.contentItemId).push(job)
  }
  for (const [contentId, jobs] of grouped) {
    if (jobs.length > 1 && !(contentId in collapsed.value)) {
      collapsed.value[contentId] = jobs.every((job) => terminal.has(job.status))
    }
  }
}, { immediate: true })

const groups = computed(() => {
  const byContent = new Map()
  const singles = []
  for (const job of props.jobs) {
    if (!job.contentItemId) {
      singles.push({ type: 'single', key: job.id, jobs: [job] })
      continue
    }
    if (!byContent.has(job.contentItemId)) byContent.set(job.contentItemId, [])
    byContent.get(job.contentItemId).push(job)
  }
  const grouped = [...byContent.entries()].map(([contentId, items]) => {
    items.sort((a, b) => (a.scheduledAt || a.createdAt || '').localeCompare(b.scheduledAt || b.createdAt || ''))
    if (items.length === 1) return { type: 'single', key: items[0].id, jobs: items }
    return { type: 'group', key: contentId, contentId, title: items[0].contentTitle || '未命名稿件', jobs: items }
  })
  return [...grouped, ...singles]
})
</script>

<template>
  <div class="job-groups">
    <section v-for="group in groups" :key="group.key" class="job-group">
      <button v-if="group.type === 'group'" class="job-group-head" type="button" :aria-expanded="!collapsed[group.contentId]" @click="collapsed[group.contentId] = !collapsed[group.contentId]">
        <router-link :to="{ name: 'studio', query: { id: group.contentId } }" @click.stop>{{ group.title }}</router-link>
        <span>{{ group.jobs.length }} 个任务 · {{ group.jobs.filter((job) => ['failed', 'needs_attention'].includes(job.status)).length }} 需处理</span>
      </button>
      <div v-if="group.type === 'single' || !collapsed[group.contentId]" class="job-group-rows">
        <JobRow
          v-for="job in group.jobs"
          :key="job.id"
          :job="job"
          :account-label="accountLabel(job)"
          :deleting="deletingJobId === job.id"
          @open="$emit('open', job)"
          @record="$emit('record', job)"
          @retry="$emit('retry', job)"
          @cancel="$emit('cancel', job)"
          @delete="$emit('delete', job)"
        />
      </div>
    </section>
  </div>
</template>

<style scoped>
.job-groups { display:flex; flex-direction:column; }
.job-group { border-bottom:1px solid var(--color-border); }
.job-group-head { display:flex; width:100%; align-items:center; gap:12px; padding:12px 8px; border:0; background:var(--color-surface-raised); color:var(--color-text-primary); text-align:left; font:inherit; cursor:pointer; }
.job-group-head a { color:inherit; text-decoration:none; font-weight:600; }
.job-group-head span { color:var(--color-text-muted); font-size:var(--font-xs); }
.job-group-rows :deep(.list-row) { cursor:pointer; }
</style>
