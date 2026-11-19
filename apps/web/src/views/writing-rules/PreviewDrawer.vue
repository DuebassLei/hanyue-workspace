<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  NAlert, NButton, NDrawer, NDrawerContent, NEmpty, NInput, NRadio, NRadioGroup, NSpin, NTag,
} from 'naive-ui'
import { api } from '../../api/client'
import { toast } from '../../ui/feedback'

const props = defineProps({
  show: Boolean,
  skillKey: String,
  scope: String,
})
const emit = defineEmits(['update:show'])

const KIND_OPTIONS = [
  { label: '文章提示词', value: 'article' },
  { label: '大纲提示词', value: 'outline' },
  { label: '修复提示词', value: 'repair' },
]

const kind = ref('article')
const sample = ref('')
const text = ref('')
const ruleSources = ref([])
const loading = ref(false)
const error = ref('')
const copied = ref(false)
let copyTimer = null
let requestSeq = 0

const scopeLabel = computed(() => (props.scope === 'personal' ? '我的副本' : '全局'))
const skillLabel = computed(() => props.skillKey || '未选择 Skill')
const hasText = computed(() => Boolean(text.value && text.value.trim()))

function buildPath() {
  const params = new URLSearchParams()
  if (props.skillKey) params.set('skillKey', props.skillKey)
  params.set('scope', props.scope === 'personal' ? 'personal' : 'global')
  params.set('kind', kind.value)
  if (kind.value === 'repair' && sample.value.trim()) params.set('sample', sample.value.trim())
  return `/writing-rules/preview?${params.toString()}`
}

async function load() {
  if (!props.show) return
  const seq = ++requestSeq
  loading.value = true
  error.value = ''
  try {
    const data = await api.get(buildPath())
    // 快速切换 kind / skill 时丢弃过期响应，避免旧内容覆盖新选择
    if (seq !== requestSeq) return
    text.value = data?.text || ''
    ruleSources.value = Array.isArray(data?.ruleSources) ? data.ruleSources : []
  } catch (err) {
    if (seq !== requestSeq) return
    error.value = err.message
    text.value = ''
    ruleSources.value = []
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function close() {
  emit('update:show', false)
}

async function copyText() {
  if (!hasText.value) return
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(text.value)
    copied.value = true
    toast('已复制')
    if (copyTimer) clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      copied.value = false
      copyTimer = null
    }, 1600)
  } catch {
    toast('复制失败，请手动选中内容复制', 'error')
  }
}

watch(
  () => props.show,
  (value) => {
    if (value) load()
  },
  { immediate: true },
)
// 生效范围变化时立刻重新拉取，保证抽屉里看到的就是当前选择
watch(
  [() => props.skillKey, () => props.scope, kind],
  () => {
    if (props.show) load()
  },
)

onBeforeUnmount(() => {
  if (copyTimer) clearTimeout(copyTimer)
})
</script>

<template>
  <n-drawer
    :show="show"
    :width="720"
    placement="right"
    style="max-width: 100vw"
    @update:show="(value) => emit('update:show', value)"
  >
    <n-drawer-content :native-scrollbar="false" closable>
      <template #header>
        <div class="preview-head">
          <div class="preview-title">预览最终提示词</div>
          <div class="preview-sub">{{ skillLabel }} · {{ scopeLabel }}</div>
        </div>
      </template>

      <div class="preview-toolbar">
        <n-radio-group v-model:value="kind" size="small" name="preview-kind">
          <n-radio v-for="option in KIND_OPTIONS" :key="option.value" :value="option.value">
            {{ option.label }}
          </n-radio>
        </n-radio-group>
        <div class="spacer" />
        <n-button size="small" quaternary :loading="loading" @click="load">重新生成</n-button>
      </div>

      <div v-if="kind === 'repair'" class="sample-block">
        <span class="field-label">草稿样本（可选）</span>
        <n-input
          v-model:value="sample"
          type="textarea"
          maxlength="2000"
          show-count
          :autosize="{ minRows: 3, maxRows: 6 }"
          placeholder="粘贴一段待修复的草稿，预览时会拼进修复提示词"
        />
      </div>

      <n-alert v-if="error" type="error" :bordered="false" class="preview-alert">
        {{ error }}
      </n-alert>

      <div class="preview-body">
        <n-spin v-if="loading" size="small" />
        <pre v-else-if="hasText" class="preview-text">{{ text }}</pre>
        <n-empty v-else description="当前没有可预览的提示词内容。" />
      </div>

      <template #footer>
        <div class="preview-footer">
          <div class="sources">
            <span class="field-label">规则来源</span>
            <div v-if="ruleSources.length" class="source-list">
              <n-tag
                v-for="source in ruleSources"
                :key="source.key"
                size="small"
                :bordered="false"
                :type="source.enabled === false ? 'default' : 'success'"
              >
                {{ source.key }} · v{{ source.version || 1 }} · {{ source.enabled === false ? '停用' : '启用' }}
              </n-tag>
            </div>
            <span v-else class="field-hint">使用代码内置常量（未落库）</span>
          </div>
          <n-button type="primary" size="small" :disabled="!hasText" @click="copyText">
            {{ copied ? '已复制' : '复制' }}
          </n-button>
        </div>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.preview-head {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}
.preview-title {
  font-size: var(--font-section);
  font-weight: 600;
}
.preview-sub {
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
  word-break: break-all;
}
.preview-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.sample-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin-top: var(--space-3);
}
.preview-alert {
  margin-top: var(--space-3);
}
.preview-body {
  margin-top: var(--space-3);
  padding: var(--space-3);
  min-height: 200px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
  overflow: auto;
}
.preview-text {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace;
  font-size: var(--font-sm);
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--color-text-secondary);
}
.preview-footer {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}
.sources {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.source-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
</style>
