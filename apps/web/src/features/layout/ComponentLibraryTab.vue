<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { NButton, NEmpty } from 'naive-ui'
import { api } from '../../api/client'
import { toast } from '../../ui/feedback'
import PhonePreview from './PhonePreview.vue'
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
const htmlMap = ref({})
const busy = ref(false)
const copyId = ref('')
let renderToken = 0

const themeOptions = computed(() =>
  props.store.visiblePresets.value
    .concat(props.store.themes.value.custom)
    .map((t) => ({ value: t.id, label: t.name })),
)

const grouped = computed(() => {
  const map = new Map()
  for (const item of components.value) {
    if (!map.has(item.category)) map.set(item.category, [])
    map.get(item.category).push(item)
  }
  return [...map.entries()]
})

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
      <span v-else class="field-hint">{{ components.length }} 个组件</span>
    </div>

    <n-empty v-if="!components.length && !busy" description="组件清单加载失败，稍后重试" />

    <section v-for="[category, items] in grouped" :key="category" class="component-group">
      <h3 class="component-group-title">{{ category }}</h3>
      <div class="component-grid">
        <article v-for="item in items" :key="item.id" class="component-card">
          <header class="component-card-head">
            <strong>{{ item.label }}</strong>
            <code class="component-fence">:::{{ item.id }}</code>
            <div class="spacer" />
            <n-button size="tiny" tertiary @click="copySnippet(item)">
              {{ copyId === item.id ? '已复制' : '复制语法' }}
            </n-button>
          </header>
          <p class="component-desc">{{ item.description }}</p>
          <div class="component-preview">
            <PhonePreview
              :html="htmlMap[item.id] || ''"
              :busy="busy && !htmlMap[item.id]"
              :height="360"
              :scale="0.74"
            />
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.component-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-4, 16px);
}
.component-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.component-toolbar-label {
  font-size: 13px;
  color: var(--color-text-secondary, #888);
  margin-right: 4px;
}
.component-group-title {
  font-size: var(--font-section, 15px);
  margin: 0 0 var(--space-3, 12px);
}
.component-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: var(--space-4, 16px);
}
.component-card {
  border: 1px solid var(--color-border, #e5e5e5);
  border-radius: var(--radius-lg, 10px);
  background: var(--color-surface, #fff);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.component-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.component-fence {
  font-size: 11px;
  color: var(--color-text-secondary, #888);
  background: var(--color-surface-raised, #f4f4f4);
  border-radius: 4px;
  padding: 1px 6px;
}
.component-desc {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-secondary, #888);
}
.component-preview {
  display: flex;
  justify-content: center;
  padding-top: 4px;
}
</style>
