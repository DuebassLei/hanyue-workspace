<script setup>
import { NButton } from 'naive-ui'
import StatusTag from '../../../components/StatusTag.vue'
import { canRetry, canCancel, canDelete, jobTargetLabel } from './jobActions'
import { formatJobTime, actualPushTime, isManualRecord } from './draftQueue'

// 单条发布任务：展示 + 上下文操作按钮。
// 操作可用性判定来自 jobActions（与页面 deleteJob 守卫共用）；
// 实际请求全部交还页面处理。
defineProps({
  job: { type: Object, required: true },
  accountLabel: { type: String, default: '未知账号' },
  deleting: { type: Boolean, default: false },
  readonly: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})
defineEmits(['retry', 'cancel', 'delete'])
</script>

<template>
  <div class="list-row" role="group" :aria-label="`${job.contentTitle || '发布任务'}，${job.status}`">
    <div class="list-main">
      <div class="list-title">
        <router-link v-if="job.contentItemId" class="job-article-link" :to="{ name: 'articles', query: { id: job.contentItemId } }" @click.stop>
          {{ job.contentTitle || '未命名稿件' }}
        </router-link>
        <span v-else>{{ job.contentTitle || '未关联稿件' }}</span>
        <span class="job-title-divider">·</span>
        {{ job.platformLabel }} · {{ jobTargetLabel(job) }}
      </div>
      <div class="list-meta">
        账号 {{ accountLabel }} · 请求号 {{ (job.requestId || job.id).slice(0, 8) }} · 尝试 {{ job.attemptCount || 0 }} 次
        <span> · 计划推送 {{ job.scheduledAt ? formatJobTime(job.scheduledAt) : '立即（入队后执行）' }}</span>
        <span> · 实际推送 {{ !isManualRecord(job) && job.remoteContentId ? formatJobTime(actualPushTime(job)) : '—' }}</span>
        <span v-if="job.remoteContentId"> · 远端 ID {{ job.remoteContentId }}</span>
      </div>
      <div v-if="job.errorMessage" class="list-meta" style="color: var(--color-danger)">
        {{ job.errorCode }}：{{ job.errorMessage }}
      </div>
    </div>
    <span v-if="isManualRecord(job)" class="list-meta">历史人工记录 · 非真实推送</span>
    <span v-else-if="job.status === 'succeeded' && !job.remoteContentId" class="list-meta">未确认推送结果（缺少远端 ID）</span>
    <StatusTag v-else :status="job.status" />
    <n-button v-if="!readonly && canRetry(job)" size="small" quaternary :disabled="busy" @click.stop="$emit('retry')">重试推送</n-button>
    <n-button v-if="!readonly && canCancel(job)" size="small" quaternary :disabled="busy" @click.stop="$emit('cancel')">取消</n-button>
    <n-button
      v-if="!readonly && canDelete(job)"
      size="small"
      type="error"
      quaternary
      :loading="deleting"
      @click.stop="$emit('delete')"
    >
      删除
    </n-button>
  </div>
</template>

<style scoped>
.list-row {
  flex-wrap: wrap;
  align-items: flex-start;
}

.list-row > .btn {
  margin-top: 2px;
}

.job-article-link {
  color: var(--color-text-primary);
  text-decoration: none;
}

.job-article-link:hover {
  color: var(--color-accent);
  text-decoration: underline;
}

.job-title-divider {
  margin: 0 4px;
  color: var(--color-text-muted);
}
</style>
