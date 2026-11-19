<script setup>
import { computed, ref, watch } from 'vue'
import {
  NAlert, NButton, NCard, NInput, NInputNumber, NPopconfirm,
  NSpin, NSwitch, NTag,
} from 'naive-ui'
import { confirm } from '../../ui/feedback'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  scope: { type: String, default: 'global' },
  readonly: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
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
    hint: '会填入写作模板的 {tone} 占位符。',
    placeholder: '例如：克制、温和、偶尔自嘲',
  },
  {
    type: 'prompt_template', label: '写作模板', kind: 'single', json: false, field: 'textarea',
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
    hint: '生成正文的目标字数，超出后会被要求压缩。',
  },
  {
    type: 'style_rule', label: '风格规则', kind: 'list', json: false, field: 'input',
    hint: '每行一条，写作时逐条约束。',
    placeholder: '例如：避免使用“赋能”“闭环”等词',
  },
  {
    type: 'review_rule', label: '审稿规则', kind: 'list', json: true,
    hint: 'JSON 对象，形状 { id, kind, value, field, message }；id 需与行标识一致。',
  },
  {
    type: 'workflow_step', label: '工作流步骤', kind: 'list', json: true,
    hint: 'JSON 对象，形状 { tool, args }。',
  },
  {
    type: 'fact_policy', label: '事实约束', kind: 'single', json: true,
    hint: 'JSON 对象，形状 { requireSources, forbidUnverifiedQuotes, placeholder, extraInstructions }。',
  },
  {
    type: 'image_policy', label: '配图策略', kind: 'single', json: true,
    hint: 'JSON 对象，控制配图数量、比例与风格偏好。',
  },
  {
    type: 'repair_policy', label: '修复策略', kind: 'single', json: true,
    hint: 'JSON 对象，形状 { retries, requireContract }。',
  },
  {
    type: 'exemplar', label: '范文示例', kind: 'list', json: true,
    hint: 'JSON 对象，形状 { title, excerpt }。',
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

const enabledCount = computed(() => viewRows.value.filter((row) => row.enabled !== false).length)
const personalEmpty = computed(
  () => props.scope === 'personal' && !props.loading && !viewRows.value.length,
)

// 签名字用来区分「父级换了一批数据」和「父级回填我刚发出的这版数据」
function signature(rows) {
  return JSON.stringify(rows)
}

function stripRow(row) {
  const next = { ...row }
  delete next._localKey
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
  viewRows.value = incoming
    .map((row, index) => ({ row: cloneRow(row), index }))
    .sort((a, b) => {
      const orderA = groupOrder.get(a.row.ruleType) ?? GROUPS.length
      const orderB = groupOrder.get(b.row.ruleType) ?? GROUPS.length
      return orderA - orderB || a.index - b.index
    })
    .map((item) => item.row)
  // 记录「当前这份数据」的签名，父级把它原样回填时就不重建缓冲
  lastEmitted = signature(viewRows.value.map(stripRow))
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

/** 统一出口：按数组顺序重排 sortOrder，再把剥离临时字段的完整数组 emit 给父级 */
function commit(next) {
  const ordered = next.map((row, index) => ({ ...row, sortOrder: index }))
  viewRows.value = ordered
  const payload = ordered.map(stripRow)
  lastEmitted = signature(payload)
  emit('update:rows', payload)
}

/** 按行 key 打补丁；不原地修改 props */
function patchRow(key, patch) {
  const next = viewRows.value.map((row) => {
    if (keyOf(row) !== key) return row
    const merged = { ...row, ...patch }
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
  patchRow(keyOf(row), { payload: { ...(row.payload || {}), text: value } })
}

function setLength(row, value) {
  patchRow(keyOf(row), { payload: { ...(row.payload || {}), value: Number(value) || 0 } })
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
      return { value: 1200 }
    case 'repair_policy':
      return { retries: 1, requireContract: true }
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
  const next = [...viewRows.value]
  // 插到同分组最后一行之后，保持分组内连续
  let insertAt = next.length
  for (let i = next.length - 1; i >= 0; i -= 1) {
    if (next[i].ruleType === type) {
      insertAt = i + 1
      break
    }
  }
  next.splice(insertAt, 0, row)
  if (isJsonType(type)) {
    jsonText.value = { ...jsonText.value, [keyOf(row)]: prettyJson(payload) }
    jsonError.value = { ...jsonError.value, [keyOf(row)]: '' }
  }
  commit(next)
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

/** 单行类删除需额外说明：删掉不等于停用，下次启动会从出厂恢复 */
async function removeSingle(row) {
  const ok = await confirm(
    '删除后下次启动会从出厂恢复，若要停用请用开关。',
    '删除这条规则',
  )
  if (!ok) return
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
    patchRow(key, { payload: parsed })
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
      <span>共 {{ viewRows.length }} 条规则（启用 {{ enabledCount }} 条）</span>
      <span class="rules-scope">{{ scope === 'personal' ? '个人副本' : '全局规则' }}</span>
      <span v-if="readonly" class="rules-scope">只读</span>
    </div>

    <n-alert v-if="personalEmpty" type="info" :bordered="false" class="rules-alert">
      当前使用全局规则；保存空内容会清空个人副本。
    </n-alert>

    <n-spin :show="loading">
      <div v-if="loading" class="rules-skeleton">
        <div v-for="n in 3" :key="n" class="rules-skeleton-card" />
      </div>

      <div v-else class="rules-groups">
        <n-card v-for="group in GROUPS" :key="group.type" size="small" class="rules-card">
          <div class="rules-card-head">
            <div class="rules-card-title">
              <span>{{ group.label }}</span>
              <n-tag size="tiny" :bordered="false">{{ group.type }}</n-tag>
              <span class="rules-count">{{ groupRows(group.type).length }} 条</span>
            </div>
            <n-button
              v-if="!locked && group.kind === 'list'"
              size="tiny"
              secondary
              @click="addRow(group)"
            >添加一行</n-button>
          </div>
          <p class="rules-hint">{{ group.hint }}</p>

          <!-- 单行类为空：说明继承关系，再给出添加入口 -->
          <div v-if="group.kind === 'single' && !groupRows(group.type).length" class="rules-empty">
            <span>使用出厂默认（继承全局 / 无此项）</span>
            <n-button v-if="!locked" size="tiny" type="primary" @click="addRow(group)">添加</n-button>
          </div>

          <div v-for="(row, idx) in groupRows(group.type)" :key="keyOf(row)" class="rules-item">
            <div class="rules-item-head">
              <n-input
                v-if="group.kind === 'list'"
                class="rules-key"
                size="tiny"
                :value="row.ruleKey"
                :readonly="locked"
                placeholder="行标识"
                @update:value="(value) => onRuleKeyInput(row, value)"
              />
              <span v-else class="rules-key-static">default</span>
              <div class="rules-spacer" />
              <n-switch
                v-if="!locked"
                size="small"
                :value="row.enabled !== false"
                @update:value="(value) => setEnabled(row, value)"
              />
              <n-tag v-else-if="row.enabled === false" size="tiny" :bordered="false">已停用</n-tag>
              <template v-if="!locked">
                <n-button
                  quaternary
                  size="tiny"
                  :disabled="idx === 0"
                  @click="moveRow(row, -1)"
                >上移</n-button>
                <n-button
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
                  删除这一条规则？
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
                style="width: 200px"
                @update:value="(value) => setLength(row, value)"
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
        </n-card>
      </div>
    </n-spin>
  </div>
</template>

<style scoped>
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
.rules-card-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.rules-card-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 1;
  min-width: 0;
  font-weight: 600;
}
.rules-count {
  font-size: var(--font-xs);
  font-weight: 400;
  color: var(--color-text-secondary);
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
  padding: var(--space-2) 0;
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}
.rules-item {
  padding: var(--space-2) 0;
  border-top: 1px dashed var(--color-border);
}
.rules-item-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
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
