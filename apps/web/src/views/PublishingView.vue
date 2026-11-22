<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCheckbox,
  NDatePicker,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSelect,
  NSpace,
  NEmpty,
  NTab,
  NTabs,
  NTag,
} from 'naive-ui'
import { api } from '../api/client'
import StatusTag from '../components/StatusTag.vue'
import PlatformCard from '../features/publish/accounts/PlatformCard.vue'
import { usePermission } from '../composables/usePermission'
import { confirm, toast } from '../ui/feedback'

// 页面只要 publishing.view（能看记录）；真正会推到公众号的动作判 publishing.use。
// 只读用户看到的是完整队列 + 一行说明，而不是一个点进去就 403 的入口。
const { can, disabledReason } = usePermission()
const canPublish = computed(() => can('publishing.use'))
const publishDenied = computed(() => disabledReason('publishing.use', '发布管理'))

const capabilities = ref([])
const accounts = ref([])
const jobs = ref([])
const connectorStatuses = ref({})
const loading = ref(true)
const queueRefreshing = ref(false)
const error = ref('')
const queueStatus = ref('all')
const queuePlatform = ref('all')
const queueAccount = ref('all')
const queueSearch = ref('')

const queueStatusOptions = [
  { value: 'all', label: '全部' },
  { value: 'queued', label: '排队中' },
  { value: 'publishing', label: '发布中' },
  { value: 'succeeded', label: '成功' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]

const queuePlatformOptions = computed(() => {
  // 只列出任务中真实出现过的平台，避免多平台目录制造无效筛选
  const present = new Map()
  jobs.value.forEach((job) => present.set(job.platform, job.platformLabel))
  return Array.from(present, ([value, label]) => ({ value, label }))
})

const queueAccountOptions = computed(() =>
  accounts.value.map((account) => ({ value: account.id, label: account.displayName })),
)

const queuePlatformModel = computed({
  get: () => (queuePlatform.value === 'all' ? null : queuePlatform.value),
  set: (value) => {
    queuePlatform.value = value || 'all'
  },
})

const queueAccountModel = computed({
  get: () => (queueAccount.value === 'all' ? null : queueAccount.value),
  set: (value) => {
    queueAccount.value = value || 'all'
  },
})

const accountSelectOptions = computed(() =>
  accounts.value
    .filter((account) => account.status === 'connected')
    .map((account) => ({ value: account.id, label: `${account.platformLabel} · ${account.displayName}` })),
)

const contentSelectOptions = computed(() =>
  contents.value.map((item) => ({ value: item.id, label: item.title })),
)

const filteredJobs = computed(() => {
  const keyword = queueSearch.value.trim().toLowerCase()
  return jobs.value.filter((job) => {
    if (queueStatus.value !== 'all' && job.status !== queueStatus.value) return false
    if (queuePlatform.value !== 'all' && job.platform !== queuePlatform.value) return false
    if (queueAccount.value !== 'all' && job.accountId !== queueAccount.value) return false
    if (keyword && !`${job.id} ${job.requestId} ${job.platformLabel} ${job.accountName} ${job.contentTitle}`.toLowerCase().includes(keyword)) return false
    return true
  })
})

const queueSummary = computed(() => ({
  total: jobs.value.length,
  active: jobs.value.filter((job) => ['queued', 'publishing', 'approved'].includes(job.status)).length,
  succeeded: jobs.value.filter((job) => job.status === 'succeeded').length,
  attention: jobs.value.filter((job) => ['failed', 'needs_attention'].includes(job.status)).length,
}))

function jobAccountLabel(job) {
  return accounts.value.find((account) => account.id === job.accountId)?.displayName || '未知账号'
}

function jobTargetLabel(job) {
  return job.target === 'draft' ? '草稿箱' : '平台发布'
}

const activeTab = ref('queue')

const showPublish = ref(false)
const publishForm = reactive({
  accountId: null,
  contentId: null,
  title: '',
  body: '',
  scheduledAt: '',
  variantId: '',
  mode: 'manual',
})
const contents = ref([])
const selectedArticle = ref(null)
const articleLoading = ref(false)
const manualEntry = ref(false)
const idempotencyKey = ref('')
const previewResult = ref(null)
const publishError = ref('')

const accountOptions = accountSelectOptions

// n-date-picker 用时间戳；提交时再转回 ISO，和原来 datetime-local 的契约一致
const scheduledAtTs = computed({
  get: () => {
    if (!publishForm.scheduledAt) return null
    const ts = new Date(publishForm.scheduledAt).getTime()
    return Number.isNaN(ts) ? null : ts
  },
  set: (value) => {
    publishForm.scheduledAt = value ? new Date(value).toISOString() : ''
  },
})

async function load(options = {}) {
  const queueOnly = options.queueOnly === true
  if (queueOnly) queueRefreshing.value = true
  else loading.value = true
  error.value = ''
  try {
    const [caps, accs, jobList, conns, contentList] = await Promise.all([
      api.get('/platforms/capabilities'),
      api.get('/platform-accounts'),
      api.get('/publishing/jobs'),
      api.get('/platforms/connectors/status'),
      api.get('/content'),
    ])
    capabilities.value = caps.platforms
    if (!activePlatform.value || !caps.platforms.some((platform) => platform.platform === activePlatform.value)) {
      activePlatform.value = caps.platforms[0]?.platform || ''
    }
    accounts.value = accs
    jobs.value = jobList
    connectorStatuses.value = Object.fromEntries(conns.platforms.map((s) => [s.platform, s]))
    contents.value = contentList
  } catch (err) {
    error.value = err.message
  } finally {
    if (queueOnly) queueRefreshing.value = false
    else loading.value = false
  }
}

async function refreshQueue() {
  await load({ queueOnly: true })
  if (!error.value) toast('发布队列已刷新')
}

function connectorModeLabel(platform) {
  const status = connectorStatuses.value[platform]
  if (!status) return '未知'
  if (status.mode === 'app_secret') return 'AppSecret'
  return '未配置'
}

// 服务器公网出口 IP：公众号 IP 白名单要填的就是这个
const egress = ref(null)
const egressLoading = ref(false)
const egressCopied = ref(false)

async function loadEgressIp(force = false) {
  if (egressLoading.value) return
  egressLoading.value = true
  egressCopied.value = false
  try {
    egress.value = await api.get(`/wechat-mp/egress-ip${force ? '?force=true' : ''}`)
  } catch (err) {
    egress.value = { ip: '', hint: `探测失败：${err.message}` }
  } finally {
    egressLoading.value = false
  }
}

async function copyEgressIp() {
  const text = egress.value?.ip
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    egressCopied.value = true
    setTimeout(() => (egressCopied.value = false), 2000)
  } catch {
    // 隐私模式或不支持 clipboard API 时，退化为选中文本让用户自己复制
    egressCopied.value = false
  }
}

onMounted(() => {
  load()
  // 出口 IP 探测只服务「填公众号白名单」这个动作，归 publishing.use；
  // 只读角色发起它只会拿到 403，白跑一次请求还留个「探测失败」在卡里。
  if (canPublish.value) loadEgressIp()
})

async function verifyAuth(account) {
  error.value = ''
  try {
    const res = await api.post(`/platform-accounts/${account.id}/test`)
    toast(res.note || (res.verified ? '官方授权验证通过。' : res.message))
    await load()
  } catch (err) {
    error.value = err.message
  }
}

async function unbind(account) {
  await api.del(`/platform-accounts/${account.id}`)
  account.status = 'unbound'
  account.autoPublishEnabled = false
}

function openPublish() {
  publishForm.accountId = accountOptions.value[0]?.value || null
  publishForm.contentId = null
  publishForm.title = ''
  publishForm.body = ''
  publishForm.scheduledAt = ''
  publishForm.variantId = ''
  publishForm.mode = 'manual'
  selectedArticle.value = null
  manualEntry.value = false
  previewResult.value = null
  publishError.value = ''
  idempotencyKey.value = `pub-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  showPublish.value = true
}

async function onSelectArticle() {
  selectedArticle.value = null
  publishForm.title = ''
  publishForm.body = ''
  previewResult.value = null
  if (!publishForm.contentId) return
  articleLoading.value = true
  publishError.value = ''
  try {
    const detail = await api.get(`/content/${publishForm.contentId}`)
    const item = detail.item
    const version = detail.versions?.[0]
    selectedArticle.value = item
    publishForm.title = item.draftTitle || item.title || ''
    publishForm.body = item.draftBody ?? version?.body ?? ''
  } catch (err) {
    publishError.value = err.message
  } finally {
    articleLoading.value = false
  }
}

// 渠道目录：左侧平台导航使用真实能力与账号状态，右侧维护当前平台。
const platformCards = computed(() =>
  capabilities.value.map((cap) => ({
    ...cap,
    accounts: accounts.value.filter((account) => account.platform === cap.platform),
  })),
)
const activePlatform = ref('')
const selectedPlatform = computed(
  () => platformCards.value.find((platform) => platform.platform === activePlatform.value) || platformCards.value[0],
)

function selectPlatform(platform) {
  activePlatform.value = platform
}

function openBind(platform) {
  if (platform !== 'wechat_official') {
    toast('该平台尚未接入，暂不支持绑定账号', 'warning')
    return
  }
  wechatBindForm.displayName = ''
  wechatBindForm.useOwnCred = false
  wechatBindForm.appId = ''
  wechatBindForm.appSecret = ''
  showWechatBind.value = true
}

async function doPreview() {
  publishError.value = ''
  previewResult.value = null
  try {
    previewResult.value = await api.post('/publishing/preview', {
      accountId: publishForm.accountId,
      title: publishForm.title,
      body: publishForm.body,
      scheduledAt: publishForm.scheduledAt ? new Date(publishForm.scheduledAt).toISOString() : null,
      variantId: publishForm.variantId || null,
    })
  } catch (err) {
    publishError.value = err.message
  }
}

async function submitJob() {
  publishError.value = ''
  try {
    const job = await api.post('/publishing/jobs', {
      accountId: publishForm.accountId,
      contentItemId: publishForm.contentId || null,
      idempotencyKey: idempotencyKey.value,
      title: publishForm.title,
      body: publishForm.body,
      scheduledAt: publishForm.scheduledAt ? new Date(publishForm.scheduledAt).toISOString() : null,
      variantId: publishForm.variantId || null,
      reviewed: true,
      mode: publishForm.mode,
    })
    jobs.value.unshift(job)
    showPublish.value = false
    toast(
      publishForm.mode === 'manual'
        ? '已加入排期。请在平台侧手动发布后回到本页回填结果。'
        : '已创建自动发布任务。平台能力未核验时不会真实提交到平台。',
    )
  } catch (err) {
    publishError.value = err.message
  }
}

const recordingJob = ref(null)
const recordForm = reactive({ remoteContentId: '', permalink: '' })

function openRecord(job) {
  recordingJob.value = job
  recordForm.remoteContentId = ''
  recordForm.permalink = ''
}

async function submitRecord() {
  try {
    const updated = await api.post(`/publishing/jobs/${recordingJob.value.id}/record-manual-result`, {
      remoteContentId: recordForm.remoteContentId || null,
      permalink: recordForm.permalink || null,
    })
    Object.assign(recordingJob.value, updated)
    recordingJob.value = null
    toast('已回填手动发布结果（来源标记为手动记录）。')
  } catch (err) {
    error.value = err.message
  }
}

const canRecord = (job) => ['approved', 'queued', 'needs_attention', 'failed', 'publishing'].includes(job.status)

async function cancelJob(job) {
  try {
    const updated = await api.post(`/publishing/jobs/${job.id}/cancel`)
    Object.assign(job, updated)
  } catch (err) {
    error.value = err.message
  }
}

async function retryJob(job) {
  try {
    const updated = await api.post(`/publishing/jobs/${job.id}/retry`)
    Object.assign(job, updated)
  } catch (err) {
    error.value = err.message
  }
}

const deletingJobId = ref('')
const canDelete = (job) => ['succeeded', 'cancelled', 'failed', 'needs_attention'].includes(job.status)

async function deleteJob(job) {
  if (!canDelete(job) || deletingJobId.value) return
  const ok = await confirm('删除后将从发布队列移除该任务，确定继续吗？', '删除发布任务')
  if (!ok) return
  deletingJobId.value = job.id
  error.value = ''
  try {
    await api.del(`/publishing/jobs/${job.id}`)
    jobs.value = jobs.value.filter((item) => item.id !== job.id)
    toast('发布任务已删除')
  } catch (err) {
    error.value = err.message
  } finally {
    deletingJobId.value = ''
  }
}

const canRetry = (job) =>
  ['failed', 'needs_attention'].includes(job.status) && !job.remoteContentId
const canCancel = (job) => ['pending_review', 'approved', 'queued', 'needs_attention'].includes(job.status)

// ---------------- 公众号账号绑定 ----------------
const wechatBusy = ref(false)
const showWechatBind = ref(false)
const wechatBindForm = reactive({ displayName: '', useOwnCred: false, appId: '', appSecret: '' })
const acctCredTarget = ref(null)
const acctCredForm = reactive({ appId: '', appSecret: '' })
const acctCredBusy = ref(false)
const acctCredError = ref('')

function openAcctCred(account) {
  acctCredTarget.value = account
  acctCredForm.appId = ''
  acctCredForm.appSecret = ''
  acctCredError.value = ''
}

async function saveAccountCred() {
  if (!acctCredTarget.value) return
  acctCredBusy.value = true
  acctCredError.value = ''
  try {
    const res = await api.put(`/wechat-mp/accounts/${acctCredTarget.value.id}/credentials`, {
      appId: acctCredForm.appId.trim(),
      appSecret: acctCredForm.appSecret.trim(),
    })
    acctCredTarget.value = null
    if (res.probeError) {
      error.value = `凭据已保存到账号，但验证未通过：${res.probeError.message}（常见原因：公众号后台 IP 白名单未包含本机出口 IP）`
    } else {
      toast('账号凭据已保存并验证通过，后续推送将使用该账号自己的凭据。')
    }
    await load()
  } catch (err) {
    acctCredError.value = err.message
  } finally {
    acctCredBusy.value = false
  }
}

async function bindWechat() {
  wechatBusy.value = true
  error.value = ''
  try {
    const payload = { displayName: wechatBindForm.displayName }
    // 多账号：勾选独立凭据时，AppID/AppSecret 加密存到该账号上；留空走部署级默认凭据
    if (wechatBindForm.useOwnCred && wechatBindForm.appId.trim() && wechatBindForm.appSecret.trim()) {
      payload.credentials = {
        appId: wechatBindForm.appId.trim(),
        appSecret: wechatBindForm.appSecret.trim(),
      }
    }
    const res = await api.post('/wechat-mp/bind', payload)
    toast(`验证通过：AppID ${res.probe.appId}，后台现有草稿 ${res.probe.draftTotal} 篇。正式群发仍需在公众号后台操作。`)
    showWechatBind.value = false
    wechatBindForm.displayName = ''
    wechatBindForm.useOwnCred = false
    wechatBindForm.appId = ''
    wechatBindForm.appSecret = ''
    await load()
  } catch (err) {
    error.value = err.message
  } finally {
    wechatBusy.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">发布管理</h1>
        <p class="page-sub">渠道平台维护与发布任务追踪分开管理</p>
      </div>
      <n-space :size="8">
        <template v-if="activeTab === 'queue'">
          <n-button secondary :loading="queueRefreshing" @click="refreshQueue">刷新队列</n-button>
          <n-button
            type="primary"
            :disabled="!canPublish"
            :title="publishDenied || '创建一条新的发布排期任务'"
            @click="openPublish"
          >
            新建排期任务
          </n-button>
        </template>
        <n-button
          v-else
          type="primary"
          :disabled="!selectedPlatform?.available || !canPublish"
          :title="publishDenied || `绑定${selectedPlatform?.label || '平台'}账号`"
          @click="selectedPlatform && openBind(selectedPlatform.platform)"
        >
          绑定{{ selectedPlatform?.label || '平台' }}账号
        </n-button>
      </n-space>
    </div>

    <n-alert v-if="error" type="error" :bordered="false" style="margin-bottom: 12px">{{ error }}</n-alert>

    <!-- 只读角色：看得到完整记录，但不给注定 403 的按钮 -->
    <n-alert v-if="!canPublish" type="info" :bordered="false" class="publish-readonly-note">
      当前角色只有「发布查看」权限：可以核对任务状态、失败原因与账号情况，创建/审核/重试/绑定账号需要「发布管理」权限。
    </n-alert>

    <n-tabs v-model:value="activeTab" type="line" animated>
      <n-tab name="queue">发布任务</n-tab>
      <n-tab name="channels">渠道平台</n-tab>
    </n-tabs>

    <!-- 发布任务 -->
    <template v-if="activeTab === 'queue'">
    <section class="queue-overview-grid">
      <div class="card queue-overview-card">
        <span class="queue-overview-label">全部任务</span>
        <strong>{{ queueSummary.total }}</strong>
        <small>当前账号可见</small>
      </div>
      <div class="card queue-overview-card">
        <span class="queue-overview-label">待处理</span>
        <strong>{{ queueSummary.active }}</strong>
        <small>排队中或发布中</small>
      </div>
      <div class="card queue-overview-card">
        <span class="queue-overview-label">发布成功</span>
        <strong>{{ queueSummary.succeeded }}</strong>
        <small>已完成任务</small>
      </div>
      <div class="card queue-overview-card is-warning">
        <span class="queue-overview-label">需要处理</span>
        <strong>{{ queueSummary.attention }}</strong>
        <small>失败或需人工核实</small>
      </div>
    </section>

    <div v-if="loading" class="stack">
      <div class="skeleton" style="height: 120px" />
      <div class="skeleton" style="height: 160px" />
    </div>

    <template v-else>
      <section class="card">
        <div class="section-head">
          <div>
            <h2 class="card-title">发布队列</h2>
            <p class="section-subtitle">查看任务状态、刷新最新结果或删除历史任务</p>
          </div>
        </div>
        <div v-if="!jobs.length" class="empty">
          <span class="empty-title">队列是空的</span>
          <span>创建任务后会显示在这里，可取消或核实后重试。</span>
        </div>
        <div v-if="jobs.length" class="queue-toolbar">
          <n-space :size="6">
            <n-button
              v-for="option in queueStatusOptions"
              :key="option.value"
              size="small"
              :type="queueStatus === option.value ? 'primary' : 'default'"
              :secondary="queueStatus !== option.value"
              :quaternary="queueStatus !== option.value"
              @click="queueStatus = option.value"
            >
              {{ option.label }}
            </n-button>
          </n-space>
          <n-space :size="8">
            <n-select
              v-if="queuePlatformOptions.length > 1"
              v-model:value="queuePlatformModel"
              :options="queuePlatformOptions"
              clearable
              placeholder="全部平台"
              size="small"
              style="width: 160px"
            />
            <n-select
              v-model:value="queueAccountModel"
              :options="queueAccountOptions"
              clearable
              placeholder="全部账号"
              size="small"
              style="width: 160px"
            />
            <n-input v-model:value="queueSearch" size="small" clearable placeholder="搜索任务" style="width: 180px" />
          </n-space>
        </div>
        <div v-if="!filteredJobs.length" class="empty">
          <span class="empty-title">没有匹配的任务</span>
          <span>可以切换筛选条件，或刷新队列获取最新任务。</span>
        </div>
        <div v-else class="list">
          <div v-for="job in filteredJobs" :key="job.id" class="list-row job-row">
            <div class="list-main">
              <div class="list-title">
                <router-link v-if="job.contentItemId" class="job-article-link" :to="{ name: 'articles', query: { id: job.contentItemId } }">
                  {{ job.contentTitle || '未命名稿件' }}
                </router-link>
                <span v-else>{{ job.contentTitle || '未关联稿件' }}</span>
                <span class="job-title-divider">·</span>
                {{ job.platformLabel }} · {{ jobTargetLabel(job) }}
              </div>
              <div class="list-meta">
                账号 {{ jobAccountLabel(job) }} · 请求号 {{ job.requestId.slice(0, 8) }} · 尝试 {{ job.attemptCount }} 次
                <span v-if="job.scheduledAt"> · 计划 {{ job.scheduledAt.slice(0, 16).replace('T', ' ') }}</span>
                <span v-if="job.remoteContentId"> · 远端 ID {{ job.remoteContentId }}</span>
              </div>
              <div v-if="job.errorMessage" class="list-meta" style="color: var(--color-danger)">
                {{ job.errorCode }}：{{ job.errorMessage }}
              </div>
            </div>
            <StatusTag :status="job.status" />
            <n-button v-if="canPublish && canRecord(job)" size="small" secondary @click="openRecord(job)">
              回填手发结果
            </n-button>
            <n-button v-if="canPublish && canRetry(job)" size="small" secondary @click="retryJob(job)">重试</n-button>
            <n-button v-if="canPublish && canCancel(job)" size="small" secondary @click="cancelJob(job)">取消</n-button>
            <n-button
              v-if="canPublish && canDelete(job)"
              size="small"
              type="error"
              secondary
              :loading="deletingJobId === job.id"
              @click="deleteJob(job)"
            >
              删除
            </n-button>
          </div>
        </div>
      </section>
    </template>
    </template>

    <!-- 渠道平台 -->
    <template v-else>
      <div v-if="loading" class="platform-layout platform-layout-loading">
        <div class="card platform-sidebar-skeleton" />
        <div class="card platform-detail-skeleton" />
      </div>
      <div v-else-if="platformCards.length" class="platform-layout">
        <aside class="card platform-sidebar" aria-label="渠道平台列表">
          <div class="platform-sidebar-head">
            <h2 class="card-title">渠道平台</h2>
            <span class="platform-count">{{ platformCards.length }}</span>
          </div>
          <nav class="platform-nav">
            <button
              v-for="platform in platformCards"
              :key="platform.platform"
              type="button"
              class="platform-nav-item"
              :class="{ 'is-active': selectedPlatform?.platform === platform.platform }"
              :aria-current="selectedPlatform?.platform === platform.platform ? 'page' : undefined"
              @click="selectPlatform(platform.platform)"
            >
              <span class="platform-nav-icon">{{ platform.label.slice(0, 1) }}</span>
              <span class="platform-nav-copy">
                <strong>{{ platform.label }}</strong>
                <small>{{ platform.available ? `${platform.accounts.length} 个账号` : '暂未接入' }}</small>
              </span>
              <span class="platform-nav-state" :class="{ 'is-ready': platform.available }" />
            </button>
          </nav>
        </aside>

        <section v-if="selectedPlatform" class="card platform-detail">
          <header class="platform-detail-head">
            <div>
              <div class="platform-detail-title-row">
                <h2 class="card-title">{{ selectedPlatform.label }}</h2>
                <n-tag :type="selectedPlatform.available ? 'success' : 'warning'" size="small" round>
                  {{ selectedPlatform.available ? '已接入' : '未接入' }}
                </n-tag>
              </div>
              <p class="section-subtitle">
                {{ selectedPlatform.available ? selectedPlatform.note : '该平台尚未接入，暂不可绑定或发布' }}
              </p>
            </div>
            <n-button
              v-if="selectedPlatform.available"
              type="primary"
              :disabled="!canPublish"
              :title="publishDenied || `为${selectedPlatform.label}再绑定一个账号`"
              @click="openBind(selectedPlatform.platform)"
            >
              绑定新账号
            </n-button>
          </header>

          <n-space v-if="selectedPlatform.available" class="platform-capabilities" :size="6">
            <n-tag v-if="selectedPlatform.draftUpload" type="success" size="small" round>草稿箱推送</n-tag>
            <n-tag v-if="selectedPlatform.publish" type="success" size="small" round>自动发布</n-tag>
            <n-tag v-if="selectedPlatform.contentMetrics" type="success" size="small" round>内容数据</n-tag>
            <n-tag
              v-if="!selectedPlatform.draftUpload && !selectedPlatform.publish && !selectedPlatform.contentMetrics"
              type="warning"
              size="small"
              round
            >暂无可用能力</n-tag>
            <n-tag
              size="small"
              round
              :type="connectorStatuses[selectedPlatform.platform]?.configured ? 'success' : 'warning'"
            >
              {{ connectorModeLabel(selectedPlatform.platform) }}
            </n-tag>
          </n-space>

          <div v-if="selectedPlatform.available" class="platform-detail-body">
            <PlatformCard
              :platform="selectedPlatform"
              :egress="egress"
              :egress-loading="egressLoading"
              :egress-copied="egressCopied"
              :readonly="!canPublish"
              :readonly-reason="publishDenied"
              @bind="openBind"
              @verify="verifyAuth"
              @unbind="unbind"
              @edit-credential="openAcctCred"
              @egress-refresh="loadEgressIp(true)"
              @egress-copy="copyEgressIp"
            />
          </div>
          <div v-else class="platform-unavailable platform-detail-empty">
            <div>
              <strong>该平台尚未开放账号维护</strong>
              <span>完成平台接入后，这里会出现账号绑定、授权验证和发布能力配置。</span>
            </div>
            <n-button disabled>暂不可用</n-button>
          </div>
        </section>
      </div>
      <n-empty v-else description="暂无可维护的渠道平台" />
    </template>

    <n-modal v-model:show="showWechatBind" preset="card" title="绑定并验证公众号" style="width: min(480px, calc(100vw - 32px))" :mask-closable="!wechatBusy">
      <p class="modal-desc">同一平台可绑定多个账号；每个账号可持有自己的凭据。</p>
      <n-form label-placement="top">
        <n-form-item label="账号备注">
          <n-input v-model:value="wechatBindForm.displayName" placeholder="例如：主号 / 分公司号" />
        </n-form-item>
        <n-form-item :show-label="false">
          <n-checkbox v-model:checked="wechatBindForm.useOwnCred">
            为该账号使用独立 AppID/AppSecret（不勾选则使用默认凭据）
          </n-checkbox>
        </n-form-item>
        <template v-if="wechatBindForm.useOwnCred">
          <n-form-item label="AppID">
            <n-input v-model:value="wechatBindForm.appId" placeholder="wx 开头的应用 ID" autocomplete="off" />
          </n-form-item>
          <n-form-item label="AppSecret">
            <n-input
              v-model:value="wechatBindForm.appSecret"
              type="password"
              show-password-on="click"
              placeholder="公众号后台生成，仅保存加密副本"
              autocomplete="new-password"
            />
          </n-form-item>
        </template>
        <p class="field-hint">
          AppID / AppSecret 在公众号后台「设置与开发 → 基本配置」获取；绑定会真实调用微信接口验证，
          请先在公众号后台「IP 白名单」中加入本机出口 IP。同一 AppID 重复绑定会更新原账号，不同 AppID 会新建账号。
        </p>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary :disabled="wechatBusy" @click="showWechatBind = false">取消</n-button>
          <n-button
            type="primary"
            :loading="wechatBusy"
            :disabled="wechatBindForm.useOwnCred && (!wechatBindForm.appId.trim() || !wechatBindForm.appSecret.trim())"
            @click="bindWechat"
          >
            绑定并验证
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal :show="!!acctCredTarget" preset="card" :title="acctCredTarget ? `修改凭据 · ${acctCredTarget.displayName}` : '修改凭据'" style="width: min(480px, calc(100vw - 32px))" :mask-closable="!acctCredBusy" @update:show="(value) => { if (!value) acctCredTarget = null }">
      <p class="modal-desc">保存前会真实调用微信接口验证；Secret 加密存储且永不回传。</p>
      <n-alert v-if="acctCredError" type="error" :bordered="false" style="margin-bottom: 12px">{{ acctCredError }}</n-alert>
      <n-form label-placement="top">
        <n-form-item label="AppID">
          <n-input v-model:value="acctCredForm.appId" placeholder="wx 开头的应用 ID" autocomplete="off" />
        </n-form-item>
        <n-form-item label="AppSecret">
          <n-input
            v-model:value="acctCredForm.appSecret"
            type="password"
            show-password-on="click"
            placeholder="公众号后台生成，仅保存加密副本"
            autocomplete="new-password"
          />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary :disabled="acctCredBusy" @click="acctCredTarget = null">取消</n-button>
          <n-button
            type="primary"
            :loading="acctCredBusy"
            :disabled="!acctCredForm.appId.trim() || !acctCredForm.appSecret.trim()"
            @click="saveAccountCred"
          >
            保存并验证
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal v-model:show="showPublish" preset="card" title="新建发布任务" style="width: min(560px, calc(100vw - 32px))">
      <p class="modal-desc">先预览校验，确认后才会入队。</p>
      <n-form label-placement="top">
        <n-form-item label="目标账号">
          <n-select
            v-model:value="publishForm.accountId"
            :options="accountOptions"
            placeholder="选择已连接账号"
            :fallback-option="false"
          />
        </n-form-item>
        <p v-if="!accountOptions.length" class="field-hint">还没有已连接账号，请先在列表中绑定并标记已连接。</p>
        <n-form-item v-if="!manualEntry" label="选择稿件">
          <n-select
            v-model:value="publishForm.contentId"
            :options="contentSelectOptions"
            :loading="articleLoading"
            :disabled="articleLoading"
            placeholder="请选择要发布的稿件"
            :fallback-option="false"
            @update:value="onSelectArticle"
          />
        </n-form-item>
        <p v-if="!manualEntry && !contents.length" class="field-hint">
          还没有稿件，请先在文章管理新建一篇，或切换为手动填写。
        </p>
        <p v-else-if="!manualEntry" class="field-hint">标题与正文会自动取自稿件的最新内容，无需重复输入。</p>

        <div v-if="selectedArticle && !manualEntry" class="card-flat article-preview">
          <div class="row row-tight">
            <strong>{{ selectedArticle.title }}</strong>
            <n-tag size="small" round>{{ selectedArticle.status === 'published' ? '已发布' : '草稿' }}</n-tag>
          </div>
          <p class="article-excerpt">{{ (selectedArticle.draftBody ?? publishForm.body ?? '').slice(0, 160) || '稿件暂无正文' }}</p>
          <router-link class="article-link" :to="{ name: 'articles', query: { id: selectedArticle.id } }">
            在文章管理打开
          </router-link>
        </div>

        <p v-if="articleLoading" class="field-hint">正在读取稿件内容…</p>

        <template v-if="manualEntry">
          <n-form-item label="标题">
            <n-input v-model:value="publishForm.title" />
          </n-form-item>
          <n-form-item label="正文">
            <n-input v-model:value="publishForm.body" type="textarea" :autosize="{ minRows: 5, maxRows: 12 }" />
          </n-form-item>
        </template>
        <n-button
          v-if="!manualEntry"
          text
          type="primary"
          size="small"
          @click="manualEntry = true; publishForm.contentId = null; selectedArticle = null"
        >
          没有对应稿件，改用手动填写
        </n-button>
        <n-button v-else text type="primary" size="small" @click="manualEntry = false">
          返回从列表选择稿件
        </n-button>
        <n-form-item label="计划时间（可选）">
          <n-date-picker v-model:value="scheduledAtTs" type="datetime" clearable style="width: 100%" />
        </n-form-item>
        <p class="field-hint">
          本页只做「排期 + 手动发布」与结果回填。
        </p>

        <n-button secondary size="small" @click="doPreview">校验并预览</n-button>

        <div v-if="previewResult" class="card-flat" style="margin-top: 12px">
          <n-space :size="6">
            <n-tag size="small" round>{{ previewResult.platformLabel }}</n-tag>
            <n-tag v-if="previewResult.issues.length" size="small" round type="warning">{{ previewResult.issues.length }} 项待检查</n-tag>
            <n-tag v-else size="small" round type="success">校验通过</n-tag>
          </n-space>
          <ul v-if="previewResult.issues.length" class="preview-issues">
            <li v-for="(issue, index) in previewResult.issues" :key="index">{{ issue.message }}</li>
          </ul>
        </div>

        <n-alert v-if="publishError" type="error" :bordered="false" style="margin-top: 12px">{{ publishError }}</n-alert>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary @click="showPublish = false">取消</n-button>
          <n-button
            type="primary"
            :disabled="!publishForm.accountId || (!manualEntry && !publishForm.contentId) || articleLoading"
            @click="submitJob"
          >
            确认并提交
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal :show="!!recordingJob" preset="card" title="回填手动发布结果" style="width: min(480px, calc(100vw - 32px))" @update:show="(value) => { if (!value) recordingJob = null }">
      <p class="modal-desc">你在平台侧发布完成后填写；此处记录会标注来源为手动。</p>
      <n-form label-placement="top">
        <n-form-item label="平台内容 ID（可选）">
          <n-input v-model:value="recordForm.remoteContentId" placeholder="平台返回的帖子 ID" />
        </n-form-item>
        <n-form-item label="链接（可选）">
          <n-input v-model:value="recordForm.permalink" placeholder="https://" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary @click="recordingJob = null">取消</n-button>
          <n-button type="primary" @click="submitRecord">确认回填</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
/* 只读说明条：与错误提示同一条视觉节奏，但不抢注意力 */
.publish-readonly-note {
  margin-bottom: 12px;
}

.modal-desc {
  margin: 0 0 12px;
  color: var(--color-text-secondary);
  font-size: var(--font-xs);
  line-height: 1.6;
}

.preview-issues {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: var(--font-sm);
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

.article-preview {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.article-excerpt {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--font-xs);
  line-height: 1.6;
}

.article-link,
.link-btn {
  align-self: flex-start;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-accent);
  font: inherit;
  font-size: var(--font-xs);
  text-decoration: none;
  cursor: pointer;
}

.article-link:hover,
.link-btn:hover {
  text-decoration: underline;
}

/* 分区 Tab 与渠道平台 */
.page-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: var(--space-4);
  border-bottom: 1px solid var(--color-border);
}

.page-tabs button {
  padding: 8px 12px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--color-text-muted);
  font: inherit;
  font-size: var(--font-sm);
  cursor: pointer;
}

.page-tabs button.is-active {
  border-bottom-color: var(--color-accent);
  color: var(--color-text-primary);
  font-weight: 600;
}

.platform-layout {
  display: grid;
  grid-template-columns: minmax(208px, 252px) minmax(0, 1fr);
  gap: var(--space-3);
  align-items: start;
}

.platform-layout-loading {
  min-height: 420px;
}

.platform-sidebar,
.platform-detail {
  min-width: 0;
}

.platform-sidebar {
  position: sticky;
  top: var(--space-3);
  padding: var(--space-3);
}

.platform-sidebar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: 2px 4px var(--space-3);
  border-bottom: 1px solid var(--color-border);
}

.platform-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  height: 24px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-text-muted);
  font-size: var(--font-xs);
}

.platform-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: var(--space-2);
}

.platform-nav-item {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) 8px;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 8px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color 160ms ease, border-color 160ms ease;
}

.platform-nav-item:hover {
  border-color: var(--color-border);
  background: var(--color-surface-raised);
}

.platform-nav-item.is-active {
  border-color: color-mix(in srgb, var(--color-accent) 32%, var(--color-border));
  background: var(--color-accent-soft);
}

.platform-nav-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
  font-size: var(--font-sm);
  font-weight: 700;
}

.platform-nav-item.is-active .platform-nav-icon {
  background: var(--color-accent);
  color: var(--color-on-accent);
}

.platform-nav-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.platform-nav-copy strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--font-sm);
  font-weight: 600;
}

.platform-nav-copy small {
  color: var(--color-text-muted);
  font-size: var(--font-xs);
}

.platform-nav-state {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-text-muted);
}

.platform-nav-state.is-ready {
  background: var(--color-success);
}

.platform-detail {
  display: flex;
  min-height: 420px;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4);
}

.platform-detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}

.platform-detail-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.platform-capabilities {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.platform-detail-body {
  min-width: 0;
}

.platform-detail-body :deep(.platform-card) {
  gap: var(--space-3);
}

.platform-unavailable {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  font-size: var(--font-xs);
}

.platform-detail-empty > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.platform-detail-empty strong {
  color: var(--color-text-primary);
  font-size: var(--font-sm);
}

.platform-sidebar-skeleton,
.platform-detail-skeleton {
  min-height: 420px;
}

.job-row {
  flex-wrap: wrap;
  align-items: flex-start;
}

.job-row > .btn {
  margin-top: 2px;
}

/* 服务器出口 IP 卡片 */
.queue-overview-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.queue-overview-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-4);
}

.queue-overview-card strong {
  font-size: 26px;
  line-height: 1;
  color: var(--color-text-primary);
}

.queue-overview-card small,
.queue-overview-label {
  color: var(--color-text-muted);
  font-size: var(--font-xs);
}

.queue-overview-card.is-warning {
  border-color: color-mix(in srgb, var(--color-danger) 26%, var(--color-border));
}

.queue-overview-card.is-warning strong {
  color: var(--color-danger);
}

.queue-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border);
}

.queue-status-tabs,
.queue-filters {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.queue-status-tabs button {
  padding: 5px 9px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-muted);
  font: inherit;
  font-size: var(--font-xs);
  cursor: pointer;
}

.queue-status-tabs button.is-active {
  border-color: var(--color-border);
  background: var(--color-surface-raised);
  color: var(--color-text-primary);
  font-weight: 600;
}

.queue-filters .select-sm,
.queue-filters .input-sm {
  width: auto;
  height: 30px;
  min-height: 30px;
  padding-top: 4px;
  padding-bottom: 4px;
  font-size: var(--font-xs);
}

.queue-filters .select-sm {
  max-width: 180px;
}

.queue-search {
  width: 180px;
}

.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.section-head .card-title {
  margin-bottom: 0;
}

.section-subtitle {
  margin: 4px 0 0;
  color: var(--color-text-muted);
  font-size: var(--font-xs);
}

.egress-card {
  border-color: color-mix(in srgb, var(--color-accent) 22%, var(--color-border));
}
.egress-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}
.egress-main {
  min-width: 0;
}
.egress-desc {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
  font-size: var(--font-xs);
  line-height: 1.6;
}
.egress-desc code {
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--color-surface-raised);
  font-family: "JetBrains Mono", Consolas, monospace;
  font-size: 11px;
}
.egress-actions {
  flex-shrink: 0;
}
.egress-value-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
}
.egress-ip {
  padding: 6px 12px;
  border: 1px dashed color-mix(in srgb, var(--color-accent) 45%, transparent);
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-family: "JetBrains Mono", Consolas, monospace;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.02em;
  cursor: pointer;
  user-select: all;
}
.egress-empty {
  color: var(--color-text-muted);
  font-size: var(--font-sm);
}
.egress-meta {
  color: var(--color-text-muted);
  font-size: 11px;
}
.egress-hint {
  margin: var(--space-2) 0 0;
  color: var(--color-text-secondary);
  font-size: var(--font-xs);
}

@media (max-width: 900px) {
  .queue-overview-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .queue-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .platform-layout {
    grid-template-columns: 1fr;
  }

  .platform-sidebar {
    position: static;
  }

  .platform-nav {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .queue-overview-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: var(--space-2);
  }

  .queue-overview-card {
    padding: var(--space-3);
  }

  .queue-filters > * {
    flex: 1 1 120px;
  }

  .queue-search {
    width: auto;
  }

  .platform-detail {
    padding: var(--space-3);
  }

  .platform-detail-head {
    flex-direction: column;
  }

  .platform-nav {
    grid-template-columns: 1fr;
  }
}
</style>
