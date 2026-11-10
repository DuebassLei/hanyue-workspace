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
const error = ref('')
const notice = ref('')

const showBind = ref(false)
const bindForm = reactive({ platform: 'wechat_official', displayName: '' })

const showPublish = ref(false)
const publishForm = reactive({
  accountId: '',
  title: '',
  body: '',
  scheduledAt: '',
  variantId: '',
  mode: 'manual',
})
const idempotencyKey = ref('')
const previewResult = ref(null)
const publishError = ref('')

const accountOptions = computed(() =>
  accounts.value.filter((a) => a.status === 'connected').map((a) => ({ id: a.id, label: `${a.platformLabel} · ${a.displayName}` })),
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [caps, accs, jobList, conns] = await Promise.all([
      api.get('/platforms/capabilities'),
      api.get('/platform-accounts'),
      api.get('/publishing/jobs'),
      api.get('/platforms/connectors/status'),
    ])
    capabilities.value = caps.platforms
    accounts.value = accs
    jobs.value = jobList
    connectorStatuses.value = Object.fromEntries(conns.platforms.map((s) => [s.platform, s]))
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
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

async function bindAccount() {
  try {
    const created = await api.post('/platform-accounts', {
      platform: bindForm.platform,
      displayName: bindForm.displayName,
    })
    accounts.value.push(created)
    showBind.value = false
    notice.value = '账号已创建；点击「验证授权」用服务端配置的公众号凭据做真实验证。'
  } catch (err) {
    error.value = err.message
  }
}

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
  publishForm.title = ''
  publishForm.body = ''
  publishForm.scheduledAt = ''
  publishForm.variantId = ''
  publishForm.mode = 'manual'
  previewResult.value = null
  publishError.value = ''
  idempotencyKey.value = `pub-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  showPublish.value = true
}

const currentCapability = computed(() =>
  capabilities.value.find((c) => c.platform === accounts.value.find((a) => a.id === publishForm.accountId)?.platform),
)

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
        <p class="page-sub">账号绑定、排期队列与发布结果管理</p>
      </div>
      <div class="row row-tight">
        <button class="btn btn-ghost btn-sm" @click="showBind = true">绑定账号</button>
        <button class="btn btn-sm" @click="openPublish">新建发布任务</button>
      </div>
    </div>

    <div v-if="error" class="banner banner-danger" role="alert">{{ error }}</div>
    <div v-if="notice" class="banner banner-info" role="status">{{ notice }}</div>

    <div class="banner banner-warning">
      公众号排版与草稿推送已整合到创作空间→右侧「发布」面板，此处管理账号绑定与发布队列。
    </div>

    <!-- 服务器出口 IP：公众号 IP 白名单要填的就是这个 -->
    <section class="card egress-card">
      <div class="egress-row">
        <div class="egress-main">
          <h2 class="card-title">服务器公网出口 IP</h2>
          <p class="egress-desc">
            公众号后台「设置与开发 → 基本配置 → IP 白名单」需要填这个地址，否则推送会报
            <code>errcode=40164</code>。
          </p>
        </div>
        <div class="egress-actions">
          <button class="btn btn-ghost btn-sm" :disabled="egressLoading" @click="loadEgressIp(true)">
            {{ egressLoading ? '检测中…' : '重新检测' }}
          </button>
        </div>
      </div>
      <div v-if="egressLoading && !egress" class="skeleton" style="height: 44px; margin-top: var(--space-3)" />
      <div v-else class="egress-value-row">
        <span v-if="egress?.ip" class="egress-ip" @click="copyEgressIp">{{ egress.ip }}</span>
        <span v-else class="egress-empty">未检测到</span>
        <button v-if="egress?.ip" class="btn btn-ghost btn-sm" @click="copyEgressIp">
          {{ egressCopied ? '已复制' : '复制' }}
        </button>
        <span v-if="egress?.cached" class="egress-meta">缓存结果</span>
      </div>
      <p v-if="egress?.hint" class="egress-hint">{{ egress.hint }}</p>
    </section>

    <div v-if="loading" class="stack">
      <div class="skeleton" style="height: 120px" />
      <div class="skeleton" style="height: 160px" />
    </div>

    <template v-else>
      <section class="card">
        <h2 class="card-title">平台能力</h2>
        <div class="table-scroll">
          <table class="data">
            <thead>
              <tr>
                <th>平台</th>
                <th>接入方式</th>
                <th>草稿箱推送</th>
                <th>自动发布</th>
                <th>内容数据</th>
                <th>备注</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="cap in capabilities" :key="cap.platform">
                <td>{{ cap.label }}</td>
                <td>
                  <span class="tag" :class="connectorStatuses[cap.platform]?.configured ? 'tag-success' : 'tag-warning'">
                    {{ connectorModeLabel(cap.platform) }}
                  </span>
                </td>
                <td><span class="tag" :class="cap.draftUpload ? 'tag-success' : 'tag-warning'">{{ cap.draftUpload ? '支持' : '未接入' }}</span></td>
                <td><span class="tag" :class="cap.publish ? 'tag-success' : ''">{{ cap.publish ? '支持' : '未接入' }}</span></td>
                <td><span class="tag" :class="cap.contentMetrics ? 'tag-success' : ''">{{ cap.contentMetrics ? '支持' : '未接入' }}</span></td>
                <td class="list-meta">
                  {{ cap.note }}
                  <span v-if="connectorStatuses[cap.platform]?.reason"> · {{ connectorStatuses[cap.platform].reason }}</span>
                  <span v-if="cap.evidenceUrl"> · 证据：{{ cap.evidenceDate }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="card">
        <h2 class="card-title">已绑定账号</h2>
        <div v-if="!accounts.length" class="empty">
          <span class="empty-title">还没有绑定平台账号</span>
          <span>绑定后才能配置自动发布开关与数据同步。</span>
          <button class="btn btn-sm" @click="showBind = true">绑定账号</button>
        </div>
        <div v-else class="list">
          <div v-for="account in accounts" :key="account.id" class="list-row">
            <div class="list-main">
              <div class="list-title">{{ account.platformLabel }} · {{ account.displayName }}</div>
              <div class="list-meta">
                {{ account.hasOwnCredential ? `独立凭据 ${account.appIdMasked}` : '使用默认凭据（服务端配置）' }} ·
                来源：{{ account.authSource === 'app_secret' ? '官方凭据' : account.authSource === 'demo' ? '历史演示账号（无凭据）' : '待验证' }} ·
                自动发布：{{ account.autoPublishEnabled ? '开' : '关' }}
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
      </section>

      <section class="card">
        <h2 class="card-title">发布队列</h2>
        <div v-if="!jobs.length" class="empty">
          <span class="empty-title">队列是空的</span>
          <span>创建任务后会显示在这里，可取消或核实后重试。</span>
        </div>
        <div v-else class="list">
          <div v-for="job in jobs" :key="job.id" class="list-row">
            <div class="list-main">
              <div class="list-title">{{ job.platformLabel }} · 任务 {{ job.id.slice(0, 8) }}</div>
              <div class="list-meta">
                请求号 {{ job.requestId.slice(0, 8) }} · 尝试 {{ job.attemptCount }} 次
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
          </div>
        </div>
      </section>
    </template>

    <AppModal v-if="showBind" title="绑定公众号账号" description="凭据在服务端配置，创建后用真实验证探针校验。" @close="showBind = false">
      <div class="stack">
        <div class="field">
          <span class="field-label">平台</span>
          <div class="row" style="justify-content: flex-start">
            <span class="tag tag-accent">微信公众号（草稿箱）</span>
          </div>
          <span class="field-hint">
            凭据在服务端配置（WECHAT_MP_APPID / WECHAT_MP_APPSECRET）；创建账号后点「验证授权」做真实校验，
            验证通过即可使用排版编辑器推送草稿到公众号后台。
          </span>
        </div>
        <label class="field">
          <span class="field-label">账号备注</span>
          <input v-model="bindForm.displayName" class="input" placeholder="例如：主号" />
        </label>
        <div class="row">
          <div class="spacer" />
          <button class="btn btn-ghost" @click="showBind = false">取消</button>
          <button class="btn" @click="bindAccount">创建</button>
        </div>
      </div>
    </AppModal>

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
        <label class="field">
          <span class="field-label">标题</span>
          <input v-model="publishForm.title" class="input" />
        </label>
        <label class="field">
          <span class="field-label">正文</span>
          <textarea v-model="publishForm.body" class="textarea" style="min-height: 120px" />
        </label>
        <label class="field">
          <span class="field-label">计划时间（可选）</span>
          <input v-model="publishForm.scheduledAt" type="datetime-local" class="input" />
        </label>

        <div class="field">
          <span class="field-label">发布方式</span>
          <div class="segmented" role="radiogroup" aria-label="发布方式">
            <button
              class="segmented-item"
              :class="{ 'is-active': publishForm.mode === 'manual' }"
              role="radio"
              :aria-checked="publishForm.mode === 'manual'"
              @click="publishForm.mode = 'manual'"
            >
              排期 + 手动发布
            </button>
            <button
              class="segmented-item"
              :class="{ 'is-active': publishForm.mode === 'auto' }"
              role="radio"
              :aria-checked="publishForm.mode === 'auto'"
              :disabled="!currentCapability?.publish"
              @click="publishForm.mode = 'auto'"
            >
              自动发布
            </button>
          </div>
          <span class="field-hint">
            {{
              publishForm.mode === 'manual'
                ? '本页做排期与结果记录；公众号草稿推送请在创作空间右侧「发布」面板操作。'
                : currentCapability?.publish
                  ? '能力核验通过、账号已授权且开启自动发布时可用。'
                  : '正式群发接口未接入，暂不可用；公众号内容请走创作空间的草稿箱推送。'
            }}
          </span>
        </div>

        <div class="row">
          <button class="btn btn-ghost btn-sm" @click="doPreview">校验并预览</button>
        </div>

        <div v-if="previewResult" class="card-flat">
          <div class="row row-tight">
            <span class="tag">{{ previewResult.platformLabel }}</span>
            <span v-if="previewResult.canAutoPublish" class="tag tag-success">可以自动发布</span>
            <span v-else class="tag tag-warning">{{ previewResult.blockReason }}</span>
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
          <button class="btn" :disabled="!publishForm.accountId" @click="submitJob">确认并提交</button>
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
/* 服务器出口 IP 卡片 */
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
</style>
