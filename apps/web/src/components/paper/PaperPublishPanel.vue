<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NDatePicker, NSelect } from 'naive-ui'
import { studioExtraApi } from '../../api/studioExtra'

const props = defineProps({
  article: { type: Object, default: null },
  busy: { type: String, default: '' },
})
const emit = defineEmits(['saved', 'notify'])

const accounts = ref([])
const accountId = ref(null)
const scheduleAt = ref(null)
const review = ref(null)
const loading = ref(false)
const action = ref('')
const error = ref('')
const accountsError = ref('')
const reviewError = ref('')
let timer = null
let sequence = 0
let reviewRequest = 0

const busyAction = computed(() => Boolean(action.value || props.busy))
const accountOptions = computed(() => accounts.value.map((account) => ({
  value: account.id,
  label: account.appIdMasked ? `${account.displayName}（${account.appIdMasked}）` : account.displayName,
})))
const reviewRows = computed(() => review.value?.platforms || [])
const reviewStats = computed(() => review.value?.stats)
const publishedAt = computed(() => review.value?.publishedAt || props.article?.publishedAt)
const currentDraft = computed(() => reviewRows.value.find((row) =>
  row.target === 'draft' && row.accountId === accountId.value && row.versionId === review.value?.versionId,
))
const statusLabel = { queued: '排队中', running: '处理中', drafted: '已推送草稿箱', succeeded: '已发布', manual: '待人工处理', failed: '失败' }
const statusClass = { drafted: 'ok', succeeded: 'ok', failed: 'bad' }

const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : ''

function stopPolling() {
  if (timer) clearTimeout(timer)
  timer = null
}

function ensurePolling() {
  stopPolling()
  if (reviewRows.value.some((row) => ['queued', 'running'].includes(row.status))) {
    timer = setTimeout(loadReview, 5000)
  }
}

async function loadReview() {
  const id = props.article?.id
  if (!id) return
  const request = ++reviewRequest
  try {
    const result = await studioExtraApi.review(id)
    if (request !== reviewRequest) return
    review.value = result
    reviewError.value = ''
  } catch (err) {
    if (request !== reviewRequest) return
    reviewError.value = err.message || '读取推送结果失败'
  }
  ensurePolling()
}

async function load(id) {
  const seq = ++sequence
  ++reviewRequest
  stopPolling()
  review.value = null
  reviewError.value = ''
  error.value = ''
  accountsError.value = ''
  accounts.value = []
  accountId.value = null
  loading.value = Boolean(id)
  if (!id) return
  const [result] = await Promise.allSettled([studioExtraApi.wechatAccounts(), loadReview()])
  if (seq !== sequence) return
  if (result.status === 'fulfilled') {
    accounts.value = result.value
    accountId.value = accounts.value[0]?.id || null
  } else {
    accountsError.value = result.reason?.message || '读取已绑定公众号失败'
  }
  loading.value = false
}

async function withAction(name, run) {
  if (busyAction.value || !props.article?.id) return
  const seq = sequence
  const isCurrent = () => seq === sequence
  action.value = name
  error.value = ''
  try {
    await run(props.article.id, isCurrent)
  } catch (err) {
    if (isCurrent()) error.value = err.message || '操作失败，请重试'
  } finally {
    action.value = ''
  }
}

function saveSchedule(clear = false) {
  const at = scheduleAt.value
  if (!clear && (!at || at <= Date.now())) {
    error.value = '请选择一个晚于当前时间的计划发布时间'
    return
  }
  return withAction(clear ? 'clear' : 'schedule', async (id, isCurrent) => {
    const snapshot = await studioExtraApi.schedule(id, clear ? null : new Date(at).toISOString())
    if (!isCurrent()) return
    emit('saved', snapshot)
    scheduleAt.value = clear ? null : at
    emit('notify', clear ? '已取消排期' : `已保存排期：${formatTime(at)}`)
  })
}

function pushDraft() {
  return withAction('push', async (id, isCurrent) => {
    const result = await studioExtraApi.pushDraft(id, accountId.value)
    if (!isCurrent()) return
    if (result.status === 'queued') emit('notify', '草稿推送已排队，结果以复盘为准')
    if (result.status === 'drafted') emit('notify', '该版本已推送到所选公众号的草稿箱')
    await loadReview()
  })
}

function retryDraft(jobId) {
  return withAction(jobId, async (id, isCurrent) => {
    try {
      await studioExtraApi.retryDraft(id, jobId)
    } finally {
      if (isCurrent()) await loadReview()
    }
  })
}

watch(() => props.article?.id, load, { immediate: true })
watch(() => [props.article?.id, props.article?.scheduledFor], ([, time]) => {
  scheduleAt.value = time ? Date.parse(time) : null
}, { immediate: true })

onBeforeUnmount(() => {
  ++sequence
  ++reviewRequest
  stopPolling()
})
</script>

<template>
  <section class="publish-panel" aria-label="发布与复盘">
    <p v-if="!article" class="hint">左侧选一篇稿件。</p>
    <template v-else>
      <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
      <section class="block-section schedule-section">
        <div class="section-kicker">01 / SCHEDULE</div>
        <div class="setting-head">
          <h3>排期</h3>
          <span class="state" :class="article.scheduledFor ? 'ok' : ''">{{ article.scheduledFor ? '已排期' : '未排期' }}</span>
        </div>
        <NDatePicker v-model:value="scheduleAt" type="datetime" size="small" clearable placeholder="选择计划发布时间" :disabled="busyAction" />
        <div class="row">
          <NButton size="small" :disabled="busyAction" :loading="action === 'schedule'" @click="saveSchedule()">保存排期</NButton>
          <NButton v-if="article.scheduledFor" size="small" quaternary :disabled="busyAction" :loading="action === 'clear'" @click="saveSchedule(true)">取消排期</NButton>
        </div>
        <p class="hint">排期同步到内容日历，只记录计划，不会自动推送或发布。</p>
      </section>

      <section class="block-section">
        <div class="section-kicker">02 / DELIVERY</div>
        <div class="setting-head"><h3>微信公众号草稿箱</h3></div>
        <p class="hint">推送最近一次存档的版本，不改原稿。推送成功后，请到微信公众平台确认正式发布。</p>
        <NSelect v-model:value="accountId" :options="accountOptions" :loading="loading" :disabled="loading || busyAction || !accounts.length" size="small" placeholder="选择已绑定的公众号" />
        <p v-if="accountsError" class="panel-error" role="alert">{{ accountsError }}</p>
        <p v-else-if="!loading && !accounts.length" class="hint">请先在发布中心绑定并验证公众号。</p>
        <p v-if="!loading && review && !review.versionId" class="hint">请选好封面和正文插图，再点顶部「文章存档」。</p>
        <div class="row">
          <NButton type="primary" size="small" :disabled="loading || busyAction || !accountId || !review?.versionId || Boolean(currentDraft)" :loading="action === 'push'" @click="pushDraft">推送到草稿箱</NButton>
          <span v-if="currentDraft" class="hint">该版本：{{ statusLabel[currentDraft.status] }}</span>
        </div>
      </section>

      <section class="block-section review-section">
        <div class="section-kicker">03 / REVIEW</div>
        <div class="setting-head">
          <h3>复盘</h3>
          <NButton size="tiny" quaternary :disabled="loading || busyAction" @click="loadReview">刷新复盘</NButton>
        </div>
        <p v-if="reviewError" class="panel-error" role="alert">{{ reviewError }}</p>
        <div v-if="reviewStats" class="review-summary"><p>这篇内容已有 <strong>{{ reviewStats.blocks }}</strong> 段、<strong>{{ reviewStats.chars }}</strong> 字<span v-if="reviewStats.checked">，质检有 <strong>{{ reviewStats.flagged }}</strong> 处待改</span>。</p><span>稿件概览</span></div>
        <p v-if="publishedAt" class="hint">已发布：{{ formatTime(publishedAt) }}</p>
        <ul v-if="reviewRows.length" class="review-list" aria-live="polite">
          <li v-for="row in reviewRows" :key="row.jobId">
            <div class="row">
              <strong>{{ row.accountLabel || row.platformLabel }}</strong>
              <span class="state" :class="statusClass[row.status]">{{ statusLabel[row.status] || row.status }}</span>
              <time v-if="row.at" :datetime="row.at" class="hint">{{ formatTime(row.at) }}</time>
            </div>
            <p v-if="row.error" class="row-error">{{ row.error }}</p>
            <p v-if="row.draftMediaId" class="hint draft-id">草稿 ID：{{ row.draftMediaId }}</p>
            <a v-if="row.url" :href="row.url" target="_blank" rel="noopener noreferrer">查看已发布文章</a>
            <NButton v-if="row.canRetry" size="tiny" :disabled="busyAction" :loading="action === row.jobId" @click="retryDraft(row.jobId)">重试推送</NButton>
          </li>
        </ul>
        <p v-else-if="!loading && !reviewError" class="hint">暂无推送或发布记录。</p>
      </section>
    </template>
  </section>
</template>

<style scoped>
.publish-panel, .block-section { display: flex; flex-direction: column; gap: 8px; }
.block-section + .block-section { padding-top: 12px; border-top: 1px solid var(--color-border); }
.setting-head, .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.setting-head { justify-content: space-between; }
.setting-head h3 { margin: 0; font-size: 13px; font-weight: 600; }
.state { font-size: 11px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--color-border); color: var(--color-text-secondary); }
.state.ok { color: var(--paper-success); border-color: var(--paper-success); }
.state.bad, .panel-error, .row-error { color: var(--paper-danger); }
.panel-error { margin: 0; padding: 6px 8px; border-radius: 6px; background: var(--paper-danger-soft); }
.hint, .row-error { margin: 0; font-size: 12px; line-height: 1.6; }
.hint { color: var(--color-text-secondary); }
.draft-id { overflow-wrap: anywhere; }
.review-list { margin: 0; padding: 0; list-style: none; }
.review-list li { display: grid; gap: 4px; padding: 8px 0; border-top: 1px solid var(--color-border); font-size: 12px; }
.review-list a { color: var(--paper-accent); }
.publish-panel { gap: 12px; }
.block-section { gap: 10px; padding: 14px; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-surface-raised); }
.block-section + .block-section { padding-top: 14px; }
.section-kicker { color: var(--color-text-secondary); font-size: 10px; font-weight: 700; letter-spacing: .12em; }
.setting-head h3 { font-size: 15px; letter-spacing: -.02em; }
.schedule-section :deep(.n-date-picker), .block-section :deep(.n-select) { width: 100%; }
.review-list { display: grid; gap: 8px; }
.review-list li { padding: 10px; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-surface); }
.review-list time { margin-left: auto; }
.review-summary { padding: 14px; border: 1px solid var(--color-border); border-radius: 9px; background: radial-gradient(ellipse at 100% 0%, color-mix(in srgb, var(--color-accent) 18%, transparent), transparent 58%), radial-gradient(ellipse at 85% 100%, color-mix(in srgb, var(--color-warning) 14%, transparent), transparent 55%), var(--color-surface); }
.review-summary p { margin: 0; color: var(--color-text-secondary); font-size: 15px; line-height: 1.6; }
.review-summary strong { color: var(--color-text-primary); font-family: Georgia, serif; font-size: 20px; font-weight: 500; }
.review-summary > span { display: block; margin-top: 10px; color: var(--color-text-secondary); font-size: 11px; }
</style>
