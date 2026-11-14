<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NDropdown } from 'naive-ui'

const props = defineProps({
  tabs: { type: Array, required: true },
  activeKey: { type: String, default: '' },
})

const emit = defineEmits(['open', 'close', 'close-others', 'close-all'])

const scroller = ref(null)
// 渐隐只在真能往那边滚时才显示，否则会一直盖住第一个标签
const fade = ref({ left: false, right: false })
let observer = null

// 右键 / 更多按钮共用同一个菜单，动作针对 menuTarget 那个标签
const menu = ref({ show: false, x: 0, y: 0 })
const menuTarget = ref(null)

const hasClosable = computed(() => props.tabs.some((tab) => !tab.pinned))

const menuOptions = computed(() => {
  const target = menuTarget.value
  return [
    { label: '关闭', key: 'close', disabled: !target || target.pinned },
    {
      label: '关闭其他',
      key: 'close-others',
      disabled: !target || props.tabs.filter((tab) => !tab.pinned).length <= 1,
    },
    { label: '关闭全部', key: 'close-all', disabled: !hasClosable.value },
  ]
})

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

function openMenuAt(x, y, tab) {
  menuTarget.value = tab
  menu.value = { show: true, x, y }
}

function onContextMenu(event, tab) {
  event.preventDefault()
  openMenuAt(event.clientX, event.clientY, tab)
}

// 「更多」按钮没有具体目标时就当作关闭全部，另外两项按激活标签算
function onMoreClick(event) {
  const rect = event.currentTarget.getBoundingClientRect()
  const active = props.tabs.find((tab) => tab.key === props.activeKey) || null
  openMenuAt(rect.left, rect.bottom + 4, active)
}

function onMenuSelect(key) {
  menu.value.show = false
  const target = menuTarget.value
  if (key === 'close') {
    if (target && !target.pinned) emit('close', target.key)
  } else if (key === 'close-others') {
    if (target) emit('close-others', target.key)
  } else if (key === 'close-all') {
    emit('close-all')
  }
}
</script>

<template>
  <div class="page-tabs-wrap">
    <div class="page-tabs-scroll">
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
          @contextmenu="onContextMenu($event, tab)"
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

    <button
      v-if="hasClosable"
      type="button"
      class="page-tabs-more"
      title="标签操作：关闭其他 / 关闭全部"
      aria-label="标签操作"
      @click="onMoreClick"
    >
      <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true">
        <circle cx="3" cy="8" r="1.3" />
        <circle cx="8" cy="8" r="1.3" />
        <circle cx="13" cy="8" r="1.3" />
      </svg>
    </button>

    <n-dropdown
      trigger="manual"
      placement="bottom-end"
      :show="menu.show"
      :x="menu.x"
      :y="menu.y"
      :options="menuOptions"
      @select="onMenuSelect"
      @clickoutside="menu.show = false"
    />
  </div>
</template>

<style scoped>
.page-tabs-wrap {
  position: relative;
  display: flex;
  align-items: stretch;
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
}

.page-tabs-scroll {
  position: relative;
  flex: 1 1 auto;
  min-width: 0;
}

/* 两侧渐隐，提示「后面还有」，比一条硬滚动条干净 */
.page-tabs-scroll::before,
.page-tabs-scroll::after {
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

.page-tabs-scroll::before {
  left: 0;
  background: linear-gradient(to right, var(--color-surface), transparent);
}

.page-tabs-scroll::after {
  right: 0;
  background: linear-gradient(to left, var(--color-surface), transparent);
}

.page-tabs-scroll:has(.page-tabs.fade-left)::before,
.page-tabs-scroll:has(.page-tabs.fade-right)::after {
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

/* 常驻入口，屏幕阅读器和鼠标都能到；右键菜单是同一个菜单 */
.page-tabs-more {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  margin-right: 8px;
  border: 0;
  border-left: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text-muted);
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.page-tabs-more:hover {
  color: var(--color-text-primary);
  background: var(--color-surface-raised);
}
</style>
