<script setup>
import { onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NTag,
} from 'naive-ui'
import { api } from '../api/client'

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

async function load() {
  try {
    const [policyData, statusData] = await Promise.all([
      api.get('/agent/policy'),
      api.get('/agent/status').catch(() => null),
    ])
    policy.value = policyData
    agentStatus.value = statusData
  } catch (err) {
    error.value = err.message
  }
}

onMounted(() => {
  load()
})

</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">设置</h1>
        <p class="page-sub">Agent 运行状态、数据路径与工具权限</p>
      </div>
      <RouterLink v-slot="{ navigate }" custom :to="{ name: 'models' }">
        <n-button secondary @click="navigate">模型管理 →</n-button>
      </RouterLink>
    </div>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <section class="card">
      <h2 class="card-title">Agent 运行</h2>

      <div v-if="agentStatus" class="stack">
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
          <p class="field-hint" style="margin-top: var(--space-1)">
            涉及外部副作用的操作（发布、解绑账号、删除素材、修改模型凭据）无论 Skill 是否授权，运行时都会暂停等待人工批准。
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
