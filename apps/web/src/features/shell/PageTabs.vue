<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  tabs: { type: Array, required: true },
  activeKey: { type: String, default: '' },
})

const emit = defineEmits(['open', 'close'])

const scroller = ref(null)
// 渐隐只在真能往那边滚时才显示，否则会一直盖住第一个标签
const fade = ref({ left: false, right: false })
let observer = null

function measureFade() {
  const el = scroller.value
  if (!el) return
  fade.value = {
    left: el.scrollLeft > 1,
    right: el.scrollLeft + el.clientWidth < el.scrollWidth - 1,
  }
}

// 标签一多就会被挤到可视区外，激活项要自己滚进来，否则切过去像没反应
watch(
  () => props.activeKey,
  async (key) => {
    await nextTick()
    const el = scroller.value?.querySelector(`[data-key="${CSS.escape(key)}"]`)
    el?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    measureFade()
  },
  { immediate: true },
)

onMounted(() => {
  measureFade()
  observer = new ResizeObserver(measureFade)
  observer.observe(scroller.value)
})

onBeforeUnmount(() => observer?.disconnect())

// 滚动条平时是隐的，竖滚轮在标签条上转成横滚，超出部分才够得着
function onWheel(event) {
  const el = scroller.value
  if (!el || el.scrollWidth <= el.clientWidth) return
  if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return
  event.preventDefault()
  el.scrollLeft += event.deltaY
}
</script>

<template>
  <div class="page-tabs-wrap">
    <div
      ref="scroller"
      class="page-tabs"
      :class="{ 'fade-left': fade.left, 'fade-right': fade.right }"
      role="tablist"
      aria-label="已打开页面"
      @scroll.passive="measureFade"
      @wheel="onWheel"
    >
      <div
        v-for="tab in tabs"
        :key="tab.key"
        :data-key="tab.key"
        class="page-tab"
        :class="{ 'is-active': tab.key === activeKey }"
        role="tab"
        :aria-selected="tab.key === activeKey"
      >
        <button
          type="button"
          class="page-tab-label"
          :title="tab.title"
          @click="emit('open', tab)"
        >
          {{ tab.title }}
        </button>
        <button
          v-if="!tab.pinned"
          type="button"
          class="page-tab-close"
          :aria-label="`关闭${tab.title}`"
          @click.stop="emit('close', tab.key)"
        >
          ×
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-tabs-wrap {
  position: relative;
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
}

/* 两侧渐隐，提示「后面还有」，比一条硬滚动条干净 */
.page-tabs-wrap::before,
.page-tabs-wrap::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 1px;
  width: 24px;
  pointer-events: none;
  z-index: 1;
  opacity: 0;
  transition: opacity 0.15s ease;
}

.page-tabs-wrap::before {
  left: 0;
  background: linear-gradient(to right, var(--color-surface), transparent);
}

.page-tabs-wrap::after {
  right: 0;
  background: linear-gradient(to left, var(--color-surface), transparent);
}

.page-tabs-wrap:has(.page-tabs.fade-left)::before,
.page-tabs-wrap:has(.page-tabs.fade-right)::after {
  opacity: 1;
}

.page-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 40px;
  padding: 0 16px;
  overflow-x: auto;
  overflow-y: hidden;
  /* 滚动条平时透明，鼠标移到标签条上才浮出来，免得长期挂一条灰线 */
  scrollbar-width: thin;
  scrollbar-color: transparent transparent;
}

.page-tabs:hover {
  scrollbar-color: var(--color-border-strong) transparent;
}

.page-tabs::-webkit-scrollbar {
  height: 4px;
}

.page-tabs::-webkit-scrollbar-track {
  background: transparent;
}

.page-tabs::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: transparent;
}

.page-tabs:hover::-webkit-scrollbar-thumb {
  background: var(--color-border-strong);
}

.page-tabs::-webkit-scrollbar-thumb:hover {
  background: var(--color-text-muted);
}

.page-tab {
  display: flex;
  align-items: center;
  max-width: 160px;
  height: 28px;
  border-radius: 999px;
  color: var(--color-text-muted);
  background: transparent;
  flex: 0 0 auto;
  position: relative;
}

.page-tab:hover {
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
}

.page-tab.is-active {
  color: var(--color-accent);
  background: var(--color-accent-soft);
}

.page-tab-label,
.page-tab-close {
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.page-tab-label {
  min-width: 0;
  padding: 0 8px 0 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.page-tab-close {
  width: 20px;
  height: 20px;
  margin-right: 4px;
  border-radius: 999px;
  line-height: 1;
  font-size: 14px;
  opacity: 0;
  transition: opacity 0.12s ease;
}

.page-tab:hover .page-tab-close,
.page-tab.is-active .page-tab-close {
  opacity: 0.7;
}

.page-tab-close:hover {
  background: var(--color-surface-raised);
  color: var(--color-text-primary);
  opacity: 1;
}
</style>
