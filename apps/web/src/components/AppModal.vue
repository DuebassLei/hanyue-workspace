<script setup>
import { onMounted, onUnmounted, ref } from 'vue'

const props = defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
})
const emit = defineEmits(['close'])
const panel = ref(null)

function onKeydown(event) {
  if (event.key === 'Escape') {
    emit('close')
    return
  }
  if (event.key !== 'Tab' || !panel.value) return
  const focusable = panel.value.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
  )
  if (!focusable.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
  const target = panel.value?.querySelector('input, textarea, select, button')
  target?.focus()
})
onUnmounted(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="modal-backdrop" role="presentation" @click.self="emit('close')">
    <div
      ref="panel"
      class="modal"
      role="dialog"
      aria-modal="true"
      :aria-label="title"
    >
      <div class="row" style="margin-bottom: var(--space-3)">
        <h2 class="modal-title" style="margin: 0">{{ title }}</h2>
        <div class="spacer" />
        <button class="icon-btn" aria-label="关闭" @click="emit('close')">✕</button>
      </div>
      <p v-if="description" class="page-sub" style="margin-bottom: var(--space-3)">
        {{ description }}
      </p>
      <slot />
    </div>
  </div>
</template>
