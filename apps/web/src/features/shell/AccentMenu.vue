<script setup>
import { NPopover } from 'naive-ui'
import { ACCENTS } from '../../ui/theme'

defineProps({
  value: { type: String, default: 'green' },
})

const emit = defineEmits(['select'])

const swatch = {
  green: '#18a058',
  blue: '#2080f0',
  purple: '#7c3aed',
  cyan: '#0891b2',
  orange: '#f0a020',
  red: '#d03050',
}
</script>

<template>
  <n-popover trigger="click" placement="bottom-end" :show-arrow="false">
    <template #trigger>
      <button type="button" class="tool-btn" aria-label="主题色">
        <span class="swatch" :style="{ background: swatch[value] || swatch.green }" />
      </button>
    </template>
    <div class="accent-menu" role="listbox" aria-label="主题色">
      <button
        v-for="item in ACCENTS"
        :key="item.key"
        type="button"
        class="accent-option"
        role="option"
        :aria-selected="item.key === value"
        :aria-label="item.label"
        :title="item.label"
        @click="emit('select', item.key)"
      >
        <span class="swatch" :style="{ background: swatch[item.key] }" />
        <span v-if="item.key === value" class="accent-check" aria-hidden="true">✓</span>
      </button>
    </div>
  </n-popover>
</template>

<style scoped>
.tool-btn,
.accent-option {
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.tool-btn {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: 8px;
}

.tool-btn:hover {
  background: var(--color-surface-raised);
}

.accent-menu {
  display: flex;
  gap: 6px;
}

.accent-option {
  position: relative;
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 6px;
}

.accent-option[aria-selected='true'] {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.swatch {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--color-text-primary) 12%, transparent);
}

.accent-check {
  position: absolute;
  right: 1px;
  bottom: -1px;
  font-size: 10px;
  color: var(--color-text-primary);
}
</style>
