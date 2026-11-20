<script setup>
import { computed } from 'vue'
import { NInput, NInputNumber, NSelect, NSwitch, NButton, NAlert } from 'naive-ui'

const props = defineProps({
  type: { type: String, required: true },
  payload: { type: Object, default: () => ({}) },
  ruleKey: { type: String, default: '' },
  readonly: Boolean,
})
const emit = defineEmits(['update:payload'])
const reviewKinds = [
  { label: '必须包含指定内容', value: 'contains' },
  { label: '不能包含指定内容', value: 'not_contains' },
  { label: '字数不能少于', value: 'min_chars' },
  { label: '字数不能超过', value: 'max_chars' },
]
const reviewFields = [
  { label: '正文', value: 'markdown' }, { label: '标题', value: 'title' }, { label: '摘要', value: 'summary' },
]
const ratios = ['1:1', '3:4', '4:3', '16:9', '9:16'].map((value) => ({ label: value, value }))
const numericReview = computed(() => ['min_chars', 'max_chars'].includes(props.payload.kind))
function update(field, value) {
  const next = { ...props.payload, [field]: value }
  if (props.type === 'review_rule') next.id = props.ruleKey
  emit('update:payload', next)
}
function changeKind(kind) {
  const wasNumber = numericReview.value
  const isNumber = ['min_chars', 'max_chars'].includes(kind)
  emit('update:payload', {
    ...props.payload, id: props.ruleKey, kind,
    value: wasNumber === isNumber ? props.payload.value : (isNumber ? 0 : ''),
  })
}
const args = computed(() => Object.entries(props.payload.args || {}))
const argTypes = [
  { label: '文本', value: 'string' }, { label: '数字', value: 'number' }, { label: '是 / 否', value: 'boolean' },
]
function changeArg(index, { name, value, type } = {}) {
  const entries = [...args.value]
  const [oldName, oldValue] = entries[index]
  if (name !== undefined && name !== oldName && entries.some(([key]) => key === name)) return
  entries[index] = [name ?? oldName, type ? ({ string: '', number: 0, boolean: false })[type] : (value ?? oldValue)]
  update('args', Object.fromEntries(entries))
}
function addArg() {
  let index = 1
  while (Object.hasOwn(props.payload.args || {}, `parameter${index}`)) index += 1
  update('args', { ...props.payload.args, [`parameter${index}`]: '' })
}
function removeArg(index) {
  update('args', Object.fromEntries(args.value.filter((_, at) => at !== index)))
}
</script>

<template>
  <div class="payload-form">
    <template v-if="type === 'review_rule'">
      <label>检查哪里<span>选择这条规则作用于正文、标题还是摘要。</span></label>
      <n-select :value="payload.field ?? 'markdown'" :options="reviewFields" :disabled="readonly" @update:value="(v) => update('field', v)" />
      <label>检查方式</label>
      <n-select :value="payload.kind ?? 'not_contains'" :options="reviewKinds" :disabled="readonly" @update:value="changeKind" />
      <label>{{ numericReview ? '字数要求' : '要检查的内容' }}</label>
      <n-input-number v-if="numericReview" :value="typeof payload.value === 'number' ? payload.value : null" :min="0" :precision="0" :disabled="readonly" @update:value="(v) => update('value', v ?? 0)" />
      <n-input v-else :value="String(payload.value ?? '')" :readonly="readonly" :maxlength="200" placeholder="例如：赋能（每条规则检查一个词或短语）" @update:value="(v) => update('value', v)" />
      <label>未通过时的提示<span>可选，帮助理解需要如何修改。</span></label>
      <n-input :value="payload.message ?? ''" :readonly="readonly" :maxlength="200" placeholder="例如：请用具体表达替换空泛用语" @update:value="(v) => update('message', v)" />
    </template>

    <template v-else-if="type === 'fact_policy'">
      <div class="switch-field"><div>关键事实要求来源<small>要求模型标注可核对的来源；不会自动核实来源真实性。</small></div><n-switch :value="payload.requireSources ?? false" :disabled="readonly" @update:value="(v) => update('requireSources', v)" /></div>
      <div class="switch-field"><div>禁止编造引语<small>没有可靠出处的原话，不写成确定的引语。</small></div><n-switch :value="payload.forbidUnverifiedQuotes ?? true" :disabled="readonly" @update:value="(v) => update('forbidUnverifiedQuotes', v)" /></div>
      <label>待核实标记<span>未确认的信息用此标记提示，最多 20 字。</span></label>
      <n-input :value="payload.placeholder ?? '〔待核实〕'" :readonly="readonly" :maxlength="20" @update:value="(v) => update('placeholder', v)" />
      <label>补充事实要求<span>每行一条，最多 10 条，每条最多 300 字。</span></label>
      <n-input type="textarea" :value="(payload.extraInstructions || []).join('\n')" :readonly="readonly" :autosize="{ minRows: 3, maxRows: 10 }" placeholder="例如：涉及统计数据时说明统计时间" @update:value="(v) => update('extraInstructions', v.split('\n'))" />
    </template>

    <template v-else-if="type === 'image_policy'">
      <div class="switch-field"><div>允许 AI 配图<small>关闭后要求模型不生成 AI 图片提示词；不是生图工具的权限开关。</small></div><n-switch :value="payload.allowAiImages ?? true" :disabled="readonly" @update:value="(v) => update('allowAiImages', v)" /></div>
      <label>封面图比例</label>
      <n-select :value="payload.coverAspectRatio ?? '3:4'" :options="ratios" :disabled="readonly" @update:value="(v) => update('coverAspectRatio', v)" />
      <label>正文插图比例</label>
      <n-select :value="payload.inlineAspectRatio ?? '1:1'" :options="ratios" :disabled="readonly" @update:value="(v) => update('inlineAspectRatio', v)" />
      <label>正文插图上限<span>0 表示不需要正文插图，最多 6 张。</span></label>
      <n-input-number :value="payload.maxInlineImages ?? 3" :min="0" :max="6" :precision="0" :disabled="readonly" @update:value="(v) => update('maxInlineImages', v ?? 0)" />
      <label>统一画面风格<span>附加到生图提示词，例如水彩、低饱和色调；最多 200 字。</span></label>
      <n-input type="textarea" :value="payload.promptStyleSuffix ?? ''" :maxlength="200" :readonly="readonly" :autosize="{ minRows: 2, maxRows: 5 }" @update:value="(v) => update('promptStyleSuffix', v)" />
    </template>

    <template v-else-if="type === 'repair_policy'">
      <n-alert type="info" :bordered="false">此策略目前支持保存，生成流程尚未接入，配置后暂不会自动重试。</n-alert>
      <label>审稿未通过后的重试次数<span>0 表示不重试，最多重试 1 次。</span></label>
      <n-input-number :value="payload.retries ?? 0" :min="0" :max="1" :precision="0" :disabled="readonly" @update:value="(v) => update('retries', v ?? 0)" />
      <div class="switch-field"><div>要求补齐输出结构<small>修复时要求补齐约定的输出字段。</small></div><n-switch :value="payload.requireContract ?? false" :disabled="readonly" @update:value="(v) => update('requireContract', v)" /></div>
    </template>

    <template v-else-if="type === 'workflow_step'">
      <label>工具标识<span>填写已登记且获准的工具标识；保存时服务端会校验。不是自然语言指令。</span></label>
      <n-input :value="payload.tool ?? ''" :readonly="readonly" :maxlength="64" placeholder="填写当前 Skill 允许使用的工具" @update:value="(v) => update('tool', v)" />
      <label>工具参数<span>按工具要求填写参数名，选择值的类型；没有参数可留空。</span></label>
      <div v-for="([name, value], index) in args" :key="index" class="argument-row">
        <n-input :value="name" :readonly="readonly" placeholder="参数名（不可重复）" @update:value="(v) => changeArg(index, { name: v })" />
        <n-select :value="typeof value" :options="argTypes" :disabled="readonly" @update:value="(v) => changeArg(index, { type: v })" />
        <n-switch v-if="typeof value === 'boolean'" :value="value" :disabled="readonly" @update:value="(v) => changeArg(index, { value: v })" />
        <n-input-number v-else-if="typeof value === 'number'" :value="value" :disabled="readonly" @update:value="(v) => changeArg(index, { value: v ?? 0 })" />
        <n-input v-else :value="value" :readonly="readonly" placeholder="参数值" @update:value="(v) => changeArg(index, { value: v })" />
        <n-button v-if="!readonly" quaternary type="error" @click="removeArg(index)">移除</n-button>
      </div>
      <n-button v-if="!readonly" secondary @click="addArg">＋ 添加参数</n-button>
    </template>
  </div>
</template>

<style scoped>
.payload-form { display: grid; gap: var(--space-2); }
.payload-form label { font-weight: 500; margin-top: var(--space-2); }
.payload-form label span, .switch-field small { display: block; font-weight: 400; font-size: var(--font-xs); line-height: 1.6; color: var(--color-text-secondary); }
.switch-field { display: flex; justify-content: space-between; align-items: center; gap: var(--space-3); padding: var(--space-2) 0; }
.argument-row { display: grid; grid-template-columns: minmax(100px, 1fr) 100px minmax(100px, 1fr) auto; align-items: center; gap: var(--space-2); }
@media (max-width: 767px) { .argument-row { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); } }
</style>
