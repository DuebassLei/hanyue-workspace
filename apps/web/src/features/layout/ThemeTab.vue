<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NPopconfirm, NTag } from 'naive-ui'
import ThemeThumb from './ThemeThumb.vue'
import { toast } from '../../ui/feedback'

/**
 * 主题管理：预设（收藏/隐藏/复制/导出）+ 自定义（编辑/删除/导出/设为默认）
 * + 新建 / 导入。状态与接口都走父级传入的 useWechatLayout store（单一数据源）。
 *
 * 卡片设计：顶部是「该主题真实渲染的样张」而不是抽象色条——
 * 用户要选的是发出去长什么样，色块回答不了这个问题。
 * 交互：点样张 = 设为默认（最高频操作），收藏/隐藏在卡片头部，编辑类在底部。
 */
const props = defineProps({
  store: { type: Object, required: true },
})
const emit = defineEmits(['create', 'edit'])

const showHidden = ref(false)
const fileInput = ref(null)

const presets = computed(() =>
  showHidden.value
    ? props.store.themes.value.presets
    : props.store.themes.value.presets.filter((t) => !t.hidden),
)
const hiddenCount = computed(
  () => props.store.themes.value.presets.filter((t) => t.hidden).length,
)
const customs = computed(() => props.store.themes.value.custom)
const defaultId = computed(() => props.store.themes.value.defaultThemeId)

const allThemes = computed(() => [...presets.value, ...customs.value])

// 收藏的预设排前面，其余保持预设声明顺序（与发布抽屉的排序规则一致）
const sortedPresets = computed(() => {
  const order = props.store.themes.value.presets.map((t) => t.id)
  return [...presets.value].sort((a, b) => {
    const fav = Number(b.favorite) - Number(a.favorite)
    if (fav !== 0) return fav
    return order.indexOf(a.id) - order.indexOf(b.id)
  })
})

function thumbOf(theme) {
  return props.store.sampleHtml.value[props.store.sampleKey(theme)] || ''
}

function refreshSamples() {
  props.store.loadThemeSamples(allThemes.value).catch(() => {})
}

watch(allThemes, refreshSamples, { deep: false })
onMounted(refreshSamples)

// 标题样式的中文标签：卡片上不展示原始枚举值
const HEADING_LABELS = {
  bar: '竖线标题',
  block: '色块标题',
  number: '序号标题',
  center: '居中标题',
  underline: '下划线标题',
  'number-divider': '大号编号',
  capsule: '胶囊标题',
  banner: '通栏色条',
  'margin-note': '旁注色带',
  seal: '印章方框',
  'lead-in': '标题加导语',
  eyebrow: '眉题小标签',
  kicker: '题签编号',
  framed: '上下线题框',
  rail: '左侧色带',
  plain: '朴素质感',
  tag: '标签小标题',
  strong: '加粗小标题',
  hairline: '发丝线',
}

function styleSummary(theme) {
  const t = theme.tokens || {}
  const h1 = HEADING_LABELS[t.h1Style] || t.h1Style || ''
  return [h1, `${t.bodySize}px`, `行高 ${t.bodyLineHeight}`].filter(Boolean).join(' · ')
}

/**
 * 自定义主题的「血缘」：后端没存 baseThemeId，用 tokens 重合度推断。
 * 只在足够接近（≥80% 键相同）时才标注，避免改得面目全非还硬认亲。
 */
function baseThemeLabel(theme) {
  const tokens = theme.tokens || {}
  let best = null
  let bestScore = 0
  for (const preset of props.store.themes.value.presets) {
    const target = preset.tokens || {}
    const keys = Object.keys(tokens).filter((key) => key in target)
    if (!keys.length) continue
    const same = keys.filter((key) => tokens[key] === target[key]).length
    const score = same / keys.length
    if (score > bestScore) {
      bestScore = score
      best = preset
    }
  }
  return bestScore >= 0.8 && best ? `基于${best.name}` : '自定义'
}

async function run(action, okMessage) {
  try {
    await action()
    if (okMessage) toast(okMessage)
  } catch (err) {
    toast(err.message, 'error')
  }
}

function useTheme(theme) {
  if (theme.id === defaultId.value) return
  run(() => props.store.setDefault(theme.id), `已把「${theme.name}」设为默认主题`)
}

async function onImportFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  await run(async () => {
    const created = await props.store.importTheme(file)
    toast(`已导入主题「${created.name}」`)
  })
}

function confirmDelete(theme) {
  return theme.id === defaultId.value
    ? `「${theme.name}」是当前默认主题，删除后将回退系统默认。确认删除？`
    : `确认删除主题「${theme.name}」？`
}
</script>

<template>
  <div class="theme-tab">
    <n-alert v-if="store.error.value" type="error" :bordered="false">{{ store.error.value }}</n-alert>

    <div class="theme-toolbar">
      <n-button type="primary" size="small" @click="emit('create')">新建主题</n-button>
      <n-button size="small" @click="fileInput?.click()">导入主题</n-button>
      <input
        ref="fileInput"
        type="file"
        accept="application/json,.json"
        class="theme-file-input"
        @change="onImportFile"
      />
      <div class="spacer" />
      <span v-if="store.sampleBusy.value" class="field-hint">生成样张中…</span>
      <n-button v-if="hiddenCount" quaternary size="small" @click="showHidden = !showHidden">
        {{ showHidden ? '收起已隐藏' : `显示已隐藏 (${hiddenCount})` }}
      </n-button>
    </div>

    <section class="theme-section">
      <h3 class="theme-section-title">
        系统预设
        <span class="field-hint">点样张设为默认 · ★ 收藏后发布时置顶</span>
      </h3>
      <div class="theme-grid">
        <article
          v-for="theme in sortedPresets"
          :key="theme.id"
          class="theme-card"
          :class="{ 'is-hidden': theme.hidden, 'is-default': theme.id === defaultId }"
        >
          <button
            type="button"
            class="theme-thumb-btn"
            :aria-label="`使用「${theme.name}」作为默认排版主题`"
            :aria-current="theme.id === defaultId ? 'true' : undefined"
            @click="useTheme(theme)"
          >
            <ThemeThumb :html="thumbOf(theme)" :height="148" />
            <span v-if="theme.id === defaultId" class="theme-default-badge">✓ 当前默认</span>
            <span v-else class="theme-hover-tip">设为默认</span>
          </button>

          <div class="theme-card-body">
            <header class="theme-card-head">
              <strong class="theme-name">{{ theme.name }}</strong>
              <div class="spacer" />
              <button
                type="button"
                class="theme-icon-btn"
                :class="{ 'is-on': theme.favorite }"
                :aria-label="theme.favorite ? `取消收藏「${theme.name}」` : `收藏「${theme.name}」`"
                :aria-pressed="theme.favorite"
                :title="theme.favorite ? '取消收藏' : '收藏（发布时置顶）'"
                @click="run(() => store.toggleFavorite(theme.id))"
              >★</button>
              <button
                type="button"
                class="theme-icon-btn"
                :class="{ 'is-on': theme.hidden }"
                :aria-label="theme.hidden ? `取消隐藏「${theme.name}」` : `隐藏「${theme.name}」`"
                :aria-pressed="theme.hidden"
                :title="theme.hidden ? '取消隐藏' : '隐藏（发布时不出现）'"
                @click="run(() => store.toggleHidden(theme.id))"
              >{{ theme.hidden ? '⊘' : '◌' }}</button>
            </header>

            <p v-if="theme.description" class="theme-desc">{{ theme.description }}</p>

            <div class="theme-meta">
              <span class="theme-meta-item">{{ styleSummary(theme) }}</span>
              <span
                class="theme-dot"
                :style="{ background: theme.tokens?.primaryColor }"
                :title="`主色 ${theme.tokens?.primaryColor}`"
              />
              <span
                class="theme-dot is-soft"
                :style="{ background: theme.tokens?.primarySoft }"
                :title="`浅主色 ${theme.tokens?.primarySoft}`"
              />
            </div>

            <footer class="theme-actions">
              <n-button quaternary size="tiny" @click="emit('create', theme.id)">复制</n-button>
              <n-button quaternary size="tiny" @click="store.exportTheme(theme)">导出</n-button>
            </footer>
          </div>
        </article>
      </div>
    </section>

    <section class="theme-section">
      <h3 class="theme-section-title">
        我的主题
        <span class="field-hint">仅自己可见，可编辑</span>
      </h3>
      <div v-if="!customs.length" class="theme-empty-hint">
        还没有自定义主题。可「新建主题」从零开始，或在任意预设上点「复制」，也可以导入他人导出的主题 JSON。
      </div>
      <div v-else class="theme-grid">
        <article
          v-for="theme in customs"
          :key="theme.id"
          class="theme-card"
          :class="{ 'is-default': theme.id === defaultId }"
        >
          <button
            type="button"
            class="theme-thumb-btn"
            :aria-label="`使用「${theme.name}」作为默认排版主题`"
            :aria-current="theme.id === defaultId ? 'true' : undefined"
            @click="useTheme(theme)"
          >
            <ThemeThumb :html="thumbOf(theme)" :height="148" />
            <span v-if="theme.id === defaultId" class="theme-default-badge">✓ 当前默认</span>
            <span v-else class="theme-hover-tip">设为默认</span>
          </button>

          <div class="theme-card-body">
            <header class="theme-card-head">
              <strong class="theme-name">{{ theme.name }}</strong>
              <n-tag size="tiny" :bordered="false" class="theme-origin-tag">
                {{ baseThemeLabel(theme) }}
              </n-tag>
            </header>

            <div class="theme-meta">
              <span class="theme-meta-item">{{ styleSummary(theme) }}</span>
              <span
                class="theme-dot"
                :style="{ background: theme.tokens?.primaryColor }"
                :title="`主色 ${theme.tokens?.primaryColor}`"
              />
              <span
                class="theme-dot is-soft"
                :style="{ background: theme.tokens?.primarySoft }"
                :title="`浅主色 ${theme.tokens?.primarySoft}`"
              />
            </div>

            <footer class="theme-actions">
              <n-button quaternary size="tiny" @click="emit('edit', theme)">编辑</n-button>
              <n-button quaternary size="tiny" @click="store.exportTheme(theme)">导出</n-button>
              <n-popconfirm
                positive-text="删除"
                negative-text="取消"
                @positive-click="run(() => store.deleteTheme(theme.id), `已删除「${theme.name}」`)"
              >
                <template #trigger>
                  <n-button text size="tiny" type="error">删除</n-button>
                </template>
                {{ confirmDelete(theme) }}
              </n-popconfirm>
            </footer>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.theme-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-5, 20px);
}
.theme-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2, 8px);
}
.theme-file-input {
  display: none;
}
.theme-section-title {
  font-size: var(--font-section, 15px);
  margin: 0 0 var(--space-3, 12px);
}
.theme-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--space-4, 16px);
}

.theme-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: border-color 0.16s, box-shadow 0.16s;
}
.theme-card:hover {
  border-color: var(--color-border-strong);
  box-shadow: var(--shadow-md);
}
.theme-card.is-default {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent) inset;
}
.theme-card.is-hidden {
  opacity: 0.5;
}

/* 样张本身是按钮：点击即设为默认 */
.theme-thumb-btn {
  position: relative;
  display: block;
  width: 100%;
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
  line-height: 0;
}
.theme-thumb-btn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}
.theme-default-badge,
.theme-hover-tip {
  position: absolute;
  top: 8px;
  right: 8px;
  font-size: 11px;
  line-height: 1;
  padding: 4px 8px;
  border-radius: 999px;
  letter-spacing: 0;
}
.theme-default-badge {
  background: var(--color-accent);
  color: var(--color-on-accent);
  font-weight: 600;
}
.theme-hover-tip {
  background: var(--color-inverse-scrim-strong);
  color: var(--color-inverse-on-scrim);
  opacity: 0;
  transition: opacity 0.16s;
}
.theme-thumb-btn:hover .theme-hover-tip,
.theme-thumb-btn:focus-visible .theme-hover-tip {
  opacity: 1;
}

.theme-card-body {
  padding: 10px 12px 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
}
.theme-card-head {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 22px;
}
.theme-name {
  font-size: 14px;
  font-weight: 600;
}
.theme-origin-tag {
  font-size: 10px;
  color: var(--color-text-secondary);
}
.theme-icon-btn {
  border: none;
  background: none;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  color: var(--color-text-secondary);
  padding: 2px 4px;
  border-radius: 5px;
}
.theme-icon-btn:hover {
  background: var(--color-surface-raised);
}
.theme-icon-btn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}
.theme-icon-btn.is-on {
  color: var(--color-warning);
}
.theme-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-secondary);
}
.theme-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--color-text-tertiary);
}
.theme-meta-item {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.theme-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  flex: none;
}
.theme-dot.is-soft {
  margin-left: -2px;
}
.theme-actions {
  display: flex;
  align-items: center;
  gap: 0;
  margin-top: auto;
  padding-top: 2px;
}
.theme-empty-hint {
  font-size: 13px;
  color: var(--color-text-secondary);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  padding: 18px;
}

@media (prefers-reduced-motion: reduce) {
  .theme-card,
  .theme-hover-tip {
    transition: none;
  }
}
</style>
