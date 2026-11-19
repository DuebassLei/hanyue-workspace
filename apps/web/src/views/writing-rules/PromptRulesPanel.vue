<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  NAlert, NButton, NDrawer, NDrawerContent, NEmpty, NForm, NFormItem,
  NInput, NSelect, NSwitch, NTag,
} from 'naive-ui'
import { api } from '../../api/client'
import { confirm, toast } from '../../ui/feedback'

const props = defineProps({
  canManage: Boolean,
})

// 分类顺序即界面分组顺序；未识别的分类会落到末尾「未分类」。
const CATEGORY_ORDER = ['contract', 'system', 'outline', 'repair', 'tone_lexicon', 'workflow_note']
const CATEGORY_LABELS = {
  contract: '输出契约',
  system: '系统规则',
  outline: '大纲提示词',
  repair: '修复提示词',
  tone_lexicon: 'AI 味词表',
  workflow_note: '流程说明',
}
const CATEGORY_OPTIONS = CATEGORY_ORDER.map((value) => ({
  value,
  label: CATEGORY_LABELS[value],
}))

const rules = ref([])
const loading = ref(true)
const error = ref('')
const busyKey = ref('')

const showEditor = ref(false)
const editingKey = ref('')
const saving = ref(false)
const formError = ref('')
const showPayload = ref(false)
const form = reactive({
  key: '',
  category: 'system',
  title: '',
  description: '',
  content: '',
  payloadText: '',
  enabled: true,
})

const groups = computed(() => {
  const buckets = CATEGORY_ORDER.map((key) => ({
    key,
    label: CATEGORY_LABELS[key],
    items: rules.value.filter((item) => item.category === key),
  }))
  const unknown = rules.value.filter((item) => !CATEGORY_LABELS[item.category])
  if (unknown.length) buckets.push({ key: 'other', label: '未分类', items: unknown })
  return buckets.filter((bucket) => bucket.items.length)
})
const editorTitle = computed(() => (editingKey.value ? '编辑规则' : '新增规则'))
const isContract = computed(() => form.category === 'contract')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get('/writing-rules/prompts')
    rules.value = Array.isArray(data) ? data : []
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function replaceRule(updated) {
  if (!updated || !updated.key) {
    load()
    return
  }
  rules.value = rules.value.map((item) => (item.key === updated.key ? updated : item))
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function openCreate() {
  editingKey.value = ''
  form.key = ''
  form.category = 'system'
  form.title = ''
  form.description = ''
  form.content = ''
  form.payloadText = ''
  form.enabled = true
  showPayload.value = true
  formError.value = ''
  showEditor.value = true
}

function openEdit(item) {
  editingKey.value = item.key
  form.key = item.key
  form.category = item.category || 'system'
  form.title = item.title || ''
  form.description = item.description || ''
  form.content = item.content || ''
  form.payloadText = isPlainObject(item.payload) && Object.keys(item.payload).length
    ? JSON.stringify(item.payload, null, 2)
    : ''
  form.enabled = item.enabled !== false
  showPayload.value = Boolean(form.payloadText)
  formError.value = ''
  showEditor.value = true
}

async function toggleEnabled(item, value) {
  if (!props.canManage) return
  const previous = item.enabled !== false
  item.enabled = value
  busyKey.value = item.key
  try {
    const updated = await api.put(`/writing-rules/prompts/${encodeURIComponent(item.key)}`, { enabled: value })
    replaceRule(updated)
  } catch (err) {
    item.enabled = previous
    error.value = err.message
    toast(err.message, 'error')
  } finally {
    busyKey.value = ''
  }
}

function buildPayload() {
  const text = form.payloadText.trim()
  if (!text) return { ok: true, payload: undefined }
  try {
    const parsed = JSON.parse(text)
    if (!isPlainObject(parsed)) {
      return { ok: false, message: '结构化 payload 必须是 JSON 对象，例如 {"buzzwords": []}' }
    }
    return { ok: true, payload: parsed }
  } catch (err) {
    return { ok: false, message: `结构化 payload 不是合法 JSON：${err.message}` }
  }
}

async function save() {
  if (!props.canManage) return
  formError.value = ''
  const key = form.key.trim()
  if (!editingKey.value) {
    if (key.length < 3) {
      formError.value = 'key 至少 3 个字符'
      return
    }
    if (/\s/.test(key) || key.includes('..')) {
      formError.value = 'key 不能包含空白字符或连续的英文点'
      return
    }
    if (!CATEGORY_LABELS[form.category]) {
      formError.value = '请选择合法的规则分类'
      return
    }
  }
  if (!form.title.trim()) {
    formError.value = '请填写规则标题'
    return
  }
  // 词表类（tone_lexicon）出厂数据的正文本来就是空的，内容全在结构化 payload 里；
  // 其余分类才要求正文非空。
  if (form.category === 'tone_lexicon') {
    if (!form.payloadText.trim()) {
      formError.value = '词表规则需要填写结构化 payload（如 {"phrases": [...]}）'
      return
    }
  } else if (!form.content.trim()) {
    formError.value = '请填写规则正文'
    return
  }
  const parsed = buildPayload()
  if (!parsed.ok) {
    formError.value = parsed.message
    return
  }

  saving.value = true
  try {
    if (editingKey.value) {
      const body = {
        title: form.title.trim(),
        description: form.description.trim(),
        content: form.content,
        enabled: form.enabled,
      }
      if (parsed.payload !== undefined) body.payload = parsed.payload
      const updated = await api.put(
        `/writing-rules/prompts/${encodeURIComponent(editingKey.value)}`,
        body,
      )
      replaceRule(updated)
      toast('已保存')
    } else {
      const created = await api.post('/writing-rules/prompts', {
        key,
        category: form.category,
        title: form.title.trim(),
        description: form.description.trim(),
        content: form.content,
        payload: parsed.payload || {},
        enabled: form.enabled,
      })
      if (created && created.key) rules.value = [created, ...rules.value]
      else await load()
      toast('已新增规则')
    }
    showEditor.value = false
  } catch (err) {
    formError.value = err.message
  } finally {
    saving.value = false
  }
}

async function remove(item) {
  if (!props.canManage || item.source !== 'user') return
  const ok = await confirm(
    `删除后无法恢复，确定删除规则「${item.title}」吗？`,
    '删除提示词规则',
  )
  if (!ok) return
  try {
    await api.del(`/writing-rules/prompts/${encodeURIComponent(item.key)}`)
    rules.value = rules.value.filter((row) => row.key !== item.key)
    toast('已删除')
  } catch (err) {
    error.value = err.message
    toast(err.message, 'error')
  }
}

onMounted(load)
</script>

<template>
  <div class="prompt-rules">
    <div class="panel-head">
      <div>
        <h2 class="panel-title">提示词规则</h2>
        <p class="panel-sub">
          控制文章输出契约、系统规则与修复提示词。改动会立刻影响后续生成与预览。
        </p>
      </div>
      <n-button v-if="canManage" type="primary" size="small" @click="openCreate">新增规则</n-button>
    </div>

    <n-alert v-if="error" type="error" :bordered="false" closable @close="error = ''">
      {{ error }}
    </n-alert>

    <div v-if="loading" class="skeleton-stack">
      <div class="skeleton" style="height: 72px" />
      <div class="skeleton" style="height: 72px" />
      <div class="skeleton" style="height: 72px" />
    </div>

    <n-empty v-else-if="!rules.length" description="还没有提示词规则，规则会回落到代码内置常量。">
      <template v-if="canManage" #extra>
        <n-button type="primary" size="small" @click="openCreate">新增第一条规则</n-button>
      </template>
    </n-empty>

    <section v-for="group in groups" :key="group.key" class="rule-group">
      <header class="group-head">
        <h3 class="group-title">{{ group.label }}</h3>
        <span class="field-hint">{{ group.items.length }} 条</span>
      </header>

      <div class="rule-list">
        <article v-for="item in group.items" :key="item.key" class="rule-row">
          <div class="rule-main">
            <div class="rule-title-line">
              <span class="rule-title">{{ item.title }}</span>
              <n-tag size="small" :bordered="false" :type="item.source === 'user' ? 'info' : 'default'">
                {{ item.source === 'user' ? '自定义' : '出厂' }}
              </n-tag>
              <n-tag size="small" :bordered="false">v{{ item.version || 1 }}</n-tag>
              <span v-if="item.enabled === false" class="tag">已停用</span>
            </div>
            <div class="rule-key">{{ item.key }}</div>
            <p v-if="item.description" class="rule-desc">{{ item.description }}</p>
          </div>

          <div class="rule-actions">
            <n-switch
              :value="item.enabled !== false"
              size="small"
              :disabled="!canManage"
              :loading="busyKey === item.key"
              :title="canManage ? '启用 / 停用该规则' : '需要 writing_rules.manage 权限'"
              @update:value="(value) => toggleEnabled(item, value)"
            />
            <n-button v-if="canManage" quaternary size="small" @click="openEdit(item)">编辑</n-button>
            <n-button
              v-if="canManage && item.source === 'user'"
              quaternary
              type="error"
              size="small"
              @click="remove(item)"
            >删除</n-button>
          </div>
        </article>
      </div>
    </section>

    <n-drawer v-model:show="showEditor" :width="640" placement="right" style="max-width: 100vw">
      <n-drawer-content :title="editorTitle" closable :native-scrollbar="false">
        <n-alert v-if="formError" type="error" :bordered="false" class="form-alert">
          {{ formError }}
        </n-alert>

        <n-form label-placement="top">
          <n-form-item label="key">
            <n-input
              v-model:value="form.key"
              :disabled="Boolean(editingKey)"
              placeholder="例如：article.system.base"
              :input-props="{ spellcheck: false }"
            />
            <span class="field-hint">
              至少 3 个字符，不能含空白或连续的英文点；保存后 key 不可修改。
            </span>
          </n-form-item>

          <n-form-item label="分类">
            <n-select v-model:value="form.category" :options="CATEGORY_OPTIONS" :disabled="Boolean(editingKey)" />
          </n-form-item>

          <n-form-item label="标题">
            <n-input v-model:value="form.title" maxlength="120" show-count placeholder="给规则起一个可读的名字" />
          </n-form-item>

          <n-form-item label="说明（可选）">
            <n-input v-model:value="form.description" maxlength="200" show-count placeholder="这条规则约束了什么" />
          </n-form-item>

          <n-alert v-if="isContract" type="warning" :bordered="false" class="form-alert">
            必须包含 title、markdown、summary、coverPrompt、inlinePrompts 五个字段名，否则保存会被拒绝。
          </n-alert>

          <n-form-item label="正文">
            <n-input
              v-model:value="form.content"
              type="textarea"
              class="mono-input"
              :rows="12"
              placeholder="规则正文（纯文本 / Markdown），生成时按顺序注入提示词"
              :input-props="{ spellcheck: false }"
            />
          </n-form-item>

          <n-form-item v-if="showPayload" label="结构化 payload（JSON，可选）">
            <n-input
              v-model:value="form.payloadText"
              type="textarea"
              class="mono-input"
              :rows="8"
              placeholder='留空表示不携带结构化部分，例如 {"buzzwords": ["赋能", "闭环"]}'
              :input-props="{ spellcheck: false }"
            />
            <span class="field-hint">必须是 JSON 对象；格式错误时不会提交请求。</span>
          </n-form-item>

          <n-form-item label="启用">
            <n-switch v-model:value="form.enabled" />
            <span class="field-hint">停用的规则不会参与提示词拼装。</span>
          </n-form-item>
        </n-form>

        <template #footer>
          <div class="drawer-footer">
            <n-button quaternary :disabled="saving" @click="showEditor = false">取消</n-button>
            <n-button type="primary" :loading="saving" :disabled="!canManage" @click="save">保存</n-button>
          </div>
        </template>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<style scoped>
.prompt-rules {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
}
.panel-head {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}
.panel-title {
  margin: 0;
  font-size: var(--font-section);
  font-weight: 600;
}
.panel-sub {
  margin: var(--space-1) 0 0;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
}
.skeleton-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.rule-group {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  overflow: hidden;
}
.group-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px dashed var(--color-border);
}
.group-title {
  margin: 0;
  font-size: var(--font-sm);
  font-weight: 600;
}
.rule-list {
  display: flex;
  flex-direction: column;
}
.rule-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.rule-row:last-child {
  border-bottom: none;
}
.rule-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.rule-title-line {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.rule-title {
  font-size: var(--font-body);
  font-weight: 600;
  word-break: break-word;
}
.rule-key {
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace;
  font-size: var(--font-xs);
  color: var(--color-text-muted, var(--color-text-secondary));
  word-break: break-all;
}
.rule-desc {
  margin: 0;
  font-size: var(--font-xs);
  line-height: 1.6;
  color: var(--color-text-secondary);
  word-break: break-word;
}
.rule-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}
.form-alert {
  margin-bottom: var(--space-3);
}
.mono-input :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace;
  font-size: var(--font-sm);
  line-height: 1.7;
}
.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
@media (max-width: 767px) {
  .rule-row {
    flex-direction: column;
    align-items: stretch;
  }
  .rule-actions {
    justify-content: flex-end;
  }
}
</style>
