import { computed, reactive, ref } from 'vue'
import { api } from '../api/client'

/** 公众号发布表单、提交校验与草稿箱推送；排版由独立工作台维护。 */
export function useWechatPublish() {
  const wechat = ref(null)
  const wechatError = ref('')
  const wechatNotice = ref('')
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
  const wechatContentId = ref('')
  const wechatVersionId = ref('')
  const wechatKey = ref('')
  const wechatPreview = ref(null)
  const wechatPushError = ref('')
  const wechatAccounts = ref([])
  const pushAccountOptions = computed(() =>
    wechatAccounts.value.map((a) => ({
      id: a.id,
      label: a.hasOwnCredential
        ? `${a.displayName}（${a.appIdMasked}）`
        : `${a.displayName}（默认凭据）`,
    })),
  )
  const assets = ref([])
  const imageAssets = computed(() =>
    assets.value.filter((a) => (a.mimeType || '').startsWith('image/')),
  )

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

  async function loadAssets() {
    try {
      assets.value = await api.get('/assets?limit=100')
    } catch (err) {
      wechatPushError.value = err.message
    }
  }

  function wechatPayload() {
    return {
      accountId: wechatAccountId.value,
      contentItemId: wechatContentId.value || null,
      versionId: wechatVersionId.value || null,
      title: wechatForm.title,
      body: wechatForm.body,
      digest: wechatForm.digest,
      author: wechatForm.author,
      contentSourceUrl: wechatForm.contentSourceUrl,
      // 顺序有意义：第一张是封面，其余按选中顺序进入正文。
      assetIds: [wechatForm.coverId, ...wechatForm.inlineIds].filter(Boolean),
    }
  }

  async function previewWechatDraft() {
    wechatPushError.value = ''
    wechatPreview.value = null
    try {
      wechatPreview.value = await api.post('/wechat-mp/drafts/preview', wechatPayload())
    } catch (err) {
      wechatPushError.value = err.message
    }
  }

  /** 推送到草稿箱，返回创建的任务；失败时返回 null。 */
  async function pushWechatDraft() {
    wechatPushError.value = ''
    try {
      return await api.post('/wechat-mp/drafts', {
        ...wechatPayload(),
        needOpenComment: wechatForm.needOpenComment,
        idempotencyKey: wechatKey.value,
      })
    } catch (err) {
      wechatPushError.value = err.message
      return null
    }
  }

  /**
   * 仅初始化本次发布输入，不读取主题或触发排版。
   * @param {{ title?: string, body?: string, contentId?: string, versionId?: string }} defaults
   */
  function initForm(defaults = {}) {
    wechatContentId.value = defaults.contentId || ''
    wechatVersionId.value = defaults.versionId || ''
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
    wechatKey.value = `wx-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  }

  return {
    wechat,
    wechatError,
    wechatNotice,
    wechatForm,
    wechatAccountId,
    wechatVersionId,
    wechatKey,
    wechatPreview,
    wechatPushError,
    wechatAccounts,
    pushAccountOptions,
    assets,
    imageAssets,
    loadWechat,
    loadAssets,
    wechatPayload,
    previewWechatDraft,
    pushWechatDraft,
    initForm,
  }
}
