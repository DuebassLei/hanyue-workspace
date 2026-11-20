<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  NAlert, NButton, NCard, NInput, NInputNumber, NModal, NPopconfirm,
  NSpin, NSwitch, NTag,
} from 'naive-ui'
import { confirm } from '../../ui/feedback'
import RulePayloadForm from './RulePayloadForm.vue'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  scope: { type: String, default: 'global' },
  readonly: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  // 创作空间「流程」面板 deep-link：进入后定位并短暂高亮该规则分组
  focusType: { type: String, default: '' },
})

const emit = defineEmits(['update:rows'])

// 权限由父级判定后通过 readonly 传入，本组件只按 readonly 收口所有写操作
const locked = computed(() => props.readonly)

/**
 * 分组定义：顺序即界面顺序。
 * kind=single 表示该类型在单个 skill 下只有一条；kind=list 表示可多行。
 * json=true 的行用 JSON 文本编辑 payload，其余走普通输入控件。
 */
const GROUPS = [
  {
    type: 'tone', label: '语气', kind: 'single', json: false, field: 'input',
    primary: true,
    hint: '会填入写作模板的 {tone} 占位符。',
    placeholder: '例如：克制、温和、偶尔自嘲',
  },
  {
    type: 'prompt_template', label: '写作模板', kind: 'single', json: false, field: 'textarea',
    primary: true,
    hint: '保留 {tone} 占位符，生成时会替换为上面的语气。',
    placeholder: '例如：请以{tone}的口吻写一篇关于……',
  },
  {
    type: 'theme', label: '默认排版主题', kind: 'single', json: false, field: 'input',
    hint: '系统预设主题 id，如 ink-green。',
    placeholder: 'ink-green',
  },
  {
    type: 'target_length', label: '目标字数', kind: 'single', json: false, field: 'number',
    primary: true,
    hint: '生成正文的目标字数，超出后会被要求压缩。',
  },
  {
    type: 'style_rule', label: '风格规则', kind: 'list', json: false, field: 'input',
    primary: true, batch: true,
    hint: '每行一条，写作时逐条约束。',
    placeholder: '例如：避免使用“赋能”“闭环”等词',
  },
  {
    type: 'review_rule', label: '审稿规则', kind: 'list', json: true,
    hint: '选择检查对象、检查方式和要求，系统自动生成规则标识。',
  },
  {
    type: 'workflow_step', label: '工作流步骤', kind: 'list', json: true,
    hint: '按执行顺序添加工具，再逐项填写参数，无需编写 JSON。',
  },
  {
    type: 'fact_policy', label: '事实约束', kind: 'single', json: true,
    hint: '明确来源与引语要求，对未核实的信息保留提示。',
  },
  {
    type: 'image_policy', label: '配图策略', kind: 'single', json: true,
    hint: '选择配图比例、数量和统一画面风格。',
  },
  {
    type: 'repair_policy', label: '修复策略', kind: 'single', json: true,
    hint: '设置审核后的修复偏好；当前仅保存配置，尚未接入生成流程。',
  },
  {
    type: 'exemplar', label: '范文示例', kind: 'list', json: true,
    hint: '提供标题和参考片段，帮助模型学习表达方式；最多 3 篇。',
  },
]

// 列表类的 ruleKey 前缀
const RULE_KEY_PREFIX = {
  style_rule: 'style',
  review_rule: 'review',
  workflow_step: 'wf',
  exemplar: 'ex',
}

// 内部行的稳定 key：有 id 用 id，本地新增行用 _localKey（提交前会被剥掉）
function keyOf(row) {
  return row._localKey || `id-${row.id}`
}

let localSeq = 0
const viewRows = ref([])
const jsonText = ref({})
const jsonError = ref({})
let lastEmitted = ''

const enabledCount = computed(() => viewRows.value.filter((row) => !isPlaceholder(row) && row.enabled !== false).length)
const configuredCount = computed(() => viewRows.value.filter((row) => !isPlaceholder(row)).length)
const editorSection = ref('writing')
const EDITOR_SECTIONS = [
  { key: 'writing', label: '写作设置', hint: '先确定口吻、模板和篇幅，再补充风格要求。', types: ['tone', 'prompt_template', 'target_length', 'style_rule'] },
  { key: 'quality', label: '审稿与范文', hint: '配置质量要求与参考范文，让生成结果更稳定。', types: ['review_rule', 'exemplar', 'fact_policy'] },
  { key: 'advanced', label: '排版与执行', hint: '按需调整排版、配图、工作流及自动修复策略。', types: ['theme', 'image_policy', 'workflow_step', 'repair_policy'] },
]
const currentSection = computed(() => EDITOR_SECTIONS.find((section) => section.key === editorSection.value))
const visibleGroups = computed(() => currentSection.value.types.map((type) => GROUPS.find((group) => group.type === type)))
const technicalDetails = ref(false)

function selectSection(section) {
  editorSection.value = section.key
  // 切换分区后直接可编辑，不需要逐项再展开。
  expanded.value = { ...expanded.value, ...Object.fromEntries(section.types.map((type) => [type, true])) }
}

function setSectionExpanded(value) {
  expanded.value = { ...expanded.value, ...Object.fromEntries(currentSection.value.types.map((type) => [type, value])) }
}
const personalEmpty = computed(
  () => props.scope === 'personal' && !props.loading && !configuredCount.value,
)

// ---------------------------------------------------------------- 分组折叠
// 常用四项默认展开，其余收起；展开状态按规则类型记，切换 Skill 时保留，
// 因为分组集合对所有 Skill 都一样，记住「我上次开了哪些」比每次重置更省事。
const expanded = ref({})

function groupExpanded(group) {
  const stored = expanded.value[group.type]
  return stored === undefined ? Boolean(group.primary) : stored
}

function toggleGroup(group) {
  expanded.value = { ...expanded.value, [group.type]: !groupExpanded(group) }
}

/** 收起时标题上的状态：没有真正配置过的显示「未配置」，否则给启用条数。 */
function groupStatus(group) {
  const rows = groupRows(group.type).filter((row) => !isPlaceholder(row))
  if (!rows.length) return { text: '未配置', empty: true }
  const enabled = rows.filter((row) => row.enabled !== false).length
  return { text: `已启用 ${enabled} 条`, empty: false }
}

// ---------------------------------------------------------------- deep-link 定位
// 流程面板「配置规则」跳转进来时：切到该类型所在分区、展开分组、滚动并高亮一下。
// 只在数据就绪后执行一次，之后的保存/刷新不再打扰用户位置。
const focusFlash = ref('')
let focusApplied = false
let focusTimer = null

function applyFocus(type) {
  const section = EDITOR_SECTIONS.find((item) => item.types.includes(type))
  if (!section) return
  if (editorSection.value !== section.key) {
    selectSection(section)
  } else {
    expanded.value = { ...expanded.value, [type]: true }
  }
  nextTick(() => {
    const el = document.getElementById(`rules-group-${type}`)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    focusFlash.value = type
    clearTimeout(focusTimer)
    focusTimer = setTimeout(() => {
      focusFlash.value = ''
    }, 2400)
  })
}

function maybeApplyFocus() {
  if (focusApplied || !props.focusType || props.loading) return
  focusApplied = true
  applyFocus(props.focusType)
}

onMounted(maybeApplyFocus)
watch(() => props.loading, maybeApplyFocus)
onBeforeUnmount(() => clearTimeout(focusTimer))

// ---------------------------------------------------------------- 单行类型预填
// 预填行只是界面占位：带 _placeholder 标记，保存时由 isRowEmpty 判定为空的会被丢弃，
// 不入库——「没填」必须仍然等于「未配置、走继承」，不能因为预填就落成空规则。

function isPlaceholder(row) {
  return Boolean(row._placeholder)
}

/** 一行是否还停留在「什么都没填」的状态，按类型各自判断。 */
function isRowEmpty(row) {
  const payload = row.payload || {}
  const group = GROUPS.find((item) => item.type === row.ruleType)
  if (!group) return false
  if (group.field === 'input' || group.field === 'textarea') return !String(payload.text || '').trim()
  if (group.field === 'number') return payload.value === null || payload.value === undefined || payload.value === ''
  if (row.ruleType === 'review_rule') {
    return !payload.kind && !payload.value && !payload.field && !payload.message
  }
  if (row.ruleType === 'workflow_step') return !payload.tool
  if (row.ruleType === 'exemplar') return !payload.title && !payload.excerpt
  if (row.ruleType === 'repair_policy') {
    // 预填行带着出厂默认值，用户没改过任何字段时仍算未配置
    if (isPlaceholder(row) && !row._touched) return true
    return payload.retries === undefined && payload.requireContract === undefined
  }
  // fact_policy / image_policy：空对象即未配置
  return !Object.keys(payload).length
}

/**
 * 保证每个单行类型在界面上都有一行可编辑。
 * 只在「外部数据换了一批」时调用，不能放进 commit 的回路里，否则占位行会来回增删。
 */
function withPlaceholders(rows) {
  const next = [...rows]
  for (const group of GROUPS) {
    if (group.kind !== 'single') continue
    if (next.some((row) => row.ruleType === group.type)) continue
    const payload = defaultPayload(group.type, 'default')
    const row = {
      id: '',
      ruleType: group.type,
      ruleKey: 'default',
      payload,
      sortOrder: 0,
      enabled: true,
      source: 'manual',
      _localKey: `local-${++localSeq}`,
      _placeholder: true,
    }
    // 插到该分组应处的位置：紧跟前一个已存在分组的最后一行
    const order = GROUPS.findIndex((item) => item.type === group.type)
    let insertAt = next.length
    for (let i = 0; i < next.length; i += 1) {
      const at = GROUPS.findIndex((item) => item.type === next[i].ruleType)
      if (at > order) {
        insertAt = i
        break
      }
    }
    next.splice(insertAt, 0, row)
  }
  return next
}

// 签名字用来区分「父级换了一批数据」和「父级回填我刚发出的这版数据」
function signature(rows) {
  return JSON.stringify(rows)
}

function stripRow(row) {
  const next = { ...row }
  delete next._localKey
  delete next._placeholder
  delete next._touched
  return next
}

function cloneRow(row) {
  return {
    ...row,
    payload: row.payload && typeof row.payload === 'object' ? { ...row.payload } : {},
    _localKey: row._localKey || (row.id ? '' : `local-${++localSeq}`),
  }
}

/** 外部数据变化（切换 skill / scope、父级重新加载）时重建内部状态与 JSON 缓冲 */
function syncFromProps(rows) {
  const incoming = Array.isArray(rows) ? rows : []
  // 按分组固定顺序归档，组内保持接口给出的先后，保证 sortOrder 与界面一致
  const groupOrder = new Map(GROUPS.map((group, index) => [group.type, index]))
  viewRows.value = withPlaceholders(
    incoming
      .map((row, index) => ({ row: cloneRow(row), index }))
      .sort((a, b) => {
        const orderA = groupOrder.get(a.row.ruleType) ?? GROUPS.length
        const orderB = groupOrder.get(b.row.ruleType) ?? GROUPS.length
        return orderA - orderB || a.index - b.index
      })
      .map((item) => item.row),
  )
  // 记录「当前这份数据」的签名，父级把它原样回填时就不重建缓冲。
  // 占位行不入库，签名只覆盖真正会提交的行，否则父级回填永远对不上、每次按键都重建缓冲。
  lastEmitted = signature(viewRows.value.filter((row) => !isPlaceholder(row)).map(stripRow))
  const nextText = {}
  const nextError = {}
  for (const row of viewRows.value) {
    const key = keyOf(row)
    if (isJsonType(row.ruleType)) nextText[key] = prettyJson(row.payload)
    // 缓冲重建后旧的解析错误不再成立
    nextError[key] = ''
  }
  jsonText.value = nextText
  jsonError.value = nextError
}

watch(
  () => props.rows,
  (rows) => {
    const incoming = Array.isArray(rows) ? rows : []
    // 父级只是回填我们刚 emit 的数组：保留本地缓冲与临时 key，避免打断输入
    if (signature(incoming.map(stripRow)) === lastEmitted) return
    syncFromProps(rows)
  },
  { immediate: true, deep: true },
)

function isJsonType(type) {
  const group = GROUPS.find((item) => item.type === type)
  return Boolean(group && group.json)
}

function prettyJson(payload) {
  try {
    return JSON.stringify(payload && typeof payload === 'object' ? payload : {}, null, 2)
  } catch {
    return '{}'
  }
}

/** 统一出口：按数组顺序重排 sortOrder，再把剥离临时字段的完整数组 emit 给父级。
 *  仍为空的预填占位行在这里过滤，不进入提交数组（没填 = 未配置）。
 *  用户一旦写过内容，行就转正（见 setText / setLength），此后不再被过滤，
 *  这样「填了又删」不会让提交数组抖动、父级回填对不上签名而重建输入框。 */
function commit(next) {
  const ordered = next.map((row, index) => ({ ...row, sortOrder: index }))
  viewRows.value = ordered
  const payload = ordered
    .filter((row) => !(row._placeholder && isRowEmpty(row)))
    .map(stripRow)
  lastEmitted = signature(payload)
  emit('update:rows', payload)
}

/** 按行 key 打补丁；不原地修改 props。
 *  _touched 标记「用户确实改过这行」，让带着出厂默认值的预填行能在首次编辑后转正。 */
function patchRow(key, patch) {
  const next = viewRows.value.map((row) => {
    if (keyOf(row) !== key) return row
    const merged = { ...row, ...patch, _touched: true }
    if (patch.payload) merged.payload = { ...patch.payload }
    return merged
  })
  commit(next)
}

function groupRows(type) {
  return viewRows.value.filter((row) => row.ruleType === type)
}

function textOf(row) {
  return row.payload?.text ?? ''
}

function setText(row, value) {
  // 占位行首次写入内容即转正：此后即便清空文本也保留该行，
  // 否则「填了又删」会让行在提交数组里时有时无，父级回填对不上签名、输入被重建打断。
  // 真正的「空行不入库」统一在父级保存时过滤。
  const extra = isPlaceholder(row) ? { _placeholder: false } : {}
  patchRow(keyOf(row), { ...extra, payload: { ...(row.payload || {}), text: value } })
}

function setPayload(row, payload) {
  const key = keyOf(row)
  jsonText.value = { ...jsonText.value, [key]: prettyJson(payload) }
  jsonError.value = { ...jsonError.value, [key]: '' }
  patchRow(key, { _placeholder: false, payload })
}

function setPayloadField(row, field, value) {
  const payload = { ...(row.payload || {}), [field]: value }
  const key = keyOf(row)
  jsonText.value = { ...jsonText.value, [key]: prettyJson(payload) }
  patchRow(key, { _placeholder: false, payload })
}

function setLength(row, value) {
  const extra = isPlaceholder(row) && value !== null ? { _placeholder: false } : {}
  patchRow(keyOf(row), { ...extra, payload: { ...(row.payload || {}), value: value ?? null } })
}

function setEnabled(row, value) {
  patchRow(keyOf(row), { enabled: value })
}

function moveRow(row, delta) {
  const key = keyOf(row)
  const siblings = groupRows(row.ruleType)
  const pos = siblings.findIndex((item) => keyOf(item) === key)
  const target = pos + delta
  if (pos < 0 || target < 0 || target >= siblings.length) return
  const next = [...viewRows.value]
  const from = next.findIndex((item) => keyOf(item) === key)
  const to = next.findIndex((item) => keyOf(item) === keyOf(siblings[target]))
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  commit(next)
}

/** 列表类新行的 ruleKey 用「前缀-序号」生成，跳过已占用的编号 */
function nextRuleKey(prefix) {
  const used = new Set(viewRows.value.map((row) => row.ruleKey))
  let index = 1
  while (used.has(`${prefix}-${index}`)) index += 1
  return `${prefix}-${index}`
}

function defaultPayload(type, ruleKey) {
  switch (type) {
    case 'tone':
    case 'prompt_template':
    case 'theme':
    case 'style_rule':
      return { text: '' }
    case 'target_length':
      // null 而非 0：0 会被后端当成合法字数，而「没填」必须等于「未配置」
      return { value: null }
    case 'repair_policy':
    case 'fact_policy':
    case 'image_policy':
      // 空对象起步。这三个类型的「空」就是没有任何键，预填行因此不会被当成已配置。
      return {}
    case 'review_rule':
      return { id: ruleKey, kind: 'check', value: '', field: '', message: '' }
    case 'workflow_step':
      return { tool: '', args: {} }
    case 'exemplar':
      return { title: '', excerpt: '' }
    default:
      // fact_policy / image_policy 等按空对象起步，由用户按契约填写
      return {}
  }
}

function contextFromRows() {
  const first = viewRows.value[0] || props.rows[0] || {}
  return {
    skillKey: first.skillKey || '',
    ownerUserId: first.ownerUserId ?? null,
  }
}

function addRow(group) {
  const type = group.type
  const ruleKey = group.kind === 'single' ? 'default' : nextRuleKey(RULE_KEY_PREFIX[type] || type)
  const payload = defaultPayload(type, ruleKey)
  const row = {
    id: '',
    ...contextFromRows(),
    ruleType: type,
    ruleKey,
    payload,
    sortOrder: groupRows(type).length,
    enabled: true,
    source: 'manual',
    _localKey: `local-${++localSeq}`,
  }
  insertRows(type, [row])
}

/** 把若干新行插到同分组最后一行之后，保持分组内连续，并补上 JSON 缓冲。 */
function insertRows(type, rows) {
  const next = [...viewRows.value]
  let insertAt = next.length
  for (let i = next.length - 1; i >= 0; i -= 1) {
    if (next[i].ruleType === type) {
      insertAt = i + 1
      break
    }
  }
  next.splice(insertAt, 0, ...rows)
  if (isJsonType(type)) {
    const nextText = { ...jsonText.value }
    const nextError = { ...jsonError.value }
    for (const row of rows) {
      nextText[keyOf(row)] = prettyJson(row.payload)
      nextError[keyOf(row)] = ''
    }
    jsonText.value = nextText
    jsonError.value = nextError
  }
  commit(next)
}

// ---------------------------------------------------------------- 风格规则批量添加
// 只对纯文本的列表类型开放：一行一条贴进来，追加到现有规则后面，不覆盖。
// 空行、与已有规则文本完全相同的行直接跳过，避免批量粘贴最常见的翻车。

const batchGroup = ref(null)
const batchText = ref('')

const batchPreview = computed(() => {
  const group = batchGroup.value
  if (!group) return { accepted: [], blanks: 0, duplicates: 0 }
  const existing = new Set(
    groupRows(group.type).map((row) => String(textOf(row)).trim()).filter(Boolean),
  )
  const accepted = []
  let blanks = 0
  let duplicates = 0
  for (const line of batchText.value.split('\n')) {
    const text = line.trim()
    if (!text) {
      blanks += 1
      continue
    }
    if (existing.has(text) || accepted.includes(text)) {
      duplicates += 1
      continue
    }
    accepted.push(text)
  }
  return { accepted, blanks, duplicates }
})

const batchSummary = computed(() => {
  const { accepted, blanks, duplicates } = batchPreview.value
  const parts = [`将添加 ${accepted.length} 条`]
  if (blanks) parts.push(`跳过 ${blanks} 条空行`)
  if (duplicates) parts.push(`跳过 ${duplicates} 条重复`)
  return parts.join('，')
})

function openBatch(group) {
  batchGroup.value = group
  batchText.value = ''
}

function closeBatch() {
  batchGroup.value = null
  batchText.value = ''
}

function confirmBatch() {
  const group = batchGroup.value
  const { accepted } = batchPreview.value
  if (!group || !accepted.length) {
    closeBatch()
    return
  }
  const rows = accepted.map((text) => ({
    id: '',
    ...contextFromRows(),
    ruleType: group.type,
    ruleKey: '',
    payload: { text },
    sortOrder: 0,
    enabled: true,
    source: 'manual',
    _localKey: `local-${++localSeq}`,
  }))
  // 逐条取号会互相撞车：统一在这里按序号重排，跳过现有 key
  const used = new Set(viewRows.value.map((row) => row.ruleKey))
  const prefix = RULE_KEY_PREFIX[group.type] || group.type
  let index = 1
  for (const row of rows) {
    while (used.has(`${prefix}-${index}`)) index += 1
    row.ruleKey = `${prefix}-${index}`
    used.add(row.ruleKey)
    index += 1
  }
  insertRows(group.type, rows)
  closeBatch()
}

function removeRow(row) {
  const key = keyOf(row)
  const nextText = { ...jsonText.value }
  const nextError = { ...jsonError.value }
  delete nextText[key]
  delete nextError[key]
  jsonText.value = nextText
  jsonError.value = nextError
  commit(viewRows.value.filter((item) => keyOf(item) !== key))
}

/** 单行类删除需额外说明：删掉不等于停用，下次启动会从出厂恢复。
 *  预填的占位行从未入库，删它只是清掉界面占位，不弹这个警告。 */
async function removeSingle(row) {
  if (!isPlaceholder(row)) {
    const ok = await confirm(
      '删除后下次启动会从出厂恢复，若要停用请用开关。',
      '删除这条规则',
    )
    if (!ok) return
  }
  removeRow(row)
}

/** 列表类行标识变更：review_rule 的 payload.id 必须跟着走 */
function onRuleKeyInput(row, value) {
  const ruleKey = String(value || '')
  const patch = { ruleKey }
  if (row.ruleType === 'review_rule') {
    patch.payload = { ...(row.payload || {}), id: ruleKey }
    // 同步 JSON 缓冲里的 id，避免文本与实际 payload 不一致（文本非法时保留原样）
    const key = keyOf(row)
    const text = jsonText.value[key]
    if (text !== undefined) {
      try {
        const parsed = JSON.parse(text)
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          parsed.id = ruleKey
          jsonText.value = { ...jsonText.value, [key]: JSON.stringify(parsed, null, 2) }
        }
      } catch {
        // 用户正在输入的 JSON 尚未闭合，保留原始文本
      }
    }
  }
  patchRow(keyOf(row), patch)
}

/** JSON 缓冲：解析成功才 emit，失败只标红，不发出非法 payload */
function onJsonInput(row, value) {
  const key = keyOf(row)
  const text = String(value ?? '')
  jsonText.value = { ...jsonText.value, [key]: text }
  try {
    const parsed = JSON.parse(text)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('需要一个 JSON 对象')
    }
    // 契约要求：审稿规则的 payload.id 与行标识保持一致
    if (row.ruleType === 'review_rule') parsed.id = row.ruleKey
    jsonError.value = { ...jsonError.value, [key]: '' }
    // 占位行（策略类以 {} 预填）首次解析出内容即转正，理由同 setText
    const extra = isPlaceholder(row) ? { _placeholder: false } : {}
    patchRow(key, { ...extra, payload: parsed })
  } catch (err) {
    jsonError.value = { ...jsonError.value, [key]: `JSON 解析失败：${err.message}` }
  }
}

function jsonValue(row) {
  const key = keyOf(row)
  return jsonText.value[key] ?? prettyJson(row.payload)
}

function jsonStatus(row) {
  return jsonError.value[keyOf(row)] ? 'error' : undefined
}
</script>

<template>
  <div class="rules-panel">
    <div class="rules-summary">
      <span>已配置 {{ configuredCount }} 条规则（启用 {{ enabledCount }} 条）</span>
      <span class="rules-scope">{{ scope === 'personal' ? '个人副本' : '全局规则' }}</span>
      <span v-if="readonly" class="rules-scope">只读</span>
      <n-button size="tiny" quaternary @click="technicalDetails = !technicalDetails">
        {{ technicalDetails ? '隐藏技术标识' : '显示技术标识' }}
      </n-button>
    </div>

    <nav class="editor-sections" aria-label="Skill 设置分区">
      <button
        v-for="section in EDITOR_SECTIONS"
        :key="section.key"
        type="button"
        :class="{ active: editorSection === section.key }"
        :aria-pressed="editorSection === section.key"
        @click="selectSection(section)"
      >{{ section.label }}</button>
    </nav>
    <div class="editor-section-head">
      <span class="rules-hint">{{ currentSection.hint }}</span>
      <div class="row">
        <n-button size="tiny" quaternary @click="setSectionExpanded(true)">展开全部</n-button>
        <n-button size="tiny" quaternary @click="setSectionExpanded(false)">收起全部</n-button>
      </div>
    </div>

    <n-alert v-if="personalEmpty" type="info" :bordered="false" class="rules-alert">
      当前使用全局规则；保存空内容会清空个人副本。
    </n-alert>

    <n-spin :show="loading">
      <div v-if="loading" class="rules-skeleton">
        <div v-for="n in 3" :key="n" class="rules-skeleton-card" />
      </div>

      <div v-else class="rules-groups">
        <n-card
          v-for="group in visibleGroups"
          :id="`rules-group-${group.type}`"
          :key="group.type"
          size="small"
          class="rules-card"
          :class="{ 'is-focus': focusFlash === group.type }"
        >
          <div class="rules-card-head">
            <button
              type="button"
              class="rules-card-title"
              :aria-expanded="groupExpanded(group)"
              @click="toggleGroup(group)"
            >
              <span class="rules-caret" :class="{ open: groupExpanded(group) }">▸</span>
              <span>{{ group.label }}</span>
              <n-tag v-if="technicalDetails" size="tiny" :bordered="false">{{ group.type }}</n-tag>
              <span class="rules-status" :class="{ 'is-empty': groupStatus(group).empty }">
                {{ groupStatus(group).text }}
              </span>
            </button>
            <n-button
              v-if="!locked && group.batch && groupExpanded(group)"
              size="tiny"
              secondary
              @click="openBatch(group)"
            >批量添加</n-button>
            <n-button
              v-if="!locked && group.kind === 'list' && groupExpanded(group)"
              size="tiny"
              secondary
              @click="addRow(group)"
            >添加一行</n-button>
          </div>

          <template v-if="groupExpanded(group)">
            <p class="rules-hint">{{ group.hint }}</p>

            <!-- 单行类为空：说明继承关系，再给出添加入口 -->
            <div v-if="group.kind === 'single' && !groupRows(group.type).length" class="rules-empty">
              <span>使用出厂默认（继承全局 / 无此项）</span>
              <n-button v-if="!locked" size="tiny" type="primary" @click="addRow(group)">添加</n-button>
            </div>

            <div v-if="group.kind === 'list' && !groupRows(group.type).length" class="rules-empty">
              暂无{{ group.label }}，点击右上方添加；不配置时沿用默认行为。
            </div>
            <div v-for="(row, idx) in groupRows(group.type)" :key="keyOf(row)" class="rules-item">
            <div class="rules-item-head">
              <n-input
                v-if="technicalDetails && group.kind === 'list'"
                size="tiny"
                :value="row.ruleKey"
                :readonly="locked"
                placeholder="行标识"
                @update:value="(value) => onRuleKeyInput(row, value)"
              />
              <span v-else class="rules-key-static">{{ group.kind === 'list' ? `第 ${idx + 1} 条` : '内容设置' }}</span>
              <div class="rules-spacer" />
              <span v-if="!locked" class="rules-key-static">{{ row.enabled !== false ? '启用' : '停用' }}</span>
              <n-switch
                v-if="!locked"
                :aria-label="`${group.label}第 ${idx + 1} 条启用状态`"
                size="small"
                :value="row.enabled !== false"
                @update:value="(value) => setEnabled(row, value)"
              />
              <n-tag v-else-if="row.enabled === false" size="tiny" :bordered="false">已停用</n-tag>
              <template v-if="!locked">
                <n-button
                  v-if="group.kind === 'list'"
                  quaternary
                  size="tiny"
                  :disabled="idx === 0"
                  @click="moveRow(row, -1)"
                >上移</n-button>
                <n-button
                  v-if="group.kind === 'list'"
                  quaternary
                  size="tiny"
                  :disabled="idx === groupRows(group.type).length - 1"
                  @click="moveRow(row, 1)"
                >下移</n-button>
                <n-popconfirm
                  v-if="group.kind === 'list'"
                  @positive-click="removeRow(row)"
                >
                  <template #trigger>
                    <n-button quaternary size="tiny" type="error">删除</n-button>
                  </template>
                  删除这一条规则？{{ props.scope === 'global' ? '（全局行删掉后，下次启动会被出厂数据补回来）' : '' }}
                </n-popconfirm>
                <n-button
                  v-else
                  quaternary
                  size="tiny"
                  type="error"
                  @click="removeSingle(row)"
                >删除</n-button>
              </template>
            </div>

            <div class="rules-item-body">
              <n-input
                v-if="group.field === 'input'"
                :value="textOf(row)"
                :readonly="locked"
                :placeholder="group.placeholder"
                @update:value="(value) => setText(row, value)"
              />
              <n-input
                v-else-if="group.field === 'textarea'"
                type="textarea"
                :value="textOf(row)"
                :readonly="locked"
                :placeholder="group.placeholder"
                :autosize="{ minRows: 4, maxRows: 12 }"
                :input-props="{ spellcheck: false }"
                @update:value="(value) => setText(row, value)"
              />
              <n-input-number
                v-else-if="group.field === 'number'"
                :value="row.payload?.value ?? null"
                :disabled="locked"
                :min="100"
                :max="20000"
                :step="100"
                placeholder="未配置，继承默认"
                style="width: 200px"
                @update:value="(value) => setLength(row, value)"
              />
              <div v-else-if="row.ruleType === 'exemplar'" class="structured-fields">
                <label>范文标题</label>
                <n-input :value="row.payload?.title || ''" :readonly="locked" placeholder="便于识别这篇范文的标题" @update:value="(value) => setPayloadField(row, 'title', value)" />
                <label>参考片段</label>
                <n-input type="textarea" :value="row.payload?.excerpt || ''" :readonly="locked" :autosize="{ minRows: 4, maxRows: 14 }" placeholder="粘贴值得学习的表达、结构或叙述片段" @update:value="(value) => setPayloadField(row, 'excerpt', value)" />
              </div>
              <RulePayloadForm
                v-else-if="['review_rule', 'fact_policy', 'image_policy', 'repair_policy', 'workflow_step'].includes(row.ruleType)"
                :type="row.ruleType"
                :payload="row.payload || {}"
                :rule-key="row.ruleKey"
                :readonly="locked"
                @update:payload="(payload) => setPayload(row, payload)"
              />
              <template v-else>
                <n-input
                  type="textarea"
                  class="rules-json"
                  :value="jsonValue(row)"
                  :readonly="locked"
                  :status="jsonStatus(row)"
                  :autosize="{ minRows: 3, maxRows: 16 }"
                  :input-props="{ spellcheck: false }"
                  @update:value="(value) => onJsonInput(row, value)"
                />
                <p v-if="jsonError[keyOf(row)]" class="rules-error">{{ jsonError[keyOf(row)] }}</p>
              </template>
            </div>
          </div>
          </template>
        </n-card>
      </div>
    </n-spin>

    <n-modal
      :show="batchGroup !== null"
      preset="card"
      :title="`批量添加${batchGroup ? batchGroup.label : ''}`"
      style="max-width: 520px"
      @update:show="(show) => { if (!show) closeBatch() }"
    >
      <p class="rules-hint" style="margin-top: 0">
        一行一条，确认后追加到现有规则后面，不会覆盖已有的。空行和与已有规则完全相同的行会自动跳过。
      </p>
      <n-input
        v-model:value="batchText"
        type="textarea"
        placeholder="每行一条规则"
        :autosize="{ minRows: 6, maxRows: 16 }"
      />
      <p class="rules-hint">{{ batchSummary }}</p>
      <template #footer>
        <div class="rules-batch-foot">
          <n-button quaternary @click="closeBatch">取消</n-button>
          <n-button type="primary" :disabled="!batchPreview.accepted.length" @click="confirmBatch">
            添加 {{ batchPreview.accepted.length }} 条
          </n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.editor-sections {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-1);
  background: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.editor-sections button {
  flex: 1;
  padding: var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-sm, 6px);
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  cursor: pointer;
}
.editor-sections button.active {
  color: var(--color-primary);
  border-color: var(--color-primary);
  font-weight: 600;
}
.editor-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.structured-fields {
  display: grid;
  gap: var(--space-2);
}
.structured-fields label {
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}
.rules-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.rules-summary {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}
.rules-scope {
  padding: 0 var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--font-xs);
}
.rules-groups {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.rules-card.is-focus {
  border-color: var(--color-primary, #6a5cff);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-primary, #6a5cff) 22%, transparent);
  transition: box-shadow 250ms ease, border-color 250ms ease;
}
.rules-card-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.rules-card-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 1;
  min-width: 0;
  padding: 0;
  border: none;
  background: none;
  text-align: left;
  font: inherit;
  font-weight: 600;
  color: inherit;
  cursor: pointer;
}
.rules-caret {
  display: inline-block;
  font-size: 10px;
  color: var(--color-text-secondary);
  transition: transform 0.15s ease;
}
.rules-caret.open {
  transform: rotate(90deg);
}
.rules-status {
  font-size: var(--font-xs);
  font-weight: 400;
  color: var(--color-text-secondary);
}
.rules-status.is-empty {
  color: var(--color-text-tertiary, var(--color-text-secondary));
  opacity: 0.7;
}
.rules-batch-foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
.rules-hint {
  margin: var(--space-1) 0 var(--space-2);
  font-size: var(--font-xs);
  line-height: 1.6;
  color: var(--color-text-secondary);
}
.rules-empty {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
  padding: var(--space-3);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-sm, 6px);
  background: var(--color-surface-raised);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}
.rules-item {
  margin-top: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-primary);
  border-radius: var(--radius-sm, 6px);
  background: var(--color-surface-raised);
}
.rules-item-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--color-border);
}
.rules-spacer {
  flex: 1;
}
.rules-key {
  width: 140px;
  flex: 0 0 auto;
}
.rules-key-static {
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}
.rules-item-body {
  margin-top: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm, 6px);
  background: var(--color-surface, var(--color-bg));
}
.rules-json :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace;
  font-size: var(--font-sm);
  line-height: 1.6;
}
.rules-error {
  margin: var(--space-1) 0 0;
  font-size: var(--font-xs);
  color: var(--color-danger);
}
.rules-alert {
  margin-bottom: 0;
}
.rules-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.rules-skeleton-card {
  height: 96px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
}
@media (max-width: 767px) {
  .rules-key {
    width: 96px;
  }
}
</style>
