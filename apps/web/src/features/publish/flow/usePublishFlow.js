import { computed, reactive, ref } from 'vue'
import { api } from '../../../api/client'

const MODES = ['draft', 'manual', 'auto', 'record']

function makeKey(prefix = 'pub') {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

/**
 * Shared publish-flow kernel.
 * The two entry shells only provide defaults and render the result; all
 * validation, preview, per-account submission and partial-failure handling
 * lives behind this small interface.
 */
export function usePublishFlow(options = {}) {
  const draftAdapter = options.draftAdapter || null
  const step = ref(options.lockStep1 === true ? 2 : 1)
  const busy = ref(false)
  const error = ref('')
  const notice = ref('')
  const submitProgress = ref(null)
  const results = ref([])
  const previewByAccount = reactive({})
  const contentLoading = ref(false)
  const contents = ref([])
  const accounts = ref([])
  const capabilities = ref([])
  const initialized = ref(false)

  const form = reactive({
    contentId: options.contentId || '',
    manualEntry: false,
    title: options.title || '',
    body: options.body || '',
    scheduledAt: '',
    mode: options.lockedMode || 'manual',
    accountIds: [],
    remoteContentId: '',
    permalink: '',
    assetIds: [],
    themeId: 'ink-green',
    coverAssetId: '',
    inlineAssetIdsText: '',
  })

  const lockedStep = options.lockStep1 === true
  const lockedMode = options.lockedMode || ''
  const isDraftMode = computed(() => form.mode === 'draft')
  const selectedAccounts = computed(() =>
    accounts.value.filter((account) => form.accountIds.includes(account.id)),
  )
  const modeAvailability = computed(() => {
    const selected = selectedAccounts.value
    const draft = selected.length > 0 && selected.every((account) => account.platform === 'wechat_official')
    const auto = selected.length > 0 && selected.every((account) => {
      const cap = capabilities.value.find((item) => item.platform === account.platform)
      return Boolean(
        cap?.confirmed &&
        cap.publish &&
        account.autoPublishEnabled &&
        account.authSource === 'oauth',
      )
    })
    return { draft, manual: selected.length > 0, auto, record: selected.length > 0 }
  })
  const canContinue = computed(() => {
    if (step.value === 1) {
      return form.manualEntry
        ? Boolean(form.title.trim() && form.body.trim())
        : Boolean(form.contentId && form.title.trim() && form.body.trim())
    }
    if (step.value === 2) {
      return selectedAccounts.value.length > 0 && MODES.includes(form.mode) && modeAvailability.value[form.mode]
    }
    return !busy.value
  })
  const hasBlockingPreview = computed(() =>
    Object.values(previewByAccount).some((preview) =>
      (preview?.issues || []).some((issue) => issue.level === 'error'),
    ),
  )

  function reset(defaults = {}) {
    step.value = lockedStep ? 2 : 1
    busy.value = false
    error.value = ''
    notice.value = ''
    submitProgress.value = null
    results.value = []
    Object.keys(previewByAccount).forEach((key) => delete previewByAccount[key])
    form.contentId = defaults.contentId ?? options.contentId ?? ''
    form.manualEntry = defaults.manualEntry ?? false
    form.title = defaults.title ?? options.title ?? ''
    form.body = defaults.body ?? options.body ?? ''
    form.scheduledAt = defaults.scheduledAt || ''
    form.mode = lockedMode || defaults.mode || 'manual'
    form.accountIds = defaults.accountIds ? [...defaults.accountIds] : []
    form.remoteContentId = ''
    form.permalink = ''
    form.assetIds = defaults.assetIds ? [...defaults.assetIds] : []
    form.themeId = defaults.themeId || 'ink-green'
    form.coverAssetId = defaults.coverAssetId || ''
    form.inlineAssetIdsText = defaults.inlineAssetIds ? defaults.inlineAssetIds.join(',') : ''
  }

  async function load() {
    busy.value = true
    error.value = ''
    try {
      const [contentList, accountList, capabilityResult] = await Promise.all([
        api.get('/content'),
        api.get('/platform-accounts'),
        api.get('/platforms/capabilities'),
      ])
      contents.value = contentList
      accounts.value = accountList.filter((account) => account.status === 'connected')
      capabilities.value = capabilityResult.platforms || []
      if (!form.accountIds.length && options.accountId) form.accountIds = [options.accountId]
      initialized.value = true
    } catch (err) {
      error.value = err.message
    } finally {
      busy.value = false
    }
  }

  async function selectContent(contentId) {
    form.contentId = contentId
    form.manualEntry = !contentId
    if (!contentId) return
    contentLoading.value = true
    error.value = ''
    try {
      const detail = await api.get(`/content/${contentId}`)
      const item = detail.item
      const version = detail.versions?.[0]
      form.title = item.draftTitle || item.title || ''
      form.body = item.draftBody ?? version?.body ?? ''
    } catch (err) {
      error.value = err.message
    } finally {
      contentLoading.value = false
    }
  }

  function next() {
    if (step.value === 1 && !canContinue.value) {
      error.value = '请先填写标题和正文'
      return
    }
    if (step.value === 2 && !canContinue.value) {
      error.value = '请选择至少一个已连接账号，并选择发布方式'
      return
    }
    error.value = ''
    step.value = Math.min(3, step.value + 1)
  }

  function back() {
    if (step.value > (lockedStep ? 2 : 1)) step.value -= 1
  }

  async function preview() {
    error.value = ''
    const assetIds = form.mode === 'draft'
      ? [form.coverAssetId, ...form.inlineAssetIdsText.split(',').map((id) => id.trim())].filter(Boolean)
      : form.assetIds
    form.assetIds = assetIds
    for (const account of selectedAccounts.value) {
      try {
        previewByAccount[account.id] = form.mode === 'draft'
          ? await (draftAdapter?.preview
              ? draftAdapter.preview(account, form)
              : api.post('/wechat-mp/drafts/preview', {
              accountId: account.id,
              contentItemId: form.contentId || null,
              title: form.title,
              body: form.body,
              assetIds: form.assetIds,
              themeId: form.themeId,
            }))
          : await api.post('/publishing/preview', {
              accountId: account.id,
              title: form.title,
              body: form.body,
              assetIds: form.assetIds,
              scheduledAt: form.scheduledAt ? new Date(form.scheduledAt).toISOString() : null,
            })
      } catch (err) {
        previewByAccount[account.id] = { issues: [{ level: 'error', message: err.message }] }
      }
    }
  }

  async function submit() {
    if (busy.value || !selectedAccounts.value.length) return
    if (hasBlockingPreview.value) {
      error.value = '预检存在阻塞项，请修正内容或目标账号后再提交'
      return
    }
    busy.value = true
    error.value = ''
    notice.value = ''
    results.value = []
    if (form.mode === 'draft') {
      form.assetIds = [form.coverAssetId, ...form.inlineAssetIdsText.split(',').map((id) => id.trim())].filter(Boolean)
    }
    submitProgress.value = { current: 0, total: selectedAccounts.value.length }

    for (const account of selectedAccounts.value) {
      submitProgress.value.current += 1
      try {
        let job
        if (form.mode === 'draft') {
          if (account.platform !== 'wechat_official') {
            throw new Error('草稿箱推送仅支持公众号账号')
          }
          job = await (draftAdapter?.submit
            ? draftAdapter.submit(account, form)
            : api.post('/wechat-mp/drafts', {
            accountId: account.id,
            contentItemId: form.contentId || null,
            idempotencyKey: makeKey('wx'),
            title: form.title,
            body: form.body,
            assetIds: form.assetIds,
            themeId: form.themeId,
            }))
        } else {
          job = await api.post('/publishing/jobs', {
            accountId: account.id,
            contentItemId: form.contentId || null,
            idempotencyKey: makeKey('pub'),
            title: form.title,
            body: form.body,
            assetIds: form.assetIds,
            scheduledAt: form.scheduledAt ? new Date(form.scheduledAt).toISOString() : null,
            reviewed: true,
            mode: form.mode === 'record' ? 'manual' : form.mode,
          })
          if (form.mode === 'record') {
            job = await api.post(`/publishing/jobs/${job.id}/record-manual-result`, {
              remoteContentId: form.remoteContentId || null,
              permalink: form.permalink || null,
            })
          }
        }
        if (!job) throw new Error('发布接口未返回任务，请检查内容后重试')
        results.value.push({ account, ok: true, job })
      } catch (err) {
        results.value.push({ account, ok: false, error: err.message })
      }
    }

    busy.value = false
    submitProgress.value = null
    const failed = results.value.filter((result) => !result.ok).length
    notice.value = failed
      ? `已完成 ${results.value.length - failed}/${results.value.length} 个账号，${failed} 个失败，可单独重试。`
      : `已完成 ${results.value.length} 个账号的提交。`
  }

  async function retryResult(result) {
    if (!result?.account || busy.value) return
    busy.value = true
    try {
      let job
      if (result.job?.id) {
        if (!['failed', 'needs_attention'].includes(result.job.status)) return
        job = await api.post(`/publishing/jobs/${result.job.id}/retry`)
      } else if (form.mode === 'draft' && draftAdapter?.submit) {
        job = await draftAdapter.submit(result.account, form)
      } else {
        job = await api.post('/publishing/jobs', {
          accountId: result.account.id,
          contentItemId: form.contentId || null,
          idempotencyKey: makeKey('pub'),
          title: form.title,
          body: form.body,
          assetIds: form.assetIds,
          scheduledAt: form.scheduledAt ? new Date(form.scheduledAt).toISOString() : null,
          reviewed: true,
          mode: form.mode === 'record' ? 'manual' : form.mode,
        })
        if (form.mode === 'record') {
          job = await api.post(`/publishing/jobs/${job.id}/record-manual-result`, {
            remoteContentId: form.remoteContentId || null,
            permalink: form.permalink || null,
          })
        }
      }
      if (!job) throw new Error('发布接口未返回任务')
      result.ok = true
      result.job = job
      result.error = ''
    } catch (err) {
      result.error = err.message
    } finally {
      busy.value = false
    }
  }

  return {
    step,
    busy,
    error,
    notice,
    form,
    contents,
    accounts,
    capabilities,
    initialized,
    contentLoading,
    selectedAccounts,
    modeAvailability,
    isDraftMode,
    canContinue,
    hasBlockingPreview,
    previewByAccount,
    submitProgress,
    results,
    reset,
    load,
    selectContent,
    next,
    back,
    preview,
    submit,
    retryResult,
    setMode(mode) {
      if (MODES.includes(mode) && (!lockedMode || mode === lockedMode)) form.mode = mode
    },
    toggleAccount(id) {
      const index = form.accountIds.indexOf(id)
      if (index >= 0) form.accountIds.splice(index, 1)
      else form.accountIds.push(id)
    },
    toggleManual(value) {
      form.manualEntry = value
      if (value) form.contentId = ''
    },
  }
}
