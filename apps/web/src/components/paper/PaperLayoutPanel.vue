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
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NInput, NSwitch } from 'naive-ui'
import { studioExtraApi } from '../../api/studioExtra'
import { BANNER_MODES, COLOR_PRESETS, DEFAULT_OPTIONS, STYLE_PRESETS } from '../../features/layout/studioPresets'

const props = defineProps({
  article: { type: Object, default: null },
  busy: { type: String, default: '' },
})
const emit = defineEmits(['change', 'saved', 'notify'])

const local = ref({ ...DEFAULT_OPTIONS })
const catalog = ref({ styles: [], themes: [], svgCards: [], banners: [] })
const loading = ref(false)
const saving = ref(false)
const error = ref('')

// 程序化赋值（载入 / 重置）期间不往外抛 change，避免父级收到一次假的用户改动
let syncing = false
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
const banners = computed(() => BANNER_MODES.map((item) => ({ id: item.id, label: item.label, desc: item.desc })))
const currentStyle = computed(() => styles.value.find((item) => item.id === local.value.style) || null)
const currentTheme = computed(() => themes.value.find((item) => item.id === local.value.theme) || null)

async function load() {
  const articleId = props.article?.id
  if (!articleId) return
  const seq = ++sequence
  loading.value = true
  error.value = ''
  try {
    const data = await studioExtraApi.renderOptions(articleId)
    if (seq !== sequence || disposed) return
    syncing = true
    // 初始值优先级：本地兜底 → 服务端默认值 → 稿件当前生效选项（接口已补默认、回填标题）
    const defaults = data?.catalog?.defaults || {}
    const current = data?.options || props.article?.layoutOptions || {}
    local.value = { ...DEFAULT_OPTIONS, ...defaults, ...current }
    catalog.value = {
      styles: data?.catalog?.styles || [],
      themes: data?.catalog?.themes || [],
      svgCards: data?.catalog?.svgCards || [],
      banners: data?.catalog?.banners || [],
    }
    await nextTick()
    syncing = false
    // 载入后主动同步一次，父级才知道要拿什么选项去渲染
    emit('change', { ...local.value })
  } catch (err) {
    if (seq !== sequence || disposed) return
    syncing = false
    error.value = err.message || '排版选项载入失败'
  } finally {
    if (seq === sequence && !disposed) loading.value = false
  }
}

function pick(patch) {
  local.value = { ...local.value, ...patch }
}

async function saveDefault() {
  const articleId = props.article?.id
  if (!articleId || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const snapshot = await studioExtraApi.saveLayout(articleId, { ...local.value })
    if (snapshot) emit('saved', snapshot)
    emit('notify', '排版已设为这篇稿件的默认样式')
  } catch (err) {
    error.value = err.message || '保存失败，请重试'
    emit('notify', error.value)
  } finally {
    saving.value = false
  }
}

watch(local, (value) => {
  if (syncing) return
  emit('change', { ...value })
}, { deep: true })

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
          <h3>开篇横幅</h3>
          <span>{{ banners.find((item) => item.id === local.banner)?.label || '无' }}</span>
        </div>
        <div class="banner-grid">
          <button
            v-for="item in banners"
            :key="item.id"
            type="button"
            class="banner-chip"
            :class="{ active: local.banner === item.id }"
            :title="item.desc"
            @click="pick({ banner: item.id })"
          >{{ item.label }}</button>
        </div>
        <p class="setting-caption">横幅要用「横幅」页生成规格后才会出现在正文开头。</p>
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
