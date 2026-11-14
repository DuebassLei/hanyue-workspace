export function isGenerationActive(shot, stage) {
  const status = shot?.[`${stage}Status`]
  return ['queued', 'generating'].includes(status)
}

export function hasActiveGeneration(shot) {
  return isGenerationActive(shot, 'sketch') || isGenerationActive(shot, 'final')
}

export function applyGenerationQueued(shot, stage, response) {
  if (!['sketch', 'final'].includes(stage) || !response?.jobId) return shot
  return {
    ...shot,
    [`${stage}Status`]: response.status === 'generating' ? 'generating' : 'queued',
    [`${stage}JobId`]: response.jobId,
    ...(stage === 'final' ? { finalAssetIds: [] } : {}),
  }
}

export function generationStatusLabel(shot) {
  for (const [stage, label] of [['sketch', '草图'], ['final', '正式图']]) {
    const status = shot?.[`${stage}Status`]
    if (status === 'queued') return `${label}排队中`
    if (status === 'generating') return `${label}绘制中`
  }
  if (shot?.sketchStatus === 'failed' || shot?.finalStatus === 'failed') return '生成失败'
  if (shot?.sketchStatus === 'succeeded') return '草图完成'
  return '待构图'
}
