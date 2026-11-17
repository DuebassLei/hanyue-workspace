<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { NButton, NEmpty, NInput } from 'naive-ui'
import { api } from '../../api/client'
import { toast } from '../../ui/feedback'
import { useLayoutComponents } from '../../composables/useLayoutComponents'

/**
 * 小组件库：组件清单来自后端注册表（GET /wechat-mp/components），
 * 新增围栏类型后端落一条注册表记录即可在此出现。
 * 每个组件的示例正文用当前选中主题实时渲染（服务端同一渲染函数）。
 */
const props = defineProps({
  store: { type: Object, required: true },
})

const { components, load: loadComponents } = useLayoutComponents()
const themeId = ref('')
const searchQuery = ref('')
const htmlMap = ref({})
const busy = ref(false)
const copyId = ref('')
let renderToken = 0

const themeOptions = computed(() =>
  props.store.visiblePresets.value
    .concat(props.store.themes.value.custom)
    .map((t) => ({ value: t.id, label: t.name })),
)

const filteredComponents = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase()
  if (!query) return components.value
  return components.value.filter((item) =>
    [item.label, item.id, item.description, item.category]
      .some((value) => String(value || '').toLocaleLowerCase().includes(query)),
  )
})

/** 保持分类顺序的扁平列表：所有卡片流进同一网格，避免单卡分类留下大片空位 */
const orderedItems = computed(() => {
  const map = new Map()
  for (const item of filteredComponents.value) {
    if (!map.has(item.category)) map.set(item.category, [])
    map.get(item.category).push(item)
  }
  return [...map.values()].flat()
})

const categories = computed(() => [...new Set(orderedItems.value.map((item) => item.category))])

/** 分类 → 首个组件 id，供快速跳转定位 */
const categoryAnchors = computed(() => {
  const anchors = new Map()
  for (const item of orderedItems.value) {
    if (!anchors.has(item.category)) anchors.set(item.category, item.id)
  }
  return anchors
})

function jumpToCategory(category) {
  const target = document.getElementById(`component-card-${categoryAnchors.value.get(category)}`)
  if (!target) return
  // 页面外层是 n-scrollbar 容器，scrollIntoView 在嵌套布局里不可靠，直接找最近的可滚动祖先
  let scroller = target.parentElement
  while (scroller && scroller.scrollHeight <= scroller.clientHeight + 1) scroller = scroller.parentElement
  if (scroller) {
    const top = target.getBoundingClientRect().top - scroller.getBoundingClientRect().top
    scroller.scrollTo({ top: scroller.scrollTop + top - 16, behavior: 'smooth' })
  }
  target.classList.add('component-card-flash')
  setTimeout(() => target.classList.remove('component-card-flash'), 1200)
}

/** 与 PhonePreview 白底包裹层同一内联样式，保证脱离手机壳后字号/行高一致 */
function wrappedSample(html) {
  if (!html) return ''
  return `<div style="padding:14px 16px;background:#ffffff;color:#333;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;">${html}</div>`
}

async function renderAll() {
  const token = ++renderToken
  if (!themeId.value) return
  busy.value = true
  try {
    const entries = await Promise.all(
      components.value.map(async (item) => {
        const result = await api.post('/wechat-mp/drafts/render', {
          body: item.sampleBody,
          themeId: themeId.value,
        })
        return [item.id, result.html]
      }),
    )
    if (token === renderToken) htmlMap.value = Object.fromEntries(entries)
  } catch (err) {
    if (token === renderToken) toast(`组件预览渲染失败：${err.message}`, 'error')
  } finally {
    if (token === renderToken) busy.value = false
  }
}

watch(themeId, renderAll)

onMounted(async () => {
  try {
    await loadComponents()
  } catch (err) {
    toast(`组件清单加载失败：${err.message}`, 'error')
  }
  if (!themeId.value) {
    themeId.value = props.store.themes.value.defaultThemeId || props.store.visiblePresets.value[0]?.id || ''
  }
  renderAll()
})

async function copySnippet(item) {
  try {
    await navigator.clipboard.writeText(item.snippet)
  } catch {
    // 剪贴板 API 不可用（非安全上下文）时退回临时 textarea
    const el = document.createElement('textarea')
    el.value = item.snippet
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    el.remove()
  }
  copyId.value = item.id
  setTimeout(() => (copyId.value === item.id ? (copyId.value = '') : null), 1600)
  toast(`已复制「${item.label}」语法，粘贴到正文即可`)
}
</script>

<template>
  <div class="component-tab">
    <div class="component-toolbar">
      <span class="component-toolbar-label">预览主题</span>
      <n-button
        v-for="theme in themeOptions"
        :key="theme.value"
        size="tiny"
        :type="themeId === theme.value ? 'primary' : 'default'"
        :secondary="themeId !== theme.value"
        @click="themeId = theme.value"
      >{{ theme.label }}</n-button>
      <div class="spacer" />
      <span v-if="busy" class="field-hint">渲染中…</span>
      <span v-else class="field-hint">{{ filteredComponents.length }} / {{ components.length }} 个组件</span>
    </div>

    <div class="component-discovery">
      <n-input
        v-model:value="searchQuery"
        class="component-search"
        clearable
        size="small"
        placeholder="搜索组件名称、语法或描述"
        aria-label="搜索小组件"
      >
        <template #prefix><span class="component-search-icon" aria-hidden="true" /></template>
      </n-input>
      <nav v-if="categories.length > 1" class="component-jump" aria-label="组件分类快速导航">
        <span class="component-jump-label">快速跳转</span>
        <button
          v-for="category in categories"
          :key="category"
          type="button"
          class="component-jump-button"
          @click="jumpToCategory(category)"
        >{{ category }}</button>
      </nav>
    </div>

    <n-empty v-if="!components.length && !busy" description="组件清单加载失败，稍后重试" />
    <n-empty v-else-if="components.length && !filteredComponents.length" description="没有匹配的小组件" />

    <div class="component-grid">
      <article
        v-for="item in orderedItems"
        :id="`component-card-${item.id}`"
        :key="item.id"
        class="component-card"
      >
        <header class="component-card-head">
          <span class="component-category-tag">{{ item.category }}</span>
          <strong>{{ item.label }}</strong>
          <code class="component-fence">:::{{ item.id }}</code>
          <div class="spacer" />
          <n-button size="tiny" tertiary @click="copySnippet(item)">
            {{ copyId === item.id ? '已复制' : '复制语法' }}
          </n-button>
        </header>
        <p class="component-desc">{{ item.description }}</p>
        <!-- 组件库不套手机壳：正文按微信字号直接渲染，超长时卡内滚动 -->
        <div v-if="busy && !htmlMap[item.id]" class="component-sample component-sample-busy">
          排版渲染中…
        </div>
        <div v-else class="component-sample" v-html="wrappedSample(htmlMap[item.id])" />
      </article>
    </div>
  </div>
</template>

<style scoped>
.component-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.component-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.component-toolbar-label {
  font-size: 13px;
  color: var(--color-text-secondary);
  margin-right: 4px;
}
.component-discovery {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface-raised);
}
.component-search {
  flex: 0 1 280px;
  min-width: 180px;
}
.component-search-icon {
  position: relative;
  display: block;
  width: 13px;
  height: 13px;
  border: 1.5px solid currentColor;
  border-radius: 50%;
}
.component-search-icon::after {
  content: '';
  position: absolute;
  right: -4px;
  bottom: -2px;
  width: 5px;
  height: 1.5px;
  border-radius: 1px;
  background: currentColor;
  transform: rotate(45deg);
  transform-origin: left center;
}
.component-jump {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.component-jump::-webkit-scrollbar {
  display: none;
}
.component-jump-label {
  flex: none;
  font-size: 12px;
  color: var(--color-text-secondary);
}
.component-jump-button {
  flex: none;
  border: 0;
  border-radius: 999px;
  padding: 5px 10px;
  color: var(--color-text-secondary);
  background: var(--color-surface);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: color 160ms ease, background-color 160ms ease;
}
.component-jump-button:hover,
.component-jump-button:focus-visible {
  color: var(--color-accent);
  background: var(--color-accent-soft);
  outline: none;
}
.component-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: var(--space-4);
  scroll-margin-top: 16px;
}
.component-card {
  scroll-margin-top: 72px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  padding: 14px 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: box-shadow 200ms ease, border-color 200ms ease, transform 200ms ease;
}
.component-card:hover {
  border-color: color-mix(in srgb, var(--color-accent) 35%, var(--color-border));
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}
@keyframes component-card-flash {
  0%, 60% { box-shadow: 0 0 0 2px var(--color-accent); }
  100% { box-shadow: 0 0 0 2px transparent; }
}
.component-card-flash {
  animation: component-card-flash 1200ms ease-out;
}
.component-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.component-category-tag {
  flex: none;
  font-size: 11px;
  line-height: 1;
  padding: 4px 7px;
  border-radius: 999px;
  color: var(--color-accent);
  background: var(--color-accent-soft);
}
.component-fence {
  font-size: 11px;
  color: var(--color-text-secondary);
  background: var(--color-surface-raised);
  border-radius: 4px;
  padding: 1px 6px;
}
.component-desc {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-secondary);
}
/* 正文样张：白底即微信正文底色，超长卡内滚动 */
.component-sample {
  margin-top: 4px;
  max-height: 300px;
  overflow-y: auto;
  overscroll-behavior: contain;
  border-radius: var(--radius-md);
  box-shadow: inset 0 0 0 1px var(--color-border);
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--color-text-muted) 45%, transparent) transparent;
}
.component-sample::-webkit-scrollbar {
  width: 4px;
}
.component-sample::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--color-text-muted) 45%, transparent);
  border-radius: 4px;
}
.component-sample :deep(img) {
  max-width: 100%;
}
.component-sample-busy {
  display: grid;
  place-items: center;
  min-height: 96px;
  color: var(--color-text-muted);
  font-size: 13px;
}
@media (max-width: 720px) {
  .component-discovery {
    align-items: stretch;
    flex-direction: column;
  }
  .component-search {
    flex-basis: auto;
    width: 100%;
  }
  .component-jump {
    width: 100%;
  }
}
</style>
