<script setup>
/**
 * ModelsView — 模型管理：模型连接的增删改、连通性测试，以及每个连接的模型代号与能力登记。
 *
 * 从设置页的「模型与 API」分栏独立出来：这里是会被反复打开的地方（换模型、试连通性），
 * 不该埋在一堆偏好设置里。数据和顶栏状态条同源（/models/connections、/models/status），
 * 改完连接广播 hanyue:model-status-changed，顶栏立刻重算，不用等 60 秒轮询。
 *
 * 文本模型和生图模型是两条互不相干的链路（成文 / 出图），列表按类型分组，
 * 新增也走两个独立入口，弹窗里同时只出现一种类型的字段与提示，不把两种配置摆一起让人挑。
 *
 * 顶部「模型」区块一条链路一行：左边是当前生效的模型，右边就地选默认
 * （/preferences 按用户存服务端）。文本默认排候选链首位仍可降级，
 * 生图默认优先于 IMAGE_PROVIDER_* 环境变量，
 * 两条偏好各存一份，切换后顶栏与各创作页的下拉立即跟着变。
 */
import { computed, h, nextTick, onMounted, reactive, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCheckbox,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSelect,
  NSpace,
  NTag,
} from 'naive-ui'
import { api } from '../api/client'
import { toast, confirm } from '../ui/feedback'
import { usePermission } from '../composables/usePermission'
import { useModelSelectionStore } from '../stores/modelSelection'
import { useSessionStore } from '../stores/session'
import StatusTag from '../components/StatusTag.vue'

// 页面本身只要求 models.view：能确认「现在挂的是哪个模型、通不通」。
// 换 Key、删连接、登记模型代号是 models.manage，两者分开——
// 删错一个连接全站创作停摆，看和改不该是同一个权限。
const { can, isSuperadmin } = usePermission()
const canManage = computed(() => can('models.manage'))
const session = useSessionStore()
const platformDefaults = reactive({ text: null, image: null })
const platformSaving = ref('')

function isOwnConnection(conn) {
  return Boolean(session.user?.id && conn.ownerUserId === session.user.id)
}

function confirmOtherConnection(conn, action) {
  if (!conn || !isSuperadmin.value || isOwnConnection(conn)) return true
  return confirm(`这是 ${conn.ownerLabel || '其他用户'} 的连接。${action}可能影响对方的使用或产生费用，确定继续？`, '跨用户操作确认')
}

function crossUserQuery(conn) {
  return conn && isSuperadmin.value && !isOwnConnection(conn) ? '?confirmCrossUser=true' : ''
}

function editingConnection() {
  return connections.value.find((conn) => conn.id === editingId.value)
}

const connections = ref([])
const statusRows = ref([])
const error = ref('')
const statusLoading = ref(false)

/**
 * 全局默认模型：服务端 /preferences 持久化，跨设备生效。
 * 文本与生图是两条独立链路，各存各的，互不覆盖；不选（空值）表示按自动顺序降级。
 */
const defaults = reactive({
  textId: null,
  textLabel: null,
  textIgnored: null,
  imageId: null,
  imageLabel: null,
  imageIgnored: null,
})
// 正在保存的是哪一路，用来只禁用对应的下拉，不把两个一起锁住
const savingKind = ref('')
const modelSelection = useModelSelectionStore()

const showConnection = ref(false)
const editingId = ref('')
// 编辑时密钥留空表示不改动；只有显式勾选才清空
const clearKey = ref(false)
// 从供应商拉回来的模型列表（只用于下拉填充，不落库）
const remoteModels = ref([])
const modelsLoading = ref(false)
const modelsHint = ref('')
// 弹窗里的「测试配置」结果：真实请求的结论，不预判成功
const probe = reactive({ running: false, state: 'idle', message: '', detail: '' })
const probeAlertRef = ref(null)
const connForm = reactive({
  displayName: '',
  providerType: 'openai_compatible',
  baseUrl: '',
  apiKey: '',
  modelCode: '',
  modelType: 'text',
  capabilities: 'text.generate,text.stream',
  imageSizes: '',
})

// 类型决定能力登记：文本模型用于 AI 成文，生图模型用于素材页 AI 生图
const MODEL_TYPES = [
  {
    value: 'text',
    label: '文本模型',
    short: '文本',
    desc: '用于 AI 成文、内容优化、卡片摘要；能力登记为 text.generate、text.stream。',
    caps: 'text.generate,text.stream',
    namePlaceholder: '例如：主力文本模型',
    codePlaceholder: '例如：gpt-4o-mini',
    empty: '还没有文本模型连接：AI 成文会明确报错（provider_unavailable），不会产生占位内容。',
  },
  {
    value: 'image',
    label: '生图模型',
    short: '生图',
    desc: '用于素材页 AI 生图、封面配图；能力登记为 image.generate。',
    caps: 'image.generate',
    namePlaceholder: '例如：主力生图模型',
    codePlaceholder: '例如：gemini-2.5-flash-image',
    empty: '还没有生图模型连接：AI 生图会明确报错，不会返回占位图。',
  },
]

// 与顶栏状态条同一套口径：绿色只代表链路齐且连接测过
const STATE_HINT = {
  ok: '可用',
  blocked: '不可用',
  unchecked: '待确认',
  none: '未配置',
}
const KIND_LABEL = {
  text: '文本模型',
  image: '生图模型',
}
const KIND_SHORT = {
  text: '文本',
  image: '生图',
}
// 探测结论的中文名：详情行里不直接甩 model_rejected 这种机器码
const PROBE_STATUS_LABEL = {
  connected: '连接正常',
  missing_credential: '缺少密钥',
  unauthorized: '凭据被拒',
  adapter_unavailable: '适配器未接入',
  invalid_base_url: '地址不可用',
  provider_unavailable: '供应商不可用',
  provider_unreachable: '无法连接供应商',
  endpoint_not_supported: '接口不支持',
  model_not_found: '模型不存在',
  model_rejected: '模型请求被拒',
}

const statusCards = computed(() =>
  statusRows.value.map((row) => ({
    ...row,
    kindLabel: KIND_LABEL[row.kind] || row.kind,
    hint: STATE_HINT[row.state] || row.state,
    name: row.displayName || row.modelCode || '未配置',
  })),
)

/** 某条链路可选的默认模型：启用且能力匹配；带连接名，避免不同连接里的同名代号分不清 */
function modelChoices(kind) {
  const rows = []
  for (const conn of connections.value.filter(isOwnConnection)) {
    for (const model of conn.models || []) {
      if (!model.enabled) continue
      const capable =
        kind === 'image'
          ? (model.capabilities || []).includes('image.generate')
          : model.isTextModel === true
      if (!capable) continue
      // 名字优先用显示名：改了显示名之后下拉还挂着底层代号，看着像没改成功
      rows.push({
        value: model.id,
        label: `${conn.displayName} · ${model.displayName || model.modelCode}`,
      })
    }
  }
  return rows
}

/** 失效的默认模型也要能在下拉里显示出来，否则界面像「没设置」而服务端其实还存着 */
function withCurrentDefault(rows, currentId, label) {
  if (!currentId || rows.some((row) => row.value === currentId)) return rows
  return [...rows, { value: currentId, label: `${label || currentId}（已失效）` }]
}

const textDefaultOptions = computed(() => [
  { value: '', label: '自动：按连接顺序选第一个可用文本模型（失败自动降级）' },
  ...withCurrentDefault(modelChoices('text'), defaults.textId, defaults.textLabel),
])

const imageDefaultOptions = computed(() => [
  { value: '', label: '自动：默认模型 → 环境变量 → 第一个可用生图模型' },
  ...withCurrentDefault(modelChoices('image'), defaults.imageId, defaults.imageLabel),
])

/**
 * 「现在用的是哪个」和「不指定时先用哪个」本来拆成两块，同一批模型名、同一套状态
 * 各说了一遍，还容易对不上（默认那块写 A、生效那块写 B）。
 * 合成一块：一条模型链路一行，左边答「现在生效」，右边直接选「默认」。
 */
const mergedModelRows = computed(() =>
  ['text', 'image'].map((kind) => {
    const card = statusCards.value.find((row) => row.kind === kind)
    const ignored = kind === 'text' ? defaults.textIgnored : defaults.imageIgnored
    const label = kind === 'text' ? defaults.textLabel : defaults.imageLabel
    return {
      kind,
      kindLabel: KIND_LABEL[kind],
      state: card?.state || 'none',
      hint: card?.hint || STATE_HINT.none,
      name: card?.name || '未配置',
      modelCode: card?.modelCode || '',
      connectionName: card?.connectionName || '',
      message: card?.message || '还没接入这类模型，先在下面添加连接并测试。',
      pickValue: (kind === 'text' ? defaults.textId : defaults.imageId) || '',
      pickOptions: kind === 'text' ? textDefaultOptions.value : imageDefaultOptions.value,
      pickLabel: `默认${KIND_LABEL[kind]}`,
      pickHint: ignored
        ? `当前默认已失效（${ignored.message}），会按自动顺序降级；修好连接后自动恢复。`
        : label
          ? (kind === 'text' ? '失败仍会按候选链自动降级。' : '显式选择优先于环境变量供应商。')
          : (kind === 'text'
            ? '未设置：按连接顺序取第一个可用文本模型。'
            : '未设置：环境变量供应商优先。'),
    }
  }),
)

function applyDefaults(pref) {
  defaults.textId = pref.defaultTextModelId || null
  defaults.textLabel = pref.defaultTextModelLabel || null
  defaults.textIgnored = pref.defaultTextModelIgnored || null
  defaults.imageId = pref.defaultImageModelId || null
  defaults.imageLabel = pref.defaultImageModelLabel || null
  defaults.imageIgnored = pref.defaultImageModelIgnored || null
}

async function loadPlatformDefaults() {
  if (!isSuperadmin.value) return
  try {
    const data = await api.get('/models/platform-defaults')
    platformDefaults.text = data.defaultTextModelId || null
    platformDefaults.image = data.defaultImageModelId || null
  } catch (err) {
    error.value = err.message
  }
}

async function setPlatformDefault(kind, value) {
  platformSaving.value = kind
  error.value = ''
  try {
    const data = await api.patch('/models/platform-defaults', {
      [kind === 'text' ? 'defaultTextModelId' : 'defaultImageModelId']: value || null,
    })
    platformDefaults.text = data.defaultTextModelId || null
    platformDefaults.image = data.defaultImageModelId || null
    notifyModelStatus()
    toast('平台兜底模型已更新。')
  } catch (err) {
    error.value = err.message
  } finally {
    platformSaving.value = ''
  }
}

async function loadDefaults() {
  try {
    applyDefaults(await api.get('/preferences'))
  } catch (err) {
    error.value = err.message
  }
}

/**
 * 切换默认模型（kind: text | image），保存后立即重算顶栏与本页左侧的生效信息。
 * 失败时把下拉退回原值——不能让界面显示成「已切换」而服务端其实没存上。
 */
async function setDefault(kind, value) {
  error.value = ''
  const previous = kind === 'text' ? defaults.textId : defaults.imageId
  savingKind.value = kind
  try {
    const pref = await api.patch(
      '/preferences',
      kind === 'text'
        ? { defaultTextModelId: value || '' }
        : { defaultImageModelId: value || '' },
    )
    applyDefaults(pref)
    // 创作页下拉读的是同一个 store，刷新后「自动（默认：xxx）」立刻对齐
    await modelSelection.load(true)
    notifyModelStatus()
    toast(
      value
        ? `已设为全局默认${kind === 'text' ? '文本' : '生图'}模型，未指定模型时优先使用。`
        : `已清除默认${kind === 'text' ? '文本' : '生图'}模型，恢复按自动顺序选择。`,
    )
  } catch (err) {
    if (kind === 'text') defaults.textId = previous
    else defaults.imageId = previous
    error.value = err.message
  } finally {
    savingKind.value = ''
  }
}

function isDefaultModel(model) {
  return model.id === defaults.textId || model.id === defaults.imageId
}

/** 单个模型的类型：空能力的历史模型按文本处理，与后端 is_text_model 口径一致 */
function modelKind(model) {
  const caps = model?.capabilities || []
  return caps.includes('image.generate') ? 'image' : 'text'
}

/**
 * 连接的归类：整条连接都是生图 → 生图；都是文本 → 文本；
 * 混杂或还没登记模型 → 其他（单独一栏，不硬塞进某一类，免得点错删错）。
 */
function connectionKind(conn) {
  const kinds = (conn.models || []).map(modelKind)
  if (!kinds.length) return 'other'
  if (kinds.every((kind) => kind === 'image')) return 'image'
  if (kinds.every((kind) => kind === 'text')) return 'text'
  return 'other'
}

const grouped = computed(() => {
  const buckets = { text: [], image: [], other: [] }
  for (const conn of connections.value) buckets[connectionKind(conn)].push(conn)
  return buckets
})

/** 分组列表：文本、生图固定有序；「其他」只在真有这类连接时出现 */
const sections = computed(() => MODEL_TYPES.map((type) => ({
  ...type,
  rows: connections.value.filter((conn) => {
    const models = conn.models || []
    return models.some((model) => modelKind(model) === type.value) || (!models.length && type.value === 'text')
  }),
  addLabel: `添加${type.label}`,
})))

/** 当前弹窗对应的类型 */
const activeType = computed(
  () => MODEL_TYPES.find((item) => item.value === connForm.modelType) || MODEL_TYPES[0],
)
const otherType = computed(
  () => MODEL_TYPES.find((item) => item.value !== connForm.modelType) || MODEL_TYPES[1],
)

/** 「其他」栏里的连接可能是混合能力，标签上要标出每个模型的类型 */
function isOtherKind(conn) {
  return connectionKind(conn) === 'other'
}

function modelTagText(conn, model) {
  const code = model.modelCode || '未填写代号'
  return isOtherKind(conn) ? `${KIND_SHORT[modelKind(model)]} · ${code}` : code
}

function selectModelType(type) {
  connForm.modelType = type
  const found = MODEL_TYPES.find((item) => item.value === type)
  if (found) connForm.capabilities = found.caps
}

/** 弹窗顶部的「改为另一种类型」：只改这一条的能力登记，不换连接 */
function toggleModelType() {
  selectModelType(otherType.value.value)
}

/** 当前下拉里的关键字；用它做排序、计数与高亮，菜单一关就清空（和 naive-ui 内部行为保持一致） */
const modelSearch = ref('')

/**
 * 关键字比较时忽略大小写与 - _ . / 空格这些分隔符：
 * 输 gpt6terra 也能命中 gpt-6.1-terra，不用记住供应商怎么拼的。
 */
function looseKey(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[\s\-_./]+/g, '')
}

const modelSearchTerms = computed(() =>
  String(modelSearch.value || '')
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean),
)

/** 下拉里的过滤：空格分隔多个关键字（要同时命中），忽略大小写与分隔符 */
function filterModelOption(pattern, option) {
  const terms = String(pattern || '')
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
  if (!terms.length) return true
  const haystack = looseKey(option?.value ?? option?.label)
  return terms.every((term) => haystack.includes(looseKey(term)))
}

/** 命中排序：开头就命中的排前面，其次是 / 后半段命中，最后是中间命中 */
function matchRank(code, terms) {
  const head = looseKey(terms[0])
  if (!head) return 9
  if (looseKey(code).startsWith(head)) return 0
  const tail = String(code).split('/').pop() || ''
  if (looseKey(tail).startsWith(head)) return 1
  return 2
}

/**
 * 模型代号下拉：已拉到的供应商模型按相关度排；列表里没有的代号在末尾补一行「自定义」，
 * 而不是像 tag 模式那样固定塞到第一行——否则输完关键字一回车就把原文存成模型代号了。
 */
const modelOptions = computed(() => {
  const terms = modelSearchTerms.value
  const rows = remoteModels.value
  if (!terms.length) return rows.map((row) => ({ label: row.id, value: row.id }))
  // 搜索时按相关度排，免得精准命中的模型被一串同名前缀挤到后面
  const options = [...rows]
    .sort((a, b) => matchRank(a.id, terms) - matchRank(b.id, terms) || a.id.localeCompare(b.id))
    .map((row) => ({ label: row.id, value: row.id }))
  const raw = String(modelSearch.value || '').trim()
  // 代号里不会有空格，带空格说明是在叠关键字搜索，不该冒出一个「自定义代号」
  if (raw && !/\s/.test(raw) && !rows.some((row) => row.id === raw)) {
    options.push({ label: raw, value: raw, custom: true })
  }
  return options
})

const matchedModelCount = computed(() => {
  const terms = modelSearchTerms.value
  if (!terms.length) return remoteModels.value.length
  return remoteModels.value.filter((row) => {
    const haystack = looseKey(row.id)
    return terms.every((term) => haystack.includes(looseKey(term)))
  }).length
})

const modelCodePlaceholder = computed(() =>
  remoteModels.value.length
    ? `输入关键字筛选 ${remoteModels.value.length} 个模型，或直接填代号`
    : activeType.value.codePlaceholder,
)

function hasRemoteModels() {
  return remoteModels.value.length > 0
}

/** 把命中的片段标出来，四十个模型里一眼看到命中在哪 */
function renderModelLabel(option) {
  const code = String(option?.value ?? '')
  const terms = modelSearchTerms.value
  if (!terms.length || !code) return code
  // 手打的代号不在供应商列表里：明确写成「自定义」，免得和真实模型混为一谈
  if (option?.custom) {
    return h('span', { class: 'model-option-custom' }, `使用自定义代号「${code}」`)
  }
  const lower = code.toLowerCase()
  const ranges = []
  for (const term of terms) {
    let from = 0
    while (from <= lower.length) {
      const at = lower.indexOf(term, from)
      if (at < 0) break
      ranges.push([at, at + term.length])
      from = at + term.length
    }
  }
  if (!ranges.length) return code
  ranges.sort((a, b) => a[0] - b[0])
  const merged = []
  for (const [start, end] of ranges) {
    const last = merged[merged.length - 1]
    if (last && start <= last[1]) last[1] = Math.max(last[1], end)
    else merged.push([start, end])
  }
  const parts = []
  let cursor = 0
  for (const [start, end] of merged) {
    if (start > cursor) parts.push(code.slice(cursor, start))
    parts.push(h('span', { class: 'model-hit' }, code.slice(start, end)))
    cursor = end
  }
  if (cursor < code.length) parts.push(code.slice(cursor))
  return h('span', { class: 'model-option-label' }, parts)
}

function handleModelSearch(value) {
  modelSearch.value = value || ''
}

/** 菜单关掉时 naive-ui 会清空自己的输入，这里跟着清，避免高亮/计数残留 */
function handleModelMenuToggle(open) {
  if (!open) modelSearch.value = ''
}

function resetProbe() {
  probe.running = false
  probe.state = 'idle'
  probe.message = ''
  probe.detail = ''
  modelsHint.value = ''
  remoteModels.value = []
  modelSearch.value = ''
}

/**
 * 按当前填写的 Base URL / 密钥拉一次供应商模型列表。
 * 编辑已有连接时密钥可以留空——后端会回落到该连接已存的密文。
 */
async function fetchRemoteModels() {
  modelsHint.value = ''
  if (!connForm.baseUrl.trim()) {
    modelsHint.value = '先填写 Base URL，再拉取模型列表'
    return
  }
  const conn = editingConnection()
  if (!(await confirmOtherConnection(conn, '拉取模型列表'))) return
  modelsLoading.value = true
  try {
    const res = await api.post(`/models/probe/models${crossUserQuery(conn)}`, {
      baseUrl: connForm.baseUrl.trim(),
      apiKey: connForm.apiKey.trim(),
      providerType: connForm.providerType,
      connectionId: editingId.value || null,
    })
    const rows = res.models || []
    remoteModels.value = rows
    if (res.status !== 'connected') {
      modelsHint.value = `拉取失败：${res.message}`
      return
    }
    if (!rows.length) {
      modelsHint.value = '供应商返回的模型列表为空，只能手动填写代号'
      return
    }
    // 只有一个模型时直接填上，省一次点击；多个就交给下拉选
    if (rows.length === 1) connForm.modelCode = rows[0].id
    modelsHint.value = `已拉取 ${res.modelCount} 个模型${res.truncated ? '（列表过长，只显示前 300 个）' : ''}`
  } catch (err) {
    remoteModels.value = []
    modelsHint.value = `拉取失败：${err.message}`
  } finally {
    modelsLoading.value = false
  }
}

/**
 * 测试当前这套配置：连接是否通 + 这个模型代号能不能用。
 * 文本模型真发一次 1 token 请求；生图模型只核对列表，不替用户花钱出图。
 */
async function runProbe() {
  probe.detail = ''
  if (!connForm.baseUrl.trim()) {
    // 本地先拦一道：没必要为必填项缺失打一次 400
    probe.running = false
    probe.state = 'fail'
    probe.message = '请先填写 Base URL'
    return
  }
  const conn = editingConnection()
  if (!(await confirmOtherConnection(conn, '测试配置'))) return
  probe.running = true
  probe.state = 'running'
  probe.message = '正在向供应商发起真实请求…'
  try {
    const res = await api.post(`/models/probe/test${crossUserQuery(conn)}`, {
      baseUrl: connForm.baseUrl.trim(),
      apiKey: connForm.apiKey.trim(),
      providerType: connForm.providerType,
      connectionId: editingId.value || null,
      modelCode: (connForm.modelCode || '').trim(),
      kind: connForm.modelType,
    })
    const detail = [
      PROBE_STATUS_LABEL[res.status] || res.status,
      res.httpStatus ? `HTTP ${res.httpStatus}` : '',
      res.latencyMs ? `${res.latencyMs}ms` : '',
      res.modelCount ? `供应商 ${res.modelCount} 个模型` : '',
    ]
      .filter(Boolean)
      .join(' · ')
    probe.state = res.verified ? (res.modelVerified ? 'ok' : 'warn') : 'fail'
    probe.message = res.message
    probe.detail = detail
  } catch (err) {
    probe.state = 'fail'
    probe.message = err.message
    probe.detail = ''
  } finally {
    probe.running = false
    // 弹窗比屏幕高时结论会落在可视区外，滚过去，别让用户以为点了没反应
    await nextTick()
    probeAlertRef.value?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
}

/**
 * 保存后顺手跑一次「已保存连接」的探测，把状态从 untested 变成真实结论。
 * 不 await：保存本身不该被供应商的 8 秒超时拖住；失败就保留 untested，不假装已连接。
 */
function settleStatus(conn) {
  if (!conn?.id) return
  api
    .post(`/models/connections/${conn.id}/test${crossUserQuery(conn)}`)
    .then((result) => {
      conn.status = result.status
    })
    .catch(() => {})
    .finally(() => notifyModelStatus())
}

/** 地址 / 密钥 / 协议一改，之前拉到的列表与测试结论都不作数了 */
watch(
  () => [connForm.baseUrl, connForm.apiKey, connForm.providerType],
  () => resetProbe(),
)

/** 换类型只作废测试结论：模型列表是供应商维度的，同一份列表仍然有效 */
watch(
  () => connForm.modelType,
  () => {
    probe.state = 'idle'
    probe.message = ''
    probe.detail = ''
  },
)

async function loadConnections() {
  try {
    const rows = []
    let cursor = ''
    do {
      const page = await api.getPage(`/models/connections?limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`)
      rows.push(...(page.data || []))
      cursor = page.nextCursor || ''
    } while (cursor)
    connections.value = rows
  } catch (err) {
    error.value = err.message
  }
}

async function loadStatus() {
  statusLoading.value = true
  try {
    statusRows.value = (await api.get('/models/status')) || []
  } catch (err) {
    error.value = err.message
  } finally {
    statusLoading.value = false
  }
}

/** 连接 / 模型一改，顶栏状态条与本页的生效信息都立刻重算，不用等轮询。 */
function notifyModelStatus() {
  window.dispatchEvent(new Event('hanyue:model-status-changed'))
  loadStatus()
}

/** 本机环回地址允许明文 HTTP，其余主机必须 HTTPS（与后端 ssrf.py 的规则一致） */
function isLoopbackBaseUrl(url) {
  try {
    const host = new URL(url).hostname.toLowerCase().replace(/^\[|\]$/g, '')
    return host === 'localhost' || host === 'localhost.localdomain' || host === '127.0.0.1' || host === '::1'
  } catch {
    return false
  }
}

function resetForm(type = 'text') {
  editingId.value = ''
  clearKey.value = false
  connForm.displayName = ''
  connForm.providerType = 'openai_compatible'
  connForm.baseUrl = ''
  connForm.apiKey = ''
  connForm.modelCode = ''
  connForm.imageSizes = ''
  resetProbe()
  selectModelType(type)
}

/** 从哪一栏点进来的，就新建哪一类，弹窗里不再让人二次挑类型 */
function openCreate(type) {
  resetForm(type === 'image' ? 'image' : 'text')
  showConnection.value = true
}

function openEdit(conn) {
  resetForm('text')
  editingId.value = conn.id
  connForm.displayName = conn.displayName
  connForm.providerType = conn.providerType
  connForm.baseUrl = conn.baseUrl || ''
  connForm.modelCode = conn.models?.[0]?.modelCode || ''
  const sizes = conn.models?.[0]?.supportedImageSizes || {}
  connForm.imageSizes = Object.keys(sizes).length ? JSON.stringify(sizes, null, 2) : ''
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
  if (connForm.baseUrl && !/^https?:\/\//i.test(connForm.baseUrl)) {
    error.value = 'Base URL 必须以 http:// 或 https:// 开头'
    return
  }
  if (connForm.baseUrl && connForm.baseUrl.toLowerCase().startsWith('http://') && !isLoopbackBaseUrl(connForm.baseUrl)) {
    error.value = 'Base URL 必须使用 HTTPS（仅本机地址允许 HTTP）'
    return
  }
  let imageSizes = {}
  if (connForm.modelType === 'image' && connForm.imageSizes.trim()) {
    try {
      imageSizes = JSON.parse(connForm.imageSizes)
      if (!imageSizes || Array.isArray(imageSizes) || typeof imageSizes !== 'object') throw new Error()
    } catch {
      error.value = '精确尺寸必须是 JSON 对象，例如 {"1:1":"1024x1024"}'
      return
    }
    for (const [ratio, size] of Object.entries(imageSizes)) {
      const match = typeof size === 'string' && /^([1-9][0-9]{0,3})x([1-9][0-9]{0,3})$/.exec(size)
      if (!/^[0-9]{1,4}(?:\.[0-9]{1,4})?:[0-9]{1,4}(?:\.[0-9]{1,4})?$/.test(ratio) || !match) {
        error.value = `图片比例或尺寸格式不合法：${ratio}=${size}`
        return
      }
      const [rw, rh] = ratio.split(':').map(Number)
      const width = Number(match[1]), height = Number(match[2])
      if (!rw || !rh || width * height > 16000000 || Math.abs(width / height - rw / rh) >= 0.001) {
        error.value = `尺寸 ${size} 与比例 ${ratio} 不符或超过像素限制`
        return
      }
    }
  }
  const editing = editingConnection()
  if (editingId.value && !(await confirmOtherConnection(editing, '修改连接及模型，并测试连接'))) return
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
      const updated = await api.patch(`/models/connections/${editingId.value}${crossUserQuery(editing)}`, payload)
      const idx = connections.value.findIndex((c) => c.id === updated.id)
      if (idx >= 0) connections.value[idx] = updated
      await syncModelCode(updated, connForm.modelCode, false, connForm.capabilities, imageSizes)
      showConnection.value = false
      notifyModelStatus()
      // 后台补一次真实探测，状态从 untested 变成结论；不阻塞保存
      settleStatus(updated)
      toast('连接已更新。')
      return
    }
    const conn = await api.post('/models/connections', {
      displayName: connForm.displayName,
      providerType: connForm.providerType,
      baseUrl: connForm.baseUrl,
      apiKey: connForm.apiKey,
    })
    await syncModelCode(conn, connForm.modelCode, true, connForm.capabilities, imageSizes)
    connections.value.push(conn)
    showConnection.value = false
    notifyModelStatus()
    settleStatus(conn)
    toast('连接已保存；密钥只在服务端加密存储，接口不会返回明文。')
  } catch (err) {
    error.value = err.message
  }
}

/** 新增或修改该连接下的第一个模型代号，保持与「模型代号」输入一致 */
async function syncModelCode(conn, code, isNew = false, capabilities = '', imageSizes = {}) {
  const trimmed = (code || '').trim()
  const caps = capabilities
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
  const capList = caps.length ? caps : ['text.generate', 'text.stream']
  const existing = conn.models?.[0]
  if (!trimmed) {
    if (!isNew && existing?.id) {
      await api.del(`/models/catalog/${existing.id}${crossUserQuery(conn)}`)
      conn.models = []
    }
    return
  }
  const capsChanged =
    existing && JSON.stringify(existing.capabilities || []) !== JSON.stringify(capList)
  // 展示名没人手工维护过（等于旧代号）时跟着代号走，否则改完代号两处名字对不上
  const nameStale =
    existing && existing.modelCode !== trimmed && existing.displayName === existing.modelCode
  const sizesChanged = capList.includes('image.generate') && JSON.stringify(existing?.supportedImageSizes || {}) !== JSON.stringify(imageSizes)
  if (existing && (existing.modelCode !== trimmed || capsChanged || sizesChanged)) {
    const model = await api.patch(`/models/catalog/${existing.id}${crossUserQuery(conn)}`, {
      modelCode: trimmed,
      capabilities: capList,
      ...(capList.includes('image.generate') ? { supportedImageSizes: imageSizes } : {}),
      ...(nameStale ? { displayName: trimmed } : {}),
    })
    conn.models = [model]
  } else if (!existing) {
    const model = await api.post(`/models/catalog${crossUserQuery(conn)}`, {
      connectionId: conn.id,
      modelCode: trimmed,
      capabilities: capList,
      ...(capList.includes('image.generate') ? { supportedImageSizes: imageSizes } : {}),
    })
    conn.models = [model]
  }
}

async function testConnection(conn) {
  if (!(await confirmOtherConnection(conn, '测试连接'))) return
  error.value = ''
  try {
    const result = await api.post(`/models/connections/${conn.id}/test${crossUserQuery(conn)}`)
    conn.status = result.status
    notifyModelStatus()
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
  if (isOwnConnection(conn)) {
    if (!(await confirm(`删除连接「${conn.displayName}」及其所有模型？此操作无法撤销。`, '删除模型连接'))) return
  } else if (!(await confirmOtherConnection(conn, '删除连接及其模型'))) return
  try {
    await api.del(`/models/connections/${conn.id}${crossUserQuery(conn)}`)
    connections.value = connections.value.filter((c) => c.id !== conn.id)
    notifyModelStatus()
  } catch (err) {
    error.value = err.message
  }
}

const providerOptions = [
  { value: 'openai_compatible', label: 'OpenAI 兼容' },
  { value: 'vendor_native', label: '供应商原生（适配器未接入）' },
]

watch(isSuperadmin, (allowed) => {
  if (allowed) loadPlatformDefaults()
})

onMounted(() => {
  loadConnections()
  loadStatus()
  loadDefaults()
  loadPlatformDefaults()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">模型管理</h1>
        <p class="page-sub">
          文本和生图模型分别管理；可在列表中设置个人默认。超级管理员可将自己的模型设为平台兜底，供其他用户使用并产生费用。
        </p>
      </div>
    </div>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <n-alert v-if="!canManage" type="info" :bordered="false">
      你当前只有「模型查看」权限：能看到正在使用的模型、连接状态与密钥掩码；
      新增连接、修改密钥、删除连接需要「模型管理」权限，请联系超级管理员。
    </n-alert>

    <section v-for="section in sections" :key="section.value" class="card">
      <div class="row">
        <h2 class="card-title" style="margin: 0">{{ section.label }}</h2>
        <span class="group-count">{{ section.rows.length }} 个连接</span>
        <div class="spacer" />
        <n-button
          v-if="section.addLabel"
          size="small"
          secondary
          :disabled="!canManage"
          :title="canManage ? '' : '需要「模型管理」权限'"
          @click="openCreate(section.value)"
        >
          {{ section.addLabel }}
        </n-button>
      </div>
      <p class="page-sub">{{ section.desc }}</p>

      <n-empty v-if="!section.rows.length" :description="section.empty || '暂无连接'" size="small">
        <template v-if="section.addLabel" #extra>
          <n-button
            size="small"
            :disabled="!canManage"
            :title="canManage ? '' : '需要「模型管理」权限'"
            @click="openCreate(section.value)"
          >
            {{ section.addLabel }}
          </n-button>
        </template>
      </n-empty>
      <div v-else class="list">
        <div v-for="conn in section.rows" :key="conn.id" class="list-row">
          <div class="list-main">
            <div class="list-title">{{ conn.displayName }}</div>
            <div v-if="isSuperadmin" class="list-meta">所有者：{{ conn.ownerLabel || conn.ownerUserId || '未知' }}{{ isOwnConnection(conn) ? '（本人）' : '' }}</div>
            <div class="list-meta">
              {{ conn.providerType }} · {{ conn.baseUrl || '未设置 Base URL' }} ·
              密钥 {{ conn.keyMasked || '未配置' }}
            </div>
            <div v-if="conn.models.length" class="row row-tight" style="margin-top: 6px">
              <template v-for="model in conn.models.filter((item) => modelKind(item) === section.value)" :key="model.id">
                <n-tag
                  size="small"
                  :type="isOtherKind(conn) ? (modelKind(model) === 'image' ? 'info' : 'warning') : 'default'"
                  round
                >
                  {{ modelTagText(conn, model) }}
                </n-tag>
                <n-tag v-if="isDefaultModel(model) && isOwnConnection(conn)" size="small" type="success" round>个人默认</n-tag>
                <n-tag v-if="isSuperadmin && platformDefaults[section.value] === model.id" size="small" type="info" round>平台兜底</n-tag>
                <n-button v-if="isSuperadmin && isOwnConnection(conn)" size="tiny" quaternary :disabled="!model.enabled || Boolean(platformSaving)" @click="setPlatformDefault(section.value, platformDefaults[section.value] === model.id ? '' : model.id)">
                  {{ platformDefaults[section.value] === model.id ? '取消平台兜底' : '设为平台兜底' }}
                </n-button>
              </template>
            </div>
            <div v-else class="list-meta" style="margin-top: 4px">未登记模型 · 编辑后填写模型代号</div>
          </div>
          <StatusTag :status="conn.status" />
          <template v-if="isOwnConnection(conn)">
            <n-button
              v-for="model in conn.models.filter((item) => modelKind(item) === section.value)"
              :key="`default-${model.id}`"
              size="small"
              secondary
              :disabled="!model.enabled || Boolean(savingKind)"
              :title="!model.enabled ? '模型已停用，无法设为默认' : `默认模型：${model.displayName || model.modelCode}`"
              @click="setDefault(section.value, (section.value === 'text' ? defaults.textId : defaults.imageId) === model.id ? '' : model.id)"
            >
              {{ (section.value === 'text' ? defaults.textId : defaults.imageId) === model.id ? '取消默认' : '设为默认' }}{{ conn.models.filter((item) => modelKind(item) === section.value).length > 1 ? ` · ${model.displayName || model.modelCode}` : '' }}
            </n-button>
          </template>
          <n-button
            quaternary
            size="small"
            :disabled="!canManage"
            :title="canManage ? '重新测试这个连接的连通性' : '需要「模型管理」权限'"
            @click="testConnection(conn)"
          >
            测试
          </n-button>
          <n-button
            quaternary
            size="small"
            :disabled="!canManage"
            :title="canManage ? '修改连接与密钥' : '需要「模型管理」权限'"
            @click="openEdit(conn)"
          >
            编辑
          </n-button>
          <n-button
            quaternary
            size="small"
            type="error"
            :disabled="!canManage"
            :title="canManage ? '删除这个连接及其模型登记' : '需要「模型管理」权限'"
            @click="removeConnection(conn)"
          >
            删除
          </n-button>
        </div>
      </div>

      <p v-if="section.value === 'image'" class="field-hint">
        个人模型和平台兜底不可用时，还可能使用环境变量 IMAGE_PROVIDER_* 配置的生图服务。
      </p>
    </section>

    <n-modal
      v-model:show="showConnection"
      preset="card"
      class="model-modal"
      :title="`${editingId ? '编辑' : '添加'}${activeType.label}连接`"
      style="width: min(560px, calc(100vw - 32px))"
    >
      <div class="model-kind-banner">
        <div class="model-kind-text">
          <div class="model-kind-name">{{ activeType.label }}</div>
          <div class="model-kind-desc">{{ activeType.desc }}</div>
        </div>
        <n-button quaternary size="tiny" :focusable="false" @click="toggleModelType">
          改为{{ otherType.label }}
        </n-button>
      </div>

      <n-form class="model-form" label-placement="top">
        <n-form-item label="连接名称">
          <n-input v-model:value="connForm.displayName" :placeholder="activeType.namePlaceholder" />
        </n-form-item>
        <n-form-item label="供应商类型">
          <n-select v-model:value="connForm.providerType" :options="providerOptions" />
          <template v-if="connForm.providerType === 'vendor_native'" #feedback>
            该协议适配器尚未实现，连接可保存，但「测试」会明确返回 adapter_unavailable，不会静默回退。
          </template>
        </n-form-item>
        <n-form-item label="Base URL">
          <n-input v-model:value="connForm.baseUrl" placeholder="https://api.example.com/v1 或 http://127.0.0.1:11434/v1" />
          <template #feedback>
            公网地址仅允许 HTTPS；本机环回地址（localhost / 127.0.0.1）可用 HTTP，供本机模型服务。
            保存与请求前都会校验目标地址，拒绝内网与云元数据地址。
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
        <n-form-item :label="`模型代号${editingId ? '' : '（可选）'}`">
          <div class="model-code-row">
            <n-select
              v-model:value="connForm.modelCode"
              class="model-code-select"
              filterable
              clearable
              :options="modelOptions"
              :filter="filterModelOption"
              :render-label="renderModelLabel"
              :consistent-menu-width="false"
              :placeholder="modelCodePlaceholder"
              :loading="modelsLoading"
              @search="handleModelSearch"
              @update:show="handleModelMenuToggle"
            >
              <template #header>
                <div v-if="hasRemoteModels()" class="model-menu-head">
                  <span class="model-menu-count">
                    {{
                      modelSearchTerms.length
                        ? `匹配 ${matchedModelCount} / 共 ${remoteModels.length} 个`
                        : `共 ${remoteModels.length} 个模型`
                    }}
                  </span>
                  <span class="model-menu-tip">
                    {{ modelSearchTerms.length ? '空格分隔可叠加关键字' : '直接输入关键字筛选' }}
                  </span>
                </div>
              </template>
            </n-select>
            <n-button
              secondary
              :loading="modelsLoading"
              :disabled="!connForm.baseUrl.trim()"
              @click="fetchRemoteModels"
            >
              拉取模型列表
            </n-button>
          </div>
          <template #feedback>
            <span v-if="modelsHint">{{ modelsHint }}<br /></span>
            <span v-if="hasRemoteModels()">
              关键字可用空格叠加（如 <code>gpt 6 terra</code>），忽略大小写与 - . / 分隔符；有匹配时回车取第一条。<br />
            </span>
            能力按类型登记（{{ activeType.caps }}），不会被调用未登记的能力。
            <template v-if="editingId">留空将删除该连接下已登记的模型。</template>
          </template>
        </n-form-item>
        <n-form-item v-if="connForm.modelType === 'image'" label="成稿台精确尺寸（可选）">
          <n-input v-model:value="connForm.imageSizes" type="textarea" :rows="3" placeholder='例如 {"1:1":"1024x1024"}' />
          <template #feedback>请仅按供应商文档填写确认支持的比例与像素尺寸，JSON 格式，例如 {"1:1":"1024x1024"}；留空表示未确认。保存不代表已验证供应商能力。环境变量供应商不能在此配置，请选择已登记的默认生图模型。</template>
        </n-form-item>
      </n-form>

      <div v-if="probe.state !== 'idle'" ref="probeAlertRef" class="probe-alert">
        <n-alert
          :type="probe.state === 'ok' ? 'success' : probe.state === 'warn' ? 'warning' : probe.state === 'running' ? 'info' : 'error'"
          :bordered="false"
        >
          {{ probe.message }}
          <div v-if="probe.detail" class="probe-detail">{{ probe.detail }}</div>
        </n-alert>
      </div>

      <template #footer>
        <div class="row">
          <n-button quaternary :loading="probe.running" @click="runProbe">测试配置</n-button>
          <div class="spacer" />
          <n-space>
            <n-button quaternary @click="showConnection = false">取消</n-button>
            <n-button type="primary" @click="saveConnection">保存</n-button>
          </n-space>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
/* 每类模型一行：左边答「现在用哪个」，右边答「不指定时先用哪个」，窄屏竖排 */
.model-merge {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.model-merge-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(240px, 320px);
  gap: var(--space-4);
  align-items: start;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  min-width: 0;
}

.model-merge-now,
.model-merge-pick {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.model-merge-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.model-merge-kind {
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}

.model-merge-name {
  font-size: var(--font-body);
  font-weight: 600;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 标签和说明都靠左对齐，避免和下拉框的说明挤成两行高低不齐 */
.model-merge-pick-label {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.model-merge-note {
  margin: 0;
  line-height: 1.5;
}

@media (max-width: 780px) {
  .model-merge-row {
    grid-template-columns: 1fr;
  }
}

.model-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
}

/* 绿（可用）/ 红（断链）/ 灰（未验证、未配置），浮层与卡片都带文字状态，不只靠颜色 */
.model-dot.is-ok {
  color: var(--color-success);
}

.model-dot.is-blocked {
  color: var(--color-danger);
}

.model-dot.is-unchecked,
.model-dot.is-none {
  color: var(--color-text-muted);
}

.group-count {
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  margin-left: var(--space-2);
}

/* 弹窗顶部固定当前类型：类型由入口决定，这里不走「二选一」的卡片选择器 */
.model-kind-banner {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3);
  margin-bottom: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
}

.model-kind-text {
  flex: 1;
  min-width: 0;
}

.model-kind-name {
  font-size: var(--font-body);
  font-weight: 600;
  color: var(--color-text-primary);
}

.model-kind-desc {
  margin-top: 2px;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  line-height: 1.5;
}

/* naive-ui 的表单项本身没有行距，反馈文字会贴住下一个标签，这里补一行间隙 */
.model-form :deep(.n-form-item) {
  margin-bottom: var(--space-4);
}

.model-form :deep(.n-form-item:last-child) {
  margin-bottom: 0;
}

/* 模型代号：既能从供应商列表里选，也能直接手打一个没列出来的代号 */
.model-code-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
}

.model-code-select {
  flex: 1;
  min-width: 0;
}

/* 下拉顶部计数条：先告诉你能搜，再告诉你搜到了几个 */
.model-menu-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 0 var(--space-3);
  padding: 2px 4px 6px;
  margin-bottom: 2px;
  border-bottom: 1px solid var(--color-border);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
}

.model-menu-count {
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.model-menu-tip {
  flex: none;
  color: var(--color-text-muted);
  white-space: nowrap;
}

/* 命中的关键字段落：用弱底色的软高亮，深浅主题都不刺眼 */

.probe-alert {
  margin-top: var(--space-2);
}

.probe-detail {
  margin-top: 2px;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}

/* 窄屏：状态与三个操作挤在一行会把连接名压成省略号，改为换行排 */
@media (max-width: 640px) {
  .list-row {
    flex-wrap: wrap;
    row-gap: var(--space-2);
  }

  .list-row .list-main {
    flex: 1 0 100%;
  }

  .list-row .list-title {
    white-space: normal;
  }

  /* 窄屏下型号名很长，下拉占满一行、按钮换到下一行，别把下拉挤成 175px 只显示半截代号 */
  .model-code-row {
    flex-wrap: wrap;
    row-gap: var(--space-2);
  }

  .model-code-select {
    flex: 1 0 100%;
  }
}
</style>

<style>
/*
 * 弹窗是 teleport 到 body 的，scoped 选择器够不到它的内部结构，
 * 这里按弹窗自己的类名兜住：高度封顶、内容区滚动，底部按钮始终留在视野里。
 */
.model-modal.n-modal {
  max-height: calc(100vh - 40px);
}

.model-modal .n-card-content {
  overflow: auto;
  min-height: 0;
}

/*
 * 下拉行里的高亮与「自定义代号」文字是 render-label 用 h() 建的 vnode，
 * 属于 naive-ui 的渲染上下文、拿不到本组件的 scoped 标记，所以放全局。
 */
.model-hit {
  border-radius: var(--radius-sm);
  padding: 0 1px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent-soft);
}

/* 列表里没有这个代号时，下拉第一行是「用你输入的原文」，写清楚，别让人误以为命中了模型 */
.model-option-custom {
  color: var(--color-text-muted);
}
</style>
