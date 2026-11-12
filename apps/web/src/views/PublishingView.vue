<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api/client'
import AppModal from '../components/AppModal.vue'
import StatusTag from '../components/StatusTag.vue'

const capabilities = ref([])
const accounts = ref([])
const jobs = ref([])
const connectorStatuses = ref({})
const loading = ref(true)
const queueRefreshing = ref(false)
const error = ref('')
const notice = ref('')
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
  return [
    { value: 'all', label: '全部平台' },
    ...Array.from(present, ([value, label]) => ({ value, label })),
  ]
})

const queueAccountOptions = computed(() => [
  { value: 'all', label: '全部账号' },
  ...accounts.value.map((account) => ({ value: account.id, label: account.displayName })),
])

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
  accountId: '',
  contentId: '',
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

const accountOptions = computed(() =>
  accounts.value.filter((a) => a.status === 'connected').map((a) => ({ id: a.id, label: `${a.platformLabel} · ${a.displayName}` })),
)

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
  if (!error.value) notice.value = '发布队列已刷新'
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
  loadEgressIp()
})

async function verifyAuth(account) {
  error.value = ''
  try {
    const res = await api.post(`/platform-accounts/${account.id}/test`)
    notice.value = res.note || (res.verified ? '官方授权验证通过。' : res.message)
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
  publishForm.accountId = accountOptions.value[0]?.id || ''
  publishForm.contentId = ''
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

// 渠道目录：已接入平台展示真实能力与账号，未接入平台只作占位
const platformCards = computed(() =>
  capabilities.value.map((cap) => ({
    ...cap,
    accounts: accounts.value.filter((account) => account.platform === cap.platform),
  })),
)

function openBind(platform) {
  if (platform !== 'wechat_official') {
    notice.value = '该平台尚未接入，暂不支持绑定账号'
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
    notice.value =
      publishForm.mode === 'manual'
        ? '已加入排期。请在平台侧手动发布后回到本页回填结果。'
        : '已创建自动发布任务。平台能力未核验时不会真实提交到平台。'
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
    notice.value = '已回填手动发布结果（来源标记为手动记录）。'
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
  if (!window.confirm('删除后将从发布队列移除该任务，确定继续吗？')) return
  deletingJobId.value = job.id
  error.value = ''
  try {
    await api.del(`/publishing/jobs/${job.id}`)
    jobs.value = jobs.value.filter((item) => item.id !== job.id)
    notice.value = '发布任务已删除'
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
      notice.value = '账号凭据已保存并验证通过，后续推送将使用该账号自己的凭据。'
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
  notice.value = ''
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
    notice.value = `验证通过：AppID ${res.probe.appId}，后台现有草稿 ${res.probe.draftTotal} 篇。正式群发仍需在公众号后台操作。`
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
      <div class="row row-tight">
        <template v-if="activeTab === 'queue'">
          <button class="btn btn-ghost btn-sm" :disabled="queueRefreshing" @click="refreshQueue">
            {{ queueRefreshing ? '刷新中…' : '刷新队列' }}
          </button>
          <button class="btn btn-sm" @click="openPublish">新建排期任务</button>
        </template>
        <button v-else class="btn btn-sm" @click="openBind('wechat_official')">绑定公众号账号</button>
      </div>
    </div>

    <div v-if="error" class="banner banner-danger" role="alert">{{ error }}</div>
    <div v-if="notice" class="banner banner-info" role="status">{{ notice }}</div>

    <nav class="page-tabs" role="tablist" aria-label="发布管理分区">
      <button
        type="button"
        role="tab"
        :aria-selected="activeTab === 'queue'"
        :class="{ 'is-active': activeTab === 'queue' }"
        @click="activeTab = 'queue'"
      >
        发布任务
      </button>
      <button
        type="button"
        role="tab"
        :aria-selected="activeTab === 'channels'"
        :class="{ 'is-active': activeTab === 'channels' }"
        @click="activeTab = 'channels'"
      >
        渠道平台
      </button>
    </nav>

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
          <div class="queue-status-tabs" role="tablist" aria-label="任务状态筛选">
            <button v-for="option in queueStatusOptions" :key="option.value" type="button" :class="{ 'is-active': queueStatus === option.value }" @click="queueStatus = option.value">
              {{ option.label }}
            </button>
          </div>
          <div class="queue-filters">
            <select v-if="queuePlatformOptions.length > 2" v-model="queuePlatform" class="select select-sm" aria-label="平台筛选">
              <option v-for="option in queuePlatformOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
            <select v-model="queueAccount" class="select select-sm" aria-label="账号筛选">
              <option v-for="option in queueAccountOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
            </select>
            <input v-model="queueSearch" class="input input-sm queue-search" placeholder="搜索任务" aria-label="搜索任务" />
          </div>
        </div>
        <div v-if="!filteredJobs.length" class="empty">
          <span class="empty-title">没有匹配的任务</span>
          <span>可以切换筛选条件，或刷新队列获取最新任务。</span>
        </div>
        <div v-else class="list">
          <div v-for="job in filteredJobs" :key="job.id" class="list-row">
            <div class="list-main">
              <div class="list-title">
                <router-link v-if="job.contentItemId" class="job-article-link" :to="{ name: 'studio', query: { id: job.contentItemId } }">
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
            <button v-if="canRecord(job)" class="btn btn-ghost btn-sm" @click="openRecord(job)">
              回填手发结果
            </button>
            <button v-if="canRetry(job)" class="btn btn-ghost btn-sm" @click="retryJob(job)">重试</button>
            <button v-if="canCancel(job)" class="btn btn-ghost btn-sm" @click="cancelJob(job)">取消</button>
            <button
              v-if="canDelete(job)"
              class="btn btn-danger btn-sm"
              :disabled="deletingJobId === job.id"
              @click="deleteJob(job)"
            >
              {{ deletingJobId === job.id ? '删除中…' : '删除' }}
            </button>
          </div>
        </div>
      </section>
    </template>
    </template>

    <!-- 渠道平台 -->
    <template v-else>
      <div v-if="loading" class="stack">
        <div class="skeleton" style="height: 160px" />
        <div class="skeleton" style="height: 160px" />
      </div>
      <div v-else class="platform-grid">
        <section
          v-for="platform in platformCards"
          :key="platform.platform"
          class="card platform-card"
          :class="{ 'is-unavailable': !platform.available }"
        >
          <header class="platform-card-head">
            <div>
              <h2 class="card-title">{{ platform.label }}</h2>
              <p class="section-subtitle">
                {{ platform.available ? platform.note : '该平台尚未接入，暂不可绑定或发布' }}
              </p>
            </div>
            <span class="tag" :class="platform.available ? 'tag-success' : 'tag-warning'">
              {{ platform.available ? '已接入' : '未接入' }}
            </span>
          </header>

          <div v-if="platform.available" class="platform-capabilities">
            <span v-if="platform.draftUpload" class="tag tag-success">草稿箱推送</span>
            <span v-if="platform.publish" class="tag tag-success">自动发布</span>
            <span v-if="platform.contentMetrics" class="tag tag-success">内容数据</span>
            <span
              v-if="!platform.draftUpload && !platform.publish && !platform.contentMetrics"
              class="tag tag-warning"
            >暂无可用能力</span>
            <span
              class="tag"
              :class="connectorStatuses[platform.platform]?.configured ? 'tag-success' : 'tag-warning'"
            >
              {{ connectorModeLabel(platform.platform) }}
            </span>
          </div>

          <template v-if="platform.available">
            <div v-if="!platform.accounts.length" class="empty">
              <span class="empty-title">还没有绑定账号</span>
              <span>绑定并验证后即可使用该平台的发布能力。</span>
              <button class="btn btn-sm" @click="openBind(platform.platform)">绑定账号</button>
            </div>
            <div v-else class="list">
              <div v-for="account in platform.accounts" :key="account.id" class="list-row">
                <div class="list-main">
                  <div class="list-title">{{ account.displayName }}</div>
                  <div class="list-meta">
                    {{ account.hasOwnCredential ? `独立凭据 ${account.appIdMasked}` : '使用默认凭据（服务端配置）' }} ·
                    来源：{{ account.authSource === 'app_secret' ? '官方凭据' : account.authSource === 'demo' ? '历史演示账号（无凭据）' : '待验证' }}
                  </div>
                  <div v-if="account.platform === 'wechat_official'" class="account-egress">
                    <span>公众号 IP 白名单</span>
                    <code v-if="egress?.ip">{{ egress.ip }}</code>
                    <span v-else class="account-egress-muted">未检测到</span>
                    <button class="btn btn-ghost btn-xs" :disabled="egressLoading" @click="loadEgressIp(true)">
                      {{ egressLoading ? '检测中…' : '重新检测' }}
                    </button>
                    <button v-if="egress?.ip" class="btn btn-ghost btn-xs" @click="copyEgressIp">
                      {{ egressCopied ? '已复制' : '复制' }}
                    </button>
                    <span class="account-egress-hint">请填入公众号后台「设置与开发 → 基本配置 → IP 白名单」</span>
                  </div>
                </div>
                <StatusTag :status="account.status" />
                <button
                  v-if="account.platform === 'wechat_official' && account.authSource !== 'demo'"
                  class="btn btn-ghost btn-sm"
                  @click="openAcctCred(account)"
                >
                  修改凭据
                </button>
                <button
                  v-if="account.authSource !== 'demo'"
                  class="btn btn-ghost btn-sm"
                  @click="verifyAuth(account)"
                >
                  验证授权
                </button>
                <button
                  v-if="account.status !== 'unbound'"
                  class="btn btn-danger btn-sm"
                  @click="unbind(account)"
                >
                  解绑
                </button>
              </div>
            </div>
            <div class="row">
              <button class="btn btn-ghost btn-sm" @click="openBind(platform.platform)">绑定新账号</button>
            </div>
          </template>

          <div v-else class="platform-unavailable">
            <span>接入后会在这里出现账号绑定与能力配置。</span>
            <button class="btn btn-sm" disabled>暂不可用</button>
          </div>
        </section>
      </div>
    </template>

    <AppModal v-if="showWechatBind" title="绑定并验证公众号" description="同一平台可绑定多个账号；每个账号可持有自己的凭据。" @close="showWechatBind = false">
      <div class="stack">
        <label class="field">
          <span class="field-label">账号备注</span>
          <input v-model="wechatBindForm.displayName" class="input" placeholder="例如：主号 / 分公司号" />
        </label>
        <label class="row" style="justify-content: flex-start; gap: 8px; cursor: pointer">
          <input v-model="wechatBindForm.useOwnCred" type="checkbox" />
          <span>为该账号使用独立 AppID/AppSecret（不勾选则使用默认凭据）</span>
        </label>
        <template v-if="wechatBindForm.useOwnCred">
          <label class="field">
            <span class="field-label">AppID</span>
            <input v-model="wechatBindForm.appId" class="input" placeholder="wx 开头的应用 ID" autocomplete="off" />
          </label>
          <label class="field">
            <span class="field-label">AppSecret</span>
            <input
              v-model="wechatBindForm.appSecret"
              class="input"
              type="password"
              placeholder="公众号后台生成，仅保存加密副本"
              autocomplete="new-password"
            />
          </label>
        </template>
        <div class="field-hint">
          AppID / AppSecret 在公众号后台「设置与开发 → 基本配置」获取；绑定会真实调用微信接口验证，
          请先在公众号后台「IP 白名单」中加入本机出口 IP。同一 AppID 重复绑定会更新原账号，不同 AppID 会新建账号。
        </div>
        <div class="row">
          <div class="spacer" />
          <button class="btn btn-ghost" @click="showWechatBind = false">取消</button>
          <button
            class="btn"
            :disabled="wechatBusy || (wechatBindForm.useOwnCred && (!wechatBindForm.appId.trim() || !wechatBindForm.appSecret.trim()))"
            @click="bindWechat"
          >
            {{ wechatBusy ? '验证中…' : '绑定并验证' }}
          </button>
        </div>
      </div>
    </AppModal>

    <AppModal
      v-if="acctCredTarget"
      :title="`修改凭据 · ${acctCredTarget.displayName}`"
      description="保存前会真实调用微信接口验证；Secret 加密存储且永不回传。"
      @close="acctCredTarget = null"
    >
      <div class="stack">
        <div v-if="acctCredError" class="banner banner-danger" role="alert">{{ acctCredError }}</div>
        <label class="field">
          <span class="field-label">AppID</span>
          <input v-model="acctCredForm.appId" class="input" placeholder="wx 开头的应用 ID" autocomplete="off" />
        </label>
        <label class="field">
          <span class="field-label">AppSecret</span>
          <input
            v-model="acctCredForm.appSecret"
            class="input"
            type="password"
            placeholder="公众号后台生成，仅保存加密副本"
            autocomplete="new-password"
          />
        </label>
        <div class="row">
          <div class="spacer" />
          <button class="btn btn-ghost" @click="acctCredTarget = null">取消</button>
          <button
            class="btn"
            :disabled="acctCredBusy || !acctCredForm.appId.trim() || !acctCredForm.appSecret.trim()"
            @click="saveAccountCred"
          >
            {{ acctCredBusy ? '保存并验证中…' : '保存并验证' }}
          </button>
        </div>
      </div>
    </AppModal>

    <AppModal v-if="showPublish" title="新建发布任务" description="先预览校验，确认后才会入队。" @close="showPublish = false">
      <div class="stack">
        <label class="field">
          <span class="field-label">目标账号</span>
          <select v-model="publishForm.accountId" class="select">
            <option value="">选择已连接账号</option>
            <option v-for="option in accountOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
          </select>
          <span v-if="!accountOptions.length" class="field-hint">还没有已连接账号，请先在列表中绑定并标记已连接。</span>
        </label>
        <label v-if="!manualEntry" class="field">
          <span class="field-label">选择创作空间稿件</span>
          <select v-model="publishForm.contentId" class="select" :disabled="articleLoading" @change="onSelectArticle">
            <option value="">请选择要发布的稿件</option>
            <option v-for="item in contents" :key="item.id" :value="item.id">{{ item.title }}</option>
          </select>
          <span v-if="!contents.length" class="field-hint">
            创作空间还没有稿件，请先去创作空间写一篇，或切换为手动填写。
          </span>
          <span v-else class="field-hint">标题与正文会自动取自稿件的最新内容，无需重复输入。</span>
        </label>

        <div v-if="selectedArticle && !manualEntry" class="card-flat article-preview">
          <div class="row row-tight">
            <strong>{{ selectedArticle.title }}</strong>
            <span class="tag">{{ selectedArticle.status === 'published' ? '已发布' : '草稿' }}</span>
          </div>
          <p class="article-excerpt">{{ (selectedArticle.draftBody ?? publishForm.body ?? '').slice(0, 160) || '稿件暂无正文' }}</p>
          <router-link class="article-link" :to="{ name: 'studio', query: { id: selectedArticle.id } }">
            在创作空间打开
          </router-link>
        </div>

        <p v-if="articleLoading" class="field-hint">正在读取稿件内容…</p>

        <label v-if="manualEntry" class="field">
          <span class="field-label">标题</span>
          <input v-model="publishForm.title" class="input" />
        </label>
        <label v-if="manualEntry" class="field">
          <span class="field-label">正文</span>
          <textarea v-model="publishForm.body" class="textarea" style="min-height: 120px" />
        </label>
        <button v-if="!manualEntry" type="button" class="link-btn" @click="manualEntry = true; publishForm.contentId = ''; selectedArticle = null">
          没有对应稿件，改用手动填写
        </button>
        <button v-else type="button" class="link-btn" @click="manualEntry = false">
          返回从创作空间选择稿件
        </button>
        <label class="field">
          <span class="field-label">计划时间（可选）</span>
          <input v-model="publishForm.scheduledAt" type="datetime-local" class="input" />
        </label>

        <div class="field">
          <span class="field-label">发布方式</span>
          <p class="field-hint" style="margin: 0">
            本页只做「排期 + 手动发布」与结果回填。需要直接推送公众号草稿箱，请到创作空间右侧「发布」面板操作。
          </p>
        </div>

        <div class="row">
          <button class="btn btn-ghost btn-sm" @click="doPreview">校验并预览</button>
        </div>

        <div v-if="previewResult" class="card-flat">
          <div class="row row-tight">
            <span class="tag">{{ previewResult.platformLabel }}</span>
            <span v-if="previewResult.issues.length" class="tag tag-warning">{{ previewResult.issues.length }} 项待检查</span>
            <span v-else class="tag tag-success">校验通过</span>
          </div>
          <ul v-if="previewResult.issues.length" style="margin: var(--space-2) 0 0; padding-left: 18px; font-size: var(--font-sm)">
            <li v-for="(issue, index) in previewResult.issues" :key="index">
              {{ issue.message }}
            </li>
          </ul>
        </div>

        <p v-if="publishError" class="field-error" role="alert">{{ publishError }}</p>

        <div class="row">
          <div class="spacer" />
          <button class="btn btn-ghost" @click="showPublish = false">取消</button>
          <button class="btn" :disabled="!publishForm.accountId || (!manualEntry && !publishForm.contentId) || articleLoading" @click="submitJob">确认并提交</button>
        </div>
      </div>
    </AppModal>

    <AppModal
      v-if="recordingJob"
      title="回填手动发布结果"
      description="你在平台侧发布完成后填写；此处记录会标注来源为手动。"
      @close="recordingJob = null"
    >
      <div class="stack">
        <label class="field">
          <span class="field-label">平台内容 ID（可选）</span>
          <input v-model="recordForm.remoteContentId" class="input" placeholder="平台返回的帖子 ID" />
        </label>
        <label class="field">
          <span class="field-label">链接（可选）</span>
          <input v-model="recordForm.permalink" class="input" placeholder="https://" />
        </label>
        <div class="row">
          <div class="spacer" />
          <button class="btn btn-ghost" @click="recordingJob = null">取消</button>
          <button class="btn" @click="submitRecord">确认回填</button>
        </div>
      </div>
    </AppModal>
  </div>
</template>

<style scoped>
/* 稿件关联 */
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

.platform-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: var(--space-3);
}

.platform-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.platform-card.is-unavailable {
  opacity: 0.72;
}

.platform-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-2);
}

.platform-capabilities {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.platform-unavailable {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  font-size: var(--font-xs);
}

/* 平台账号专属配置 */
.account-egress {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  padding: 7px 9px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-raised);
  font-size: 11px;
}

.account-egress code {
  padding: 2px 5px;
  border-radius: 3px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-family: 'JetBrains Mono', Consolas, monospace;
}

.account-egress-muted,
.account-egress-hint {
  color: var(--color-text-muted);
}

.account-egress-hint {
  flex-basis: 100%;
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
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
  padding: var(--space-3) 0;
  border-top: 1px solid var(--color-border);
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
  height: 30px;
  padding-top: 4px;
  padding-bottom: 4px;
  font-size: var(--font-xs);
}

.queue-search {
  width: 150px;
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

  .platform-grid {
    grid-template-columns: 1fr;
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
}
</style>
