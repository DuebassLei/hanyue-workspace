/**
 * 生图供应商列表。
 *
 * 与图片生成页共用 `/ai/image-provider/status`：modelId 为空表示环境变量供应商，
 * 有值表示连接目录里带 image.generate 的模型。服务端已把「全局默认生图模型」排在
 * 第一位，所以未选时取列表第一项，就等于跟随用户在模型管理页设置的默认。
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
