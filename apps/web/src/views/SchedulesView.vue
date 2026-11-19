<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpin,
  NSwitch,
  NTag,
  NTooltip,
} from 'naive-ui'
import { api } from '../api/client'
import { toast } from '../ui/feedback'
import CustomScriptsPanel from '../features/schedules/CustomScriptsPanel.vue'

const CRON_PRESETS = [
  { label: '每 30 分钟', value: '*/30 * * * *' },
  { label: '每小时整点', value: '0 * * * *' },
  { label: '每天 08:00', value: '0 8 * * *' },
  { label: '每天 08/12/18', value: '0 8,12,18 * * *' },
  { label: '每周一 09:00', value: '0 9 * * 1' },
]

const STATUS_TEXT = {
  queued: '排队中',
  running: '执行中',
  succeeded: '成功',
  failed: '失败',
  skipped: '已跳过',
  cancelled: '已取消',
  missed: '已错过',
}

const STATUS_TYPE = {
  queued: 'default',
  running: 'info',
  succeeded: 'success',
  failed: 'error',
  skipped: 'warning',
  cancelled: 'default',
  missed: 'warning',
}

const rows = ref([])
const types = ref([])
const systemWritable = ref(false)
const loading = ref(true)
const error = ref('')
const busyId = ref('')
const confirmingId = ref('')
let confirmTimer = null

const showEditor = ref(false)
const editingId = ref('')
const saving = ref(false)
const formError = ref('')
const previewTimes = ref([])
const previewError = ref('')

const showRuns = ref(false)
const runsTask = ref(null)
const runs = ref([])
const runsLoading = ref(false)
const runsCursor = ref(null)
const scripts = ref([])
const scriptsPanel = ref(null)
const expandedRunId = ref('')

const form = reactive({
  taskType: '',
  name: '',
  paramsText: '{}',
  params: {},
  scheduleKind: 'cron',
  cron: '0 8 * * *',
  intervalValue: 30,
  intervalUnit: 60,
  timezone: 'Asia/Shanghai',
  enabled: true,
})

const typeMap = computed(() => Object.fromEntries(types.value.map((item) => [item.key, item])))

/** 可新建的类型：user 类型随便建；system 类型全库只能一条且要可写 */
const creatableTypes = computed(() =>
  types.value.filter((item) => {
    if (item.scope === 'user') return true
    if (!systemWritable.value) return false
    return !rows.value.some((row) => row.scope === 'system' && row.taskType === item.key)
  }),
)

const intervalSeconds = computed(() => Math.max(60, Number(form.intervalValue || 0) * Number(form.intervalUnit)))

const currentType = computed(() => typeMap.value[form.taskType] || null)
const currentFields = computed(() => currentType.value?.params?.fields || [])
const isUserScriptType = computed(() => String(form.taskType || '').startsWith('user-script.'))
const taskTypeOptions = computed(() =>
  (editingId.value ? types.value : creatableTypes.value).map((item) => ({
    label: item.title,
    value: item.key,
  })),
)
const scriptCreatableTypes = computed(() => types.value.filter((item) => item.key.startsWith('user-script.')))

function toggleRun(id) {
  expandedRunId.value = expandedRunId.value === id ? '' : id
}

function formatOutput(value) {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function hasScriptOutput(run) {
  return (
    run.exitCode !== null && run.exitCode !== undefined
    || (run.result !== null && run.result !== undefined)
    || !!run.stdout
    || !!run.stderr
  )
}

function fmtTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [list, meta] = await Promise.all([api.get('/schedules'), api.get('/schedules/types')])
    rows.value = list.items || []
    types.value = meta.items || []
    systemWritable.value = !!meta.systemWritable
    await reloadScripts()
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function defaultParams(fields) {
  const out = {}
  for (const field of fields) {
    if (field.default !== null && field.default !== undefined) out[field.name] = field.default
  }
  return out
}

function refreshScriptTypes() {
  const keys = new Set(types.value.map((item) => item.key))
  for (const script of scripts.value) {
    const key = `user-script.${script.id}`
    if (!keys.has(key)) {
      types.value.push({
        key,
        title: `自定义脚本 · ${script.name}`,
        description: '运行参数必须是 JSON 对象，脚本可通过 get_params() 读取。',
        scope: 'user',
      })
    }
  }
  const validKeys = new Set(scripts.value.map((script) => `user-script.${script.id}`))
  types.value = types.value.filter(
    (item) => !item.key.startsWith('user-script.') || validKeys.has(item.key),
  )
}

async function reloadScripts() {
  await scriptsPanel.value?.load?.()
  refreshScriptTypes()
}

function handleScriptsChanged() {
  refreshScriptTypes()
}

function openCreate() {
  const first = creatableTypes.value[0]
  if (!first) return
  editingId.value = ''
  formError.value = ''
  previewTimes.value = []
  previewError.value = ''
  form.taskType = first.key
  form.name = first.title
  form.paramsText = isUserScriptType.value
    ? '{}'
    : JSON.stringify(defaultParams(first.params.fields || []), null, 2)
  form.scheduleKind = 'cron'
  form.cron = first.defaultCron || '0 8 * * *'
  form.intervalValue = 30
  form.intervalUnit = 60
  form.timezone = 'Asia/Shanghai'
  form.enabled = true
  showEditor.value = true
  refreshPreview()
}

function openEdit(row) {
  const type = typeMap.value[row.taskType]
  editingId.value = row.id
  formError.value = ''
  previewTimes.value = []
  previewError.value = ''
  form.taskType = row.taskType
  form.name = row.name
  form.paramsText = JSON.stringify(row.params || {}, null, 2)
  form.scheduleKind = row.scheduleKind
  form.cron = row.cron || type?.defaultCron || '0 8 * * *'
  form.intervalValue = Math.max(1, Math.round((row.intervalSeconds || 1800) / 60))
  form.intervalUnit = 60
  form.timezone = row.timezone || 'Asia/Shanghai'
  form.enabled = row.enabled
  showEditor.value = true
  refreshPreview()
}

let previewTimer = null
function schedulePreview() {
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(refreshPreview, 320)
}

async function refreshPreview() {
  if (isUserScriptType.value) return
  previewError.value = ''
  previewTimes.value = []
  const payload = {
    scheduleKind: form.scheduleKind,
    cron: form.scheduleKind === 'cron' ? form.cron : '',
    intervalSeconds: form.scheduleKind === 'interval' ? intervalSeconds.value : null,
    timezone: form.timezone,
    count: 3,
  }
  try {
    const res = await api.post('/schedules/preview', payload)
    previewTimes.value = res.times || []
  } catch (err) {
    previewError.value = err.message
  }
}

watch(() => [form.scheduleKind, form.cron, form.intervalValue, form.intervalUnit, form.timezone], schedulePreview)
watch(
  () => scripts.value,
  () => refreshScriptTypes(),
  { deep: true },
)

function paramsPayload() {
  if (isUserScriptType.value) {
    try {
      const parsed = JSON.parse(form.paramsText || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        formError.value = '运行参数必须是 JSON 对象'
        return null
      }
      return parsed
    } catch {
      formError.value = '运行参数 JSON 解析失败，请检查格式'
      return null
    }
  }
  const out = {}
  for (const field of currentFields.value) {
    const value = form.params[field.name]
    if (Array.isArray(value)) {
      if (value.length) out[field.name] = value
    } else if (value !== null && value !== undefined && value !== '') {
      out[field.name] = value
    }
  }
  return out
}

async function save() {
  formError.value = ''
  if (!form.name.trim()) {
    formError.value = '请填写任务名称'
    return
  }
  if (form.scheduleKind === 'cron' && !form.cron.trim()) {
    formError.value = '请填写 cron 表达式'
    return
  }
  const params = paramsPayload()
  if (params === null) return
  const payload = {
    name: form.name.trim(),
    params,
    scheduleKind: form.scheduleKind,
    cron: form.scheduleKind === 'cron' ? form.cron.trim() : '',
    intervalSeconds: form.scheduleKind === 'interval' ? intervalSeconds.value : null,
    timezone: form.timezone.trim(),
    enabled: form.enabled,
  }
  saving.value = true
  try {
    if (editingId.value) {
      const updated = await api.patch(`/schedules/${editingId.value}`, payload)
      rows.value = rows.value.map((row) => (row.id === updated.id ? updated : row))
      toast(`已更新「${updated.name}」`)
    } else {
      const created = await api.post('/schedules', { ...payload, taskType: form.taskType })
      rows.value = [created, ...rows.value]
      toast(`已新建「${created.name}」`)
    }
    showEditor.value = false
  } catch (err) {
    formError.value = err.message
  } finally {
    saving.value = false
  }
}

async function toggle(row) {
  busyId.value = row.id
  error.value = ''
  try {
    const updated = await api.patch(`/schedules/${row.id}`, { enabled: !row.enabled })
    rows.value = rows.value.map((item) => (item.id === updated.id ? updated : item))
  } catch (err) {
    error.value = err.message
  } finally {
    busyId.value = ''
  }
}

async function runNow(row) {
  busyId.value = row.id
  error.value = ''
  try {
    const res = await api.post(`/schedules/${row.id}/run`, {})
    const updated = res.schedule
    rows.value = rows.value.map((item) => (item.id === updated.id ? updated : item))
    if (res.action === 'dispatched') toast('已投递，本次执行不占用定时槽位')
    else toast(`本次未执行：${res.reason || '上一次仍在执行'}`)
  } catch (err) {
    error.value = err.message
  } finally {
    busyId.value = ''
  }
}

async function remove(row) {
  // 二次确认放在按钮上（同提示词库的删除），避免误删掉一条配好参数的任务
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
    await api.del(`/schedules/${row.id}`)
    rows.value = rows.value.filter((item) => item.id !== row.id)
    toast(`已删除「${row.name}」`)
  } catch (err) {
    error.value = err.message
  } finally {
    busyId.value = ''
  }
}

async function openRuns(row) {
  runsTask.value = row
  runs.value = []
  runsCursor.value = null
  showRuns.value = true
  await loadRuns()
}

async function loadRuns() {
  if (!runsTask.value) return
  runsLoading.value = true
  try {
    const query = `limit=20${runsCursor.value ? `&cursor=${encodeURIComponent(runsCursor.value)}` : ''}`
    const page = await api.getPage(`/schedules/${runsTask.value.id}/runs?${query}`)
    runs.value = [...runs.value, ...(page.data?.items || [])]
    runsCursor.value = page.nextCursor || null
  } catch (err) {
    error.value = err.message
  } finally {
    runsLoading.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => {
  if (previewTimer) clearTimeout(previewTimer)
  if (confirmTimer) clearTimeout(confirmTimer)
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">定时任务</h1>
        <p class="page-sub">
          到点自动执行的动作。任务类型由后端注册，这里只管时间与参数；执行仍走统一任务队列，可查历史、可取消。
        </p>
      </div>
      <n-tooltip :disabled="creatableTypes.length > 0">
        <template #trigger>
          <n-button
            type="primary"
            :disabled="creatableTypes.length === 0 || scriptCreatableTypes.length === 0"
            @click="openCreate"
          >
            新建任务
          </n-button>
        </template>
        当前没有可新建的类型：已接入的类型都是系统级，全库只能有一条，直接改它即可
      </n-tooltip>
    </div>

    <n-alert v-if="error" type="error" closable @close="error = ''">{{ error }}</n-alert>

    <n-spin :show="loading">
      <n-empty v-if="!loading && rows.length === 0" description="还没有定时任务">
        <template #extra>
          <n-button v-if="creatableTypes.length && scriptCreatableTypes.length" size="small" @click="openCreate">新建任务</n-button>
          <span v-else class="sched-empty-hint">
            系统级任务会在首次登录时自动创建；若没有出现，检查后端定时调度器是否启动。
          </span>
        </template>
      </n-empty>

      <ul v-else class="sched-list">
        <li v-for="row in rows" :key="row.id" class="sched-row" :class="{ 'is-paused': !row.enabled }">
          <div class="sched-main">
            <div class="sched-title-line">
              <span class="sched-name">{{ row.name }}</span>
              <n-tag v-if="row.scope === 'system'" size="small" :bordered="false">系统</n-tag>
              <n-tag
                v-if="row.lastStatus"
                size="small"
                :type="STATUS_TYPE[row.lastStatus] || 'default'"
                :bordered="false"
              >
                {{ STATUS_TEXT[row.lastStatus] || row.lastStatus }}
              </n-tag>
              <n-tag v-if="!row.enabled" size="small" type="warning" :bordered="false">已停用</n-tag>
            </div>
            <div class="sched-meta">
              <span class="sched-type">{{ typeMap[row.taskType]?.title || row.taskType }}</span>
              <span class="sched-dot">·</span>
              <span>{{ row.scheduleLabel }}</span>
              <span class="sched-dot">·</span>
              <span>下次 {{ fmtTime(row.nextRunAt) }}</span>
              <span class="sched-dot">·</span>
              <span>{{ row.timezone }}</span>
            </div>
            <div v-if="row.pausedReason" class="sched-paused-reason">
              {{ row.pausedReason }}
              <span v-if="row.consecutiveFailures">（连续失败 {{ row.consecutiveFailures }} 次）</span>
            </div>
          </div>

          <div class="sched-actions">
            <n-switch
              :value="row.enabled"
              size="small"
              :disabled="!row.editable || busyId === row.id"
              @update:value="toggle(row)"
            />
            <n-button size="tiny" tertiary @click="openRuns(row)">历史</n-button>
            <n-button
              size="tiny"
              tertiary
              :disabled="!row.editable || !row.enabled || busyId === row.id"
              @click="runNow(row)"
            >
              立即执行
            </n-button>
            <n-button size="tiny" tertiary :disabled="!row.editable" @click="openEdit(row)">编辑</n-button>
            <n-button
              v-if="row.scope === 'user'"
              size="tiny"
              tertiary
              type="error"
              :disabled="!row.editable || busyId === row.id"
              @click="remove(row)"
            >
              {{ confirmingId === row.id ? '确认删除' : '删除' }}
            </n-button>
          </div>
        </li>
      </ul>
    </n-spin>

    <CustomScriptsPanel ref="scriptsPanel" v-model:scripts="scripts" @updated="handleScriptsChanged" />

    <!-- 新建 / 编辑 -->
    <n-modal v-model:show="showEditor" preset="card" class="sched-modal" :title="editingId ? '编辑定时任务' : '新建定时任务'">
      <n-form label-placement="top" :show-feedback="false">
        <n-form-item label="任务类型">
          <n-select
            v-model:value="form.taskType"
            :options="taskTypeOptions"
            :disabled="!!editingId"
          />
        </n-form-item>
        <p v-if="currentType?.description" class="sched-type-desc">{{ currentType.description }}</p>

        <n-form-item v-if="isUserScriptType" label="运行参数（JSON 对象）">
          <n-input
            v-model:value="form.paramsText"
            type="textarea"
            class="sched-params-json"
            :rows="6"
            placeholder="{}"
          />
        </n-form-item>

        <n-form-item label="名称">
          <n-input v-model:value="form.name" placeholder="例：拉取热点榜单" />
        </n-form-item>

        <template v-for="field in currentFields" :key="field.name">
          <n-form-item :label="field.title">
            <n-select
              v-if="field.type === 'array'"
              v-model:value="form.params[field.name]"
              multiple
              filterable
              tag
              clearable
              :placeholder="field.description || '留空表示全部'"
              :options="(field.options || []).map((value) => ({ label: value, value }))"
            />
            <n-input
              v-else-if="field.type === 'string'"
              v-model:value="form.params[field.name]"
              :placeholder="field.description"
            />
            <n-input-number
              v-else-if="field.type === 'integer' || field.type === 'number'"
              v-model:value="form.params[field.name]"
              :placeholder="field.description"
            />
            <n-switch v-else-if="field.type === 'boolean'" v-model:value="form.params[field.name]" />
            <n-input v-else v-model:value="form.params[field.name]" :placeholder="field.description" />
          </n-form-item>
        </template>

        <n-form-item label="时间">
          <div class="sched-schedule-editor">
            <n-select
              v-model:value="form.scheduleKind"
              class="sched-kind"
              :options="[
                { label: '按 cron 表达式', value: 'cron' },
                { label: '固定间隔', value: 'interval' },
              ]"
            />
            <template v-if="form.scheduleKind === 'cron'">
              <n-input v-model:value="form.cron" placeholder="分 时 日 月 周，例：0 8,12,18 * * *" />
              <div class="sched-presets">
                <n-button
                  v-for="preset in CRON_PRESETS"
                  :key="preset.value"
                  size="tiny"
                  tertiary
                  @click="form.cron = preset.value"
                >
                  {{ preset.label }}
                </n-button>
              </div>
            </template>
            <template v-else>
              <div class="sched-interval">
                <n-input-number v-model:value="form.intervalValue" :min="1" class="sched-interval-num" />
                <n-select
                  v-model:value="form.intervalUnit"
                  class="sched-interval-unit"
                  :options="[
                    { label: '分钟', value: 60 },
                    { label: '小时', value: 3600 },
                  ]"
                />
              </div>
            </template>
          </div>
        </n-form-item>

        <n-form-item label="时区">
          <n-input v-model:value="form.timezone" placeholder="Asia/Shanghai" />
        </n-form-item>

        <div class="sched-preview">
          <span class="sched-preview-label">未来触发</span>
          <span v-if="previewError" class="sched-preview-error">{{ previewError }}</span>
          <span v-else-if="!previewTimes.length" class="sched-preview-muted">计算中…</span>
          <span v-else class="sched-preview-times">{{ previewTimes.map(fmtTime).join('　·　') }}</span>
        </div>

        <n-form-item label="启用">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>

        <n-alert v-if="formError" type="error" class="sched-form-error">{{ formError }}</n-alert>
      </n-form>

      <template #footer>
        <div class="sched-modal-footer">
          <n-button :disabled="saving" @click="showEditor = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="save">保存</n-button>
        </div>
      </template>
    </n-modal>

    <!-- 运行历史 -->
    <n-modal v-model:show="showRuns" preset="card" class="sched-runs-modal" :title="`运行历史 · ${runsTask?.name || ''}`">
      <n-empty v-if="!runsLoading && runs.length === 0" description="这个任务还没有触发记录" />
      <ul v-else class="sched-runs">
        <li v-for="run in runs" :key="run.id" class="sched-run">
          <div class="sched-run-head">
            <n-tag size="small" :type="STATUS_TYPE[run.status] || 'default'" :bordered="false">
              {{ STATUS_TEXT[run.status] || run.status }}
            </n-tag>
            <span class="sched-run-time">{{ fmtTime(run.triggeredAt) }}</span>
            <span class="sched-run-source">{{ run.source === 'manual' ? '手动' : '定时' }}</span>
            <span v-if="run.durationMs" class="sched-run-duration">
              {{ (run.durationMs / 1000).toFixed(1) }}s
            </span>
          </div>
          <p v-if="run.resultSummary" class="sched-run-note">{{ run.resultSummary }}</p>
          <p v-if="run.skipReason" class="sched-run-note">{{ run.skipReason }}</p>
          <p v-if="run.errorMessage" class="sched-run-error">
            {{ run.errorCode }}: {{ run.errorMessage }}
          </p>
          <button
            v-if="hasScriptOutput(run)"
            type="button"
            class="sched-run-toggle"
            @click="toggleRun(run.id)"
          >
            {{ expandedRunId === run.id ? '收起输出' : '展开输出' }}
          </button>
          <div v-if="expandedRunId === run.id" class="sched-run-output">
            <p v-if="run.exitCode !== null && run.exitCode !== undefined" class="sched-run-output-line">
              exitCode：{{ run.exitCode }}
            </p>
            <p v-if="run.result !== null && run.result !== undefined" class="sched-run-output-line">result</p>
            <pre v-if="run.result !== null && run.result !== undefined" class="sched-run-log">{{ formatOutput(run.result) }}</pre>
            <p v-if="run.stdout" class="sched-run-output-line">stdout</p>
            <pre v-if="run.stdout" class="sched-run-log">{{ run.stdout }}</pre>
            <p v-if="run.stderr" class="sched-run-output-line">stderr</p>
            <pre v-if="run.stderr" class="sched-run-log">{{ run.stderr }}</pre>
          </div>
        </li>
      </ul>
      <div v-if="runsCursor" class="sched-runs-more">
        <n-button size="small" :loading="runsLoading" @click="loadRuns">加载更早</n-button>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.sched-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.sched-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg, 10px);
  background: var(--color-surface);
}
.sched-row.is-paused {
  background: var(--color-surface-muted);
}
.sched-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.sched-title-line {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.sched-name {
  font-size: var(--font-body);
  font-weight: 600;
  color: var(--color-text-primary);
}
.sched-meta {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-wrap: wrap;
  font-size: var(--font-sm);
  color: var(--color-text-muted);
}
.sched-dot {
  color: var(--color-border-strong);
}
.sched-paused-reason {
  font-size: var(--font-xs);
  color: var(--color-danger);
}
.sched-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}
.sched-empty-hint {
  font-size: var(--font-sm);
  color: var(--color-text-muted);
}
.sched-type-desc {
  /* 表单关了 show-feedback，form-item 底部没有反馈留白，
     这里必须给正间距：负 margin 会把文字顶进上面的下拉框里（实测重叠 4px） */
  margin: var(--space-2) 0 var(--space-3);
  font-size: var(--font-xs);
  line-height: 1.5;
  color: var(--color-text-muted);
}
.sched-schedule-editor {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.sched-kind {
  max-width: 200px;
}
.sched-presets {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}
.sched-interval {
  display: flex;
  gap: var(--space-2);
}
.sched-interval-num {
  width: 120px;
}
.sched-interval-unit {
  width: 120px;
}
.sched-preview {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-3);
  border-radius: var(--radius-md, 8px);
  background: var(--color-surface-muted);
  font-size: var(--font-sm);
}
.sched-preview-label {
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.sched-preview-times {
  color: var(--color-text-secondary);
}
.sched-preview-muted {
  color: var(--color-text-muted);
}
.sched-preview-error {
  color: var(--color-danger);
}
.sched-form-error {
  margin-top: var(--space-2);
}
.sched-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
/* naive-ui 的 modal 走 teleport 渲染到 body，scoped 的 [data-v-*] 属性挂不上去，
   所以这两个类必须写在 :global 里——写进普通 scoped 规则里是静默失效的
   （表现：抽屉永远全屏宽、内容长了把保存按钮顶出视口）。 */
:global(.sched-modal) {
  width: min(560px, 94vw);
  /* 字段多的类型（如简报有 4 个参数）会把卡片的保存/取消挤出视口，
     限高后让内容区自己滚，按钮始终可达 */
  max-height: 88vh;
}
:global(.sched-modal .n-card-content) {
  /* 卡片是 flex 列布局：不收缩 + 不设 min-height:0，内容会直接顶出卡片而不滚动 */
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
:global(.sched-runs-modal) {
  width: min(640px, 94vw);
  max-height: 88vh;
}
:global(.sched-runs-modal .n-card-content) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
.sched-runs {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.sched-run {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 8px);
}
.sched-run-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-sm);
  color: var(--color-text-muted);
}
.sched-run-note {
  margin: var(--space-1) 0 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
.sched-run-error {
  margin: var(--space-1) 0 0;
  font-size: var(--font-xs);
  color: var(--color-danger);
  word-break: break-all;
}
.sched-params-json :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
  tab-size: 4;
}
.sched-run-toggle {
  margin-top: var(--space-1);
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-primary);
  font-size: var(--font-xs);
  cursor: pointer;
}
.sched-run-output {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin-top: var(--space-2);
}
.sched-run-output-line {
  margin: 0;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}
.sched-run-log {
  margin: 0;
  max-height: 180px;
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
.sched-runs-more {
  display: flex;
  justify-content: center;
  margin-top: var(--space-3);
}
@media (max-width: 900px) {
  .sched-row {
    flex-direction: column;
    align-items: stretch;
  }
  .sched-actions {
    flex-wrap: wrap;
  }
}
</style>
