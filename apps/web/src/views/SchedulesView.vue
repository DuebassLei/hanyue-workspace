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
} from 'naive-ui'
import { api } from '../api/client'
import { toast, confirm } from '../ui/feedback'
import { usePermission } from '../composables/usePermission'

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

const DEFAULT_CODE = `def run(params, ctx):
    """统一契约：run(params, ctx) -> dict，界面摘要优先取返回值里的 summary。"""
    log('params', params)
    return {"summary": "脚本已执行"}
`

const { can } = usePermission()
const canManageScripts = computed(() => can('scheduled-script.admin'))

const rows = ref([]) // 定时配置（ScheduledTask）
const types = ref([]) // 脚本类型元数据（内置 + 已发布的用户脚本）
const scripts = ref([]) // 用户脚本（ScheduledScript）
const systemWritable = ref(false)
const loading = ref(true)
const error = ref('')
const busyId = ref('')
const confirmingId = ref('')
let confirmTimer = null

const examples = ref([])

// ── 统一编辑器（脚本模式 / 内置任务模式共用一个弹窗）──────────────────
const showEditor = ref(false)
const editorMode = ref('script') // 'script' = 代码+时间一体；'task' = 内置任务只配参数与时间
const editingTaskId = ref('') // 正在编辑的定时配置 id（有才走 PATCH）
const editingScriptId = ref('') // 正在编辑的用户脚本 id
const saving = ref(false)
const formError = ref('')
const previewTimes = ref([])
const previewError = ref('')

const showRuns = ref(false)
const runsTask = ref(null)
const runs = ref([])
const runsLoading = ref(false)
const runsCursor = ref(null)
const expandedRunId = ref('')

const showResult = ref(false)
const result = ref(null)
const resultError = ref('')

const form = reactive({
  taskType: '',
  name: '',
  description: '',
  draftCode: '',
  testParamsText: '{}',
  paramsText: '{}',
  params: {},
  timeoutSeconds: 30,
  scheduled: true,
  scheduleKind: 'cron',
  cron: '0 8 * * *',
  intervalValue: 30,
  intervalUnit: 60,
  timezone: 'Asia/Shanghai',
  enabled: true,
})

const typeMap = computed(() => Object.fromEntries(types.value.map((item) => [item.key, item])))
const currentType = computed(() => typeMap.value[form.taskType] || null)
const currentFields = computed(() => currentType.value?.params?.fields || [])
const isUserScriptType = computed(() => String(form.taskType || '').startsWith('user-script.'))

/**
 * 统一列表：不再分「定时任务」和「脚本」两块。
 * - task：内置脚本（有定时配置）
 * - script：用户脚本（自带定时配置引用，可能还没配时间）
 * - orphan：脚本已删但定时配置还在（只能改时间/删除）
 */
const entries = computed(() => {
  const list = []
  const scheduledIds = new Set()
  for (const row of rows.value) {
    if (!row.taskType.startsWith('user-script.')) list.push({ kind: 'task', row })
  }
  for (const script of scripts.value) {
    const schedule = rows.value.find((r) => r.taskType === `user-script.${script.id}`) || null
    if (schedule) scheduledIds.add(schedule.id)
    list.push({ kind: 'script', script, schedule })
  }
  for (const row of rows.value) {
    if (row.taskType.startsWith('user-script.') && !scheduledIds.has(row.id)) {
      list.push({ kind: 'orphan', row })
    }
  }
  return list
})

function scheduleOf(script) {
  return rows.value.find((r) => r.taskType === `user-script.${script.id}`) || null
}

function entryName(entry) {
  if (entry.kind === 'script') return entry.script.name
  return entry.row.name
}

function entrySwitchValue(entry) {
  if (entry.kind === 'task' || entry.kind === 'orphan') return entry.row.enabled
  return entry.script.enabled && (entry.schedule ? entry.schedule.enabled : true)
}

function entryMeta(entry) {
  if (entry.kind === 'script') {
    const parts = []
    parts.push(`v${entry.script.publishedVersion || '草稿'}`)
    parts.push(`${entry.script.timeoutSeconds}s`)
    if (entry.schedule) {
      parts.push(entry.schedule.scheduleLabel)
      if (entry.schedule.nextRunAt) parts.push(`下次 ${fmtTime(entry.schedule.nextRunAt)}`)
      if (!entry.schedule.enabled) parts.push('定时已停用')
    } else {
      parts.push('未配置定时')
    }
    return parts
  }
  const row = entry.row
  return [
    typeMap.value[row.taskType]?.title || row.taskType,
    row.scheduleLabel,
    row.nextRunAt ? `下次 ${fmtTime(row.nextRunAt)}` : '',
    row.timezone,
  ].filter(Boolean)
}

function lastStatusOf(entry) {
  if (entry.kind === 'script') return entry.schedule?.lastStatus || ''
  return entry.row.lastStatus || ''
}

function fmtTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
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

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [list, meta, scriptList] = await Promise.all([
      api.get('/schedules'),
      api.get('/schedules/types'),
      api.get('/schedules/scripts'),
    ])
    rows.value = list.items || []
    types.value = meta.items || []
    systemWritable.value = !!meta.systemWritable
    scripts.value = scriptList.items || []
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function loadExamples() {
  if (examples.value.length) return
  try {
    const data = await api.get('/schedules/scripts/examples')
    examples.value = data.items || []
  } catch {
    examples.value = [] // 示例加载失败不阻塞编辑器
  }
}

function defaultParams(fields) {
  const out = {}
  for (const field of fields) {
    if (field.default !== null && field.default !== undefined) out[field.name] = field.default
  }
  return out
}

// ── 打开编辑器 ────────────────────────────────────────────────────────
function resetEditor() {
  formError.value = ''
  previewTimes.value = []
  previewError.value = ''
  form.taskType = ''
  form.name = ''
  form.description = ''
  form.draftCode = ''
  form.testParamsText = '{}'
  form.paramsText = '{}'
  form.params = {}
  form.timeoutSeconds = 30
  form.scheduled = true
  form.scheduleKind = 'cron'
  form.cron = '0 8 * * *'
  form.intervalValue = 30
  form.intervalUnit = 60
  form.timezone = 'Asia/Shanghai'
  form.enabled = true
}

function openCreate() {
  resetEditor()
  editingTaskId.value = ''
  editingScriptId.value = ''
  editorMode.value = 'script'
  form.draftCode = DEFAULT_CODE
  showEditor.value = true
  loadExamples()
  refreshPreview()
}

function openEdit(entry) {
  resetEditor()
  if (entry.kind === 'script') {
    editorMode.value = 'script'
    editingScriptId.value = entry.script.id
    editingTaskId.value = entry.schedule?.id || ''
    form.taskType = `user-script.${entry.script.id}`
    form.name = entry.script.name
    form.description = entry.script.description || ''
    form.draftCode = entry.script.draftCode || ''
    form.timeoutSeconds = Number(entry.script.timeoutSeconds || 30)
    form.enabled = entry.script.enabled
    const schedule = entry.schedule
    if (schedule) {
      form.scheduled = true
      form.name = schedule.name || form.name
      form.paramsText = JSON.stringify(schedule.params || {}, null, 2)
      form.scheduleKind = schedule.scheduleKind
      form.cron = schedule.cron || '0 8 * * *'
      form.intervalValue = Math.max(1, Math.round((schedule.intervalSeconds || 1800) / 60))
      form.timezone = schedule.timezone || 'Asia/Shanghai'
      form.enabled = schedule.enabled && entry.script.enabled
    } else {
      form.scheduled = false
    }
  } else {
    // 内置脚本 / 孤儿配置：只配参数与时间
    editorMode.value = 'task'
    const row = entry.row
    editingScriptId.value = ''
    editingTaskId.value = row.id
    form.taskType = row.taskType
    form.name = row.name
    form.paramsText = JSON.stringify(row.params || {}, null, 2)
    form.params = { ...defaultParams(currentType.value?.params?.fields || []), ...(row.params || {}) }
    form.scheduled = true
    form.scheduleKind = row.scheduleKind
    form.cron = row.cron || currentType.value?.defaultCron || '0 8 * * *'
    form.intervalValue = Math.max(1, Math.round((row.intervalSeconds || 1800) / 60))
    form.intervalUnit = 60
    form.timezone = row.timezone || 'Asia/Shanghai'
    form.enabled = row.enabled
  }
  showEditor.value = true
  refreshPreview()
}

// ── 时间预览 ─────────────────────────────────────────────────────────
let previewTimer = null
function schedulePreview() {
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(refreshPreview, 320)
}

async function refreshPreview() {
  if (!form.scheduled) {
    previewTimes.value = []
    previewError.value = ''
    return
  }
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

watch(() => [form.scheduled, form.scheduleKind, form.cron, form.intervalValue, form.intervalUnit, form.timezone], schedulePreview)

const intervalSeconds = computed(() => Math.max(60, Number(form.intervalValue || 0) * Number(form.intervalUnit)))

// ── 保存 ─────────────────────────────────────────────────────────────
function validateSchedule() {
  if (!form.name.trim()) {
    formError.value = '请填写名称'
    return false
  }
  if (form.scheduled && form.scheduleKind === 'cron' && !form.cron.trim()) {
    formError.value = '请填写 cron 表达式'
    return false
  }
  return true
}

function schedulePayload() {
  return {
    name: form.name.trim(),
    params: paramsPayload(),
    scheduleKind: form.scheduleKind,
    cron: form.scheduleKind === 'cron' ? form.cron.trim() : '',
    intervalSeconds: form.scheduleKind === 'interval' ? intervalSeconds.value : null,
    timezone: form.timezone.trim(),
    enabled: form.enabled,
  }
}

function paramsPayload() {
  if (editorMode.value === 'script' || isUserScriptType.value) {
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
  if (!validateSchedule()) return
  const params = paramsPayload()
  if (params === null) return
  saving.value = true
  try {
    if (editorMode.value === 'script') {
      await saveScriptWithSchedule(params)
    } else {
      await saveTaskSchedule(params)
    }
  } catch (err) {
    formError.value = err.message
  } finally {
    saving.value = false
  }
}

/** 脚本模式：保存草稿 → 发布 → 建/改定时配置，一次保存全部生效 */
async function saveScriptWithSchedule(params) {
  const scriptPayload = {
    name: form.name.trim(),
    description: form.description.trim(),
    draftCode: form.draftCode,
    timeoutSeconds: Number(form.timeoutSeconds),
    enabled: form.enabled,
  }
  const saved = editingScriptId.value
    ? await api.patch(`/schedules/scripts/${editingScriptId.value}`, scriptPayload)
    : await api.post('/schedules/scripts', scriptPayload)
  scripts.value = upsert(scripts.value, saved)
  editingScriptId.value = saved.id
  const published = await api.post(`/schedules/scripts/${saved.id}/publish`)
  scripts.value = upsert(scripts.value, published)

  if (form.scheduled) {
    const payload = { ...schedulePayload(), params }
    if (editingTaskId.value) {
      await api.patch(`/schedules/${editingTaskId.value}`, payload)
    } else {
      await api.post('/schedules', { ...payload, taskType: `user-script.${saved.id}` })
    }
  } else if (editingTaskId.value) {
    // 关掉「到点自动执行」：保留配置但停用，时间不丢
    await api.patch(`/schedules/${editingTaskId.value}`, { enabled: false })
  }
  await load()
  showEditor.value = false
  toast(`已保存并发布「${published.name}」v${published.publishedVersion}`)
}

/** 内置任务模式：只更新定时配置（参数 + 时间） */
async function saveTaskSchedule(params) {
  const payload = { ...schedulePayload(), params }
  const updated = await api.patch(`/schedules/${editingTaskId.value}`, payload)
  rows.value = rows.value.map((row) => (row.id === updated.id ? updated : row))
  showEditor.value = false
  toast(`已更新「${updated.name}」`)
}

function upsert(list, row) {
  if (!row?.id) return list
  return list.some((item) => item.id === row.id)
    ? list.map((item) => (item.id === row.id ? row : item))
    : [row, ...list]
}

// ── 行操作 ───────────────────────────────────────────────────────────
async function toggle(entry) {
  const next = !entrySwitchValue(entry)
  busyId.value = entryKey(entry)
  error.value = ''
  try {
    if (entry.kind === 'script') {
      const updated = await api.patch(`/schedules/scripts/${entry.script.id}`, { enabled: next })
      scripts.value = upsert(scripts.value, updated)
      if (entry.schedule) {
        const schedule = await api.patch(`/schedules/${entry.schedule.id}`, { enabled: next })
        rows.value = rows.value.map((row) => (row.id === schedule.id ? schedule : row))
      }
    } else {
      const updated = await api.patch(`/schedules/${entry.row.id}`, { enabled: next })
      rows.value = rows.value.map((row) => (row.id === updated.id ? updated : row))
    }
  } catch (err) {
    error.value = err.message
  } finally {
    busyId.value = ''
  }
}

function entryKey(entry) {
  return entry.kind === 'script' ? `script:${entry.script.id}` : `task:${entry.row.id}`
}

async function runNow(entry) {
  busyId.value = entryKey(entry)
  error.value = ''
  try {
    if (entry.kind === 'script' && !entry.schedule) {
      // 没配定时的脚本：直接手动执行一次并等结果
      await runScriptOnce(entry.script)
      return
    }
    const target = entry.kind === 'script' ? entry.schedule : entry.row
    const res = await api.post(`/schedules/${target.id}/run`, {})
    const updated = res.schedule
    rows.value = rows.value.map((row) => (row.id === updated.id ? updated : row))
    if (res.action === 'dispatched') toast('已投递，本次执行不占用定时槽位')
    else toast(`本次未执行：${res.reason || '上一次仍在执行'}`)
  } catch (err) {
    error.value = err.message
  } finally {
    busyId.value = ''
  }
}

/** 手动执行未配置定时的脚本：/run 是入队回执，轮询 jobs 到终态拿结果 */
async function runScriptOnce(script) {
  const { jobId } = await api.post(`/schedules/scripts/${script.id}/run`)
  if (!jobId) throw new Error('入队失败：未返回 jobId')
  result.value = null
  resultError.value = ''
  showResult.value = true
  const deadline = Date.now() + 120_000
  let job = null
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 1000))
    job = await api.get(`/jobs/${jobId}`)
    if (['succeeded', 'failed', 'cancelled', 'stopped'].includes(job.status)) break
  }
  if (!job || !['succeeded', 'failed', 'cancelled', 'stopped'].includes(job.status)) {
    throw new Error('执行超时：任务仍在队列中，请稍后查看结果')
  }
  if (job.status !== 'succeeded') {
    resultError.value = job.errorMessage || `执行失败（${job.errorCode || job.status}）`
    result.value = job.result || null
    return
  }
  result.value = job.result || {}
}

/** 脚本模式编辑器里的测试验证：测草稿代码，回滚执行不落库 */
async function testFromEditor() {
  formError.value = ''
  const timeout = Number(form.timeoutSeconds)
  if (!Number.isInteger(timeout) || timeout < 5 || timeout > 300) {
    formError.value = '超时秒数必须在 5 到 300 之间'
    return
  }
  let testParams = {}
  try {
    testParams = JSON.parse(form.testParamsText || '{}')
  } catch {
    formError.value = '测试参数 JSON 解析失败，请检查格式'
    return
  }
  saving.value = true
  try {
    // 测试验证测的是草稿；新建脚本先落草稿拿到 id
    if (!editingScriptId.value) {
      const saved = await api.post('/schedules/scripts', {
        name: form.name.trim() || '未命名脚本',
        description: form.description.trim(),
        draftCode: form.draftCode,
        timeoutSeconds: timeout,
        enabled: form.enabled,
      })
      scripts.value = upsert(scripts.value, saved)
      editingScriptId.value = saved.id
    }
    const data = await api.post(`/schedules/scripts/${editingScriptId.value}/test`, { params: testParams })
    result.value = null
    resultError.value = ''
    if (data.ok) {
      result.value = {
        summary: data.result?.summary || '（脚本未返回 summary）',
        result: data.result,
        stdout: data.stdout,
        stderr: data.stderr,
        durationMs: data.durationMs,
      }
    } else {
      resultError.value = `${data.error?.code || 'script_failed'}: ${data.error?.message || '执行失败'}`
    }
    showResult.value = true
  } catch (err) {
    resultError.value = err.message
    showResult.value = true
  } finally {
    saving.value = false
  }
}

/** 内置任务模式编辑器里的测试运行（按当前参数回滚执行发布版脚本） */
async function testTaskFromEditor() {
  formError.value = ''
  const params = paramsPayload()
  if (params === null) return
  saving.value = true
  try {
    const data = await api.post('/schedules/test-run', { taskType: form.taskType, params })
    result.value = null
    resultError.value = ''
    if (data.ok) {
      result.value = { summary: data.result?.summary || '—', result: data.result, durationMs: data.durationMs }
    } else {
      resultError.value = `${data.error?.code || 'script_failed'}: ${data.error?.message || '执行失败'}`
    }
    showResult.value = true
  } catch (err) {
    resultError.value = err.message
    showResult.value = true
  } finally {
    saving.value = false
  }
}

async function remove(entry) {
  const key = entryKey(entry)
  // 二次确认放在按钮上（同提示词库的删除），避免误删
  if (confirmingId.value !== key) {
    confirmingId.value = key
    if (confirmTimer) clearTimeout(confirmTimer)
    confirmTimer = setTimeout(() => {
      confirmingId.value = ''
      confirmTimer = null
    }, 3000)
    return
  }
  if (confirmTimer) clearTimeout(confirmTimer)
  confirmingId.value = ''
  busyId.value = key
  error.value = ''
  try {
    if (entry.kind === 'script') {
      await api.del(`/schedules/scripts/${entry.script.id}`)
      if (entry.schedule) {
        await api.del(`/schedules/${entry.schedule.id}`)
      }
      toast(`已删除脚本「${entry.script.name}」`)
    } else {
      await api.del(`/schedules/${entry.row.id}`)
      toast(`已删除「${entry.row.name}」`)
    }
    await load()
  } catch (err) {
    error.value = err.message
  } finally {
    busyId.value = ''
  }
}

async function openRuns(entry) {
  const target = entry.kind === 'script' ? entry.schedule : entry.row
  if (!target) return
  runsTask.value = target
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

async function applyExample(key) {
  const example = examples.value.find((item) => item.key === key)
  if (!example) return
  if (form.draftCode.trim() && form.draftCode !== DEFAULT_CODE && !(await confirm(`用「${example.title}」替换当前代码？`, '替换脚本代码'))) return
  form.draftCode = example.code
  if (!form.name.trim()) form.name = example.title
  if (!form.description.trim()) form.description = example.description
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
        <h1 class="page-title">定时脚本</h1>
        <p class="page-sub">
          到点自动执行的动作全部是脚本：内置脚本和你自己写的脚本在同一个列表里，写代码、配时间、测试验证都在一处完成。
        </p>
      </div>
      <n-button type="primary" :disabled="!canManageScripts" @click="openCreate">新建脚本</n-button>
    </div>

    <n-alert v-if="error" type="error" closable @close="error = ''">{{ error }}</n-alert>
    <n-alert v-if="!canManageScripts" type="warning">
      当前角色缺少「定时脚本管理」权限：内置脚本可改时间与参数，新建/编辑代码需要该权限。
    </n-alert>

    <n-spin :show="loading">
      <n-empty v-if="!loading && entries.length === 0" description="还没有脚本">
        <template #extra>
          <n-button size="small" :disabled="!canManageScripts" @click="openCreate">新建脚本</n-button>
        </template>
      </n-empty>

      <ul v-else class="sched-list">
        <li
          v-for="entry in entries"
          :key="entryKey(entry)"
          class="sched-row"
          :class="{ 'is-paused': !entrySwitchValue(entry) }"
        >
          <div class="sched-main">
            <div class="sched-title-line">
              <span class="sched-name">{{ entryName(entry) }}</span>
              <n-tag
                v-if="entry.kind === 'script'"
                size="small"
                :type="entry.script.publishedVersion ? 'success' : 'default'"
                :bordered="false"
              >
                {{ entry.script.publishedVersion ? `已发布 v${entry.script.publishedVersion}` : '草稿' }}
              </n-tag>
              <n-tag
                v-if="lastStatusOf(entry)"
                size="small"
                :type="STATUS_TYPE[lastStatusOf(entry)] || 'default'"
                :bordered="false"
              >
                {{ STATUS_TEXT[lastStatusOf(entry)] || lastStatusOf(entry) }}
              </n-tag>
              <n-tag v-if="!entrySwitchValue(entry)" size="small" type="warning" :bordered="false">已停用</n-tag>
            </div>
            <div class="sched-meta">
              <template v-for="(part, index) in entryMeta(entry)" :key="`${entryKey(entry)}-${index}`">
                <span v-if="index > 0" class="sched-dot">·</span>
                <span>{{ part }}</span>
              </template>
            </div>
            <div v-if="entry.row?.pausedReason" class="sched-paused-reason">
              {{ entry.row.pausedReason }}
              <span v-if="entry.row?.consecutiveFailures">（连续失败 {{ entry.row.consecutiveFailures }} 次）</span>
            </div>
            <p v-if="entry.kind === 'script' && entry.script.description" class="sched-desc">
              {{ entry.script.description }}
            </p>
          </div>

          <div class="sched-actions">
            <n-switch
              :value="entrySwitchValue(entry)"
              size="small"
              :disabled="busyId === entryKey(entry) || (entry.kind !== 'script' && !entry.row.editable)"
              @update:value="toggle(entry)"
            />
            <n-button
              v-if="(entry.kind === 'script' && entry.schedule) || entry.kind !== 'script'"
              size="tiny"
              tertiary
              @click="openRuns(entry)"
            >
              历史
            </n-button>
            <n-button
              size="tiny"
              tertiary
              :disabled="
                busyId === entryKey(entry)
                  || (entry.kind === 'script' && !entry.script.publishedVersion)
                  || (entry.kind !== 'script' && (!entry.row.editable || !entry.row.enabled))
              "
              @click="runNow(entry)"
            >
              立即执行
            </n-button>
            <n-button
              size="tiny"
              tertiary
              :disabled="entry.kind === 'script' ? !canManageScripts : !entry.row.editable"
              @click="openEdit(entry)"
            >
              编辑
            </n-button>
            <n-button
              v-if="entry.kind !== 'task'"
              size="tiny"
              tertiary
              type="error"
              :disabled="busyId === entryKey(entry) || (entry.kind === 'script' && !canManageScripts)"
              @click="remove(entry)"
            >
              {{ confirmingId === entryKey(entry) ? '确认删除' : '删除' }}
            </n-button>
          </div>
        </li>
      </ul>
    </n-spin>

    <!-- 统一编辑器：脚本模式（代码+时间一体）/ 内置任务模式（参数+时间） -->
    <n-modal
      v-model:show="showEditor"
      preset="card"
      :class="editorMode === 'script' ? 'scripts-modal' : 'sched-modal'"
      :title="editorMode === 'script' ? (editingScriptId ? '编辑脚本' : '新建脚本') : '编辑内置脚本'"
    >
      <n-form label-placement="top" :show-feedback="false">
        <template v-if="editorMode === 'script'">
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
          <n-form-item label="插入常用示例">
            <n-select
              :options="examples.map((item) => ({ label: item.title, value: item.key }))"
              placeholder="从示例开始（会替换当前代码）"
              :value="null"
              @update:value="applyExample"
            />
          </n-form-item>
          <n-form-item label="Python 代码（def run(params, ctx) → dict，项目全部依赖可用）">
            <n-input
              v-model:value="form.draftCode"
              type="textarea"
              class="code-input"
              :rows="14"
              placeholder="def run(params, ctx): ..."
            />
          </n-form-item>
          <n-form-item label="测试参数（JSON，测试验证时传给 params）">
            <n-input
              v-model:value="form.testParamsText"
              type="textarea"
              class="code-input"
              :rows="2"
              placeholder="{}"
            />
          </n-form-item>
          <n-form-item label="运行参数（JSON，定时触发时传给 params）">
            <n-input
              v-model:value="form.paramsText"
              type="textarea"
              class="code-input"
              :rows="2"
              placeholder="{}"
            />
          </n-form-item>
        </template>

        <template v-else>
          <n-form-item v-if="isUserScriptType" label="运行参数（JSON 对象）">
            <n-input
              v-model:value="form.paramsText"
              type="textarea"
              class="code-input"
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
        </template>

        <n-form-item label="到点自动执行">
          <n-switch v-model:value="form.scheduled" />
        </n-form-item>

        <template v-if="form.scheduled">
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
        </template>

        <n-form-item label="启用">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>

        <n-alert v-if="formError" type="error" class="sched-form-error">{{ formError }}</n-alert>
      </n-form>

      <template #footer>
        <div class="sched-modal-footer">
          <n-button :disabled="saving" @click="showEditor = false">取消</n-button>
          <n-button
            tertiary
            :loading="saving"
            @click="editorMode === 'script' ? testFromEditor() : testTaskFromEditor()"
          >
            测试验证
          </n-button>
          <n-button type="primary" :loading="saving" @click="save">
            {{ editorMode === 'script' ? '保存并发布' : '保存' }}
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- 测试验证 / 手动执行结果（不落库回滚执行） -->
    <n-modal v-model:show="showResult" preset="card" class="scripts-result-modal" title="执行结果">
      <n-alert v-if="resultError" type="error">{{ resultError }}</n-alert>
      <template v-else-if="result">
        <div class="result-meta">
          <span>summary：{{ result.summary || '—' }}</span>
          <span v-if="result.durationMs !== undefined && result.durationMs !== null">
            耗时：{{ (result.durationMs / 1000).toFixed(2) }}s（测试执行已回滚，不落库）
          </span>
        </div>
        <p v-if="result.result" class="log-text">result</p>
        <pre v-if="result.result" class="log-body">{{ formatOutput(result.result) }}</pre>
        <p v-if="result.stdout" class="log-text">stdout</p>
        <pre v-if="result.stdout" class="log-body">{{ result.stdout }}</pre>
        <p v-if="result.stderr" class="log-text">stderr</p>
        <pre v-if="result.stderr" class="log-body">{{ result.stderr }}</pre>
      </template>
      <n-spin v-else :show="true" />
    </n-modal>

    <!-- 运行历史 -->
    <n-modal v-model:show="showRuns" preset="card" class="sched-runs-modal" :title="`运行历史 · ${runsTask?.name || ''}`">
      <n-empty v-if="!runsLoading && runs.length === 0" description="这个脚本还没有触发记录" />
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
            @click="expandedRunId = expandedRunId === run.id ? '' : run.id"
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
  border-radius: var(--radius-md);
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
.sched-desc {
  margin: 0;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}
.sched-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  flex-wrap: wrap;
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
.code-input :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
  tab-size: 4;
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
.scripts-form-grid {
  display: grid;
  grid-template-columns: 1fr 160px;
  gap: var(--space-3);
}
/* naive-ui 的 modal 走 teleport 渲染到 body，scoped 的 [data-v-*] 属性挂不上去，
   所以这两个类必须写在 :global 里——写进普通 scoped 规则里是静默失效的。 */
:global(.sched-modal) {
  width: min(560px, 94vw);
  max-height: 88vh;
}
:global(.sched-modal .n-card-content) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
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
:global(.sched-runs-modal) {
  width: min(640px, 94vw);
  max-height: 88vh;
}
:global(.sched-runs-modal .n-card-content) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
@media (max-width: 900px) {
  .sched-row {
    flex-direction: column;
    align-items: stretch;
  }
  .sched-actions {
    flex-wrap: wrap;
  }
  .scripts-form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
