<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCheckbox,
  NCheckboxGroup,
  NEmpty,
  NSpace,
  NTab,
  NTabs,
  NTag,
} from 'naive-ui'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'

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
// 写作风格判定只用于分组展示；规则本身在「创作规则」页维护。

/** 写作风格 = 自定义 Skill，或带 tone 的内置风格（与后端 styleSkills 判定保持一致） */
function isStyle(skill) {
  return skill.source === 'custom' || Boolean(skill.manifest?.tone)
}

const styleSkills = computed(() => skills.value.filter(isStyle))
/** 流程型 Skill：供 Agent 编排调用，与写作风格分区展示，避免同一批条目出现两次 */
const flowSkills = computed(() => skills.value.filter((skill) => !isStyle(skill)))

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
      <!-- 写作规则已统一到「创作规则」页：这里只留入口与只读概览，避免两处编辑同一份数据 -->
      <div class="card-flat" style="margin-top: var(--space-3)">
        <div class="row" style="gap: var(--space-2)">
          <strong>写作风格与创作规则</strong>
          <n-tag size="small" round>统一入口</n-tag>
          <div class="spacer" />
          <RouterLink v-slot="{ navigate }" custom :to="{ name: 'writing-rules' }">
            <n-button type="primary" size="small" @click="navigate">打开创作规则 →</n-button>
          </RouterLink>
        </div>
        <p class="field-hint" style="margin-top: var(--space-2)">
          语气、风格规则、审稿规则、工作流步骤、目标字数、事实/配图/修复策略，以及输出契约、系统提示词与
          AI 味词表，都在「创作规则」页维护：管理员改全局（需创作规则管理权限），每位成员维护自己的副本。
          本页不再提供编辑入口，避免两处改同一份数据后互相覆盖。
        </p>
        <div class="list-meta" style="margin-top: var(--space-2)">
          当前可选写作风格 {{ styleSkills.length }} 个 · 流程 Skill {{ flowSkills.length }} 个 ·
          风格仍在创作空间的 AI 助手里选用
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
