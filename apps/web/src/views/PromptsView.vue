<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert, NButton, NEmpty, NForm, NFormItem, NInput, NInputNumber,
  NModal, NSelect, NSpace, NSwitch,
} from 'naive-ui'
import { api, fetchBlobUrl } from '../api/client'
import { usePermission } from '../composables/usePermission'
import { toast } from '../ui/feedback'

const router = useRouter()
const route = useRoute()
const { disabledReason } = usePermission()
// 统一提示词库全部全局：增删改与生成示例图仅管理员可用，普通用户只读。
const manageDenied = computed(() => disabledReason('image_styles.manage', '管理提示词库'))
const canManage = computed(() => !manageDenied.value)

const DEFAULT_CATEGORY = '通用'
const TYPE_OPTIONS = [
  { label: '全部', value: '' },
  { label: '普通', value: 'normal' },
  { label: '封面图', value: 'cover' },
  { label: '文章配图', value: 'illustration' },
  { label: '基础风格', value: 'base' },
  { label: '分镜', value: 'comic_panel' },
  { label: '参考', value: 'reference' },
]
const TYPE_LABELS = {
  normal: '普通', cover: '封面图', illustration: '文章配图',
  base: '基础风格', comic_panel: '分镜', reference: '参考',
}
const ASPECT_OPTIONS = [
  { label: '3:4', value: '3:4' },
  { label: '1:1', value: '1:1' },
  { label: '16:9', value: '16:9' },
  { label: '9:16', value: '9:16' },
]

const prompts = ref([])
const loading = ref(true)
const error = ref('')
const keyword = ref('')
const activeType = ref('')
const activeCategory = ref(null)
const previewUrls = ref({})

const showEditor = ref(false)
const editingId = ref('')
const saving = ref(false)
const form = reactive({
  title: '', category: DEFAULT_CATEGORY, tags: '', content: '', negativeContent: '',
  promptType: 'normal', summary: '', sortOrder: 0, enabled: true,
  aspectRatio: '3:4', previewAssetIds: [],
})

// 示例图生成状态
const previewBusy = ref(false)
const previewStatus = ref('') // queued / succeeded / failed
const previewMessage = ref('')
let previewJobId = ''
let previewPollTimer = null
let previewPollRounds = 0

const previewModalOpen = ref(false)
const previewImage = ref('')
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
    if (activeType.value && item.promptType !== activeType.value) return false
    if (activeCategory.value && item.category !== activeCategory.value) return false
    if (!q) return true
    const haystack = [
      item.title, item.content, item.negativeContent, item.category,
      TYPE_LABELS[item.promptType] || '', ...(item.tags || []),
    ].join(' ').toLowerCase()
    return haystack.includes(q)
  })
})
const editorTitle = computed(() => (editingId.value ? '编辑提示词' : '新建提示词'))
const contentCount = computed(() => form.content.length)

function syncPrompt(updated) {
  prompts.value = [updated, ...prompts.value.filter((item) => item.id !== updated.id)]
}

async function resolvePreviewUrls() {
  const ids = [...new Set(prompts.value.flatMap((item) => item.previewAssetIds || []))]
  for (const id of ids) {
    if (previewUrls.value[id]) continue
    try {
      previewUrls.value[id] = await fetchBlobUrl(`/assets/${id}/raw`)
    } catch {
      // 素材可能已被删除；忽略缩略图，卡片仍可用
    }
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    prompts.value = await api.get('/prompts')
    await resolvePreviewUrls()
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function applyQueryType() {
  const value = String(route.query.type || '')
  if (TYPE_OPTIONS.some((option) => option.value === value)) activeType.value = value
}

watch(() => route.query.type, applyQueryType)

function resetFilter() {
  keyword.value = ''
  activeType.value = ''
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
  form.negativeContent = ''
  form.promptType = activeType.value || 'normal'
  form.summary = ''
  form.sortOrder = 0
  form.enabled = true
  form.aspectRatio = '3:4'
  form.previewAssetIds = []
  previewStatus.value = ''
  previewMessage.value = ''
  showEditor.value = true
}

function openEdit(item) {
  editingId.value = item.id
  form.title = item.title
  form.category = item.category || DEFAULT_CATEGORY
  form.tags = (item.tags || []).join(', ')
  form.content = item.content
  form.negativeContent = item.negativeContent || ''
  form.promptType = item.promptType || 'normal'
  form.summary = item.summary || ''
  form.sortOrder = item.sortOrder || 0
  form.enabled = item.enabled !== false
  form.aspectRatio = '3:4'
  form.previewAssetIds = [...(item.previewAssetIds || [])]
  previewStatus.value = ''
  previewMessage.value = ''
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
    error.value = '请填写正向提示词内容'
    return
  }
  const payload = {
    title: form.title.trim(),
    category: form.category.trim() || DEFAULT_CATEGORY,
    tags: parseTags(form.tags),
    content: form.content.trim(),
    negativeContent: form.negativeContent.trim(),
    promptType: form.promptType,
    summary: form.summary.trim(),
    sortOrder: Number(form.sortOrder) || 0,
    enabled: form.enabled,
    previewAssetIds: form.previewAssetIds,
  }
  saving.value = true
  try {
    if (editingId.value) {
      const updated = await api.patch(`/prompts/${editingId.value}`, payload)
      syncPrompt(updated)
      toast(`已更新「${updated.title}」`)
    } else {
      const created = await api.post('/prompts', payload)
      syncPrompt(created)
      toast(`已新建「${created.title}」，可在图片生成页直接套用`)
    }
    showEditor.value = false
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

function stopPreviewPoll() {
  if (previewPollTimer) clearInterval(previewPollTimer)
  previewPollTimer = null
}

async function onPreviewSucceeded(job) {
  const ids = (job.result?.assetIds || []).filter(Boolean)
  previewBusy.value = false
  if (!ids.length) {
    previewStatus.value = 'failed'
    previewMessage.value = '出图成功但没有返回素材，请重试'
    return
  }
  form.previewAssetIds = [...new Set([...(form.previewAssetIds || []), ...ids])]
  const note = job.result?.negativePromptMerged ? '（负向词已并入正向文本）' : ''
  try {
    const updated = await api.patch(`/prompts/${editingId.value}`, {
      previewAssetIds: form.previewAssetIds,
    })
    syncPrompt(updated)
    await resolvePreviewUrls()
    previewStatus.value = 'succeeded'
    previewMessage.value = `示例图已生成并保存${note}`
    toast(previewMessage.value)
  } catch (err) {
    previewStatus.value = 'failed'
    previewMessage.value = `示例图已生成，但回写失败：${err.message}`
  }
}

function startPreviewPoll() {
  stopPreviewPoll()
  previewPollRounds = 0
  previewPollTimer = setInterval(async () => {
    previewPollRounds += 1
    try {
      const job = await api.get(`/jobs/${previewJobId}`)
      if (job.status === 'succeeded') {
        stopPreviewPoll()
        await onPreviewSucceeded(job)
      } else if (job.status === 'failed') {
        stopPreviewPoll()
        previewBusy.value = false
        previewStatus.value = 'failed'
        previewMessage.value = job.errorMessage || '生成失败，请检查供应商配置'
      } else if (previewPollRounds >= 120) {
        stopPreviewPoll()
        previewBusy.value = false
        previewStatus.value = 'failed'
        previewMessage.value = '生成超时，请稍后重试'
      }
    } catch {
      // 单次查询失败不中断轮询
    }
  }, 1500)
}

async function generatePreview() {
  if (!editingId.value) {
    error.value = '请先保存提示词，再生成示例图'
    return
  }
  if (!form.content.trim()) {
    error.value = '请先填写正向提示词内容'
    return
  }
  error.value = ''
  previewBusy.value = true
  previewStatus.value = 'queued'
  previewMessage.value = '已提交生图任务，等待出图…'
  try {
    const res = await api.post('/ai/image-generations', {
      prompt: form.content.trim(),
      negativePrompt: form.negativeContent.trim() || null,
      aspectRatio: form.aspectRatio,
      purpose: form.promptType === 'cover' ? 'cover' : 'inline',
      count: 1,
    })
    previewJobId = res.jobId
    startPreviewPoll()
  } catch (err) {
    previewBusy.value = false
    previewStatus.value = 'failed'
    previewMessage.value = err.message
  }
}

function openPreview(assetId) {
  const url = previewUrls.value[assetId]
  if (!url) return
  previewImage.value = url
  previewModalOpen.value = true
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

onMounted(() => {
  applyQueryType()
  load()
})
onBeforeUnmount(() => {
  stopPreviewPoll()
  if (copyTimer) clearTimeout(copyTimer)
  if (confirmTimer) clearTimeout(confirmTimer)
  for (const url of Object.values(previewUrls.value)) URL.revokeObjectURL(url)
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">提示词库</h1>
        <p class="page-sub">
          统一维护普通 / 封面 / 配图等提示词，支持正反向两段与示例图预览。
          {{ canManage ? '你正在以管理员身份维护全局提示词库。' : '当前为只读视图，编辑需 image_styles.manage 权限。' }}
        </p>
      </div>
      <n-button v-if="canManage" type="primary" @click="openCreate">新建提示词</n-button>
    </div>

    <n-space class="prompt-tabs" role="tablist" aria-label="提示词库视图" :size="8">
      <n-button
        v-for="option in TYPE_OPTIONS"
        :key="option.value"
        :type="activeType === option.value ? 'primary' : 'secondary'"
        size="small"
        role="tab"
        :aria-selected="activeType === option.value"
        @click="activeType = option.value"
      >{{ option.label }}</n-button>
      <n-button secondary size="small" role="tab" :aria-selected="false" @click="goHanddraw">
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
          placeholder="搜索名称、正反向内容或标签"
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
            <n-button v-if="canManage" type="primary" size="small" @click="openCreate">
              新建第一条提示词
            </n-button>
            <span v-else class="field-hint">请管理员先在后台添加提示词。</span>
          </template>
        </n-empty>
      </div>
      <div v-else-if="!filtered.length" class="empty">
        <n-empty description="换个关键词，或清空筛选再试试。">
          <template #extra>
            <n-button secondary size="small" @click="resetFilter">清空筛选</n-button>
          </template>
        </n-empty>
      </div>
      <div v-else class="prompt-grid">
        <article v-for="item in filtered" :key="item.id" class="prompt-card">
          <header class="prompt-head">
            <div class="prompt-title" :title="item.title">{{ item.title }}</div>
            <span class="tag tag-accent">{{ TYPE_LABELS[item.promptType] || item.promptType || '普通' }}</span>
            <span v-if="item.enabled === false" class="tag">已停用</span>
            <span v-if="item.source === 'ai'" class="tag">AI 沉淀</span>
          </header>
          <div v-if="(item.previewAssetIds || []).length" class="prompt-previews">
            <button
              v-for="assetId in item.previewAssetIds"
              :key="assetId"
              type="button"
              class="prompt-preview-thumb"
              :title="`查看示例图 ${assetId}`"
              @click="openPreview(assetId)"
            >
              <img v-if="previewUrls[assetId]" :src="previewUrls[assetId]" alt="示例图" />
            </button>
          </div>
          <p v-if="item.summary" class="prompt-summary">{{ item.summary }}</p>
          <p class="prompt-content">{{ item.content }}</p>
          <p v-if="item.negativeContent" class="prompt-negative">
            负向：{{ item.negativeContent }}
          </p>
          <div v-if="(item.tags || []).length" class="row row-tight prompt-tags">
            <span v-for="tag in item.tags" :key="tag" class="tag">{{ tag }}</span>
          </div>
          <footer class="prompt-actions">
            <n-button quaternary size="small" @click="copyPrompt(item)">
              {{ copiedId === item.id ? '已复制' : '复制' }}
            </n-button>
            <div class="spacer" />
            <template v-if="canManage">
              <n-button quaternary size="small" @click="openEdit(item)">编辑</n-button>
              <n-button type="error" size="small" @click="remove(item)">
                {{ confirmingId === item.id ? '确认删除' : '删除' }}
              </n-button>
            </template>
          </footer>
        </article>
      </div>
    </section>

    <n-modal
      v-model:show="showEditor"
      preset="card"
      :title="editorTitle"
      class="prompt-editor"
      style="width: min(760px, calc(100vw - 32px))"
      :mask-closable="!saving && !previewBusy"
    >
      <n-form label-placement="top" class="prompt-editor-form">
        <n-form-item label="名称">
          <n-input
            v-model:value="form.title"
            maxlength="120"
            show-count
            placeholder="例如：水彩窗台橘猫"
          />
        </n-form-item>
        <div class="prompt-editor-row">
          <n-form-item label="类型">
            <n-select
              v-model:value="form.promptType"
              :options="TYPE_OPTIONS.filter((option) => option.value)"
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
        </div>
        <div class="prompt-editor-row">
          <n-form-item label="标签（可选）">
            <n-input v-model:value="form.tags" placeholder="用逗号分隔，例如：水彩, 治愈, 封面" />
          </n-form-item>
          <n-form-item label="摘要（可选）">
            <n-input v-model:value="form.summary" maxlength="120" placeholder="卡片上显示的一句话说明" />
          </n-form-item>
        </div>
        <div class="prompt-editor-row">
          <n-form-item label="排序（小的在前）">
            <n-input-number v-model:value="form.sortOrder" :min="0" :max="9999" />
          </n-form-item>
          <n-form-item label="启用">
            <n-switch v-model:value="form.enabled" />
            <span class="field-hint">停用的提示词不会出现在生成时的风格列表中。</span>
          </n-form-item>
        </div>
        <n-form-item label="正向提示词" class="prompt-editor-body">
          <n-input
            v-model:value="form.content"
            type="textarea"
            placeholder="例如：一只橘猫坐在木质窗台上晒太阳，水彩插画风格，柔和光线，干净背景"
            :input-props="{ spellcheck: false }"
          />
          <span class="field-hint">{{ contentCount }} 字 · 生成时作为主提示词提交</span>
        </n-form-item>
        <n-form-item label="负向提示词（可选）" class="prompt-editor-negative">
          <n-input
            v-model:value="form.negativeContent"
            type="textarea"
            placeholder="例如：低清, 水印, 文字错乱"
            :autosize="{ minRows: 2, maxRows: 5 }"
          />
          <span class="field-hint">
            供应商支持时独立透传；不支持时会并入正向文本并在结果中标注。
          </span>
        </n-form-item>
        <div class="prompt-editor-preview">
          <div class="row prompt-editor-preview-head">
            <span class="field-label">示例图</span>
            <n-select
              v-model:value="form.aspectRatio"
              :options="ASPECT_OPTIONS"
              size="small"
              style="width: 120px"
            />
            <n-button
              size="small"
              :loading="previewBusy"
              :disabled="!editingId || previewBusy"
              :title="editingId ? '用当前正/负向提示词生成一张示例图' : '请先保存提示词再生成示例图'"
              @click="generatePreview"
            >生成示例图</n-button>
          </div>
          <p class="field-hint">
            封面比例 2.35:1 暂未被供应商尺寸表支持，示例图将回落为 3:4（已明示，不静默声称支持）。
          </p>
          <p v-if="previewMessage" class="field-hint" :class="{ 'is-error': previewStatus === 'failed' }">
            {{ previewMessage }}
          </p>
          <div class="row prompt-editor-preview-list">
            <button
              v-for="assetId in form.previewAssetIds"
              :key="assetId"
              type="button"
              class="prompt-preview-thumb"
              @click="openPreview(assetId)"
            >
              <img v-if="previewUrls[assetId]" :src="previewUrls[assetId]" alt="示例图" />
            </button>
            <span v-if="!form.previewAssetIds.length" class="field-hint">还没有示例图。</span>
          </div>
        </div>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary :disabled="saving || previewBusy" @click="showEditor = false">取消</n-button>
          <n-button type="primary" :loading="saving" :disabled="previewBusy" @click="save">保存</n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal
      v-model:show="previewModalOpen"
      preset="card"
      title="示例图预览"
      style="width: min(900px, calc(100vw - 32px))"
    >
      <img v-if="previewImage" :src="previewImage" alt="示例图大图" class="preview-large" />
    </n-modal>
  </div>
</template>

<style scoped>
.prompt-tabs {
  margin-bottom: var(--space-3);
  width: fit-content;
  flex-wrap: wrap;
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
  flex-wrap: wrap;
}
.prompt-title {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.prompt-previews,
.prompt-editor-preview-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}
.prompt-preview-thumb {
  width: 72px;
  height: 72px;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  cursor: pointer;
}
.prompt-preview-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.prompt-summary {
  margin: 0;
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
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
.prompt-negative {
  margin: 0;
  font-size: var(--font-xs, var(--font-sm));
  line-height: 1.5;
  color: var(--color-text-muted, var(--color-text-secondary));
  display: -webkit-box;
  -webkit-line-clamp: 2;
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
.preview-large {
  display: block;
  max-width: 100%;
  max-height: 70vh;
  margin: 0 auto;
}
.is-error {
  color: var(--color-danger);
}
@media (max-width: 767px) {
  .prompt-search,
  .prompt-filter {
    flex: 1 1 100%;
    max-width: none;
    width: 100%;
  }
  .prompt-editor-row {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* 弹窗内容区限高，正文自己滚，保存按钮始终贴在视口里 */
.prompt-editor-form {
  display: flex;
  flex-direction: column;
  max-height: min(68vh, 640px);
  min-height: 0;
}
.prompt-editor-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-3);
}
.prompt-editor-body {
  flex: 1 1 auto;
  min-height: 140px;
  display: flex;
  flex-direction: column;
}
.prompt-editor-body :deep(.n-form-item-blank) {
  flex: 1 1 auto;
  min-height: 140px;
  align-items: stretch;
}
.prompt-editor-body :deep(.n-input) {
  height: 100%;
}
.prompt-editor-body :deep(.n-input-wrapper) {
  height: 100%;
  align-items: stretch;
}
.prompt-editor-body :deep(textarea) {
  height: 100% !important;
  max-height: none;
  resize: none;
  line-height: 1.7;
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace;
  font-size: var(--font-sm);
}
.prompt-editor-preview-head {
  gap: var(--space-2);
  align-items: center;
}
</style>
