/**
 * 生图供应商列表。
 *
 * 与图片生成页共用 `/ai/image-provider/status`：modelId 为空表示环境变量供应商，
 * 有值表示连接目录里带 image.generate 的模型。未选时把空串传给后端，由后端按
 * 环境变量优先、否则取第一个可用连接解析。
 */

export function providerOptions(providers) {
  return (providers || []).map((item) => ({
    label: item.label,
    value: item.modelId || '',
  }))
}

/** 当前选中项不在列表里时回落到第一项，避免留下失效 id。 */
export function resolveSelectedModelId(providers, selectedModelId) {
  const list = providers || []
  if (list.some((item) => (item.modelId || '') === selectedModelId)) return selectedModelId
  return list[0] ? list[0].modelId || '' : ''
}
