<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { NModal } from 'naive-ui'
import { NAV_ITEMS } from '../../ui/nav'
import { useRbacStore } from '../../stores/rbac'

const props = defineProps({
  show: { type: Boolean, default: false },
  tabs: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:show', 'go'])

const rbac = useRbacStore()
// 服务端菜单为准：没权限的页面不该出现在搜索里，否则搜到了点进去是 403
const navSource = computed(() => (rbac.menus.length ? rbac.navItems : NAV_ITEMS))
const query = ref('')
const composing = ref(false)
const active = ref(0)
const inputEl = ref(null)

const keyword = computed(() => query.value.trim().toLowerCase())

const navHits = computed(() =>
  navSource.value.filter((item) => {
    if (!keyword.value) return true
    return `${item.label} ${item.short}`.toLowerCase().includes(keyword.value)
  }).map((item) => ({
    id: `nav:${item.name}`,
    kind: '页面',
    title: item.label,
    hint: item.group,
    name: item.name,
    query: {},
  })),
)

const tabHits = computed(() =>
  props.tabs
    .filter((tab) => !tab.pinned)
    .filter((tab) => !keyword.value || tab.title.toLowerCase().includes(keyword.value))
    .map((tab) => ({
      id: `tab:${tab.key}`,
      kind: '已打开',
      title: tab.title,
      hint: '标签',
      name: tab.name,
      query: tab.query,
    })),
)

const results = computed(() => [...navHits.value, ...tabHits.value])

watch(
  () => props.show,
  async (open) => {
    if (!open) return
    query.value = ''
    active.value = 0
    await nextTick()
    inputEl.value?.focus()
  },
)

watch(results, (list) => {
  if (active.value >= list.length) active.value = 0
})

function close() {
  emit('update:show', false)
}

function pick(item) {
  if (!item) return
  emit('go', { name: item.name, query: item.query })
  close()
}

function onKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    close()
    return
  }
  if (event.isComposing || composing.value) return
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    active.value = results.value.length ? (active.value + 1) % results.value.length : 0
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    active.value = results.value.length
      ? (active.value - 1 + results.value.length) % results.value.length
      : 0
  } else if (event.key === 'Enter') {
    event.preventDefault()
    pick(results.value[active.value])
  }
}
</script>

<template>
  <n-modal :show="show" @update:show="emit('update:show', $event)">
    <div class="palette" role="dialog" aria-label="搜索菜单" @keydown="onKeydown">
      <input
        ref="inputEl"
        v-model="query"
        class="palette-input"
        type="text"
        placeholder="搜索页面或已打开标签"
        @compositionstart="composing = true"
        @compositionend="composing = false"
      />
      <ul v-if="results.length" class="palette-list" role="listbox">
        <li v-for="(item, index) in results" :key="item.id">
          <button
            type="button"
            class="palette-item"
            :class="{ 'is-active': index === active }"
            role="option"
            :aria-selected="index === active"
            @mouseenter="active = index"
            @click="pick(item)"
          >
            <span class="palette-kind">{{ item.kind }}</span>
            <span class="palette-title">{{ item.title }}</span>
            <span class="palette-hint">{{ item.hint }}</span>
          </button>
        </li>
      </ul>
      <p v-else class="palette-empty">没有匹配的页面</p>
    </div>
  </n-modal>
</template>

<style scoped>
.palette {
  width: min(520px, calc(100vw - 32px));
  background: var(--color-surface);
  color: var(--color-text-primary);
  border-radius: 10px;
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}

.palette-input {
  width: 100%;
  box-sizing: border-box;
  border: 0;
  border-bottom: 1px solid var(--color-border);
  background: transparent;
  color: inherit;
  font: inherit;
  padding: 14px 16px;
  outline: none;
}

.palette-list {
  list-style: none;
  margin: 0;
  padding: 6px;
  max-height: 320px;
  overflow: auto;
}

.palette-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  padding: 8px 10px;
  cursor: pointer;
}

.palette-item.is-active {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.palette-kind {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--color-text-muted);
}

.palette-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.palette-hint {
  margin-left: auto;
  font-size: 12px;
  color: var(--color-text-muted);
}

.palette-empty {
  margin: 0;
  padding: 16px;
  color: var(--color-text-muted);
  font-size: 13px;
}
</style>
