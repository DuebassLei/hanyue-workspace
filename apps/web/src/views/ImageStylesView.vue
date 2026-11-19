<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
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
  NSpace,
  NSwitch,
} from 'naive-ui'
import { api } from '../api/client'
import { toast } from '../ui/feedback'

const PURPOSE_LABELS = { base: '基础风格', cover: '封面', inline: '配图' }
const PURPOSE_OPTIONS = [
  { label: '基础风格', value: 'base' },
  { label: '封面', value: 'cover' },
  { label: '配图', value: 'inline' },
]
const FILTER_OPTIONS = [{ label: '全部', value: 'all' }, ...PURPOSE_OPTIONS]

const styles = ref([])
const loading = ref(true)
const error = ref('')
const purposeFilter = ref('all')

const showEditor = ref(false)
const editingId = ref('')
const saving = ref(false)
const form = reactive({
  name: '',
  purpose: 'base',
  prompt: '',
  summary: '',
  sortOrder: 0,
  enabled: true,
})

const confirmingId = ref('')
let confirmTimer = null

const filtered = computed(() => {
  const rows = purposeFilter.value === 'all'
    ? styles.value
    : styles.value.filter((item) => item.purpose === purposeFilter.value)
  return [...rows].sort((a, b) => (a.sortOrder - b.sortOrder) || String(a.id).localeCompare(String(b.id)))
})

const editorTitle = computed(() => (editingId.value ? '编辑风格' : '新建风格'))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const groups = await Promise.all(
      PURPOSE_OPTIONS.map((option) => api.get(`/image-styles?purpose=${option.value}&includeDisabled=1`)),
    )
    styles.value = groups.flat()
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = ''
  form.name = ''
  form.purpose = purposeFilter.value === 'all' ? 'base' : purposeFilter.value
  form.prompt = ''
  form.summary = ''
  form.sortOrder = 0
  form.enabled = true
  showEditor.value = true
}

function openEdit(item) {
  editingId.value = item.id
  form.name = item.name
  form.purpose = item.purpose
  form.prompt = item.prompt
  form.summary = item.summary || ''
  form.sortOrder = item.sortOrder ?? 0
  form.enabled = item.enabled !== false
  showEditor.value = true
}

async function save() {
  error.value = ''
  if (!form.name.trim()) {
    error.value = '请填写风格名称'
    return
  }
  if (!form.prompt.trim()) {
    error.value = '请填写提示词正文'
    return
  }
  const payload = {
    name: form.name.trim(),
    purpose: form.purpose,
    prompt: form.prompt.trim(),
    summary: form.summary.trim(),
    sortOrder: Number(form.sortOrder) || 0,
    enabled: form.enabled,
  }
  saving.value = true
  try {
    if (editingId.value) {
      const updated = await api.patch(`/image-styles/${editingId.value}`, payload)
      styles.value = styles.value.map((item) => (item.id === updated.id ? updated : item))
      toast(`已更新「${updated.name}」`)
    } else {
      const created = await api.post('/image-styles', payload)
      styles.value = [...styles.value, created]
      toast(`已新建「${created.name}」`)
    }
    showEditor.value = false
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

async function remove(item) {
  if (confirmingId.value !== item.id) {
    confirmingId.value = item.id
    if (confirmTimer) clearTimeout(confirmTimer)
    confirmTimer = setTimeout(() => {
      confirmingId.value = ''
      confirmTimer = null
    }, 3000)
    return
  }
  if (confirmTimer) clearTimeout(confirmTimer)
  confirmingId.value = ''
  error.value = ''
  try {
    await api.del(`/image-styles/${item.id}`)
    styles.value = styles.value.filter((row) => row.id !== item.id)
    toast(`已删除「${item.name}」`)
  } catch (err) {
    error.value = err.message
  }
}

onMounted(load)
onBeforeUnmount(() => {
  if (confirmTimer) clearTimeout(confirmTimer)
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">风格库</h1>
        <p class="page-sub">维护基础风格、封面和配图提示词。停用后不再出现在创作空间的选择里。</p>
      </div>
      <n-button type="primary" @click="openCreate">新建风格</n-button>
    </div>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <section class="card">
      <div class="row style-toolbar">
        <n-select
          v-model:value="purposeFilter"
          class="style-filter"
          :options="FILTER_OPTIONS"
          aria-label="按用途筛选"
        />
        <div class="spacer" />
        <span class="field-hint">{{ filtered.length }} / {{ styles.length }} 条</span>
      </div>

      <div v-if="loading" class="skeleton" style="height: 120px; margin-top: var(--space-3)" />
      <div v-else-if="!styles.length" class="empty">
        <n-empty description="还没有风格">
          <template #extra>
            <n-button type="primary" size="small" @click="openCreate">新建第一条风格</n-button>
          </template>
        </n-empty>
      </div>
      <div v-else-if="!filtered.length" class="empty">
        <n-empty description="这个用途下还没有风格。">
          <template #extra>
            <n-button secondary size="small" @click="purposeFilter = 'all'">查看全部</n-button>
          </template>
        </n-empty>
      </div>
      <div v-else class="style-grid">
        <article v-for="item in filtered" :key="item.id" class="style-card">
          <header class="style-head">
            <div class="style-title" :title="item.name">{{ item.name }}</div>
            <span class="tag tag-accent">{{ PURPOSE_LABELS[item.purpose] || item.purpose }}</span>
            <span v-if="!item.enabled" class="tag">已停用</span>
          </header>
          <p v-if="item.summary" class="style-summary">{{ item.summary }}</p>
          <p class="style-content">{{ item.prompt }}</p>
          <footer class="style-actions">
            <span class="field-hint">排序 {{ item.sortOrder }}</span>
            <div class="spacer" />
            <n-button quaternary size="small" @click="openEdit(item)">编辑</n-button>
            <n-button type="error" size="small" @click="remove(item)">
              {{ confirmingId === item.id ? '确认删除' : '删除' }}
            </n-button>
          </footer>
        </article>
      </div>
    </section>

    <n-modal
      v-model:show="showEditor"
      preset="card"
      :title="editorTitle"
      class="style-editor"
      style="width: min(720px, calc(100vw - 32px))"
      :mask-closable="!saving"
    >
      <n-form label-placement="top" class="style-editor-form">
        <n-form-item label="名称">
          <n-input v-model:value="form.name" maxlength="40" show-count placeholder="例如：杂志插画" />
        </n-form-item>
        <div class="style-editor-row">
          <n-form-item label="用途">
            <n-select v-model:value="form.purpose" :options="PURPOSE_OPTIONS" />
          </n-form-item>
          <n-form-item label="排序">
            <n-input-number v-model:value="form.sortOrder" :min="0" :max="9999" style="width: 100%" />
          </n-form-item>
        </div>
        <n-form-item label="说明">
          <n-input v-model:value="form.summary" maxlength="120" show-count placeholder="给创作者看的一句话说明，可选" />
        </n-form-item>
        <n-form-item label="提示词正文" class="style-editor-body">
          <n-input
            v-model:value="form.prompt"
            type="textarea"
            maxlength="2000"
            show-count
            placeholder="会拼在画面描述前面，例如：editorial illustration, clean composition"
            :input-props="{ spellcheck: false }"
          />
        </n-form-item>
        <n-form-item label="启用">
          <n-switch v-model:value="form.enabled" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary :disabled="saving" @click="showEditor = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="save">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.style-toolbar {
  align-items: center;
}
.style-filter {
  width: 160px;
}
.style-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-3);
}
.style-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
}
.style-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
.style-title {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.style-summary,
.style-content {
  margin: 0;
  font-size: var(--font-sm);
  line-height: 1.65;
  word-break: break-word;
}
.style-summary {
  color: var(--color-text);
}
.style-content {
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.style-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: auto;
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
}
.style-editor-form {
  display: flex;
  flex-direction: column;
  max-height: min(68vh, 640px);
  min-height: 0;
}
.style-editor-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 140px);
  gap: var(--space-3);
}
.style-editor-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.style-editor-body :deep(.n-form-item-blank) {
  flex: 1 1 auto;
  min-height: 140px;
  align-items: stretch;
}
.style-editor-body :deep(.n-input) {
  height: 100%;
}
.style-editor-body :deep(.n-input-wrapper) {
  height: 100%;
  align-items: stretch;
}
.style-editor-body :deep(textarea) {
  height: 100% !important;
  max-height: none;
  resize: none;
  line-height: 1.7;
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace;
  font-size: var(--font-sm);
}
@media (max-width: 767px) {
  .style-filter {
    flex: 1 1 100%;
    width: 100%;
  }
  .style-editor-row {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
