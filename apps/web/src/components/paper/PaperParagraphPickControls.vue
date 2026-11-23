<script setup>
defineProps({
  active: Boolean,
  count: { type: Number, required: true },
  disabled: Boolean,
})
defineEmits(['start', 'clear', 'finish'])
</script>

<template>
  <div class="paragraph-pick-controls">
    <span v-if="active">在稿纸上点选段落，Shift 可连选</span>
    <span aria-live="polite">已选 {{ count }} 段</span>
    <button v-if="count" type="button" :disabled="disabled" @click="$emit('clear')">清空</button>
    <button v-if="active" type="button" @click="$emit('finish')">完成勾选</button>
    <button v-else type="button" :disabled="disabled" @click="$emit('start')">去稿纸勾选</button>
  </div>
</template>

<style scoped>
.paragraph-pick-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  font-size: 12px;
  color: var(--color-text-secondary);
}
button {
  padding: 0;
  border: 0;
  background: none;
  color: var(--paper-accent);
  font: inherit;
  cursor: pointer;
}
button:disabled {
  color: var(--color-text-tertiary);
  cursor: not-allowed;
}
</style>
