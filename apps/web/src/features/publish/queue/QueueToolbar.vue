<script setup>
import { NInput, NSelect } from 'naive-ui'

// 发布队列筛选条：状态 Tab + 平台/账号下拉 + 搜索框。
// 全部受控：值由页面持有，这里只回抛 update: 事件（与仓库 props+emit 约定一致）。
defineProps({
  statusOptions: { type: Array, default: () => [
    { value: 'all', label: '全部' },
    { value: 'queued', label: '排队中' },
    { value: 'publishing', label: '发布中' },
    { value: 'succeeded', label: '成功' },
    { value: 'failed', label: '失败' },
    { value: 'cancelled', label: '已取消' },
  ] },
  platformOptions: { type: Array, required: true },
  accountOptions: { type: Array, required: true },
  status: { type: String, default: 'all' },
  platform: { type: String, default: 'all' },
  account: { type: String, default: 'all' },
  search: { type: String, default: '' },
  showStatus: { type: Boolean, default: true },
})
defineEmits(['update:status', 'update:platform', 'update:account', 'update:search'])
</script>

<template>
  <div class="queue-toolbar">
    <div v-if="showStatus" class="queue-status-tabs" role="tablist" aria-label="任务状态筛选">
      <button
        v-for="option in statusOptions"
        :key="option.value"
        type="button"
        :class="{ 'is-active': status === option.value }"
        @click="$emit('update:status', option.value)"
      >
        {{ option.label }}
      </button>
    </div>
    <div class="queue-filters">
      <n-select
        v-if="platformOptions.length > 2"
        :value="platform"
        :options="platformOptions"
        size="small"
        class="queue-select"
        aria-label="平台筛选"
        @update:value="$emit('update:platform', $event)"
      />
      <n-select
        :value="account"
        :options="accountOptions"
        size="small"
        class="queue-select"
        aria-label="账号筛选"
        @update:value="$emit('update:account', $event)"
      />
      <n-input
        :value="search"
        size="small"
        clearable
        class="queue-search"
        placeholder="搜索任务"
        aria-label="搜索任务"
        @update:value="$emit('update:search', $event || '')"
      />
    </div>
  </div>
</template>

<style scoped>
.queue-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border);
}

.queue-status-tabs,
.queue-filters {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.queue-status-tabs button {
  padding: 5px 9px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-muted);
  font: inherit;
  font-size: var(--font-xs);
  cursor: pointer;
}

.queue-status-tabs button.is-active {
  border-color: var(--color-border);
  background: var(--color-surface-raised);
  color: var(--color-text-primary);
  font-weight: 600;
}

.queue-select {
  width: 160px;
}

.queue-search {
  width: 180px;
}

@media (max-width: 900px) {
  .queue-toolbar {
    align-items: stretch;
    flex-direction: column;
  }
}

@media (max-width: 560px) {
  .queue-filters > * {
    flex: 1 1 120px;
  }

  .queue-search,
  .queue-select {
    width: auto;
  }
}
</style>
