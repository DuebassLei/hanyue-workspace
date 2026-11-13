/**
 * 发布任务行操作可用性判定（纯函数）。
 *
 * JobRow（按钮显隐）与 PublishingView（deleteJob 提交守卫）共用同一份规则，
 * 避免两处各写一份谓词日后漂移。
 */

export const canRecord = (job) =>
  ['approved', 'queued', 'needs_attention', 'failed', 'publishing'].includes(job.status)

export const canRetry = (job) =>
  ['failed', 'needs_attention'].includes(job.status) && !job.remoteContentId

export const canCancel = (job) =>
  ['pending_review', 'approved', 'queued', 'needs_attention'].includes(job.status)

export const canDelete = (job) =>
  ['succeeded', 'cancelled', 'failed', 'needs_attention'].includes(job.status)

export const jobTargetLabel = (job) => (job.target === 'draft' ? '草稿箱' : '平台发布')
