<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import StatusTag from '../components/StatusTag.vue'

const session = useSessionStore()
const tab = ref('theme')

const connections = ref([])
const skills = ref([])
const error = ref('')
const notice = ref('')

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

const MODE_TAG = {
  ready: 'tag-success',
  local: 'tag-warning',
  degraded: 'tag-warning',
}

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

const TABS = [
  { key: 'theme', label: '外观与主题' },
  { key: 'models', label: '模型与 API' },
  { key: 'skills', label: 'Agent 与 Skills' },
  { key: 'data', label: '数据与安全' },
]

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

onMounted(load)

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
      notice.value = '连接已更新。'
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
    notice.value = '连接已保存；密钥只在服务端加密存储，接口不会返回明文。'
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
    notice.value = `${result.message}${detail}`
    if (!result.verified) error.value = '连接未通过真实连通性验证，状态已如实记录。'
  } catch (err) {
    error.value = err.message
  }
}

async function removeConnection(conn) {
  await api.del(`/models/connections/${conn.id}`)
  connections.value = connections.value.filter((c) => c.id !== conn.id)
}

async function toggleSkill(skill) {
  try {
    const updated = await api.patch(`/skills/${skill.id}`, {
      enabled: !skill.enabled,
      grantedToolIds: skill.allowedToolIds,
      approvalPolicy: 'risk_based',
    })
    Object.assign(skill, updated)
  } catch (err) {
    error.value = err.message
  }
}

// ---------- 写作风格（自定义 Skill）----------
const THEME_OPTIONS = [
  { value: 'ink-green', label: '墨绿简约 · 知识/职场/干货' },
  { value: 'deep-blue', label: '深海蓝 · 科技/财经/分析' },
  { value: 'warm-orange', label: '暖橙生活 · 生活/情感/美食' },
  { value: 'mono', label: '极简黑白 · 深度长文/访谈' },
  { value: 'terracotta-warm', label: '暖陶土 · 工具推荐/产品测评' },
  { value: 'blue-business', label: '商务蓝 · 职场干货/产品教程' },
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

/** 只展示带写作风格配置的 Skill（内置 article-drafting + 用户自定义） */
const styleSkills = computed(() =>
  skills.value.filter((s) => s.source === 'custom' || s.manifest?.tone),
)

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
  notice.value = ''
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
      notice.value = '写作风格已更新，下次 AI 成文即生效。'
    } else if (styleForkSourceId.value) {
      // 定制内置风格：创建个人副本，内置原版在列表中被副本替换
      await api.post(`/skills/${styleForkSourceId.value}/customize`, {
        displayName: styleForm.displayName,
        description: styleForm.description,
        manifest: buildStyleManifest(),
      })
      showStyle.value = false
      await load()
      notice.value = '内置风格已定制，仅对当前账号生效；删除副本可恢复默认。'
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
      skills.value.push(created)
      notice.value = '写作风格已创建，可在创作空间的 AI 助手里选用。'
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
      notice.value = '已恢复内置默认风格。'
    } else {
      skills.value = skills.value.filter((s) => s.id !== skill.id)
      notice.value = '写作风格已删除。'
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

    <div v-if="error" class="banner banner-danger" role="alert">{{ error }}</div>
    <div v-if="notice" class="banner banner-info" role="status">{{ notice }}</div>

    <div class="row row-tight" style="overflow-x: auto">
      <button
        v-for="item in TABS"
        :key="item.key"
        class="btn btn-sm"
        :class="tab === item.key ? '' : 'btn-ghost'"
        @click="tab = item.key"
      >
        {{ item.label }}
      </button>
    </div>

    <section v-if="tab === 'theme'" class="card">
      <h2 class="card-title">主题</h2>
      <p class="page-sub">选择后立即生效并跨会话保存；首次加载遵循系统偏好。</p>
      <div class="row" style="margin-top: var(--space-3)">
        <button
          v-for="item in THEMES"
          :key="item.value"
          class="btn"
          :class="session.theme === item.value ? '' : 'btn-ghost'"
          @click="session.setTheme(item.value)"
        >
          {{ item.label }}
        </button>
      </div>
      <div class="card-flat" style="margin-top: var(--space-4)">
        <div class="row">
          <span class="tag tag-accent">主色</span>
          <span class="tag tag-success">成功</span>
          <span class="tag tag-warning">等待</span>
          <span class="tag tag-danger">错误</span>
          <span class="tag tag-info">信息</span>
        </div>
        <p class="field-hint" style="margin-top: var(--space-2)">
          状态同时用颜色和文字表达；两种主题下均满足普通文字对比度 ≥ 4.5:1。
        </p>
      </div>
    </section>

    <section v-if="tab === 'models'" class="card">
      <div class="row">
        <h2 class="card-title" style="margin: 0">模型连接</h2>
        <div class="spacer" />
        <button class="btn btn-sm" @click="openCreate">添加连接</button>
      </div>
      <div v-if="!connections.length" class="empty">
        <span class="empty-title">还没有模型连接</span>
        <span>未配置时生成请求会明确报错（provider_unavailable），不会产生占位内容。</span>
        <button class="btn btn-sm" @click="openCreate">添加第一个连接</button>
      </div>
      <div v-else class="list">
        <div v-for="conn in connections" :key="conn.id" class="list-row">
          <div class="list-main">
            <div class="list-title">{{ conn.displayName }}</div>
            <div class="list-meta">
              {{ conn.providerType }} · {{ conn.baseUrl || '未设置 Base URL' }} ·
              密钥 {{ conn.keyMasked || '未配置' }}
            </div>
            <div v-if="conn.models.length" class="row row-tight" style="margin-top: 6px">
              <span
                v-for="model in conn.models"
                :key="model.id"
                class="tag"
                :class="modelTagInfo(model).tagClass"
              >
                {{ modelTagInfo(model).label }} · {{ model.modelCode }}
              </span>
            </div>
            <div v-else class="list-meta" style="margin-top: 4px">未登记模型 · 编辑后填写模型代号</div>
          </div>
          <StatusTag :status="conn.status" />
          <button class="btn btn-ghost btn-sm" @click="testConnection(conn)">测试</button>
          <button class="btn btn-ghost btn-sm" @click="openEdit(conn)">编辑</button>
          <button class="btn btn-danger btn-sm" @click="removeConnection(conn)">删除</button>
        </div>
      </div>
    </section>

    <section v-if="tab === 'skills'" class="card">
      <h2 class="card-title">Agent 与 Skills</h2>
      <p class="page-sub">只有已启用的 Skill 才能被 Agent 节点调用；工具授权在运行时重新校验。</p>

      <div v-if="agentStatus" class="stack" style="margin-top: var(--space-3)">
        <div class="card-flat">
          <div class="row" style="gap: var(--space-2)">
            <strong>Agent 运行状态</strong>
            <span class="tag" :class="MODE_TAG[agentStatus.mode.code] || ''">
              {{ agentStatus.mode.label }}
            </span>
            <div class="spacer" />
            <span class="list-meta" style="margin: 0">检查于 {{ checkedAtLabel(agentStatus.checkedAt) }}</span>
            <button class="btn btn-ghost btn-sm" @click="load">刷新</button>
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
                {{ agentStatus.skills.enabled ? '已启用，可被 Agent 调用' : 'Agent 需至少启用一个' }}
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
            <span class="tag">本机实际位置</span>
          </div>
          <p class="field-hint" style="margin-top: var(--space-2)">
            Agent 读取与写入的目录；路径来自服务端运行配置，不是界面推测。
          </p>
          <div class="stack" style="margin-top: var(--space-2)">
            <div v-for="item in agentStatus.paths" :key="item.kind" class="path-row">
              <div class="row" style="gap: var(--space-2)">
                <span class="path-label">{{ item.label }}</span>
                <span class="tag" :class="item.exists ? 'tag-success' : 'tag-warning'">
                  {{ item.exists ? '存在' : '未创建' }}
                </span>
                <span v-if="item.exists && !item.writable" class="list-meta" style="margin: 0">只读</span>
                <div class="spacer" />
                <button class="btn btn-ghost btn-sm" @click="copyPath(item)">
                  {{ copyLabel(item) }}
                </button>
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
          <span class="tag">AI 写作可选</span>
          <div class="spacer" />
          <button class="btn btn-sm" @click="openStyleCreate">新建写作风格</button>
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
                <span class="tag" :class="skill.source === 'custom' ? 'tag-accent' : ''">
                  {{ styleTagLabel(skill) }}
                </span>
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
              <button class="btn btn-ghost btn-sm" @click="openStyleEdit(skill)">编辑</button>
              <button class="btn btn-danger btn-sm" @click="removeStyle(skill)">
                {{ skill.customized ? '恢复默认' : '删除' }}
              </button>
            </template>
            <button v-else class="btn btn-ghost btn-sm" @click="openStyleEdit(skill)">编辑</button>
          </div>
        </div>
        <div v-else class="empty" style="margin-top: var(--space-2)">
          <span class="empty-title">还没有自定义写作风格</span>
          <span>新建后可在创作空间的 AI 助手里选用。</span>
        </div>
      </div>

      <div class="list" style="margin-top: var(--space-3)">
        <div v-for="skill in skills" :key="skill.id" class="list-row">
          <div class="list-main">
            <div class="list-title">{{ skill.displayName }} <span class="list-meta">v{{ skill.version }}</span></div>
            <div class="list-meta">
              {{ skill.description }}
              <br />
              工具：{{ skill.allowedToolIds.join('、') || '无' }} · 风险：{{ skill.riskLevel }}
            </div>
          </div>
          <span class="tag" :class="skill.enabled ? 'tag-success' : ''">
            {{ skill.enabled ? '已启用' : '未启用' }}
          </span>
          <button class="btn btn-ghost btn-sm" @click="toggleSkill(skill)">
            {{ skill.enabled ? '停用' : '启用' }}
          </button>
        </div>
      </div>
      <div v-if="policy" class="stack" style="margin-top: var(--space-4)">
        <div class="card-flat">
          <div class="row">
            <strong>工具权限分级</strong>
            <div class="spacer" />
            <span class="tag">服务端强制</span>
          </div>
          <p class="field-hint">
            权限由服务端按工具分级强制执行，模型无法自行提权；下表为本机实际生效的策略。
          </p>
          <div class="stack" style="margin-top: var(--space-3)">
            <div v-for="group in policy.groups" :key="group.risk">
              <div class="row" style="gap: var(--space-2)">
                <span
                  class="tag"
                  :class="{
                    'tag-danger': group.risk === 'forbidden',
                    'tag-warning': group.requiresApproval,
                    'tag-success': group.risk === 'read_only',
                  }"
                >
                  {{ group.label }}
                </span>
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

    <Teleport to="body">
      <div v-if="showConnection" class="modal-backdrop" @click.self="showConnection = false">
        <div
          class="modal"
          role="dialog"
          aria-modal="true"
          :aria-label="editingId ? '编辑模型连接' : '添加模型连接'"
        >
          <div class="row">
            <h2 class="modal-title" style="margin: 0">
              {{ editingId ? '编辑模型连接' : '添加模型连接' }}
            </h2>
            <div class="spacer" />
            <button class="icon-btn" aria-label="关闭" @click="showConnection = false">✕</button>
          </div>
          <div class="stack" style="margin-top: var(--space-3)">
            <label class="field">
              <span class="field-label">连接名称</span>
              <input v-model="connForm.displayName" class="input" placeholder="例如：主力文本模型" />
            </label>
            <label class="field">
              <span class="field-label">供应商类型</span>
              <select v-model="connForm.providerType" class="select">
                <option value="openai_compatible">OpenAI 兼容</option>
                <option value="vendor_native">供应商原生（适配器未接入）</option>
              </select>
              <span v-if="connForm.providerType === 'vendor_native'" class="field-hint">
                该协议适配器尚未实现，连接可保存，但「测试」会明确返回 adapter_unavailable，不会静默回退。
              </span>
            </label>
            <label class="field">
              <span class="field-label">Base URL</span>
              <input v-model="connForm.baseUrl" class="input" placeholder="https://api.example.com/v1" />
              <span class="field-hint">
                仅允许 HTTPS 公网地址：保存时校验字面量，请求前解析 DNS 并拒绝内网 / 环回 /
                链路本地 / 云元数据地址，且逐跳校验重定向。
              </span>
            </label>
            <label class="field">
              <span class="field-label">API Key</span>
              <input
                v-model="connForm.apiKey"
                class="input"
                type="password"
                autocomplete="off"
                :placeholder="editingId ? '留空表示不修改当前密钥' : ''"
              />
              <span class="field-hint">
                {{
                  editingId
                    ? '保存后不再回显明文；留空即保留原密钥。'
                    : '保存后不再回显明文。'
                }}
              </span>
            </label>
            <label v-if="editingId" class="row" style="gap: var(--space-2)">
              <input v-model="clearKey" type="checkbox" />
              <span class="field-hint" style="margin: 0">清空已有密钥（清空后该连接不可调用）</span>
            </label>
            <div class="field">
              <span class="field-label">模型类型</span>
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
              <span class="field-hint">
                {{ currentModelType()?.desc }}；能力随类型自动登记（{{ currentModelType()?.caps }}），不会被调用未登记的能力。
              </span>
            </div>
            <label class="field">
              <span class="field-label">模型代号{{ editingId ? '' : '（可选）' }}</span>
              <input
                v-model="connForm.modelCode"
                class="input"
                :placeholder="connForm.modelType === 'image' ? '例如：gemini-2.5-flash-image' : '例如：gpt-4o-mini'"
              />
              <span v-if="editingId" class="field-hint">留空将删除该连接下已登记的模型。</span>
            </label>
            <div class="row">
              <div class="spacer" />
              <button class="btn btn-ghost" @click="showConnection = false">取消</button>
              <button class="btn" @click="saveConnection">保存</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 写作风格编辑弹窗 -->
      <div v-if="showStyle" class="modal-backdrop" @click.self="showStyle = false">
        <div
          class="modal"
          role="dialog"
          aria-modal="true"
          :aria-label="styleModalTitle"
        >
          <div class="row">
            <h2 class="modal-title" style="margin: 0">{{ styleModalTitle }}</h2>
            <div class="spacer" />
            <button class="icon-btn" aria-label="关闭" @click="showStyle = false">✕</button>
          </div>
          <div class="stack" style="margin-top: var(--space-3)">
            <label class="field">
              <span class="field-label">风格名称</span>
              <input v-model="styleForm.displayName" class="input" placeholder="例如：犀利锐评" />
            </label>
            <label v-if="!styleEditingId && !styleForkSourceId" class="field">
              <span class="field-label">标识（可选）</span>
              <input v-model="styleForm.skillKey" class="input" placeholder="例如：sharp-review" />
              <span class="field-hint">留空自动生成；只能用小写字母、数字、连字符。</span>
            </label>
            <label class="field">
              <span class="field-label">说明</span>
              <input v-model="styleForm.description" class="input" placeholder="这个风格适合什么场景" />
            </label>
            <label class="field">
              <span class="field-label">语气风格</span>
              <textarea
                v-model="styleForm.tone"
                class="textarea"
                style="min-height: 72px"
                placeholder="例如：语气犀利直接，多用短句和反问，观点鲜明，不绕弯子"
              />
              <span class="field-hint">会填入写作 prompt 的 {tone} 占位符，直接影响文章语气。</span>
            </label>
            <label class="field">
              <span class="field-label">风格规则（每行一条）</span>
              <textarea
                v-model="styleForm.styleRules"
                class="textarea"
                style="min-height: 96px"
                placeholder="不用 emoji&#10;每段不超过 3 行&#10;关键结论前置"
              />
              <span class="field-hint">这些规则会作为「必须遵守」附加到写作指令中。</span>
            </label>
            <label class="field">
              <span class="field-label">默认排版主题</span>
              <select v-model="styleForm.defaultTheme" class="select">
                <option v-for="theme in THEME_OPTIONS" :key="theme.value" :value="theme.value">
                  {{ theme.label }}
                </option>
              </select>
            </label>
            <label class="field">
              <span class="field-label">高级：完整 Prompt 模板（可选）</span>
              <textarea
                v-model="styleForm.promptTemplate"
                class="textarea"
                style="min-height: 110px"
                placeholder="留空使用系统内置模板；可用 {tone} 占位符引用上面的语气风格"
              />
              <span class="field-hint">
                留空时沿用内置模板（输出严格 JSON）。填写后需保留 JSON 输出结构，否则会导致解析失败。
              </span>
            </label>
            <div class="row">
              <div class="spacer" />
              <button class="btn btn-ghost" @click="showStyle = false">取消</button>
              <button class="btn" @click="saveStyle">保存</button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
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
