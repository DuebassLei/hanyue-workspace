<script setup>
import { computed, ref, watch } from 'vue'
import { NAlert, NButton, NDatePicker, NForm, NFormItem, NInput, NModal, NSpace, NTag } from 'naive-ui'
import JobTimeline from './JobTimeline.vue'
import { canRecord, canRetry } from './jobActions'

const props = defineProps({
  job: { type: Object, default: null },
  detail: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'approve', 'cancel', 'retry', 'record', 'delete', 'save'])

const scheduledAt = ref(null)
const title = ref('')
const body = ref('')
const errorAdvice = {
  timeout: '结果未知。请到平台后台核实：已发布后回填结果，未发布才可重试。',
  network: '结果未知。请先到平台后台核实，不要盲目重发。',
  rate_limited: '平台限流，稍后可重试。',
  server_error: '平台服务异常，可稍后重试。',
  auth_expired: '授权失效，请到账号侧栏重新验证或修改凭据。',
  forbidden: '授权或权限不足，请到账号侧栏重新验证。',
  validation_failed: '内容未通过平台校验，修改内容后重建任务。',
  content_rejected: '内容被平台拒绝，修改内容后重建任务。',
  adapter_not_implemented: '该平台暂未实现自动发布，请手动发布后回填。',
}
const advice = computed(() => errorAdvice[props.detail?.errorCode] || '')

function toLocalInput(stamp) {
  if (!stamp) return null
  const date = new Date(stamp)
  if (Number.isNaN(date.getTime())) return null
  const pad = (value) => String(value).padStart(2, '0')
  return new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate(),
    date.getHours(),
    date.getMinutes(),
  ).getTime()
}

function toLocalStamp(value) {
  if (!value) return null
  const date = new Date(value)
  const pad = (part) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function prepare() {
  scheduledAt.value = toLocalInput(props.detail?.scheduledAt)
  title.value = props.detail?.contentTitle || ''
  body.value = props.detail?.payloadSnapshot?.body || ''
}

watch(() => props.detail, prepare, { immediate: true })

function save() {
  emit('save', {
    scheduledAt: toLocalStamp(scheduledAt.value),
    title: title.value,
    body: body.value,
  })
}
</script>

<template>
  <n-modal
    :show="!!job"
    preset="card"
    :title="job?.contentTitle || '发布任务详情'"
    style="width: min(640px, calc(100vw - 32px))"
    :mask-closable="!busy"
    @update:show="(value) => { if (!value) emit('close') }"
  >
    <div v-if="job" class="detail-stack">
      <p class="page-sub" style="margin: 0">查看任务状态、尝试记录与可用操作。</p>
      <div class="detail-heading">
        <span>{{ job.platformLabel }} · {{ job.accountName || '目标账号' }}</span>
        <n-tag size="small">{{ job.status }}</n-tag>
      </div>
      <section>
        <h3>状态时间线</h3>
        <JobTimeline :entries="detail?.statusHistory || []" />
      </section>
      <n-alert v-if="job.errorMessage" type="error" :bordered="false">
        <strong>{{ job.errorCode }}</strong>
        <p>{{ job.errorMessage }}</p>
        <p v-if="advice">下一步：{{ advice }}</p>
      </n-alert>
      <section v-if="detail?.records?.length">
        <h3>结果记录</h3>
        <div v-for="record in detail.records" :key="record.id" class="record-item">
          {{ record.resultStatus }} · {{ record.remoteContentId || '无远端 ID' }} · {{ record.permalink || '无链接' }}
        </div>
      </section>
      <section v-if="['approved', 'queued'].includes(job.status)">
        <h3>改期 / 修改</h3>
        <n-form label-placement="top">
          <n-form-item label="计划时间">
            <n-date-picker v-model:value="scheduledAt" type="datetime" clearable style="width: 100%" />
          </n-form-item>
          <n-form-item label="标题">
            <n-input v-model:value="title" />
          </n-form-item>
          <n-form-item label="正文">
            <n-input v-model:value="body" type="textarea" :rows="4" />
          </n-form-item>
        </n-form>
        <n-button size="small" quaternary :disabled="busy" @click="save">保存修改</n-button>
      </section>
      <n-space class="detail-actions" :size="8">
        <n-button v-if="job.status === 'pending_review'" type="primary" :disabled="busy" @click="emit('approve')">审核通过</n-button>
        <n-button v-if="canRetry(job)" quaternary :disabled="busy" @click="emit('retry')">重试</n-button>
        <n-button v-if="['approved', 'queued', 'needs_attention'].includes(job.status)" quaternary :disabled="busy" @click="emit('cancel')">取消</n-button>
        <n-button v-if="canRecord(job)" quaternary @click="emit('record')">回填结果</n-button>
        <n-button v-if="['succeeded', 'cancelled', 'failed', 'needs_attention'].includes(job.status)" type="error" quaternary :disabled="busy" @click="emit('delete')">删除</n-button>
      </n-space>
    </div>
  </n-modal>
</template>

<style scoped>
.detail-stack { display: flex; flex-direction: column; gap: 18px; }
.detail-heading, .detail-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.detail-heading { justify-content: space-between; color: var(--color-text-secondary); font-size: var(--font-sm); }
.detail-stack h3 { margin: 0 0 10px; color: var(--color-text-primary); font-size: var(--font-sm); }
.detail-stack p { margin: 6px 0 0; }
.record-item { padding: 8px 0; border-bottom: 1px solid var(--color-border); color: var(--color-text-secondary); font-size: var(--font-xs); }
</style>
