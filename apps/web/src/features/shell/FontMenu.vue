<script setup>
import { NPopover } from 'naive-ui'
import { FONT_SIZES } from '../../ui/theme'

defineProps({
  value: { type: String, default: 'md' },
})

const emit = defineEmits(['select'])
</script>

<template>
  <n-popover trigger="click" placement="bottom-end" :show-arrow="false">
    <template #trigger>
      <button type="button" class="tool-btn" aria-label="字号">
        <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
          <path d="M4 15 8.2 5h1.1L13.5 15M5.4 11.2h6.2" stroke-linecap="round" stroke-linejoin="round" />
          <path d="M13.2 9.2 15.4 5h.7L18.4 11" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </template>
    <div class="font-menu" role="listbox" aria-label="字号">
      <button
        v-for="size in FONT_SIZES"
        :key="size.key"
        type="button"
        class="font-option"
        role="option"
        :aria-selected="size.key === value"
        @click="emit('select', size.key)"
      >
        <span>{{ size.label }}</span>
        <span class="font-px">{{ size.px }}</span>
      </button>
    </div>
  </n-popover>
</template>

<style scoped>
.tool-btn {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.tool-btn:hover {
  background: var(--color-surface-raised);
  color: var(--color-text-primary);
}

.font-menu {
  display: flex;
  flex-direction: column;
  min-width: 132px;
  margin: -4px;
}

.font-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  height: 32px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.font-option:hover,
.font-option[aria-selected='true'] {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.font-px {
  font-size: 12px;
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}
</style>
