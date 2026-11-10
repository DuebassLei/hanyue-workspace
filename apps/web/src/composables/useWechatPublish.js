import { computed, reactive, ref } from 'vue'
import { api } from '../api/client'

/**
 * 公众号草稿箱推送能力：排版渲染、校验预览、推送到草稿箱。
 * 在创作空间右侧「发布」面板中使用。
 */
export function useWechatPublish() {
  // 公众号配置状态
  const wechat = ref(null)
  const wechatError = ref('')
  const wechatNotice = ref('')

  // 推送表单
  const wechatForm = reactive({
    title: '',
    body: '',
    digest: '',
    author: '',
    contentSourceUrl: '',
    coverId: '',
    inlineIds: [],
    needOpenComment: false,
  })
  const wechatAccountId = ref('')
  const wechatKey = ref('')
  const wechatPreview = ref(null)
  const wechatPushError = ref('')

  // 可用的公众号账号（已连接）
  const wechatAccounts = ref([])
  const pushAccountOptions = computed(() =>
    wechatAccounts.value.map((a) => ({
      id: a.id,
      label: a.hasOwnCredential
        ? `${a.displayName}（${a.appIdMasked}）`
        : `${a.displayName}（默认凭据）`,
    })),
  )

  // 排版主题
  const themes = ref({ presets: [], custom: [], defaultThemeId: 'ink-green' })
  const wechatThemeId = ref('ink-green')
  const allThemes = computed(() => [
    ...(themes.value.presets || []),
    ...(themes.value.custom || []),
  ])

  // 实时排版预览
  const layoutResult = ref(null)
  const layoutBusy = ref(false)
  const layoutError = ref('')
  let layoutTimer = null
  let layoutRequestId = 0
  const bodyInput = ref(null)

  const layoutHtml = computed(() => {
    if (!layoutResult.value?.html) return ''
    return `<div style="padding:16px 18px;background:#ffffff;color:#333;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;">${layoutResult.value.html}</div>`
  })

  // 素材库（封面图 / 正文内图）
  const assets = ref([])
  const imageAssets = computed(() =>
    assets.value.filter((a) => (a.mimeType || '').startsWith('image/')),
  )
  const inlineOptions = computed(() =>
    imageAssets.value.filter((a) => a.id !== wechatForm.coverId),
  )

  // ── 加载 ──────────────────────────────────────────────────────────────────

  async function loadWechat() {
    wechatError.value = ''
    try {
      const [status, accts] = await Promise.all([
        api.get('/wechat-mp/status'),
        api.get('/platform-accounts'),
      ])
      wechat.value = status
      wechatAccounts.value = accts.filter(
        (a) => a.platform === 'wechat_official' && a.status === 'connected',
      )
      if (!wechatAccountId.value) {
        wechatAccountId.value =
          wechat.value?.account?.id || wechatAccounts.value[0]?.id || ''
      }
    } catch (err) {
      wechatError.value = err.message
    }
  }

  async function loadThemes() {
    try {
      themes.value = await api.get('/wechat-mp/themes')
      wechatThemeId.value = themes.value.defaultThemeId || 'ink-green'
    } catch (err) {
      layoutError.value = err.message
    }
  }

  async function loadAssets() {
    try {
      assets.value = await api.get('/assets?limit=100')
    } catch (err) {
      wechatPushError.value = err.message
    }
  }

  // ── 排版渲染 ──────────────────────────────────────────────────────────────

  async function renderLayout() {
    const requestId = ++layoutRequestId
    if (!wechatForm.body.trim()) {
      layoutResult.value = null
      layoutBusy.value = false
      return
    }
    layoutBusy.value = true
    layoutError.value = ''
    try {
      const result = await api.post('/wechat-mp/drafts/render', {
        title: wechatForm.title,
        body: wechatForm.body,
        themeId: wechatThemeId.value,
        assetIds: wechatForm.inlineIds,
      })
      if (requestId === layoutRequestId) layoutResult.value = result
    } catch (err) {
      if (requestId !== layoutRequestId) return
      layoutError.value = err.message
      layoutResult.value = null
    } finally {
      if (requestId === layoutRequestId) layoutBusy.value = false
    }
  }

  function scheduleLayout() {
    clearTimeout(layoutTimer)
    layoutTimer = setTimeout(renderLayout, 300)
  }

  /** 在光标处插入排版语法；没有焦点时追加到末尾 */
  function insertSnippet(snippet) {
    const el = bodyInput.value
    if (!el) {
      wechatForm.body += snippet
      scheduleLayout()
      return
    }
    const start = el.selectionStart ?? wechatForm.body.length
    const end = el.selectionEnd ?? start
    wechatForm.body =
      wechatForm.body.slice(0, start) + snippet + wechatForm.body.slice(end)
    requestAnimationFrame(() => {
      el.focus()
      const caret = start + snippet.length
      el.setSelectionRange(caret, caret)
    })
    scheduleLayout()
  }

  // ── 预览 / 推送 ────────────────────────────────────────────────────────────

  function wechatPayload() {
    return {
      accountId: wechatAccountId.value,
      title: wechatForm.title,
      body: wechatForm.body,
      digest: wechatForm.digest,
      author: wechatForm.author,
      contentSourceUrl: wechatForm.contentSourceUrl,
      // 顺序有意义：第一张是封面，其余按选中顺序进入正文
      assetIds: [wechatForm.coverId, ...wechatForm.inlineIds].filter(Boolean),
      needOpenComment: wechatForm.needOpenComment,
      themeId: wechatThemeId.value,
    }
  }

  async function previewWechatDraft() {
    wechatPushError.value = ''
    wechatPreview.value = null
    try {
      wechatPreview.value = await api.post(
        '/wechat-mp/drafts/preview',
        wechatPayload(),
      )
    } catch (err) {
      wechatPushError.value = err.message
    }
  }

  /**
   * 推送到草稿箱，返回创建的 job 对象；失败时返回 null。
   */
  async function pushWechatDraft() {
    wechatPushError.value = ''
    try {
      const job = await api.post('/wechat-mp/drafts', {
        ...wechatPayload(),
        idempotencyKey: wechatKey.value,
      })
      return job
    } catch (err) {
      wechatPushError.value = err.message
      return null
    }
  }

  // ── 初始化表单 ────────────────────────────────────────────────────────────

  /**
   * 用稿件内容初始化发布表单，并触发排版渲染。
   * @param {{ title?: string, body?: string }} defaults
   */
  function initForm(defaults = {}) {
    clearTimeout(layoutTimer)
    layoutRequestId += 1
    layoutBusy.value = false
    wechatForm.title = defaults.title || ''
    wechatForm.body = defaults.body || ''
    wechatForm.digest = ''
    wechatForm.author = wechat.value?.authorDefault || ''
    wechatForm.contentSourceUrl = ''
    wechatForm.coverId = ''
    wechatForm.inlineIds = []
    wechatForm.needOpenComment = false
    wechatPreview.value = null
    wechatPushError.value = ''
    layoutResult.value = null
    layoutError.value = ''
    wechatKey.value = `wx-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    if (defaults.body) scheduleLayout()
  }

  return {
    // 状态
    wechat,
    wechatError,
    wechatNotice,
    wechatForm,
    wechatAccountId,
    wechatKey,
    wechatPreview,
    wechatPushError,
    wechatAccounts,
    pushAccountOptions,
    themes,
    wechatThemeId,
    allThemes,
    layoutResult,
    layoutBusy,
    layoutError,
    layoutHtml,
    assets,
    imageAssets,
    inlineOptions,
    bodyInput,
    // 动作
    loadWechat,
    loadThemes,
    loadAssets,
    renderLayout,
    scheduleLayout,
    insertSnippet,
    wechatPayload,
    previewWechatDraft,
    pushWechatDraft,
    initForm,
  }
}
