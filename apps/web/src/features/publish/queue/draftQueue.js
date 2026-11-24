export const draftPlatformOptions = [
  { value: 'wechat_official', label: '微信公众号 · 草稿箱' },
  { value: 'xiaohongshu', label: '小红书（暂未接入）', disabled: true },
  { value: 'douyin', label: '抖音（暂未接入）', disabled: true },
  { value: 'zhihu', label: '知乎（暂未接入）', disabled: true },
]

export function draftSubmission({ platform, accountId, mode, scheduleAt }, now = Date.now()) {
  if (platform !== 'wechat_official') throw new Error('该平台尚未接入草稿推送')
  if (!accountId) throw new Error('请选择已绑定并验证的公众号账号')
  if (mode === 'scheduled' && (!Number.isFinite(scheduleAt) || scheduleAt <= now)) {
    throw new Error('请选择一个晚于当前时间的计划推送时间')
  }
  return {
    platforms: [platform],
    accountId,
    scheduledAt: mode === 'scheduled' ? new Date(scheduleAt).toISOString() : null,
  }
}

export const formatJobTime = (value) => value
  ? new Date(value).toLocaleString('zh-CN', { hour12: false })
  : '—'

export function actualPushTime(job) {
  if (!job) return null
  // 失败/取消任务也会写 completedAt，那不是「推送时间」；只有真进过草稿箱的任务才回填完成时间
  if (job.pushedAt || job.actualPushedAt) return job.pushedAt || job.actualPushedAt
  return ['drafted', 'succeeded'].includes(job.status) ? job.completedAt || null : null
}

export function isManualRecord(job) {
  return job.mode === 'manual' || job.resultSource === 'manual' || job.target === 'manual'
}
