<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
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

const session = useSessionStore()
const route = useRoute()
const router = useRouter()

// 分栏既能在页内点，也能由外部入口直接指定（?tab=xxx）
// 模型与 API 已独立成「模型管理」页，不再作为这里的分栏
const SETTINGS_TABS = ['theme', 'skills', 'data']

function tabFromQuery(value) {
  const key = String(value || '')
  return SETTINGS_TABS.includes(key) ? key : 'theme'
}

const tab = ref(tabFromQuery(route.query.tab))

const skills = ref([])
const error = ref('')

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
    const [skillList, policyData, statusData] = await Promise.all([
      api.get('/skills'),
      api.get('/agent/policy'),
      api.get('/agent/status').catch(() => null),
    ])
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
})

// 深链到某个分栏时，即使已经在设置页也要切过去
watch(
  () => route.query.tab,
  (value) => {
    const next = tabFromQuery(value)
    if (next !== tab.value) tab.value = next
  },
)

watch(tab, (value) => {
  // 页内切分栏时同步 URL，避免再点一次顶栏入口时地址没变、页面不动
  if (tabFromQuery(route.query.tab) !== value) {
    router.replace({ query: { ...route.query, tab: value } })
  }
})

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
// （manifest 校验只认预设，自定义主题不能出现在这里）。
// 这个接口要 wechat_layout.use 或 publishing.use；普通用户会 403，退回内置快照。
const THEME_FALLBACK = [
  { value: 'ink-green', label: '墨绿简约 · 知识/职场/干货' },
  { value: 'warm-orange', label: '暖橙生活 · 生活/情感/美食' },
  { value: 'mono', label: '极简黑白 · 深度长文/访谈' },
  { value: 'terracotta-warm', label: '暖陶土 · 工具推荐/产品测评' },
  { value: 'blue-business', label: '商务蓝 · 职场干货/产品教程' },
  { value: 'dispatch', label: '公文 · 通知/报告/政务说明' },
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
        <p class="page-sub">主题、Agent 与数据边界；模型与日志已拆为「系统」菜单下的独立页面</p>
      </div>
      <RouterLink v-slot="{ navigate }" custom :to="{ name: 'models' }">
        <n-button secondary @click="navigate">模型管理 →</n-button>
      </RouterLink>
    </div>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <n-tabs v-model:value="tab" type="line" animated>
      <n-tab name="theme">外观与主题</n-tab>
      <n-tab name="skills">Agent 与 Skills</n-tab>
      <n-tab name="data">数据与安全</n-tab>
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

</style>
