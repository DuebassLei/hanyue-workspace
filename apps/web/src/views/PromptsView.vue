<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { NAlert, NButton, NEmpty, NForm, NFormItem, NInput, NModal, NSelect, NSpace } from 'naive-ui'
import { api } from '../api/client'
import { useRouter } from 'vue-router'
import { toast } from '../ui/feedback'

const router = useRouter()
const DEFAULT_CATEGORY = '通用'

const prompts = ref([])
const loading = ref(true)
const error = ref('')
const keyword = ref('')
const activeCategory = ref(null)

const showEditor = ref(false)
const editingId = ref('')
const saving = ref(false)
const form = reactive({ title: '', category: DEFAULT_CATEGORY, tags: '', content: '' })

const copiedId = ref('')
const confirmingId = ref('')
let copyTimer = null
let confirmTimer = null

const categories = computed(() => {
  const set = new Set([DEFAULT_CATEGORY])
  for (const item of prompts.value) {
    if (item.category) set.add(item.category)
  }
  return [...set]
})

const categoryOptions = computed(() => [
  { label: '全部分类', value: null },
  ...categories.value.map((item) => ({ label: item, value: item })),
])

const filtered = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  return prompts.value.filter((item) => {
    if (activeCategory.value && item.category !== activeCategory.value) return false
    if (!q) return true
    const haystack = [item.title, item.content, item.category, ...(item.tags || [])]
      .join(' ')
      .toLowerCase()
    return haystack.includes(q)
  })
})

const editorTitle = computed(() => (editingId.value ? '编辑提示词' : '新建提示词'))

async function load() {
  loading.value = true
  error.value = ''
  try {
    prompts.value = await api.get('/prompts')
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function resetFilter() {
  keyword.value = ''
  activeCategory.value = null
}

function goHanddraw() {
  router.push({ name: 'handdraw' })
}

function openCreate() {
  editingId.value = ''
  form.title = ''
  form.category = DEFAULT_CATEGORY
  form.tags = ''
  form.content = ''
  showEditor.value = true
}

function openEdit(item) {
  editingId.value = item.id
  form.title = item.title
  form.category = item.category || DEFAULT_CATEGORY
  form.tags = (item.tags || []).join(', ')
  form.content = item.content
  showEditor.value = true
}

function parseTags(value) {
  const tags = []
  for (const part of String(value || '').split(/[,，]/)) {
    const tag = part.trim()
    if (tag && !tags.includes(tag)) tags.push(tag)
  }
  return tags
}

async function save() {
  error.value = ''
  if (!form.title.trim()) {
    error.value = '请填写提示词名称'
    return
  }
  if (!form.content.trim()) {
    error.value = '请填写提示词内容'
    return
  }
  const payload = {
    title: form.title.trim(),
    category: form.category.trim() || DEFAULT_CATEGORY,
    tags: parseTags(form.tags),
    content: form.content.trim(),
  }
  saving.value = true
  try {
    if (editingId.value) {
      const updated = await api.patch(`/prompts/${editingId.value}`, payload)
      prompts.value = [updated, ...prompts.value.filter((item) => item.id !== updated.id)]
      toast(`已更新「${updated.title}」`)
    } else {
      const created = await api.post('/prompts', payload)
      prompts.value = [created, ...prompts.value]
      toast(`已新建「${created.title}」，可在图片生成页直接套用`)
    }
    showEditor.value = false
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

async function remove(item) {
  // 二次确认放在按钮上，避免误删模板
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
    await api.del(`/prompts/${item.id}`)
    prompts.value = prompts.value.filter((row) => row.id !== item.id)
    toast(`已删除「${item.title}」`)
  } catch (err) {
    error.value = err.message
  }
}

async function copyPrompt(item) {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(item.content)
    copiedId.value = item.id
    if (copyTimer) clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      copiedId.value = ''
      copyTimer = null
    }, 1600)
  } catch {
    error.value = '复制失败，请打开编辑后手动选中提示词复制'
  }
}

onMounted(load)
onBeforeUnmount(() => {
  if (copyTimer) clearTimeout(copyTimer)
  if (confirmTimer) clearTimeout(confirmTimer)
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">提示词库</h1>
        <p class="page-sub">维护常用生图提示词模板，在图片生成页一键套用或追加。</p>
      </div>
      <n-button type="primary" @click="openCreate">新建提示词</n-button>
    </div>

    <n-space class="prompt-tabs" role="tablist" aria-label="提示词库视图" :size="8">
      <n-button type="primary" size="small" role="tab" aria-selected="true" disabled>
        我的提示词
      </n-button>
      <n-button secondary size="small" role="tab" aria-selected="false" @click="goHanddraw">
        手绘风格
      </n-button>
    </n-space>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <section class="card">
      <div class="row prompt-toolbar">
        <n-input
          v-model:value="keyword"
          class="prompt-search"
          type="text"
          clearable
          placeholder="搜索名称、内容或标签"
          aria-label="搜索提示词"
        />
        <n-select
          v-model:value="activeCategory"
          class="prompt-filter"
          :options="categoryOptions"
          placeholder="全部分类"
          aria-label="按分类筛选"
        />
        <div class="spacer" />
        <span class="field-hint">{{ filtered.length }} / {{ prompts.length }} 条</span>
      </div>

      <div v-if="loading" class="skeleton" style="height: 120px; margin-top: var(--space-3)" />
      <div v-else-if="!prompts.length" class="empty">
        <n-empty description="把常用的生图描述存成模板，下次生成时可直接套用、追加或微调。">
          <template #extra>
            <n-button type="primary" size="small" @click="openCreate">新建第一条提示词</n-button>
          </template>
        </n-empty>
      </div>
      <div v-else-if="!filtered.length" class="empty">
        <n-empty description="换个关键词，或清空分类筛选再试试。">
          <template #extra>
            <n-button secondary size="small" @click="resetFilter">清空筛选</n-button>
          </template>
        </n-empty>
      </div>
      <div v-else class="prompt-grid">
        <article v-for="item in filtered" :key="item.id" class="prompt-card">
          <header class="prompt-head">
            <div class="prompt-title" :title="item.title">{{ item.title }}</div>
            <span class="tag tag-accent">{{ item.category }}</span>
          </header>
          <p class="prompt-content">{{ item.content }}</p>
          <div v-if="(item.tags || []).length" class="row row-tight prompt-tags">
            <span v-for="tag in item.tags" :key="tag" class="tag">{{ tag }}</span>
          </div>
          <footer class="prompt-actions">
            <n-button quaternary size="small" @click="copyPrompt(item)">
              {{ copiedId === item.id ? '已复制' : '复制' }}
            </n-button>
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
      style="width: min(560px, calc(100vw - 32px))"
      :mask-closable="!saving"
    >
      <n-form label-placement="top">
        <n-form-item label="名称">
          <n-input
            v-model:value="form.title"
            maxlength="120"
            placeholder="例如：水彩窗台橘猫"
          />
        </n-form-item>
        <n-form-item label="分类">
          <n-input
            v-model:value="form.category"
            list="prompt-categories"
            maxlength="32"
            placeholder="选择已有分类或直接输入"
          />
          <datalist id="prompt-categories">
            <option v-for="item in categories" :key="item" :value="item" />
          </datalist>
        </n-form-item>
        <n-form-item label="标签（可选）">
          <n-input v-model:value="form.tags" placeholder="用逗号分隔，例如：水彩, 治愈, 封面" />
        </n-form-item>
        <n-form-item label="提示词内容">
          <n-input
            v-model:value="form.content"
            type="textarea"
            :autosize="{ minRows: 6 }"
            placeholder="例如：一只橘猫坐在木质窗台上晒太阳，水彩插画风格，柔和光线，干净背景"
          />
          <span class="field-hint">在图片生成页可从提示词库直接套用或追加这段内容。</span>
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
.prompt-tabs {
  margin-bottom: var(--space-3);
  width: fit-content;
}
.prompt-toolbar {
  align-items: center;
}
.prompt-search {
  flex: 1 1 220px;
  min-width: 0;
  max-width: 360px;
}
.prompt-filter {
  width: 160px;
}
.prompt-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-3);
}
.prompt-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-raised);
}
.prompt-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
.prompt-title {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.prompt-content {
  margin: 0;
  font-size: var(--font-sm);
  line-height: 1.65;
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.prompt-tags {
  flex-wrap: wrap;
}
.prompt-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: auto;
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
}
@media (max-width: 767px) {
  .prompt-search,
  .prompt-filter {
    flex: 1 1 100%;
    max-width: none;
    width: 100%;
  }
}
</style>
