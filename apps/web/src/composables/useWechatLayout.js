import { computed, ref } from 'vue'
import { api } from '../api/client'

/**
 * 微信排版管理：主题 CRUD、展示偏好（默认/收藏/隐藏）、token schema、
 * 编辑器实时预览（raw tokens 走服务端同一渲染函数）。
 * 只在「微信排版」页使用；发布抽屉仍用 useWechatPublish。
 */
export function useWechatLayout() {
  const loading = ref(false)
  const error = ref('')
  const themes = ref({ presets: [], custom: [], defaultThemeId: 'ink-green', favoritePresetIds: [], hiddenPresetIds: [] })
  const schema = ref([])

  const allThemes = computed(() => [
    ...(themes.value.presets || []),
    ...(themes.value.custom || []),
  ])

  // 发布/预览场景展示的预设：过滤隐藏、收藏置顶
  const visiblePresets = computed(() => {
    const list = (themes.value.presets || []).filter((t) => !t.hidden)
    return [...list].sort((a, b) => Number(b.favorite) - Number(a.favorite))
  })

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const [themeData, schemaData] = await Promise.all([
        api.get('/wechat-mp/themes'),
        api.get('/wechat-mp/themes/schema'),
      ])
      themes.value = themeData
      schema.value = schemaData.fields || []
    } catch (err) {
      error.value = err.message
    } finally {
      loading.value = false
    }
  }

  /** 新建（baseThemeId）或编辑（editingId → PATCH）主题；失败抛错由调用方展示 */
  async function saveTheme({ name, tokens, baseThemeId = '', editingId = '' }) {
    if (editingId) {
      const updated = await api.patch(`/wechat-mp/themes/${editingId}`, { name, tokens })
      themes.value = {
        ...themes.value,
        custom: themes.value.custom.map((t) => (t.id === updated.id ? updated : t)),
      }
      return updated
    }
    const created = await api.post('/wechat-mp/themes', { name, tokens, baseThemeId })
    themes.value = { ...themes.value, custom: [...themes.value.custom, created] }
    return created
  }

  async function deleteTheme(id) {
    await api.del(`/wechat-mp/themes/${id}`)
    themes.value = { ...themes.value, custom: themes.value.custom.filter((t) => t.id !== id) }
  }

  /** 保存偏好（PUT 全量替换），响应即最新列表，直接整体替换状态 */
  async function setPrefs(prefs) {
    const data = await api.put('/wechat-mp/themes/preferences', {
      defaultThemeId: themes.value.defaultThemeId,
      favoritePresetIds: themes.value.favoritePresetIds,
      hiddenPresetIds: themes.value.hiddenPresetIds,
      ...prefs,
    })
    themes.value = data
    return data
  }

  function toggleFavorite(id) {
    const list = themes.value.favoritePresetIds.includes(id)
      ? themes.value.favoritePresetIds.filter((x) => x !== id)
      : [...themes.value.favoritePresetIds, id]
    return setPrefs({ favoritePresetIds: list })
  }

  function toggleHidden(id) {
    const list = themes.value.hiddenPresetIds.includes(id)
      ? themes.value.hiddenPresetIds.filter((x) => x !== id)
      : [...themes.value.hiddenPresetIds, id]
    return setPrefs({ hiddenPresetIds: list })
  }

  function setDefault(id) {
    return setPrefs({ defaultThemeId: id })
  }

  // ── 编辑器实时预览：raw tokens + 防抖 + 竞态防护 ──────────────────────────
  const previewResult = ref(null)
  const previewBusy = ref(false)
  const previewError = ref('')
  let previewTimer = null
  let previewRequestId = 0

  async function renderPreview(body, tokens, themeId = '') {
    const requestId = ++previewRequestId
    if (!body.trim()) {
      previewResult.value = null
      previewBusy.value = false
      return
    }
    previewBusy.value = true
    previewError.value = ''
    try {
      const result = await api.post('/wechat-mp/drafts/render', {
        body,
        themeId: themeId || undefined,
        tokens: tokens && Object.keys(tokens).length ? tokens : undefined,
      })
      if (requestId === previewRequestId) previewResult.value = result
    } catch (err) {
      if (requestId !== previewRequestId) return
      previewError.value = err.message
      previewResult.value = null
    } finally {
      if (requestId === previewRequestId) previewBusy.value = false
    }
  }

  function schedulePreview(body, tokens, themeId = '', delay = 300) {
    clearTimeout(previewTimer)
    previewTimer = setTimeout(() => renderPreview(body, tokens, themeId), delay)
  }

  // ── 导入 / 导出 ────────────────────────────────────────────────────────────

  function exportTheme(theme) {
    const payload = {
      app: 'hanyue-wechat-theme',
      version: 1,
      name: theme.name,
      tokens: theme.tokens,
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `wechat-theme-${theme.name || theme.id}.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  /** 解析导出文件并走 create 接口导入；后端 normalize 兜底校验（缺键补默认、未知键 422） */
  async function importTheme(file) {
    let parsed
    try {
      parsed = JSON.parse(await file.text())
    } catch {
      throw new Error('文件不是合法的 JSON')
    }
    if (!parsed || typeof parsed !== 'object' || !parsed.tokens || typeof parsed.tokens !== 'object') {
      throw new Error('缺少 tokens 字段，看起来不是主题导出文件')
    }
    const name = String(parsed.name || '').trim()
    if (!name || name.length > 60) {
      throw new Error('主题名缺失或超过 60 字')
    }
    const created = await api.post('/wechat-mp/themes', { name, tokens: parsed.tokens })
    themes.value = { ...themes.value, custom: [...themes.value.custom, created] }
    return created
  }

  return {
    loading,
    error,
    themes,
    schema,
    allThemes,
    visiblePresets,
    load,
    saveTheme,
    deleteTheme,
    setPrefs,
    toggleFavorite,
    toggleHidden,
    setDefault,
    previewResult,
    previewBusy,
    previewError,
    renderPreview,
    schedulePreview,
    exportTheme,
    importTheme,
  }
}
