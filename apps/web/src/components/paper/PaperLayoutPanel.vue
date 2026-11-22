<script setup>
/**
 * 成稿台右栏「排版」设置。
 *
 * 受控组件：只维护一份选项并 `emit('change', options)`，自己不渲染、不存稿。
 * 父级把这份 options 传给中间栏的 PaperLayoutPreview —— 全站只有它调 render，
 * 所以这里改一个值不会打两次接口（预览侧有 400ms 防抖）。
 *
 * 字段严格对齐后端 `schemas.WeChatStudioOptions`（多一个字段都会被 422 拒），
 * 当前可用字段只有：风格 / 配色 / 横幅 / 文末关注 / 作者名 / 口号 / 头像 / 标题。
 */
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { NButton, NInput, NModal, NSwitch } from 'naive-ui'
import { studioExtraApi } from '../../api/studioExtra'
import { BANNER_MODES, COLOR_PRESETS, DEFAULT_OPTIONS, STYLE_PRESETS } from '../../features/layout/studioPresets'

const props = defineProps({
  article: { type: Object, default: null },
  busy: { type: String, default: '' },
  options: { type: Object, default: null },
})
const emit = defineEmits(['change', 'saved', 'notify'])

const local = ref({ ...DEFAULT_OPTIONS })
const catalog = shallowRef({})
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const showBannerExamples = shallowRef(false)

let sequence = 0
let disposed = false

const styles = computed(() => {
  const list = catalog.value.styles?.length ? catalog.value.styles : STYLE_PRESETS
  return list.map((item) => ({ id: item.id, name: item.name, desc: item.desc || '' }))
})
const themes = computed(() => {
  const list = catalog.value.themes?.length ? catalog.value.themes : COLOR_PRESETS.map((item) => ({ id: item.id, name: item.name, main: item.color }))
  // catalog.themes 每项是 {id, name, main, soft, tint}，老结构的 swatches 也兜一下
  return list.map((item) => ({ id: item.id, name: item.name, color: item.main || (item.swatches || [])[0] || 'transparent' }))
})
const banners = BANNER_MODES
const currentBanner = computed(() => banners.find((item) => item.id === local.value.openingBanner) || null)
const bannerExamples = computed(() => (catalog.value.banners || [])
  .filter((item) => item.previewHtml)
  .map((item) => ({
    ...item,
    document: `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><style>html{color-scheme:light}body{margin:0;padding:10px;color:#333;background:#fff;font-family:sans-serif}body>section{margin:0!important}svg{max-width:100%;height:auto}</style></head><body>${item.previewHtml}</body></html>`,
  })))
const currentStyle = computed(() => styles.value.find((item) => item.id === local.value.style) || null)
const currentTheme = computed(() => themes.value.find((item) => item.id === local.value.theme) || null)

async function load() {
  const articleId = props.article?.id
  const seq = ++sequence
  catalog.value = {}
  showBannerExamples.value = false
  local.value = { ...DEFAULT_OPTIONS, ...props.article?.layoutOptions, ...props.options }
  saving.value = false
  loading.value = Boolean(articleId)
  error.value = ''
  if (!articleId) return
  try {
    const data = await studioExtraApi.renderOptions(articleId)
    if (seq !== sequence || disposed) return
    const defaults = data?.catalog?.defaults || {}
    const current = data?.options || props.article?.layoutOptions || {}
    // 未保存的选项由父组件保留；切回侧栏时优先恢复它们。
    local.value = { ...DEFAULT_OPTIONS, ...defaults, ...current, ...props.options }
    catalog.value = data?.catalog || {}
    emit('change', { ...local.value })
  } catch (err) {
    if (seq !== sequence || disposed) return
    error.value = err.message || '排版选项载入失败'
  } finally {
    if (seq === sequence && !disposed) loading.value = false
  }
}

function pick(patch) {
  local.value = { ...local.value, ...patch }
  emit('change', { ...local.value })
}

async function saveDefault() {
  const articleId = props.article?.id
  if (!articleId || saving.value) return
  const seq = sequence
  saving.value = true
  error.value = ''
  try {
    const snapshot = await studioExtraApi.saveLayout(articleId, { ...local.value })
    if (seq !== sequence || disposed) return
    if (snapshot) emit('saved', snapshot)
    emit('notify', '排版已设为这篇稿件的默认样式')
  } catch (err) {
    if (seq !== sequence || disposed) return
    error.value = err.message || '保存失败，请重试'
    emit('notify', error.value)
  } finally {
    if (seq === sequence && !disposed) saving.value = false
  }
}

watch(() => props.article?.id, load, { immediate: true })

onBeforeUnmount(() => {
  disposed = true
  sequence += 1
})
</script>

<template>
  <section class="layout-panel" aria-label="排版设置">
    <header class="panel-head">
      <span>排版</span>
      <span class="panel-note">{{ loading ? '载入中…' : '实时预览' }}</span>
    </header>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="!article" class="panel-empty">左侧选一篇稿件再排版。</p>

    <fieldset v-else class="panel-body" :disabled="loading || saving || Boolean(busy)">
      <section class="setting">
        <div class="setting-head">
          <h3>正文风格</h3>
          <span>{{ currentStyle?.name || '—' }}</span>
        </div>
        <div class="style-grid">
          <button
            v-for="item in styles"
            :key="item.id"
            type="button"
            class="style-chip"
            :class="{ active: local.style === item.id }"
            :title="item.desc"
            @click="pick({ style: item.id })"
          >{{ item.name }}</button>
        </div>
        <p v-if="currentStyle?.desc" class="setting-caption">{{ currentStyle.desc }}</p>
      </section>

      <section class="setting">
        <div class="setting-head">
          <h3>主题配色</h3>
          <span>{{ currentTheme?.name || '—' }}</span>
        </div>
        <div class="theme-grid">
          <button
            v-for="item in themes"
            :key="item.id"
            type="button"
            class="theme-chip"
            :class="{ active: local.theme === item.id }"
            :style="{ '--swatch': item.color }"
            :title="item.name"
            :aria-label="item.name"
            :aria-pressed="local.theme === item.id"
            @click="pick({ theme: item.id })"
          />
        </div>
      </section>

      <section class="setting">
        <div class="setting-head">
          <h3>开头横幅</h3>
          <NButton v-if="bannerExamples.length" size="tiny" @click="showBannerExamples = true">对比样式</NButton>
        </div>
        <div class="banner-grid">
          <button
            v-for="item in banners"
            :key="item.id"
            type="button"
            class="banner-chip"
            :class="{ active: currentBanner?.id === item.id }"
            :aria-pressed="currentBanner?.id === item.id"
            :title="item.desc"
            @click="pick({ openingBanner: item.id })"
          >{{ item.label }}</button>
        </div>
        <p class="setting-caption">{{ currentBanner?.desc || '沿用稿件原有的横幅设置。' }}</p>
      </section>

      <section class="setting">
        <div class="setting-head switch-row">
          <div>
            <h3>文末关注</h3>
            <p class="setting-caption">结尾留一张关注卡，带上作者与口号。</p>
          </div>
          <NSwitch
            :value="Boolean(local.outro)"
            size="small"
            :disabled="loading || saving || Boolean(busy)"
            @update:value="(value) => pick({ outro: value })"
          />
        </div>
        <label class="field">
          <span>作者名</span>
          <NInput
            :value="local.brandAuthor"
            size="small"
            maxlength="80"
            placeholder="显示在文末关注卡"
            @update:value="(value) => pick({ brandAuthor: value })"
          />
        </label>
        <label class="field">
          <span>口号 / 简介</span>
          <NInput
            :value="local.brandSlogan"
            size="small"
            type="textarea"
            :rows="2"
            maxlength="200"
            placeholder="一句话说明这个号在写什么"
            @update:value="(value) => pick({ brandSlogan: value })"
          />
        </label>
        <label class="field">
          <span>头像地址（https）</span>
          <NInput
            :value="local.avatarUrl"
            size="small"
            maxlength="2048"
            placeholder="留空则不显示头像"
            @update:value="(value) => pick({ avatarUrl: value })"
          />
        </label>
      </section>

      <div class="panel-actions">
        <span class="hint">{{ busy ? '有操作正在进行，稍候再保存' : '改动会同步到中间预览' }}</span>
        <NButton size="small" type="primary" :loading="saving" :disabled="loading || Boolean(busy)" @click="saveDefault">
          保存为默认
        </NButton>
      </div>
    </fieldset>

    <NModal v-model:show="showBannerExamples" preset="card" title="对比开头横幅" class="banner-comparison">
      <p class="setting-caption comparison-note">固定示例 · 品牌橙</p>
      <div class="comparison-grid">
        <figure v-for="item in bannerExamples" :key="item.id" class="comparison-item">
          <figcaption>{{ item.name }}</figcaption>
          <iframe class="comparison-frame" :title="`${item.name}示例`" :srcdoc="item.document" sandbox="" />
        </figure>
      </div>
    </NModal>
  </section>
</template>

<style scoped>
.layout-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-surface);
}
.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
}
.panel-note { font-size: 11px; font-weight: 400; color: var(--color-text-tertiary); }
.panel-body { display: flex; flex-direction: column; gap: 14px; margin: 0; padding: 0; border: 0; min-width: 0; }
.setting { display: flex; flex-direction: column; gap: 8px; }
.setting-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.setting-head.switch-row { align-items: flex-start; }
.setting-head h3 { margin: 0; font-size: 12px; font-weight: 600; }
.setting-head > span { font-size: 10px; color: var(--color-text-tertiary); }

.style-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 6px; }
.style-chip {
  padding: 7px 0;
  font-size: 11px;
  font-family: inherit;
  color: var(--color-text-secondary);
  background: transparent;
  border: 1px solid var(--paper-line);
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.style-chip.active {
  color: var(--paper-accent);
  background: var(--paper-accent-tint);
  border-color: var(--paper-accent);
  font-weight: 600;
}

.theme-grid { display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 8px 6px; }
.theme-chip {
  width: 22px;
  height: 22px;
  padding: 0;
  justify-self: center;
  border: 2px solid var(--color-surface);
  border-radius: 50%;
  background: var(--swatch);
  box-shadow: 0 0 0 1px var(--paper-line);
  cursor: pointer;
}
.theme-chip.active { box-shadow: 0 0 0 2px var(--swatch); }

.banner-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
.banner-chip {
  padding: 8px 6px;
  font-size: 11px;
  font-family: inherit;
  color: var(--color-text-secondary);
  background: var(--color-surface-muted);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  cursor: pointer;
}
.banner-chip.active {
  color: var(--paper-accent);
  background: var(--paper-accent-tint);
  border-color: var(--paper-accent);
}

.banner-comparison { width: min(860px, calc(100vw - 32px)); }
.comparison-note { margin-bottom: 12px; }
.comparison-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  max-height: 64vh;
  overflow: auto;
}
.comparison-item { min-width: 0; margin: 0; font-size: 12px; font-weight: 600; }
.comparison-frame {
  display: block;
  box-sizing: border-box;
  width: 100%;
  height: clamp(132px, 24vw, 176px);
  margin-top: 6px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: #fff;
}
@media (max-width: 560px) {
  .comparison-grid { grid-template-columns: minmax(0, 1fr); }
}

.field { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--color-text-tertiary); }
.panel-actions { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.panel-actions :deep(.n-button) { flex: none; }
.hint { font-size: 11px; color: var(--color-text-tertiary); line-height: 1.6; }
.setting-caption { margin: 0; font-size: 10px; line-height: 1.7; color: var(--color-text-tertiary); }
.panel-error {
  margin: 0;
  padding: 8px 10px;
  border-radius: 8px;
  font-size: 12px;
  color: var(--paper-danger);
  background: var(--paper-danger-soft);
  border: 1px solid var(--paper-danger-line);
}
.panel-empty { margin: 0; font-size: 12px; color: var(--color-text-tertiary); }
</style>
