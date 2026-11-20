import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api } from '../../api/client'
import { DEFAULT_OPTIONS, SAMPLE_BODY } from './studioPresets'

export function useLayoutStudio(route, router) {
  const title = ref('让好内容，拥有自己的表达')
  const body = ref(SAMPLE_BODY)
  const options = reactive({ ...DEFAULT_OPTIONS })
  const articleId = ref('')
  const versionId = ref('')
  const loading = ref(false)
  const saving = ref(false)
  const rendering = ref(false)
  const ready = ref(false)
  const error = ref('')
  const previewError = ref('')
  const renderWarnings = ref([])
  const renderStats = ref(null)
  const renderOptions = () => ({ ...options, title: title.value })
  const notice = ref('')
  const html = ref('')
  const savedAt = ref('')
  const baseline = ref('')
  let savedContent = ''
  let loadSequence = 0
  let renderSequence = 0
  let renderTimer
  let noticeTimer
  let disposed = false
  let syncingQuery = false
  const snapshot = () => JSON.stringify({ title: title.value, body: body.value, options: { ...options } })
  const contentSnapshot = () => JSON.stringify({ title: title.value, body: body.value })
  const dirty = computed(() => ready.value && baseline.value !== snapshot())
  const wrapDocument = inner => `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: http: data:; style-src 'unsafe-inline'; font-src 'none'"><style>html{background:#fff;color-scheme:light}body{margin:0;padding:30px 26px;background:#fff;color:#333;overflow-wrap:anywhere;font-family:'PingFang SC','Microsoft YaHei',sans-serif}img,svg,video{max-width:100%;height:auto}pre{white-space:pre-wrap}table{max-width:100%}@media(max-width:420px){body{padding:24px 18px}}</style></head><body>${inner}</body></html>`
  const previewDocument = computed(() => wrapDocument(html.value))

  function flash(text) {
    notice.value = text
    clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => { notice.value = '' }, 5000)
  }

  async function renderPreview() {
    clearTimeout(renderTimer)
    const sequence = ++renderSequence
    html.value = ''
    previewError.value = ''
    renderWarnings.value = []
    renderStats.value = null
    if (!ready.value || !body.value.trim()) { rendering.value = false; return }
    rendering.value = true
    try {
      const result = await api.post('/wechat-mp/layout-studio/render', {
        body: body.value, options: renderOptions(),
      })
      if (sequence !== renderSequence || disposed) return
      if (typeof result?.html !== 'string') throw new Error('服务端没有返回有效的排版正文')
      html.value = result.html
      renderWarnings.value = [...(result.warnings || []), ...(result.issues || [])].map(item => typeof item === 'string' ? item : item.message).filter(Boolean)
      renderStats.value = result.stats || null
    } catch (err) {
      if (sequence === renderSequence && !disposed) previewError.value = err.message || '排版预览失败'
    } finally {
      if (sequence === renderSequence && !disposed) rendering.value = false
    }
  }

  async function load() {
    const sequence = ++loadSequence
    ++renderSequence
    clearTimeout(renderTimer)
    loading.value = true
    ready.value = false
    error.value = ''
    previewError.value = ''
    html.value = ''
    renderWarnings.value = []
    renderStats.value = null
    notice.value = ''
    savedAt.value = ''
    articleId.value = String(route.query.articleId || '')
    versionId.value = String(route.query.versionId || '')
    try {
      Object.assign(options, DEFAULT_OPTIONS)
      if (articleId.value) {
        const detail = await api.get(`/content/${encodeURIComponent(articleId.value)}`)
        if (sequence !== loadSequence || disposed) return
        const version = versionId.value
          ? detail.versions?.find(item => item.id === versionId.value)
          : detail.versions?.find(item => item.id === detail.item?.currentVersionId) || detail.versions?.[0]
        if (!version) throw new Error(versionId.value ? '这篇文章中找不到指定版本，请从文章管理重新打开。' : '文章还没有可排版版本，请先在创作空间保存正文。')
        versionId.value = version.id
        title.value = version.title || detail.item?.title || ''
        body.value = version.body || ''
        const config = await api.get(`/wechat-mp/layout-studio/articles/${encodeURIComponent(articleId.value)}?versionId=${encodeURIComponent(version.id)}`)
        if (sequence !== loadSequence || disposed) return
        Object.assign(options, config.options || DEFAULT_OPTIONS)
        savedAt.value = config.saved ? 'saved' : ''
      } else {
        if (versionId.value) throw new Error('缺少文章 ID，请从文章管理重新进入排版。')
        title.value = '让好内容，拥有自己的表达'
        body.value = SAMPLE_BODY
      }
      baseline.value = snapshot()
      savedContent = contentSnapshot()
      ready.value = true
      await renderPreview()
    } catch (err) {
      if (sequence === loadSequence && !disposed) error.value = err.message || '文章载入失败'
    } finally {
      if (sequence === loadSequence && !disposed) loading.value = false
    }
  }

  async function save() {
    if (!articleId.value || !versionId.value || saving.value || !ready.value) return
    if (!title.value.trim() || !body.value.trim()) { error.value = '标题和正文不能为空。'; return }
    saving.value = true
    error.value = ''
    notice.value = ''
    let createdVersion = false
    try {
      if (contentSnapshot() !== savedContent) {
        const version = await api.post(`/content/${encodeURIComponent(articleId.value)}/versions`, {
          title: title.value, body: body.value, sourceType: 'manual',
        })
        versionId.value = version.id
        savedContent = contentSnapshot()
        createdVersion = true
        syncingQuery = true
        try { await router.replace({ query: { ...route.query, articleId: articleId.value, versionId: version.id } }) }
        finally { syncingQuery = false }
      }
      const result = await api.put(`/wechat-mp/layout-studio/articles/${encodeURIComponent(articleId.value)}?versionId=${encodeURIComponent(versionId.value)}`, { options: renderOptions() })
      savedAt.value = result.updatedAt || new Date().toISOString()
      baseline.value = snapshot()
      flash(createdVersion ? '正文已保存为新版本，排版配置已同步保存。' : '排版配置已保存到当前文章版本，发布时自动沿用。')
    } catch (err) {
      error.value = `${createdVersion ? '正文新版本已保存，但排版配置保存失败，请重试保存排版。' : '保存失败：'}${err.message || '请检查网络后重试'}`
    } finally { saving.value = false }
  }

  async function copyHtml() {
    if (!html.value || rendering.value) return
    try {
      await navigator.clipboard.writeText(html.value)
      flash('HTML 源码已复制。需要直接粘贴至公众号正文，请使用「复制到公众号」。')
    } catch { error.value = '浏览器未允许访问剪贴板，请允许剪贴板权限后重试。' }
  }

  async function copyRichText() {
    if (!html.value || rendering.value) return
    const doc = new DOMParser().parseFromString(html.value, 'text/html')
    const plain = doc.body.textContent || ''
    try {
      if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') throw new Error('当前浏览器不支持富文本剪贴板，请使用新版 Chrome 或 Edge。')
      await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([html.value], { type: 'text/html' }),
        'text/plain': new Blob([plain], { type: 'text/plain' }),
      })])
      flash('已复制带格式的全文，可直接粘贴到公众号编辑器。')
    } catch (err) { error.value = `复制失败：${err.message || '请允许浏览器剪贴板权限后重试。'}` }
  }

  // ---- SVG 动画卡候选面板：选段 → 生成 → 校验提示 → 插入正文 ----
  const cardPanel = reactive({
    open: false, text: '', kind: 'svg-bars', generating: false, error: '',
    result: null, title: '', lines: '', warnings: [], previewing: false,
    previewError: '', previewHtml: '', insertAt: null,
  })
  const cardPreviewDocument = computed(() => wrapDocument(cardPanel.previewHtml))

  function openCardPanel() {
    // 编辑态才有 #layout-body；取选段，插卡位置记在选段末尾。
    const area = document.getElementById('layout-body')
    let text = ''
    let insertAt = null
    if (area) {
      const start = area.selectionStart ?? 0
      const end = area.selectionEnd ?? 0
      if (end > start) { text = area.value.slice(start, end); insertAt = end }
    }
    cardPanel.open = true
    cardPanel.text = text.trim()
    cardPanel.insertAt = insertAt
    cardPanel.error = ''
    cardPanel.result = null
    cardPanel.title = ''
    cardPanel.lines = ''
    cardPanel.warnings = []
    cardPanel.previewHtml = ''
    cardPanel.previewError = ''
    if (!cardPanel.kind) cardPanel.kind = 'svg-bars'
  }

  function closeCardPanel() { cardPanel.open = false }

  function cardFence() {
    const lines = cardPanel.lines.split('\n').map(line => line.trim()).filter(Boolean)
    const head = `\`\`\`${cardPanel.kind} ${cardPanel.title.trim()}`.trimEnd()
    return `${head}\n${lines.join('\n')}\n\`\`\``
  }

  async function generateCard() {
    if (cardPanel.generating) return
    cardPanel.generating = true
    cardPanel.error = ''
    cardPanel.result = null
    try {
      const result = await api.post('/wechat-mp/layout-studio/svg-card/generate', {
        text: cardPanel.text, kind: cardPanel.kind, articleTitle: title.value.slice(0, 200),
      })
      if (cardPanel.open) {
        cardPanel.result = result.card || null
        cardPanel.title = result.card?.title || ''
        cardPanel.lines = (result.card?.lines || []).join('\n')
        await renderCardPreview()
      }
    } catch (err) {
      if (cardPanel.open) cardPanel.error = err.message || '生成失败，请稍后重试'
    } finally {
      cardPanel.generating = false
    }
  }

  async function renderCardPreview() {
    const fence = cardFence()
    if (!fence.trim() || cardPanel.previewing) return
    cardPanel.previewing = true
    cardPanel.previewError = ''
    cardPanel.warnings = []
    try {
      const result = await api.post('/wechat-mp/layout-studio/render', { body: fence, options: renderOptions() })
      if (!cardPanel.open) return
      cardPanel.previewHtml = result.html || ''
      cardPanel.warnings = [...(result.warnings || []), ...(result.issues || [])]
        .map(item => typeof item === 'string' ? item : item.message).filter(Boolean)
    } catch (err) {
      if (cardPanel.open) cardPanel.previewError = err.message || '卡片预览失败'
    } finally {
      cardPanel.previewing = false
    }
  }

  function insertCard() {
    if (!cardPanel.result) return
    const fence = cardFence()
    const at = cardPanel.insertAt ?? body.value.length
    const before = body.value.slice(0, at)
    const after = body.value.slice(at)
    const padBefore = before && !before.endsWith('\n\n') ? (before.endsWith('\n') ? '\n' : '\n\n') : ''
    body.value = `${before}${padBefore}${fence}\n\n${after}`
    cardPanel.open = false
    flash('动画卡已插入正文，预览中可查看效果；柱状图数值请核对是否与原文一致。')
  }

  watch([title, body, options], () => {
    if (!ready.value || loading.value) return
    ++renderSequence
    html.value = ''
    renderWarnings.value = []
    renderStats.value = null
    rendering.value = true
    notice.value = ''
    clearTimeout(renderTimer)
    renderTimer = setTimeout(renderPreview, 300)
  }, { deep: true, flush: 'sync' })

  watch(() => [route.query.articleId, route.query.versionId], () => {
    if (!syncingQuery) load()
  }, { immediate: true })

  const beforeUnload = event => {
    if (dirty.value && articleId.value) { event.preventDefault(); event.returnValue = '' }
  }
  window.addEventListener('beforeunload', beforeUnload)
  onBeforeUnmount(() => {
    disposed = true
    ++loadSequence
    ++renderSequence
    clearTimeout(renderTimer)
    clearTimeout(noticeTimer)
    window.removeEventListener('beforeunload', beforeUnload)
  })

  return { title, body, options, articleId, versionId, loading, saving, rendering, ready,
    error, previewError, renderWarnings, renderStats, notice, html, dirty, savedAt, previewDocument, load, save, renderPreview, copyHtml, copyRichText,
    cardPanel, cardPreviewDocument, openCardPanel, closeCardPanel, generateCard, renderCardPreview, insertCard }
}
