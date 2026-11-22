<script setup>
/**
 * 成稿台中间栏：公众号正文实时预览。
 *
 * 全站只有这一个组件调 `POST /studio/articles/{id}/render`——排版面板只维护选项，
 * 避免两个组件各渲染一次把后端打满。渲染结果通过 `rendered` 抛给父级，发布/导出可复用。
 *
 * `active` 用来省请求：切到「排版」页签才渲染，切走后不再发；切回来立即重拉一次，
 * 因为期间正文可能被对话改稿或质检改过。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NButton } from 'naive-ui'
import { studioExtraApi } from '../../api/studioExtra'

const props = defineProps({
  article: { type: Object, default: null },
  options: { type: Object, default: () => ({}) },
  active: { type: Boolean, default: false },
})
const emit = defineEmits(['rendered'])

const html = ref('')
const warnings = ref([])
const stats = ref(null)
const error = ref('')
const rendering = ref(false)
const showWarnings = ref(false)
const copied = ref(false)
const notice = ref('')

let timer = null
let copiedTimer = null
let sequence = 0
let disposed = false
const popupUrls = []

/**
 * 预览文档外壳：与 useLayoutStudio 的 wrapDocument 保持完全一致。
 * 内联样式里的白底/深字是有意写死的——公众号正文永远是浅色纸面，
 * 跟随应用深色模式会让预览与实际发布效果不一致。
 */
const wrapDocument = (inner) =>
  `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: http: data:; style-src 'unsafe-inline'; font-src 'none'"><style>html{background:#fff;color-scheme:light}body{margin:0;padding:30px 26px;background:#fff;color:#333;overflow-wrap:anywhere;font-family:'PingFang SC','Microsoft YaHei',sans-serif}img,svg,video{max-width:100%;height:auto}pre{white-space:pre-wrap}table{max-width:100%}@media(max-width:420px){body{padding:24px 18px}}</style></head><body>${inner}</body></html>`

const documentHtml = computed(() => wrapDocument(html.value))
const sourceChars = computed(() => stats.value?.sourceChars ?? 0)
const readingMinutes = computed(() => stats.value?.readingMinutes ?? 0)
const imageCount = computed(() => stats.value?.images ?? 0)
const warningCount = computed(() => warnings.value.length)

function clearTimer() {
  if (timer) clearTimeout(timer)
  timer = null
}

function reset() {
  html.value = ''
  warnings.value = []
  stats.value = null
  error.value = ''
  notice.value = ''
  showWarnings.value = false
}

function schedule(delay) {
  clearTimer()
  if (!props.active || !props.article?.id) return
  // 选项刚变化就废弃旧请求，避免防抖期间把上一个横幅当成当前结果。
  sequence += 1
  timer = setTimeout(fetchRender, delay)
}

async function fetchRender() {
  clearTimer()
  const articleId = props.article?.id
  if (!props.active || !articleId) return
  const seq = ++sequence
  rendering.value = true
  error.value = ''
  try {
    // 空 title 不下发：后端在没有 title 时才回退成稿件标题，传空串会把标题渲染丢
    const payload = { ...(props.options || {}) }
    if (!payload.title) delete payload.title
    const result = await studioExtraApi.render(articleId, Object.keys(payload).length ? payload : null)
    if (seq !== sequence || disposed) return
    if (typeof result?.html !== 'string') throw new Error('服务端没有返回可预览的正文')
    html.value = result.html
    warnings.value = (result.warnings || [])
      .map((item) => (typeof item === 'string' ? { message: item, level: 'warning' } : item))
      .filter((item) => item?.message)
    stats.value = result.stats || null
    emit('rendered', { html: result.html, warnings: warnings.value, stats: stats.value })
  } catch (err) {
    if (seq !== sequence || disposed) return
    html.value = ''
    warnings.value = []
    stats.value = null
    error.value = err.message || '排版预览失败，请重试'
  } finally {
    if (seq === sequence && !disposed) rendering.value = false
  }
}

async function copyHtml() {
  if (!html.value || rendering.value) return
  try {
    await navigator.clipboard.writeText(html.value)
    copied.value = true
    clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // 剪贴板失败只提示，不能把已有预览清成错误页
    notice.value = '浏览器未允许访问剪贴板'
    clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => { notice.value = '' }, 4000)
  }
}

function openWindow() {
  if (!html.value || rendering.value) return
  // 不立即 revoke：新标签页可能还在加载，卸载时统一回收
  const url = URL.createObjectURL(new Blob([documentHtml.value], { type: 'text/html' }))
  popupUrls.push(url)
  window.open(url, '_blank', 'noopener')
}

// 稿件切换 / 页签切回来：立即重拉；切走则取消在途请求
watch(
  () => [props.article?.id, props.active],
  ([id, active], previous) => {
    const idChanged = previous?.[0] !== id
    if (!active || !id) {
      clearTimer()
      sequence += 1
      rendering.value = false
      if (idChanged && previous) reset()
      return
    }
    if (idChanged || !previous) reset()
    fetchRender()
  },
  { immediate: true },
)

// 选项变化：保留旧画面，400ms 防抖后再渲染，避免拖滑块时连打接口
watch(
  () => props.options,
  () => {
    if (!props.active || !props.article?.id) return
    rendering.value = true
    schedule(400)
  },
  { deep: true },
)

onBeforeUnmount(() => {
  disposed = true
  sequence += 1
  clearTimer()
  clearTimeout(copiedTimer)
  popupUrls.forEach((url) => URL.revokeObjectURL(url))
  popupUrls.length = 0
})
</script>

<template>
  <div class="layout-preview" :aria-busy="rendering">
    <!-- 统计和复制按钮属于工具条，不在正文卡片里；由成稿台提供挂载点 -->
    <Teleport to="#paper-preview-bar-slot">
      <header class="preview-bar">
        <div class="bar-metrics">
          <span v-if="rendering" class="bar-loading" role="status">渲染中…</span>
          <span v-else-if="html" class="bar-ready">已同步</span>
          <span v-if="stats">{{ sourceChars }} 字 · 约 {{ readingMinutes }} 分钟</span>
          <span v-if="imageCount">· {{ imageCount }} 图</span>
          <button
            v-if="warningCount"
            class="warn-toggle"
            type="button"
            :aria-expanded="showWarnings"
            @click="showWarnings = !showWarnings"
          >
            {{ warningCount }} 条排版提示{{ showWarnings ? ' ▴' : ' ▾' }}
          </button>
          <span v-else-if="html && !rendering" class="hint">无排版问题</span>
        </div>
        <div class="bar-actions">
          <span v-if="notice" class="bar-notice">{{ notice }}</span>
          <NButton size="tiny" :disabled="!html || rendering" @click="copyHtml">{{ copied ? '已复制' : '复制 HTML' }}</NButton>
          <NButton size="tiny" :disabled="!html || rendering" @click="openWindow">新窗口打开</NButton>
        </div>
      </header>
    </Teleport>

    <ul v-if="showWarnings && warningCount" class="warn-list">
      <li v-for="(item, index) in warnings" :key="index" :class="item.level">
        <span v-if="item.field" class="warn-field">{{ item.field }}</span>
        <span>{{ item.message }}</span>
      </li>
    </ul>

    <div v-if="!article" class="preview-empty">左侧选一篇稿件，这里会显示公众号实际排版效果。</div>
    <div v-else-if="error" class="preview-error" role="alert">
      <strong>预览暂不可用</strong>
      <p>{{ error }}</p>
      <NButton size="small" @click="fetchRender">重试</NButton>
    </div>
    <div v-else-if="!html && rendering" class="preview-skeleton" role="status" aria-label="正在渲染排版">
      <span v-for="line in 8" :key="line" class="skeleton-line" :style="{ width: `${100 - (line % 4) * 11}%` }" />
    </div>
    <iframe
      v-else-if="html"
      class="preview-frame"
      title="微信公众号正文排版预览"
      :srcdoc="documentHtml"
      sandbox=""
    />
    <div v-else class="preview-empty">{{ active ? '正文还是空的，先在稿纸里写点内容。' : '切到「排版」页开始实时预览。' }}</div>
  </div>
</template>

<style scoped>
.layout-preview {
  display: flex;
  flex-direction: column;
  /* 高度由稿纸剩余空间决定（父级 flex:1）；这里不再写死 520px，
     否则长文只能在一个矮框里滚，短文又在框底下留一大块空白 */
  flex: 1 1 auto;
  min-height: 0;
  background: var(--paper-bg);
  border: 1px solid var(--paper-line);
  border-radius: 14px;
  overflow: hidden;
}
.preview-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  padding: 9px 14px;
  border-bottom: 1px solid var(--paper-line);
  background: var(--paper-header);
  font-size: 11px;
  color: var(--color-text-tertiary);
}
.bar-metrics { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.bar-loading { color: var(--paper-accent); }
.bar-loading::before {
  content: '';
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 5px;
  border-radius: 50%;
  background: currentColor;
  animation: preview-pulse 1.1s infinite;
}
.bar-ready { color: var(--paper-success); }
.bar-actions { display: flex; align-items: center; gap: 6px; }
.bar-notice { font-size: 10px; color: var(--paper-danger); }
.warn-toggle {
  padding: 1px 8px;
  font-size: 11px;
  color: var(--paper-warning);
  background: var(--paper-warning-soft);
  border: 1px solid var(--paper-warning-line);
  border-radius: 999px;
  cursor: pointer;
}
.warn-list {
  margin: 0;
  padding: 8px 14px 8px 28px;
  border-bottom: 1px solid var(--paper-line);
  background: var(--paper-warning-soft);
  font-size: 11px;
  line-height: 1.7;
  color: var(--paper-warning);
  max-height: 148px;
  overflow: auto;
}
.warn-field { margin-right: 6px; opacity: 0.7; font-variant-numeric: tabular-nums; }
/* height:0 配合 flex:1 才能让 iframe 吃到父级分配的高度，否则它按内容最小高度把卡片撑破 */
.preview-frame { flex: 1 1 0; width: 100%; height: 0; min-height: 0; border: 0; background: var(--paper-bg); }
.preview-skeleton {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 30px 26px;
  min-height: 0;
}
.skeleton-line {
  height: 12px;
  border-radius: 6px;
  background: var(--paper-line);
  animation: preview-pulse 1.2s infinite;
}
.preview-error {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 24px;
  text-align: center;
  font-size: 12px;
  color: var(--paper-danger);
}
.preview-error strong { font-size: 14px; }
.preview-error p { margin: 0; max-width: 420px; line-height: 1.7; word-break: break-word; }
.preview-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 24px;
  font-size: 12px;
  color: var(--color-text-tertiary);
}
.hint { font-size: 11px; color: var(--color-text-tertiary); }
@keyframes preview-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
</style>
