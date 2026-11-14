<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCheckbox,
  NCheckboxGroup,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSelect,
  NSpace,
  NTab,
  NTabs,
  NTag,
} from 'naive-ui'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { toast } from '../ui/feedback'
import StatusTag from '../components/StatusTag.vue'

const session = useSessionStore()
const tab = ref('theme')

const connections = ref([])
const skills = ref([])
const error = ref('')

const showConnection = ref(false)
const editingId = ref('')
const connForm = reactive({
  displayName: '',
  providerType: 'openai_compatible',
  baseUrl: '',
  apiKey: '',
  modelCode: '',
  modelType: 'text',
  capabilities: 'text.generate,text.stream',
})
// 模型类型决定能力登记：文本模型用于 AI 成文，生图模型用于素材页 AI 生图
const MODEL_TYPES = [
  {
    value: 'text',
    label: '文本模型',
    desc: 'AI 成文、内容优化、卡片摘要',
    caps: 'text.generate,text.stream',
  },
  {
    value: 'image',
    label: '生图模型',
    desc: '素材页 AI 生图、封面配图',
    caps: 'image.generate',
  },
]

function selectModelType(type) {
  connForm.modelType = type
  const found = MODEL_TYPES.find((item) => item.value === type)
  if (found) connForm.capabilities = found.caps
}

function currentModelType() {
  return MODEL_TYPES.find((item) => item.value === connForm.modelType)
}

/** 连接下已登记模型的类型标签与样式 */
function modelTagInfo(model) {
  const caps = model.capabilities || []
  if (caps.includes('image.generate')) return { label: '生图', tagClass: 'tag-info' }
  if (caps.some((item) => item.startsWith('text.'))) return { label: '文本', tagClass: 'tag-accent' }
  return { label: '未分类', tagClass: '' }
}

// 编辑时密钥留空表示不改动；只有显式勾选才清空
const clearKey = ref(false)

const policy = ref(null)
// Agent 运行模式与数据路径，全部来自 /agent/status 的真实探测
const agentStatus = ref(null)
const copiedLabel = ref('')
const copyFailed = ref(false)

async function copyPath(item) {
  let ok = false
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(item.value)
      ok = true
    }
  } catch {
    ok = false
  }
  if (!ok) {
    // 剪贴板权限被拒时退回选区复制；仍失败就提示手动选中，不假装成功
    try {
      const area = document.createElement('textarea')
      area.value = item.value
      area.setAttribute('readonly', '')
      area.style.position = 'fixed'
      area.style.opacity = '0'
      document.body.appendChild(area)
      area.select()
      ok = document.execCommand('copy')
      document.body.removeChild(area)
    } catch {
      ok = false
    }
  }
  copyFailed.value = !ok
  copiedLabel.value = item.label
  setTimeout(() => {
    if (copiedLabel.value === item.label) {
      copiedLabel.value = ''
      copyFailed.value = false
    }
  }, 2200)
}

function copyLabel(item) {
  if (copiedLabel.value !== item.label) return '复制'
  return copyFailed.value ? '请手动复制' : '已复制'
}

function checkedAtLabel(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  // 带日期，避免只看「00:06」分不清是哪天的探测结果
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const THEMES = [
  { value: 'system', label: '跟随系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '暗色' },
]

async function load() {
  try {
    const [conns, skillList, policyData, statusData] = await Promise.all([
      api.get('/models/connections'),
      api.get('/skills'),
      api.get('/agent/policy'),
      api.get('/agent/status').catch(() => null),
    ])
    connections.value = conns
    skills.value = skillList
    policy.value = policyData
    agentStatus.value = statusData
  } catch (err) {
    error.value = err.message
  }
}

onMounted(() => {
  load()
  loadThemeOptions()
  refreshLogSummary()
})

/* ---------------- 系统日志（运维查看器） ---------------- */

const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
const logService = ref('api')
const logMinLevel = ref('')
const logKeyword = ref('')
const logRequestId = ref('')
const logEntries = ref([])
const logOffset = ref(null) // null = tail 模式；数字 = 增量轮询偏移
const logLoading = ref(false)
const logError = ref('')
const logAuto = ref(true)
const logTruncated = ref(false)
const logSummary = ref({})
const logStreamRef = ref(null)
let logTimer = null

// 用户向上翻阅历史时暂停自动滚底，回到底部后恢复
const logPinnedToBottom = ref(true)

function onLogStreamScroll() {
  const el = logStreamRef.value
  if (!el) return
  logPinnedToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

async function scrollLogToBottom(force = false) {
  await nextTick()
  const el = logStreamRef.value
  if (el && (force || logPinnedToBottom.value)) el.scrollTop = el.scrollHeight
}

const logErrorCount = computed(() =>
  Object.values(logSummary.value).reduce((sum, item) => sum + (item.errorCount || 0), 0),
)

function logQuery(extra = {}) {
  const params = new URLSearchParams()
  params.set('service', extra.service ?? logService.value)
  if (extra.offset != null) params.set('offset', String(extra.offset))
  if (logMinLevel.value) params.set('level', logMinLevel.value)
  if (logKeyword.value.trim()) params.set('keyword', logKeyword.value.trim())
  if (logRequestId.value.trim()) params.set('requestId', logRequestId.value.trim())
  return `/logs?${params.toString()}`
}

async function loadLogsTail() {
  logLoading.value = true
  logError.value = ''
  try {
    const data = await api.get(logQuery({ offset: null }))
    logEntries.value = data.entries || []
    logOffset.value = data.nextOffset ?? 0
    logTruncated.value = Boolean(data.hasMore)
    await scrollLogToBottom(true)
    if (data.services && !data.services.includes(logService.value)) {
      // 该服务暂无日志文件：保持空列表即可，不强制切换
    }
  } catch (err) {
    logError.value = err.message
  } finally {
    logLoading.value = false
  }
}

async function pollLogs() {
  if (document.hidden || logLoading.value) return
  logLoading.value = true
  try {
    if (logOffset.value == null) {
      await loadLogsTail()
      return
    }
    const data = await api.get(logQuery({ offset: logOffset.value }))
    if (data.entries?.length) {
      logEntries.value = [...logEntries.value, ...data.entries].slice(-2000)
      await scrollLogToBottom()
    }
    // nextOffset 归零说明日志轮转/清空：重置为 tail 重新拉尾部
    logOffset.value = data.nextOffset === 0 ? null : data.nextOffset
    if (data.truncated) await loadLogsTail()
  } catch {
    // 轮询失败静默重试，不打断页面；连续失败由下一次手动刷新暴露
  } finally {
    logLoading.value = false
  }
}

function startLogPolling() {
  stopLogPolling()
  if (logAuto.value) logTimer = setInterval(pollLogs, 2500)
}

function stopLogPolling() {
  if (logTimer) {
    clearInterval(logTimer)
    logTimer = null
  }
}

function toggleLogAuto() {
  logAuto.value = !logAuto.value
  startLogPolling()
}

async function refreshLogSummary() {
  try {
    const data = await api.get('/logs/summary')
    logSummary.value = data.services || {}
  } catch {
    /* 概览失败不影响主功能 */
  }
}

watch(tab, (value) => {
  if (value === 'logs') {
    loadLogsTail()
    refreshLogSummary()
    startLogPolling()
  } else {
    stopLogPolling()
  }
})

onBeforeUnmount(stopLogPolling)


function resetForm() {
  editingId.value = ''
  clearKey.value = false
  connForm.displayName = ''
  connForm.providerType = 'openai_compatible'
  connForm.baseUrl = ''
  connForm.apiKey = ''
  connForm.modelCode = ''
  connForm.modelType = 'text'
  connForm.capabilities = 'text.generate,text.stream'
}

function openCreate() {
  resetForm()
  showConnection.value = true
}

function openEdit(conn) {
  resetForm()
  editingId.value = conn.id
  connForm.displayName = conn.displayName
  connForm.providerType = conn.providerType
  connForm.baseUrl = conn.baseUrl || ''
  connForm.modelCode = conn.models?.[0]?.modelCode || ''
  const caps = conn.models?.[0]?.capabilities || ['text.generate', 'text.stream']
  connForm.capabilities = caps.join(',')
  connForm.modelType = caps.includes('image.generate') ? 'image' : 'text'
  showConnection.value = true
}

async function saveConnection() {
  error.value = ''
  if (!connForm.displayName.trim()) {
    error.value = '请填写连接名称'
    return
  }
  if (connForm.baseUrl && !connForm.baseUrl.startsWith('https://')) {
    error.value = 'Base URL 必须使用 HTTPS'
    return
  }
  try {
    if (editingId.value) {
      const payload = {
        displayName: connForm.displayName,
        providerType: connForm.providerType,
        baseUrl: connForm.baseUrl,
      }
      // 只在用户真的输入了新密钥或勾选清空时才传，避免误清已有凭据
      if (clearKey.value) payload.apiKey = ''
      else if (connForm.apiKey.trim()) payload.apiKey = connForm.apiKey
      const updated = await api.patch(`/models/connections/${editingId.value}`, payload)
      const idx = connections.value.findIndex((c) => c.id === updated.id)
      if (idx >= 0) connections.value[idx] = updated
      await syncModelCode(updated, connForm.modelCode, false, connForm.capabilities)
      showConnection.value = false
      toast('连接已更新。')
      return
    }
    const conn = await api.post('/models/connections', {
      displayName: connForm.displayName,
      providerType: connForm.providerType,
      baseUrl: connForm.baseUrl,
      apiKey: connForm.apiKey,
    })
    await syncModelCode(conn, connForm.modelCode, true, connForm.capabilities)
    connections.value.push(conn)
    showConnection.value = false
    toast('连接已保存；密钥只在服务端加密存储，接口不会返回明文。')
  } catch (err) {
    error.value = err.message
  }
}

/** 新增或修改该连接下的第一个模型代号，保持与「模型代号」输入一致 */
async function syncModelCode(conn, code, isNew = false, capabilities = '') {
  const trimmed = (code || '').trim()
  const caps = capabilities
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
  const capList = caps.length ? caps : ['text.generate', 'text.stream']
  const existing = conn.models?.[0]
  if (!trimmed) {
    if (!isNew && existing?.id) {
      await api.del(`/models/catalog/${existing.id}`)
      conn.models = []
    }
    return
  }
  const capsChanged =
    existing && JSON.stringify(existing.capabilities || []) !== JSON.stringify(capList)
  if (existing && (existing.modelCode !== trimmed || capsChanged)) {
    const model = await api.patch(`/models/catalog/${existing.id}`, {
      modelCode: trimmed,
      capabilities: capList,
    })
    conn.models = [model]
  } else if (!existing) {
    const model = await api.post('/models/catalog', {
      connectionId: conn.id,
      modelCode: trimmed,
      capabilities: capList,
    })
    conn.models = [model]
  }
}

async function testConnection(conn) {
  error.value = ''
  try {
    const result = await api.post(`/models/connections/${conn.id}/test`)
    conn.status = result.status
    const detail = result.httpStatus
      ? `（HTTP ${result.httpStatus}${result.latencyMs ? ` · ${result.latencyMs}ms` : ''}）`
      : ''
    toast(`${result.message}${detail}`, result.verified ? 'success' : 'warning')
    if (!result.verified) error.value = '连接未通过真实连通性验证，状态已如实记录。'
  } catch (err) {
    error.value = err.message
  }
}

async function removeConnection(conn) {
  await api.del(`/models/connections/${conn.id}`)
  connections.value = connections.value.filter((c) => c.id !== conn.id)
}

const expandedSkillId = ref('')

async function toggleSkill(skill) {
  try {
    // 只提交用户勾选的工具；未展开过时默认沿用既有授权或全部声明
    const granted = skill.grantedToolIds?.length
      ? skill.grantedToolIds
      : [...(skill.allowedToolIds || [])]
    const updated = await api.patch(`/skills/${skill.id}`, {
      enabled: !skill.enabled,
      grantedToolIds: granted,
      approvalPolicy: skill.approvalPolicy || 'risk_based',
    })
    Object.assign(skill, updated)
  } catch (err) {
    error.value = err.message
  }
}

function toggleGrantPanel(skill) {
  expandedSkillId.value = expandedSkillId.value === skill.id ? '' : skill.id
  if (!skill.grantedToolIds?.length) {
    skill.grantedToolIds = [...(skill.allowedToolIds || [])]
  }
}

// ---------- 写作风格（自定义 Skill）----------
const RISK_LABELS = {
  read_only: '只读',
  reversible_write: '可回滚写入',
  external_side_effect: '外部副作用',
  forbidden: '禁止',
}
// 写作风格的默认排版主题：从 /wechat-mp/themes 拉取系统预设
// （manifest 校验只认预设，自定义主题不能出现在这里）；拉取失败退回内置快照。
const THEME_FALLBACK = [
  { value: 'ink-green', label: '墨绿简约 · 知识/职场/干货' },
  { value: 'deep-blue', label: '深海蓝 · 科技/财经/分析' },
  { value: 'warm-orange', label: '暖橙生活 · 生活/情感/美食' },
  { value: 'mono', label: '极简黑白 · 深度长文/访谈' },
  { value: 'terracotta-warm', label: '暖陶土 · 工具推荐/产品测评' },
  { value: 'blue-business', label: '商务蓝 · 职场干货/产品教程' },
]
const THEME_OPTIONS = ref(THEME_FALLBACK)
async function loadThemeOptions() {
  try {
    const data = await api.get('/wechat-mp/themes')
    if (data.presets?.length) {
      THEME_OPTIONS.value = data.presets.map((t) => ({
        value: t.id,
        label: t.description ? `${t.name} · ${t.description}` : t.name,
      }))
    }
  } catch {
    // 排版服务不可达时用内置快照，不阻塞设置页
  }
}
const providerOptions = [
  { value: 'openai_compatible', label: 'OpenAI 兼容' },
  { value: 'vendor_native', label: '供应商原生（适配器未接入）' },
]

const showStyle = ref(false)
const styleEditingId = ref('')
// 定制来源：编辑内置风格时记录其 id，保存时创建个人定制副本而非直接修改
const styleForkSourceId = ref('')
const styleForm = reactive({
  skillKey: '',
  displayName: '',
  description: '',
  tone: '',
  styleRules: '',
  defaultTheme: 'ink-green',
  promptTemplate: '',
})

/** 写作风格 = 自定义 Skill，或带 tone 的内置风格（与后端 styleSkills 判定保持一致） */
function isStyle(skill) {
  return skill.source === 'custom' || Boolean(skill.manifest?.tone)
}

const styleSkills = computed(() => skills.value.filter(isStyle))
/** 流程型 Skill：供 Agent 编排调用，与写作风格分区展示，避免同一批条目出现两次 */
const flowSkills = computed(() => skills.value.filter((skill) => !isStyle(skill)))

function openStyleCreate() {
  styleEditingId.value = ''
  styleForkSourceId.value = ''
  styleForm.skillKey = ''
  styleForm.displayName = ''
  styleForm.description = ''
  styleForm.tone = ''
  styleForm.styleRules = ''
  styleForm.defaultTheme = 'ink-green'
  styleForm.promptTemplate = ''
  showStyle.value = true
}

function openStyleEdit(skill) {
  styleEditingId.value = skill.source === 'custom' ? skill.id : ''
  styleForkSourceId.value = skill.source === 'custom' ? '' : skill.id
  const manifest = skill.manifest || {}
  styleForm.skillKey = skill.skillKey
  styleForm.displayName = skill.displayName
  styleForm.description = skill.description || ''
  styleForm.tone = manifest.tone || ''
  styleForm.styleRules = (manifest.styleRules || []).join('\n')
  styleForm.defaultTheme = manifest.defaultTheme || 'ink-green'
  styleForm.promptTemplate = manifest.promptTemplate || ''
  showStyle.value = true
}

function styleTagLabel(skill) {
  if (skill.customized) return '已定制'
  return skill.source === 'custom' ? '自定义' : '内置'
}

const styleModalTitle = computed(() => {
  if (styleEditingId.value) return '编辑写作风格'
  if (styleForkSourceId.value) return '定制内置风格'
  return '新建写作风格'
})

function buildStyleManifest() {
  const manifest = {}
  if (styleForm.tone.trim()) manifest.tone = styleForm.tone.trim()
  const rules = styleForm.styleRules
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  if (rules.length) manifest.styleRules = rules
  if (styleForm.defaultTheme) manifest.defaultTheme = styleForm.defaultTheme
  if (styleForm.promptTemplate.trim()) manifest.promptTemplate = styleForm.promptTemplate.trim()
  return manifest
}

async function saveStyle() {
  error.value = ''
  if (!styleForm.displayName.trim()) {
    error.value = '请填写风格名称'
    return
  }
  try {
    if (styleEditingId.value) {
      const updated = await api.put(`/skills/${styleEditingId.value}`, {
        displayName: styleForm.displayName,
        description: styleForm.description,
        manifest: buildStyleManifest(),
      })
      const idx = skills.value.findIndex((s) => s.id === updated.id)
      if (idx >= 0) skills.value[idx] = updated
      toast('写作风格已更新，下次 AI 成文即生效。')
    } else if (styleForkSourceId.value) {
      // 定制内置风格：创建个人副本，内置原版在列表中被副本替换
      await api.post(`/skills/${styleForkSourceId.value}/customize`, {
        displayName: styleForm.displayName,
        description: styleForm.description,
        manifest: buildStyleManifest(),
      })
      showStyle.value = false
      await load()
      toast('内置风格已定制，仅对当前账号生效；删除副本可恢复默认。')
      return
    } else {
      const key = styleForm.skillKey.trim() || `style-${Date.now()}`
      if (!/^[a-z0-9][a-z0-9-]{2,}$/.test(key)) {
        error.value = '标识只能用 3 位以上小写字母、数字或连字符（例如 sharp-review）'
        return
      }
      const created = await api.post('/skills', {
        skillKey: key,
        displayName: styleForm.displayName,
        description: styleForm.description,
        allowedToolIds: ['create_outline', 'generate_article'],
        manifest: buildStyleManifest(),
      })
      showStyle.value = false
      // 创建接口返回的是服务端真实状态（归属/信任/版本），以列表重载为准
      await load()
      toast(`写作风格「${created.displayName}」已创建，可在创作空间的 AI 助手里选用。`)
      return
    }
    showStyle.value = false
  } catch (err) {
    error.value = err.message
  }
}

async function removeStyle(skill) {
  error.value = ''
  try {
    await api.del(`/skills/${skill.id}`)
    if (skill.customized) {
      // 删除定制副本后内置原版重新可见，需重新拉取
      await load()
      toast('已恢复内置默认风格。')
    } else {
      skills.value = skills.value.filter((s) => s.id !== skill.id)
      toast('写作风格已删除。')
    }
  } catch (err) {
    error.value = err.message
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">设置</h1>
        <p class="page-sub">主题、模型、Agent 与数据边界</p>
      </div>
    </div>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <n-tabs v-model:value="tab" type="line" animated>
      <n-tab name="theme">外观与主题</n-tab>
      <n-tab name="models">模型与 API</n-tab>
      <n-tab name="skills">Agent 与 Skills</n-tab>
      <n-tab name="data">数据与安全</n-tab>
      <n-tab name="logs">
        <span class="log-tab-label">系统日志<span v-if="logErrorCount" class="log-dot" :title="`最近日志含 ${logErrorCount} 条错误`" /></span>
      </n-tab>
    </n-tabs>

    <section v-if="tab === 'theme'" class="card">
      <h2 class="card-title">主题</h2>
      <p class="page-sub">选择后立即生效并跨会话保存；首次加载遵循系统偏好。</p>
      <n-space style="margin-top: var(--space-3)">
        <n-button
          v-for="item in THEMES"
          :key="item.value"
          :type="session.theme === item.value ? 'primary' : 'default'"
          :secondary="session.theme !== item.value"
          @click="session.setTheme(item.value)"
        >
          {{ item.label }}
        </n-button>
      </n-space>
      <div class="card-flat" style="margin-top: var(--space-4)">
        <n-space>
          <n-tag type="warning" round>主色</n-tag>
          <n-tag type="success" round>成功</n-tag>
          <n-tag type="warning" round>等待</n-tag>
          <n-tag type="error" round>错误</n-tag>
          <n-tag type="info" round>信息</n-tag>
        </n-space>
        <p class="field-hint" style="margin-top: var(--space-2)">
          状态同时用颜色和文字表达；两种主题下均满足普通文字对比度 ≥ 4.5:1。
        </p>
      </div>
    </section>

    <section v-if="tab === 'models'" class="card">
      <div class="row">
        <h2 class="card-title" style="margin: 0">模型连接</h2>
        <div class="spacer" />
        <n-button type="primary" size="small" @click="openCreate">添加连接</n-button>
      </div>
      <n-empty v-if="!connections.length" description="未配置时生成请求会明确报错（provider_unavailable），不会产生占位内容。">
        <template #extra>
          <n-button type="primary" size="small" @click="openCreate">添加第一个连接</n-button>
        </template>
      </n-empty>
      <div v-else class="list">
        <div v-for="conn in connections" :key="conn.id" class="list-row">
          <div class="list-main">
            <div class="list-title">{{ conn.displayName }}</div>
            <div class="list-meta">
              {{ conn.providerType }} · {{ conn.baseUrl || '未设置 Base URL' }} ·
              密钥 {{ conn.keyMasked || '未配置' }}
            </div>
            <div v-if="conn.models.length" class="row row-tight" style="margin-top: 6px">
              <n-tag
                v-for="model in conn.models"
                :key="model.id"
                size="small"
                :type="modelTagInfo(model).tagClass === 'tag-info' ? 'info' : modelTagInfo(model).tagClass === 'tag-accent' ? 'warning' : 'default'"
                round
              >
                {{ modelTagInfo(model).label }} · {{ model.modelCode }}
              </n-tag>
            </div>
            <div v-else class="list-meta" style="margin-top: 4px">未登记模型 · 编辑后填写模型代号</div>
          </div>
          <StatusTag :status="conn.status" />
          <n-button quaternary size="small" @click="testConnection(conn)">测试</n-button>
          <n-button quaternary size="small" @click="openEdit(conn)">编辑</n-button>
          <n-button quaternary size="small" type="error" @click="removeConnection(conn)">删除</n-button>
        </div>
      </div>
    </section>

    <section v-if="tab === 'skills'" class="card">
      <h2 class="card-title">Agent 与 Skills</h2>
      <p class="page-sub">
        启用用于 Agent 自动调用与工具授权；写作风格在创作空间手动选用，无需启用。工具授权在运行时重新校验。
      </p>

      <div v-if="agentStatus" class="stack" style="margin-top: var(--space-3)">
        <div class="card-flat">
          <div class="row" style="gap: var(--space-2)">
            <strong>Agent 运行状态</strong>
            <n-tag :type="agentStatus.mode.code === 'ready' ? 'success' : 'warning'" round>
              {{ agentStatus.mode.label }}
            </n-tag>
            <div class="spacer" />
            <span class="list-meta" style="margin: 0">检查于 {{ checkedAtLabel(agentStatus.checkedAt) }}</span>
            <n-button quaternary size="small" @click="load">刷新</n-button>
          </div>
          <p class="field-hint" style="margin-top: var(--space-2)">{{ agentStatus.mode.detail }}</p>
          <div class="status-grid">
            <div>
              <span class="field-label">模型连接</span>
              <div class="status-value">
                {{ agentStatus.model ? agentStatus.model.displayName : '未配置' }}
              </div>
              <div class="field-hint" style="margin: 0">
                {{
                  agentStatus.model
                    ? `${agentStatus.model.modelCode} · ${agentStatus.model.providerType}`
                    : '生成不可用 · 请先配置模型连接'
                }}
              </div>
            </div>
            <div>
              <span class="field-label">工作区</span>
              <div class="status-value">{{ agentStatus.workspace.name }}</div>
              <div class="field-hint" style="margin: 0">{{ agentStatus.workspace.id }}</div>
            </div>
            <div>
              <span class="field-label">Skill 授权</span>
              <div class="status-value">
                {{ agentStatus.skills.enabled }} / {{ agentStatus.skills.total }}
              </div>
              <div class="field-hint" style="margin: 0">
                已启用 {{ agentStatus.skills.enabled }} 个可被 Agent 调用 · 当前账号可见
                {{ agentStatus.skills.total }} 个
              </div>
            </div>
            <div>
              <span class="field-label">运行</span>
              <div class="status-value">
                {{ agentStatus.runs.active }} 进行中 · {{ agentStatus.runs.pendingApprovals }} 待批准
              </div>
              <div v-if="agentStatus.runs.last" class="field-hint" style="margin: 0">
                最近：{{ agentStatus.runs.last.status }}
              </div>
              <div v-else class="field-hint" style="margin: 0">暂无运行记录</div>
            </div>
          </div>
        </div>

        <div class="card-flat">
          <div class="row" style="gap: var(--space-2)">
            <strong>数据路径</strong>
            <div class="spacer" />
            <n-tag size="small" round>本机实际位置</n-tag>
          </div>
          <p class="field-hint" style="margin-top: var(--space-2)">
            Agent 读取与写入的目录；路径来自服务端运行配置，不是界面推测。
          </p>
          <div class="stack" style="margin-top: var(--space-2)">
            <div v-for="item in agentStatus.paths" :key="item.kind" class="path-row">
              <div class="row" style="gap: var(--space-2)">
                <span class="path-label">{{ item.label }}</span>
                <n-tag size="small" :type="item.exists ? 'success' : 'warning'" round>
                  {{ item.exists ? '存在' : '未创建' }}
                </n-tag>
                <span v-if="item.exists && !item.writable" class="list-meta" style="margin: 0">只读</span>
                <div class="spacer" />
                <n-button quaternary size="small" @click="copyPath(item)">
                  {{ copyLabel(item) }}
                </n-button>
              </div>
              <code class="path-value">{{ item.value }}</code>
              <div class="field-hint" style="margin: 0">{{ item.detail }}</div>
            </div>
          </div>
        </div>
      </div>
      <!-- 写作风格：AI 成文时可选，决定语气、结构与排版主题 -->
      <div class="card-flat" style="margin-top: var(--space-3)">
        <div class="row" style="gap: var(--space-2)">
          <strong>写作风格</strong>
          <n-tag size="small" round>AI 写作可选</n-tag>
          <div class="spacer" />
          <n-button type="primary" size="small" @click="openStyleCreate">新建写作风格</n-button>
        </div>
        <p class="field-hint" style="margin-top: var(--space-2)">
          写作风格决定 AI 写作时的语气、结构规则与默认排版主题；在创作空间的 AI 助手里选用。
          编辑内置风格会生成个人定制副本（仅对当前账号生效），删除副本即恢复默认。
        </p>
        <div v-if="styleSkills.length" class="stack" style="margin-top: var(--space-2)">
          <div v-for="skill in styleSkills" :key="skill.id" class="style-row">
            <div class="list-main">
              <div class="list-title">
                {{ skill.displayName }}
                <n-tag size="small" :type="skill.source === 'custom' ? 'warning' : 'default'" round>
                  {{ styleTagLabel(skill) }}
                </n-tag>
              </div>
              <div class="list-meta">
                {{ skill.description || '未填写说明' }}
                <template v-if="skill.manifest?.tone">
                  <br />语气：{{ skill.manifest.tone }}
                </template>
                <template v-if="skill.manifest?.defaultTheme">
                  · 主题：{{ THEME_OPTIONS.find((t) => t.value === skill.manifest.defaultTheme)?.label || skill.manifest.defaultTheme }}
                </template>
              </div>
            </div>
            <template v-if="skill.source === 'custom'">
              <n-button quaternary size="small" @click="openStyleEdit(skill)">编辑</n-button>
              <n-button quaternary size="small" type="error" @click="removeStyle(skill)">
                {{ skill.customized ? '恢复默认' : '删除' }}
              </n-button>
            </template>
            <n-button v-else quaternary size="small" @click="openStyleEdit(skill)">编辑</n-button>
          </div>
        </div>
        <div v-else class="empty" style="margin-top: var(--space-2)">
          <n-empty description="新建后可在创作空间的 AI 助手里选用。" size="small" />
        </div>
      </div>

      <div class="list" style="margin-top: var(--space-3)">
        <div class="list-title" style="padding: var(--space-2) 0">流程 Skill</div>
        <div v-for="skill in flowSkills" :key="skill.id" class="skill-block">
          <div class="list-row">
            <div class="list-main">
              <div class="list-title">
                {{ skill.displayName }} <span class="list-meta">v{{ skill.version }}</span>
              </div>
              <div class="list-meta">
                {{ skill.description }}
                <br />
                工具：{{ skill.allowedToolIds.join('、') || '无' }} · 风险：{{
                  RISK_LABELS[skill.riskLevel] || skill.riskLevel
                }}
              </div>
            </div>
            <n-tag v-if="skill.trustStatus !== 'trusted'" type="warning" size="small" round>待校验</n-tag>
            <n-tag size="small" :type="skill.enabled ? 'success' : 'default'" round>
              {{ skill.enabled ? '已启用' : '未启用' }}
            </n-tag>
            <n-button
              v-if="skill.allowedToolIds.length"
              quaternary
              size="small"
              @click="toggleGrantPanel(skill)"
            >
              授权
            </n-button>
            <n-button
              quaternary
              size="small"
              :disabled="skill.trustStatus !== 'trusted'"
              @click="toggleSkill(skill)"
            >
              {{ skill.enabled ? '停用' : '启用' }}
            </n-button>
          </div>
          <div v-if="expandedSkillId === skill.id" class="grant-panel">
            <p class="field-hint" style="margin: 0">
              勾选允许 Agent 调用的工具；只能收窄，不能超出 Skill 声明范围。
            </p>
            <n-checkbox-group v-model:value="skill.grantedToolIds">
              <n-space>
                <n-checkbox v-for="tool in skill.allowedToolIds" :key="tool" :value="tool" :label="tool" />
              </n-space>
            </n-checkbox-group>
          </div>
        </div>
        <div v-if="!flowSkills.length" class="empty">
          <n-empty description="流程 Skill 随服务端内置定义同步。" />
        </div>
      </div>
      <div v-if="policy" class="stack" style="margin-top: var(--space-4)">
        <div class="card-flat">
          <div class="row">
            <strong>工具权限分级</strong>
            <div class="spacer" />
            <n-tag size="small" round>服务端强制</n-tag>
          </div>
          <p class="field-hint">
            权限由服务端按工具分级强制执行，模型无法自行提权；下表为本机实际生效的策略。
          </p>
          <div class="stack" style="margin-top: var(--space-3)">
            <div v-for="group in policy.groups" :key="group.risk">
              <div class="row" style="gap: var(--space-2)">
                <n-tag
                  size="small"
                  round
                  :type="group.risk === 'forbidden' ? 'error' : group.requiresApproval ? 'warning' : group.risk === 'read_only' ? 'success' : 'default'"
                >
                  {{ group.label }}
                </n-tag>
                <span v-if="group.requiresApproval" class="field-hint" style="margin: 0">
                  逐次人工批准
                </span>
                <span v-else-if="group.risk === 'forbidden'" class="field-hint" style="margin: 0">
                  工具层直接拒绝
                </span>
              </div>
              <p class="field-hint" style="margin-top: var(--space-1)">
                {{ group.tools.map((t) => t.label).join('、') || '无' }}
              </p>
            </div>
          </div>
        </div>
        <div class="card-flat">
          <strong>审批流</strong>
          <p class="field-hint">
            涉及外部副作用的操作（发布、解绑账号、删除素材、修改模型凭据）无论 Skill 是否授权，运行时都会暂停等待人工批准；批准前不会调用平台接口。
          </p>
        </div>
      </div>
    </section>

    <section v-if="tab === 'data'" class="card">
      <h2 class="card-title">数据与安全</h2>
      <div class="stack">
        <div class="card-flat">
          <strong>凭据存储</strong>
          <p class="field-hint">
            模型 Key 与平台令牌只存服务端加密字段，接口只返回掩码；导出包不包含密钥。
          </p>
        </div>
        <div class="card-flat">
          <strong>数据来源</strong>
          <p class="field-hint">
            平台数据仅存储用户授权范围内容；缺失指标显示“不可获取”，不会填充为 0。
          </p>
        </div>
        <div class="card-flat">
          <strong>当前账号</strong>
          <p class="field-hint">
            {{ session.user?.displayName || '未登录' }} · 工作区 {{ session.workspaceId?.slice(0, 8) || '—' }}
          </p>
        </div>
      </div>
    </section>

    <n-modal
      v-model:show="showConnection"
      preset="card"
      :title="editingId ? '编辑模型连接' : '添加模型连接'"
      style="width: min(560px, calc(100vw - 32px))"
    >
      <n-form label-placement="top">
        <n-form-item label="连接名称">
          <n-input v-model:value="connForm.displayName" placeholder="例如：主力文本模型" />
        </n-form-item>
        <n-form-item label="供应商类型">
          <n-select v-model:value="connForm.providerType" :options="providerOptions" />
          <template v-if="connForm.providerType === 'vendor_native'" #feedback>
            该协议适配器尚未实现，连接可保存，但「测试」会明确返回 adapter_unavailable，不会静默回退。
          </template>
        </n-form-item>
        <n-form-item label="Base URL">
          <n-input v-model:value="connForm.baseUrl" placeholder="https://api.example.com/v1" />
          <template #feedback>
            仅允许 HTTPS 公网地址：保存时校验字面量，请求前解析 DNS 并拒绝内网 / 环回 /
            链路本地 / 云元数据地址，且逐跳校验重定向。
          </template>
        </n-form-item>
        <n-form-item label="API Key">
          <n-input
            v-model:value="connForm.apiKey"
            type="password"
            show-password-on="click"
            autocomplete="new-password"
            :placeholder="editingId ? '留空表示不修改当前密钥' : ''"
          />
          <template #feedback>
            {{ editingId ? '保存后不再回显明文；留空即保留原密钥。' : '保存后不再回显明文。' }}
          </template>
        </n-form-item>
        <n-form-item v-if="editingId" :show-label="false">
          <n-checkbox v-model:checked="clearKey">清空已有密钥（清空后该连接不可调用）</n-checkbox>
        </n-form-item>
        <n-form-item label="模型类型">
          <div class="model-type-grid" role="radiogroup" aria-label="模型类型">
            <button
              v-for="item in MODEL_TYPES"
              :key="item.value"
              type="button"
              class="model-type-card"
              :class="{ 'is-active': connForm.modelType === item.value }"
              role="radio"
              :aria-checked="connForm.modelType === item.value"
              @click="selectModelType(item.value)"
            >
              <span class="model-type-name">{{ item.label }}</span>
              <span class="model-type-desc">{{ item.desc }}</span>
            </button>
          </div>
          <template #feedback>
            {{ currentModelType()?.desc }}；能力随类型自动登记（{{ currentModelType()?.caps }}），不会被调用未登记的能力。
          </template>
        </n-form-item>
        <n-form-item :label="`模型代号${editingId ? '' : '（可选）'}`">
          <n-input
            v-model:value="connForm.modelCode"
            :placeholder="connForm.modelType === 'image' ? '例如：gemini-2.5-flash-image' : '例如：gpt-4o-mini'"
          />
          <template v-if="editingId" #feedback>留空将删除该连接下已登记的模型。</template>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary @click="showConnection = false">取消</n-button>
          <n-button type="primary" @click="saveConnection">保存</n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal
      v-model:show="showStyle"
      preset="card"
      :title="styleModalTitle"
      style="width: min(560px, calc(100vw - 32px))"
    >
      <n-form label-placement="top">
        <n-form-item label="风格名称">
          <n-input v-model:value="styleForm.displayName" placeholder="例如：犀利锐评" />
        </n-form-item>
        <n-form-item v-if="!styleEditingId && !styleForkSourceId" label="标识（可选）">
          <n-input v-model:value="styleForm.skillKey" placeholder="例如：sharp-review" />
          <template #feedback>留空自动生成；只能用小写字母、数字、连字符。</template>
        </n-form-item>
        <n-form-item label="说明">
          <n-input v-model:value="styleForm.description" placeholder="这个风格适合什么场景" />
        </n-form-item>
        <n-form-item label="语气风格">
          <n-input
            v-model:value="styleForm.tone"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 6 }"
            placeholder="例如：语气犀利直接，多用短句和反问，观点鲜明，不绕弯子"
          />
          <template #feedback>会填入写作 prompt 的 {tone} 占位符，直接影响文章语气。</template>
        </n-form-item>
        <n-form-item label="风格规则（每行一条）">
          <n-input
            v-model:value="styleForm.styleRules"
            type="textarea"
            :autosize="{ minRows: 4, maxRows: 8 }"
            placeholder="不用 emoji&#10;每段不超过 3 行&#10;关键结论前置"
          />
          <template #feedback>这些规则会作为「必须遵守」附加到写作指令中。</template>
        </n-form-item>
        <n-form-item label="默认排版主题">
          <n-select v-model:value="styleForm.defaultTheme" :options="THEME_OPTIONS" />
        </n-form-item>
        <n-form-item label="高级：完整 Prompt 模板（可选）">
          <n-input
            v-model:value="styleForm.promptTemplate"
            type="textarea"
            :autosize="{ minRows: 4, maxRows: 10 }"
            placeholder="留空使用系统内置模板；可用 {tone} 占位符引用上面的语气风格"
          />
          <template #feedback>
            留空时沿用内置模板（输出严格 JSON）。填写后需保留 JSON 输出结构，否则会导致解析失败。
          </template>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary @click="showStyle = false">取消</n-button>
          <n-button type="primary" @click="saveStyle">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.log-tab-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.log-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-danger);
}
.log-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin: var(--space-3) 0;
}
.log-service-select {
  width: 120px;
}
.log-level-select {
  width: 130px;
}
.log-keyword-input {
  width: 180px;
}
.log-rid-input {
  width: 160px;
}
.log-summary-chip {
  font-size: var(--font-xs);
  padding: 2px 8px;
  border-radius: 10px;
}
.log-summary-chip.is-error {
  color: var(--color-danger);
  background: rgba(208, 48, 80, 0.1);
}
.log-summary-chip.is-warn {
  color: var(--color-warning);
  background: rgba(240, 160, 32, 0.12);
}
.log-alert {
  margin-bottom: var(--space-2);
}
.log-truncated-hint {
  font-size: var(--font-xs);
  color: var(--color-warning);
  margin-bottom: var(--space-2);
}
.log-stream {
  max-height: 480px;
  overflow: auto;
  background: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: var(--space-2) var(--space-3);
  font-family: 'JetBrains Mono', Consolas, Menlo, monospace;
  font-size: 12px;
  line-height: 1.7;
}
.log-empty {
  color: var(--color-text-muted);
  padding: var(--space-4);
  text-align: center;
}
.log-line {
  white-space: pre-wrap;
  word-break: break-all;
}
.log-ts {
  color: var(--color-text-muted);
  margin-right: 6px;
}
.log-level {
  display: inline-block;
  min-width: 58px;
  font-weight: 600;
  margin-right: 6px;
}
.level-error .log-level,
.level-critical .log-level,
.level-error .log-msg,
.level-critical .log-msg {
  color: var(--color-danger);
}
.level-warning .log-level,
.level-warning .log-msg {
  color: var(--color-warning);
}
.level-debug .log-msg {
  color: var(--color-text-muted);
}
.log-logger {
  color: var(--color-info);
  margin-right: 6px;
}
.log-rid {
  color: var(--color-text-muted);
  margin-right: 6px;
  cursor: pointer;
  text-decoration: underline dotted;
}
.log-line.level-continuation {
  padding-left: 18px;
  color: var(--color-text-muted);
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.status-value {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 2px 0;
  word-break: break-all;
}

.path-row {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.style-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.skill-block {
  border-bottom: 1px solid var(--color-border);
}

.skill-block:last-child {
  border-bottom: none;
}

.skill-block .list-row {
  border-bottom: none;
}

.grant-panel {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-3);
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-2);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.grant-item {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--font-xs);
}

.path-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-secondary);
}

.path-value {
  display: block;
  margin-top: var(--space-1);
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  border: 1px dashed var(--color-border);
  background: var(--color-surface-raised);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow-x: auto;
}

.model-type-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
}

.model-type-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
  transition: border-color 0.15s, background 0.15s;
}

.model-type-card:hover {
  border-color: var(--color-border-strong);
  background: var(--color-surface-raised);
}

.model-type-card.is-active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  box-shadow: 0 0 0 1px var(--color-accent);
}

.model-type-name {
  font-size: var(--font-body);
  font-weight: 600;
  color: var(--color-text-primary);
}

.model-type-card.is-active .model-type-name {
  color: var(--color-accent);
}

.model-type-desc {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

</style>
