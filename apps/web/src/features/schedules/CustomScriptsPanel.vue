<script setup>
import { computed, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSpin,
  NSwitch,
  NTag,
} from 'naive-ui'
import { api } from '../../api/client'
import { toast } from '../../ui/feedback'
import { usePermission } from '../../composables/usePermission'

const emit = defineEmits(['updated'])
const scripts = defineModel('scripts', { type: Array, default: () => [] })

const { can } = usePermission()
const canManage = computed(() => can('scheduled-script.admin'))

const loading = ref(false)
const error = ref('')
const busyId = ref('')
const confirmingId = ref('')
let confirmTimer = null

const showEditor = ref(false)
const editingId = ref('')
const saving = ref(false)
const publishing = ref(false)
const formError = ref('')

const showResult = ref(false)
const result = ref(null)
const resultError = ref('')

const form = reactive({
  name: '',
  description: '',
  draftCode: '',
  timeoutSeconds: 30,
  enabled: true,
  publishedVersion: null,
  publishedHash: '',
  publishedAt: '',
})

function fmtTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}

function scriptStatus(row) {
  return row.publishedVersion ? `已发布 v${row.publishedVersion}` : '草稿'
}

function scriptStatusType(row) {
  return row.publishedVersion ? 'success' : 'default'
}

function resetForm(row = null) {
  form.name = row?.name || ''
  form.description = row?.description || ''
  form.draftCode = row?.draftCode || ''
  form.timeoutSeconds = Number(row?.timeoutSeconds || 30)
  form.enabled = row?.enabled ?? true
  form.publishedVersion = row?.publishedVersion ?? null
  form.publishedHash = row?.publishedHash || ''
  form.publishedAt = row?.publishedAt || ''
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get('/schedules/scripts')
    scripts.value = data.items || []
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = ''
  formError.value = ''
  resetForm()
  form.draftCode = 'def run():\n    return {"summary": "自定义脚本已执行"}\n'
  showEditor.value = true
}

function openEdit(row) {
  editingId.value = row.id
  formError.value = ''
  resetForm(row)
  showEditor.value = true
}

function validateForm() {
  if (!form.name.trim()) {
    formError.value = '请填写脚本名称'
    return false
  }
  const timeout = Number(form.timeoutSeconds)
  if (!Number.isInteger(timeout) || timeout < 5 || timeout > 300) {
    formError.value = '超时秒数必须在 5 到 300 之间'
    return false
  }
  return true
}

function payload() {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    draftCode: form.draftCode,
    timeoutSeconds: Number(form.timeoutSeconds),
    enabled: form.enabled,
  }
}

async function saveDraft() {
  formError.value = ''
  if (!validateForm()) return
  saving.value = true
  try {
    const saved = editingId.value
      ? await api.patch(`/schedules/scripts/${editingId.value}`, payload())
      : await api.post('/schedules/scripts', payload())
    replaceScript(saved)
    editingId.value = saved.id
    resetForm(saved)
    toast(`已保存「${saved.name}」草稿`)
    emit('updated', saved)
  } catch (err) {
    formError.value = err.message
  } finally {
    saving.value = false
  }
}

async function publish() {
  formError.value = ''
  if (!validateForm()) return
  publishing.value = true
  try {
    const saved = editingId.value
      ? await api.patch(`/schedules/scripts/${editingId.value}`, payload())
      : await api.post('/schedules/scripts', payload())
    replaceScript(saved)
    editingId.value = saved.id
    resetForm(saved)
    const published = await api.post(`/schedules/scripts/${saved.id}/publish`)
    replaceScript(published)
    resetForm(published)
    toast(`已发布「${published.name}」v${published.publishedVersion}`)
    emit('updated', published)
  } catch (err) {
    formError.value = err.message
  } finally {
    publishing.value = false
  }
}

async function publishRow(row) {
  busyId.value = row.id
  error.value = ''
  try {
    const published = await api.post(`/schedules/scripts/${row.id}/publish`)
    replaceScript(published)
    toast(`已发布「${published.name}」v${published.publishedVersion}`)
    emit('updated', published)
  } catch (err) {
    error.value = err.message
  } finally {
    busyId.value = ''
  }
}

async function runNow(row) {
  busyId.value = row.id
  error.value = ''
  result.value = null
  resultError.value = ''
  showResult.value = true
  try {
    // /run 只是入队回执（jobId）；真正的执行结果要轮询 jobs 接口到终态。
    const { jobId } = await api.post(`/schedules/scripts/${row.id}/run`)
    if (!jobId) throw new Error('入队失败：未返回 jobId')
    const deadline = Date.now() + 120_000
    let job = null
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 1000))
      job = await api.get(`/jobs/${jobId}`)
      if (['succeeded', 'failed', 'cancelled', 'stopped'].includes(job.status)) break
    }
    if (!job || !['succeeded', 'failed', 'cancelled', 'stopped'].includes(job.status)) {
      throw new Error('执行超时：任务仍在队列中，请稍后在执行历史里查看结果')
    }
    if (job.status !== 'succeeded') {
      resultError.value = job.errorMessage || `执行失败（${job.errorCode || job.status}）`
      result.value = job.result || null
      return
    }
    result.value = job.result || {}
  } catch (err) {
    resultError.value = err.message
  } finally {
    busyId.value = ''
  }
}

async function toggle(row) {
  busyId.value = row.id
  error.value = ''
  try {
    replaceScript(await api.patch(`/schedules/scripts/${row.id}`, { enabled: !row.enabled }))
  } catch (err) {
    error.value = err.message
  } finally {
    busyId.value = ''
  }
}

async function remove(row) {
  if (confirmingId.value !== row.id) {
    confirmingId.value = row.id
    if (confirmTimer) clearTimeout(confirmTimer)
    confirmTimer = setTimeout(() => {
      confirmingId.value = ''
      confirmTimer = null
    }, 3000)
    return
  }
  if (confirmTimer) clearTimeout(confirmTimer)
  confirmingId.value = ''
  busyId.value = row.id
  error.value = ''
  try {
    await api.del(`/schedules/scripts/${row.id}`)
    scripts.value = scripts.value.filter((item) => item.id !== row.id)
    toast(`已删除「${row.name}」`)
    emit('updated', null)
  } catch (err) {
    error.value = err.message
  } finally {
    busyId.value = ''
  }
}

function replaceScript(row) {
  if (!row?.id) return
  const exists = scripts.value.some((item) => item.id === row.id)
  scripts.value = exists
    ? scripts.value.map((item) => (item.id === row.id ? row : item))
    : [row, ...scripts.value]
}

defineExpose({ load })
</script>

<template>
  <section class="scripts-panel">
    <div class="scripts-head">
      <div>
        <h2 class="scripts-title">自定义脚本</h2>
        <p class="scripts-sub">
          保存草稿后发布成「自定义脚本 · 名称」定时任务类型；运行参数由脚本通过 get_params() 读取。
        </p>
      </div>
      <n-button
        type="primary"
        :disabled="!canManage"
        :title="canManage ? '' : '需要「定时脚本管理」权限'"
        @click="openCreate"
      >
        新建脚本
      </n-button>
    </div>

    <n-alert v-if="error" type="error" closable @close="error = ''">{{ error }}</n-alert>
    <n-alert v-if="!canManage" type="warning">
      当前角色缺少「定时脚本管理」权限，只能查看已有脚本。
    </n-alert>

    <n-spin :show="loading">
      <n-empty v-if="!loading && scripts.length === 0" description="还没有自定义脚本" />
      <ul v-else class="scripts-list">
        <li v-for="row in scripts" :key="row.id" class="scripts-row">
          <div class="scripts-main">
            <div class="scripts-title-line">
              <span class="scripts-name">{{ row.name }}</span>
              <n-tag size="small" :type="scriptStatusType(row)" :bordered="false">
                {{ scriptStatus(row) }}
              </n-tag>
              <n-tag v-if="!row.enabled" size="small" type="warning" :bordered="false">已停用</n-tag>
            </div>
            <div class="scripts-meta">
              <span>{{ row.timeoutSeconds }}s</span>
              <span class="scripts-dot">·</span>
              <span>创建于 {{ fmtTime(row.createdAt) }}</span>
              <span v-if="row.publishedAt" class="scripts-dot">·</span>
              <span v-if="row.publishedAt">最近发布 {{ fmtTime(row.publishedAt) }}</span>
            </div>
            <p v-if="row.description" class="scripts-description">{{ row.description }}</p>
          </div>
          <div class="scripts-actions">
            <n-switch
              :value="row.enabled"
              size="small"
              :disabled="!canManage || busyId === row.id"
              @update:value="toggle(row)"
            />
            <n-button size="tiny" tertiary :disabled="!canManage" @click="openEdit(row)">编辑</n-button>
            <n-button size="tiny" tertiary :loading="busyId === row.id" :disabled="!canManage" @click="publishRow(row)">
              发布
            </n-button>
            <n-button size="tiny" tertiary :loading="busyId === row.id" @click="runNow(row)">试运行</n-button>
            <n-button
              size="tiny"
              tertiary
              type="error"
              :loading="busyId === row.id"
              :disabled="!canManage"
              @click="remove(row)"
            >
              {{ confirmingId === row.id ? '确认删除' : '删除' }}
            </n-button>
          </div>
        </li>
      </ul>
    </n-spin>

    <n-modal
      v-model:show="showEditor"
      preset="card"
      class="scripts-modal"
      :title="editingId ? '编辑自定义脚本' : '新建自定义脚本'"
    >
      <n-form label-placement="top" :show-feedback="false">
        <div class="scripts-form-grid">
          <n-form-item label="名称">
            <n-input v-model:value="form.name" placeholder="例：同步平台数据" />
          </n-form-item>
          <n-form-item label="超时秒数（5-300）">
            <n-input-number v-model:value="form.timeoutSeconds" :min="5" :max="300" :precision="0" />
          </n-form-item>
        </div>
        <n-form-item label="描述">
          <n-input v-model:value="form.description" type="textarea" :rows="2" placeholder="说明脚本用途" />
        </n-form-item>
        <n-form-item label="Python 代码">
          <n-input
            v-model:value="form.draftCode"
            type="textarea"
            class="code-input"
            :rows="14"
            placeholder="def run(): ..."
          />
        </n-form-item>
        <n-form-item label="启用">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
        <div class="published-meta">
          <span>已发布版本：{{ form.publishedVersion || '—' }}</span>
          <span>Hash：{{ form.publishedHash || '—' }}</span>
          <span>最近发布：{{ form.publishedAt ? fmtTime(form.publishedAt) : '—' }}</span>
        </div>
        <n-alert v-if="formError" type="error" class="scripts-form-error">{{ formError }}</n-alert>
      </n-form>
      <template #footer>
        <div class="scripts-modal-footer">
          <n-button :disabled="saving || publishing" @click="showEditor = false">取消</n-button>
          <n-button :loading="saving" :disabled="publishing" @click="saveDraft">保存草稿</n-button>
          <n-button type="primary" :loading="publishing" :disabled="saving" @click="publish">
            保存并发布
          </n-button>
        </div>
      </template>
    </n-modal>

    <n-modal v-model:show="showResult" preset="card" class="scripts-result-modal" title="试运行结果">
      <n-alert v-if="resultError" type="error">{{ resultError }}</n-alert>
      <template v-else-if="result">
        <div class="result-meta">
          <span>summary：{{ result.summary || '—' }}</span>
          <span>exitCode：{{ result.exitCode ?? '—' }}</span>
        </div>
        <p v-if="result.stdout" class="log-text">stdout</p>
        <p v-if="result.stdout" class="log-body">{{ result.stdout }}</p>
        <p v-if="result.stderr" class="log-text">stderr</p>
        <p v-if="result.stderr" class="log-body">{{ result.stderr }}</p>
      </template>
      <n-spin v-else :show="true" />
    </n-modal>
  </section>
</template>

<style scoped>
.scripts-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: var(--space-4);
}
.scripts-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}
.scripts-title {
  margin: 0;
  font-size: var(--font-lg, 18px);
  color: var(--color-text-primary);
}
.scripts-sub {
  margin: var(--space-1) 0 0;
  font-size: var(--font-sm);
  color: var(--color-text-muted);
}
.scripts-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.scripts-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg, 10px);
  background: var(--color-surface);
}
.scripts-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.scripts-title-line,
.scripts-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.scripts-name {
  font-weight: 600;
  color: var(--color-text-primary);
}
.scripts-meta {
  font-size: var(--font-sm);
  color: var(--color-text-muted);
}
.scripts-dot {
  color: var(--color-border-strong);
}
.scripts-description {
  margin: 0;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}
.scripts-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  flex-wrap: wrap;
}
.scripts-form-grid {
  display: grid;
  grid-template-columns: 1fr 160px;
  gap: var(--space-3);
}
.code-input :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
  tab-size: 4;
}
.published-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md, 8px);
  background: var(--color-surface-muted);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
.scripts-form-error {
  margin-top: var(--space-2);
}
.scripts-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
.result-meta {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}
.log-text {
  margin: var(--space-3) 0 var(--space-1);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
.log-body {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md, 8px);
  background: var(--color-surface-muted);
  color: var(--color-text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
:global(.scripts-modal) {
  width: min(860px, 94vw);
  max-height: 88vh;
}
:global(.scripts-modal .n-card-content) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
:global(.scripts-result-modal) {
  width: min(720px, 94vw);
  max-height: 88vh;
}
:global(.scripts-result-modal .n-card-content) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
@media (max-width: 900px) {
  .scripts-row {
    flex-direction: column;
    align-items: stretch;
  }
  .scripts-form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
