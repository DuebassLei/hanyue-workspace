/**
 * 发布任务行操作可用性判定（纯函数）。
 *
 * JobRow（按钮显隐）与 PublishingView（deleteJob 提交守卫）共用同一份规则，
 * 避免两处各写一份谓词日后漂移。
 */

export const canRecord = (job) =>
  job.target !== 'draft' && ['approved', 'queued', 'needs_attention', 'failed', 'publishing'].includes(job.status)

export const canRetry = (job) => {
  if (job.target !== 'draft' || job.mode === 'manual') return false
  // 后端已经按 errorCode / remoteContentId 判定过；以前端再写一份只会两边漂移
  // （例如 draft_result_unknown 必须禁止重试，避免重复进草稿箱）
  if (typeof job.canRetry === 'boolean') return job.canRetry
  return ['failed', 'needs_attention'].includes(job.status) && !job.remoteContentId
}

export const canCancel = (job) =>
  ['pending_review', 'approved', 'queued', 'needs_attention'].includes(job.status)
  || (job.target === 'draft' && job.status === 'failed')

export const canDelete = (job) =>
  ['succeeded', 'cancelled', 'failed', 'needs_attention'].includes(job.status)

export const jobTargetLabel = (job) => (job.target === 'draft' ? '草稿箱' : '平台发布')
