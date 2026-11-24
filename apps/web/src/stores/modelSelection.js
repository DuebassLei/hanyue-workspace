import { defineStore } from 'pinia'
import { api } from '../api/client'

/**
 * 模型选择是**会话级**状态：小说 / 分镜等页共用，
 * 刷新页面回到「自动」。跨设备持久化的「默认模型」存在服务端 /preferences。
 */
export const useModelSelectionStore = defineStore('modelSelection', {
  state: () => ({
    selectedModelId: '',
    catalog: [],
    defaultModelId: null,
    defaultModelLabel: null,
    defaultModelIgnored: null,
    loading: false,
    loaded: false,
    error: '',
  }),
  getters: {
    /** 只列 enabled 且带文本能力的模型；图像模型不进这套选择 */
    textModels: (state) => state.catalog.filter((item) => item.enabled && item.isTextModel),
    options: (state) => {
      const auto = state.defaultModelIgnored
        ? `自动（默认失效：${state.defaultModelIgnored.message}）`
        : state.defaultModelLabel
          ? `自动（默认：${state.defaultModelLabel}）`
          : '自动（首个失败自动降级）'
      const models = state.catalog
        .filter((item) => item.enabled && item.isTextModel && item.source !== 'other')
        .map((item) => ({
          value: item.id,
          label: `${item.source === 'platform' ? '平台提供' : (item.connectionName || '我的模型')} · ${item.displayName || item.modelCode}${item.isDefault ? '（默认）' : ''}`,
        }))
      return [{ value: '', label: auto }, ...models]
    },
    /** 不选择时不传 modelId，让服务端按「默认优先 + 降级」解析 */
    requestModelId: (state) => state.selectedModelId || undefined,
  },
  actions: {
    async load(force = false) {
      if (this.loaded && !force) return
      this.loading = true
      this.error = ''
      try {
        const [catalog, pref] = await Promise.all([
          api.get('/models/catalog'),
          api.get('/preferences'),
        ])
        this.catalog = Array.isArray(catalog) ? catalog : []
        this.defaultModelId = pref.defaultTextModelId || null
        this.defaultModelLabel = pref.defaultTextModelLabel || null
        this.defaultModelIgnored = pref.defaultTextModelIgnored || null
        // 选中项被删 / 停用 / 改成图像模型时，静默回落到「自动」，不留无效 id
        if (this.selectedModelId && !this.textModels.some((item) => item.id === this.selectedModelId)) {
          this.selectedModelId = ''
        }
        this.loaded = true
      } catch (err) {
        this.error = `模型列表读取失败：${err.message}`
      } finally {
        this.loading = false
      }
    },
    async setDefault(modelId) {
      await api.patch('/preferences', { defaultTextModelId: modelId || '' })
      await this.load(true)
    },
  },
})
