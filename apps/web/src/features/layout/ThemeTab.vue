<script setup>
import { computed, ref } from 'vue'
import { NAlert, NButton, NPopconfirm, NTag } from 'naive-ui'
import { toast } from '../../ui/feedback'

/**
 * 主题管理：预设（收藏/隐藏/复制/导出）+ 自定义（编辑/删除/导出/设为默认）
 * + 新建 / 导入。状态与接口都走父级传入的 useWechatLayout store（单一数据源）。
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

const swatchKeys = ['primaryColor', 'primarySoft', 'quoteBg', 'textColor']

function swatchStyle(theme) {
  return swatchKeys.map((key) => theme.tokens?.[key] || '#eee')
}

async function run(action, okMessage) {
  try {
    await action()
    if (okMessage) toast(okMessage)
  } catch (err) {
    toast(err.message, 'error')
  }
}

async function onImportFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  await run(
    async () => {
      const created = await props.store.importTheme(file)
      toast(`已导入主题「${created.name}」`)
    },
    '',
  )
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
      <n-button v-if="hiddenCount" quaternary size="small" @click="showHidden = !showHidden">
        {{ showHidden ? '隐藏已屏蔽的主题' : `显示已隐藏 (${hiddenCount})` }}
      </n-button>
    </div>

    <section class="theme-section">
      <h3 class="theme-section-title">系统预设</h3>
      <div class="theme-grid">
        <article
          v-for="theme in presets"
          :key="theme.id"
          class="theme-card"
          :class="{ 'is-hidden': theme.hidden, 'is-default': theme.id === defaultId }"
        >
          <div class="theme-swatch">
            <span v-for="(color, i) in swatchStyle(theme)" :key="i" :style="{ background: color }" />
          </div>
          <header class="theme-card-head">
            <strong class="theme-name">
              {{ theme.name }}
              <n-tag v-if="theme.id === defaultId" size="tiny" type="success" :bordered="false">默认</n-tag>
            </strong>
            <div class="theme-icon-actions">
              <button
                type="button"
                class="theme-icon-btn"
                :class="{ 'is-on': theme.favorite }"
                :title="theme.favorite ? '取消收藏' : '收藏（发布时置顶）'"
                @click="run(() => store.toggleFavorite(theme.id))"
              >★</button>
              <button
                type="button"
                class="theme-icon-btn"
                :class="{ 'is-on': theme.hidden }"
                :title="theme.hidden ? '取消隐藏' : '隐藏（发布时不出现）'"
                @click="run(() => store.toggleHidden(theme.id))"
              >{{ theme.hidden ? ' ⊘' : '◌' }}</button>
            </div>
          </header>
          <p class="theme-desc">{{ theme.description }}</p>
          <footer class="theme-actions">
            <n-button
              v-if="theme.id !== defaultId"
              quaternary
              size="tiny"
              @click="run(() => store.setDefault(theme.id), `已把「${theme.name}」设为默认主题`)"
            >设为默认</n-button>
            <n-button quaternary size="tiny" @click="emit('create', theme.id)">复制为自定义</n-button>
            <n-button quaternary size="tiny" @click="store.exportTheme(theme)">导出</n-button>
          </footer>
        </article>
      </div>
    </section>

    <section class="theme-section">
      <h3 class="theme-section-title">我的主题<span class="field-hint">（仅自己可见，可编辑）</span></h3>
      <div v-if="!customs.length" class="theme-empty-hint">
        还没有自定义主题。可「新建主题」从零开始，或在任意预设上点「复制为自定义」，也可以导入他人导出的主题 JSON。
      </div>
      <div v-else class="theme-grid">
        <article
          v-for="theme in customs"
          :key="theme.id"
          class="theme-card"
          :class="{ 'is-default': theme.id === defaultId }"
        >
          <div class="theme-swatch">
            <span v-for="(color, i) in swatchStyle(theme)" :key="i" :style="{ background: color }" />
          </div>
          <header class="theme-card-head">
            <strong class="theme-name">
              {{ theme.name }}
              <n-tag v-if="theme.id === defaultId" size="tiny" type="success" :bordered="false">默认</n-tag>
            </strong>
          </header>
          <p class="theme-desc">
            正文 {{ theme.tokens?.bodySize }}px · 行高 {{ theme.tokens?.bodyLineHeight }} · 主标题 {{ theme.tokens?.h1Style }}
          </p>
          <footer class="theme-actions">
            <n-button
              v-if="theme.id !== defaultId"
              quaternary
              size="tiny"
              @click="run(() => store.setDefault(theme.id), `已把「${theme.name}」设为默认主题`)"
            >设为默认</n-button>
            <n-button quaternary size="tiny" @click="emit('edit', theme)">编辑</n-button>
            <n-button quaternary size="tiny" @click="store.exportTheme(theme)">导出</n-button>
            <n-popconfirm
              :positive-text="'删除'"
              :negative-text="'取消'"
              @positive-click="run(() => store.deleteTheme(theme.id), `已删除「${theme.name}」`)"
            >
              <template #trigger>
                <n-button text size="tiny" type="error">删除</n-button>
              </template>
              {{ confirmDelete(theme) }}
            </n-popconfirm>
          </footer>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.theme-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-4, 16px);
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
  gap: var(--space-3, 12px);
}
.theme-card {
  border: 1px solid var(--color-border, #e5e5e5);
  border-radius: var(--radius-md, 8px);
  background: var(--color-surface, #fff);
  padding: 12px 14px 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.theme-card.is-default {
  border-color: var(--color-primary, #0f6e56);
}
.theme-card.is-hidden {
  opacity: 0.45;
}
.theme-swatch {
  display: flex;
  border-radius: 6px;
  overflow: hidden;
  height: 10px;
}
.theme-swatch span {
  flex: 1;
}
.theme-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.theme-name {
  font-size: 14px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.theme-icon-actions {
  display: flex;
  gap: 2px;
}
.theme-icon-btn {
  border: none;
  background: none;
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  color: var(--color-text-secondary, #999);
  padding: 3px 5px;
  border-radius: 6px;
}
.theme-icon-btn:hover {
  background: var(--color-surface-raised, #f3f3f3);
}
.theme-icon-btn.is-on {
  color: var(--color-warning, #ba7517);
}
.theme-desc {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-secondary, #888);
  min-height: 30px;
}
.theme-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-wrap: wrap;
}
.theme-empty-hint {
  font-size: 13px;
  color: var(--color-text-secondary, #888);
  border: 1px dashed var(--color-border, #ddd);
  border-radius: var(--radius-md, 8px);
  padding: 18px;
}
</style>
