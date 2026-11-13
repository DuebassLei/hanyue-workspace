<script setup>
import { computed, nextTick, ref } from 'vue'

const props = defineProps({
  // [{ name, count }]，按采集源顺序给出；count 为当天事件数
  platforms: { type: Array, default: () => [] },
  modelValue: { type: String, default: '' },
  total: { type: Number, default: 0 },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])

const root = ref(null)

const tabs = computed(() => [
  { name: '', label: '全部平台', count: props.total },
  ...props.platforms.map((item) => ({
    name: item.name,
    label: item.name,
    count: item.count || 0,
  })),
])

// 0 条的平台不让人切过去：切过去只会看到空列表，还以为页面坏了
function usable(tab) {
  return tab.name === '' || tab.count > 0
}

/** 切换 = 反选：点当前平台回到「全部」，不用再去找「全部」在哪。 */
function pick(tab) {
  if (!usable(tab)) return
  emit('update:modelValue', props.modelValue === tab.name ? '' : tab.name)
}

/** 左右方向键在可用的平台之间移动，Home/End 跳到首尾。 */
async function onKeydown(event) {
  const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End']
  if (!keys.includes(event.key)) return
  const usableNames = tabs.value.filter(usable).map((tab) => tab.name)
  if (!usableNames.length) return
  event.preventDefault()
  const current = usableNames.indexOf(props.modelValue)
  const index = current < 0 ? 0 : current
  let next = index
  if (event.key === 'ArrowLeft') next = Math.max(0, index - 1)
  if (event.key === 'ArrowRight') next = Math.min(usableNames.length - 1, index + 1)
  if (event.key === 'Home') next = 0
  if (event.key === 'End') next = usableNames.length - 1
  emit('update:modelValue', usableNames[next])
  await nextTick()
  const buttons = root.value?.querySelectorAll('button:not(:disabled)') || []
  buttons[next]?.focus()
}
</script>

<template>
  <div
    ref="root"
    class="platform-tabs"
    role="group"
    aria-label="按平台筛选热点"
    @keydown="onKeydown"
  >
    <button
      v-for="tab in tabs"
      :key="tab.name || 'all'"
      type="button"
      class="platform-tab"
      :class="{ 'is-active': modelValue === tab.name, 'is-empty': !usable(tab) }"
      :aria-pressed="modelValue === tab.name"
      :disabled="!usable(tab) || loading"
      :title="
        usable(tab)
          ? modelValue === tab.name
            ? '再次点击回到全部平台'
            : `只看 ${tab.label} 的事件`
          : `本次采集没有 ${tab.label} 的数据`
      "
      @click="pick(tab)"
    >
      {{ tab.label }}<span class="platform-count">{{ tab.count }}</span>
    </button>
  </div>
</template>

<style scoped>
.platform-tabs {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding: 2px 0 6px;
  /* 窄屏横向滚动时不被卡片圆角裁掉，同时隐藏滚动条占位 */
  scrollbar-width: thin;
  scroll-snap-type: x proximity;
}

.platform-tab {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 12px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface);
  color: var(--color-text-secondary);
  font: inherit;
  font-size: var(--font-xs);
  white-space: nowrap;
  cursor: pointer;
  scroll-snap-align: start;
  transition: border-color 120ms ease, background 120ms ease, color 120ms ease;
}

.platform-tab:hover:not(:disabled) {
  border-color: var(--color-border-strong);
  color: var(--color-text-primary);
}

.platform-tab.is-active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  color: var(--color-text-primary);
  font-weight: 600;
}

.platform-count {
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}

.platform-tab.is-active .platform-count {
  color: var(--color-text-secondary);
}

.platform-tab.is-empty {
  opacity: 0.45;
  cursor: not-allowed;
}

@media (max-width: 560px) {
  /* 触控目标不低于 44px，横向滑动切换 */
  .platform-tab {
    height: 44px;
  }
}
</style>
