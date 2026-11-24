<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  NAlert, NButton, NCheckbox, NEmpty, NInput, NModal, NSelect,
} from 'naive-ui'
import { api, apiUrl } from '../api/client'
import { toast } from '../ui/feedback'
import CharacterAssetPicker from '../features/character/CharacterAssetPicker.vue'
import { providerOptions, resolveSelectedModelId } from '../composables/imageProviders.js'

const APPEARANCE_FIELDS = [
  { key: 'hair', label: '发型' },
  { key: 'hairColor', label: '发色' },
  { key: 'eyes', label: '眼睛' },
  { key: 'face', label: '面部' },
  { key: 'outfit', label: '服装' },
  { key: 'body', label: '体型' },
  { key: 'age', label: '年龄' },
  { key: 'extra', label: '其他特征' },
]
const ANGLES = [
  { label: '正面', value: 'front' },
  { label: '侧面', value: 'side' },
  { label: '全身', value: 'full' },
  { label: '特写', value: 'close' },
  { label: '背面', value: 'back' },
  { label: '通用', value: 'any' },
]

const characters = ref([])
const library = ref([])
const loading = ref(true)
const error = ref('')
const keyword = ref('')

const showEditor = ref(false)
const editingId = ref('')
const saving = ref(false)
const confirmingId = ref('')
const form = reactive({ name: '', aliases: '', notes: '', images: [], appearance: {} })

const showExtract = ref(false)
const extractText = ref('')
const extracting = ref(false)
const extractError = ref('')
const drafts = ref([])
const committing = ref(false)

// AI 补全外貌（Q8-B：设定 + 可选正文片段；只填空白字段，逐字段勾选采纳）
const completing = ref(false)
const completeHint = ref('')
const completeSource = ref('')
const showCompleteSource = ref(false)
const completeSuggestions = ref(null)

// AI 生成参考图（Q2/3/5/9：主图锚定 + 风格记忆 + 候选→采用 + 可编辑提示词）
const GEN_STYLE_KEY = 'characters.genStyle'
const GEN_STYLES = [
  { label: '日漫', value: 'manga' },
  { label: '写实', value: 'realistic' },
  { label: '国漫', value: 'guoman' },
  { label: '水彩', value: 'watercolor' },
]
const genOpen = ref(false)
const genTargetId = ref('')
const genAngle = ref('front')
const genStyle = ref(localStorage.getItem(GEN_STYLE_KEY) || 'manga')
const genPromptOverride = ref('')
const genBusy = ref(false)
const genError = ref('')
const genImageProviders = ref([])
const genImageModelId = ref('')
const genImageModelsLoading = ref(false)
let genPollTimer = null
const genImageModelOptions = computed(() => providerOptions(genImageProviders.value))
const genImageModelHint = computed(() => (
  genImageModelOptions.value.length
    ? '这次生成用选中的模型。环境变量供应商会在列表里标成第一项。'
    : '还没有可用的生图模型。到设置里启用带生图能力的连接后再来。'
))

const genTarget = computed(() => characters.value.find((item) => item.id === genTargetId.value) || null)
const pendingActive = computed(() => (genTarget.value?.pendingImages || []).filter(
  (item) => ['queued', 'running', 'retrying'].includes(item.status),
))

watch(genStyle, (value) => localStorage.setItem(GEN_STYLE_KEY, value))

const filtered = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return characters.value
  return characters.value.filter((item) => [
    item.name, ...(item.aliases || []), ...Object.values(item.appearance || {}), item.notes,
  ].join(' ').toLowerCase().includes(q))
})

const libraryById = computed(() => new Map(library.value.map((asset) => [asset.id, asset])))

/** 参考图行上展示的缩略信息：优先用素材库记录，素材已删时退回角色本身保存的 url */
function imageAsset(image) {
  const asset = image?.assetId ? libraryById.value.get(image.assetId) : null
  return {
    name: asset?.originalName || image?.assetId || '',
    url: apiUrl(asset?.url || image?.url || ''),
  }
}

// 参考图选择弹窗：一个弹窗对应一行，避免每行都挂一个下拉
const assetPickerIndex = ref(-1)
const assetPickerOpen = computed({
  get: () => assetPickerIndex.value >= 0,
  set: (open) => { if (!open) assetPickerIndex.value = -1 },
})
const assetPickerSelectedId = computed(
  () => form.images[assetPickerIndex.value]?.assetId || '',
)
// 同一张素材在同一角色里只占一个角度位
const assetPickerDisabledIds = computed(() => form.images
  .map((image) => image.assetId)
  .filter((id, index) => id && index !== assetPickerIndex.value))

function openAssetPicker(index) {
  assetPickerIndex.value = index
}

function onAssetPicked(asset) {
  const index = assetPickerIndex.value
  const row = form.images[index]
  if (!row) return
  row.assetId = asset.id
}

function clearImageAsset(index) {
  if (form.images[index]) form.images[index].assetId = null
}

function appearanceText(item) {
  const raw = item.appearance || {}
  return APPEARANCE_FIELDS
    .filter((field) => raw[field.key])
    .map((field) => `${field.label}：${raw[field.key]}`)
    .join('，')
}

function parseAliases(value) {
  const result = []
  for (const part of String(value || '').split(/[,，、]/)) {
    const text = part.trim()
    if (text && !result.includes(text)) result.push(text)
  }
  return result.slice(0, 12)
}

function collectAppearance() {
  const result = {}
  for (const field of APPEARANCE_FIELDS) {
    const value = String(form.appearance[field.key] || '').trim()
    if (value) result[field.key] = value
  }
  return result
}

function resetForm() {
  form.name = ''
  form.aliases = ''
  form.notes = ''
  form.images = []
  form.appearance = {}
  for (const field of APPEARANCE_FIELDS) form.appearance[field.key] = ''
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [roles, assets] = await Promise.all([
      api.get('/characters'),
      api.get('/assets?limit=200'),
    ])
    characters.value = roles
    library.value = assets
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = ''
  resetForm()
  showEditor.value = true
}

function openEdit(item) {
  editingId.value = item.id
  form.name = item.name
  form.aliases = (item.aliases || []).join('，')
  form.notes = item.notes || ''
  form.images = (item.images || []).map((image) => ({
    assetId: image.assetId, angle: image.angle || 'front',
    isPrimary: !!image.isPrimary, label: image.label || '',
  }))
  form.appearance = {}
  for (const field of APPEARANCE_FIELDS) form.appearance[field.key] = item.appearance?.[field.key] || ''
  showEditor.value = true
}

function addImage() {
  form.images.push({ assetId: null, angle: 'front', isPrimary: form.images.length === 0, label: '' })
}

function removeImage(index) {
  form.images.splice(index, 1)
  if (form.images.length && !form.images.some((image) => image.isPrimary)) form.images[0].isPrimary = true
}

function setPrimary(index) {
  form.images.forEach((image, position) => { image.isPrimary = position === index })
}

async function save() {
  error.value = ''
  if (!form.name.trim()) {
    error.value = '请填写角色名'
    return
  }
  const images = form.images
    .filter((image) => image.assetId)
    .map((image) => ({
      assetId: image.assetId, angle: image.angle,
      isPrimary: !!image.isPrimary, label: image.label,
    }))
  const payload = {
    name: form.name.trim(),
    aliases: parseAliases(form.aliases),
    appearance: collectAppearance(),
    images,
    notes: form.notes.trim(),
  }
  saving.value = true
  try {
    const saved = editingId.value
      ? await api.patch(`/characters/${editingId.value}`, payload)
      : await api.post('/characters', payload)
    await load()
    showEditor.value = false
    toast(`已保存「${saved.name}」`)
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

async function remove(item) {
  if (confirmingId.value !== item.id) {
    confirmingId.value = item.id
    setTimeout(() => { if (confirmingId.value === item.id) confirmingId.value = '' }, 3000)
    return
  }
  confirmingId.value = ''
  try {
    await api.del(`/characters/${item.id}`)
    characters.value = characters.value.filter((row) => row.id !== item.id)
    toast(`已移除「${item.name}」，已引用它的分镜仍可继续使用参考图`)
  } catch (err) {
    error.value = err.message
  }
}

async function runExtract() {
  extractError.value = ''
  if (!extractText.value.trim()) {
    extractError.value = '请先粘贴一段正文'
    return
  }
  extracting.value = true
  try {
    const result = await api.post('/characters/extract', { sourceText: extractText.value.trim() })
    drafts.value = result.drafts.map((draft) => ({
      ...draft,
      selected: !draft.exists,
      action: draft.exists ? 'merge' : 'create',
    }))
    if (!drafts.value.length) extractError.value = '这段正文里没有抽到带名字的角色'
  } catch (err) {
    extractError.value = err.message
  } finally {
    extracting.value = false
  }
}

function existingByName(name) {
  const key = String(name || '').trim().toLowerCase()
  return characters.value.find((item) => item.name.trim().toLowerCase() === key)
}

async function commitDrafts() {
  const picked = drafts.value.filter((draft) => draft.selected && draft.action !== 'skip')
  if (!picked.length) {
    extractError.value = '没有勾选要入库的角色'
    return
  }
  committing.value = true
  extractError.value = ''
  let created = 0
  let merged = 0
  try {
    for (const draft of picked) {
      if (draft.action === 'merge') {
        const target = existingByName(draft.name)
        if (!target) continue
        const aliases = [...new Set([...(target.aliases || []), ...(draft.aliases || [])])]
        const appearance = { ...(draft.appearance || {}), ...(target.appearance || {}) }
        await api.patch(`/characters/${target.id}`, { aliases, appearance })
        merged += 1
      } else {
        await api.post('/characters', {
          name: draft.name, aliases: draft.aliases || [],
          appearance: draft.appearance || {}, images: [], notes: '',
        })
        created += 1
      }
    }
    await load()
    showExtract.value = false
    drafts.value = []
    extractText.value = ''
    toast(`已新建 ${created} 个、合并 ${merged} 个角色；参考图请到角色卡里补`)
  } catch (err) {
    extractError.value = err.message
    await load()
  } finally {
    committing.value = false
  }
}

async function runComplete() {
  if (!editingId.value) {
    toast('请先保存角色，再让 AI 补全外貌')
    return
  }
  completing.value = true
  try {
    const result = await api.post(`/characters/${editingId.value}/appearance/complete`, {
      hintText: completeHint.value.trim(),
      sourceText: completeSource.value.trim(),
    })
    completeSuggestions.value = Object.fromEntries(
      Object.entries(result.appearance || {}).map(([key, value]) => [key, { value, picked: true }]),
    )
    if (result.fallbackUsed) toast('首选模型不可用，已降级完成补全')
  } catch (err) {
    toast(`补全失败：${err.message}`)
  } finally {
    completing.value = false
  }
}

function adoptSuggestions() {
  const adopted = APPEARANCE_FIELDS.filter((field) => {
    const suggestion = completeSuggestions.value?.[field.key]
    return suggestion?.picked && suggestion.value && !String(form.appearance[field.key] || '').trim()
  })
  for (const field of adopted) form.appearance[field.key] = completeSuggestions.value[field.key].value
  completeSuggestions.value = null
  toast(adopted.length ? `已采纳 ${adopted.length} 个字段（只填空白，不覆盖你写过的）` : '没有可采纳的字段')
}

async function loadGenImageProviders() {
  genImageModelsLoading.value = true
  try {
    const status = await api.get('/ai/image-provider/status')
    genImageProviders.value = status.providers || []
    genImageModelId.value = resolveSelectedModelId(genImageProviders.value, genImageModelId.value)
  } catch (err) {
    genError.value = err.message
  } finally {
    genImageModelsLoading.value = false
  }
}

function openGenerate(item) {
  genTargetId.value = item.id
  genAngle.value = 'front'
  genPromptOverride.value = ''
  genError.value = ''
  genOpen.value = true
  load()
  loadGenImageProviders()
}

async function runGenerate() {
  genError.value = ''
  if (!genImageModelOptions.value.length) {
    genError.value = '没有可用的生图模型'
    return
  }
  genBusy.value = true
  try {
    await api.post(`/characters/${genTargetId.value}/images/generate`, {
      angle: genAngle.value,
      style: genStyle.value,
      modelId: genImageModelId.value || null,
      ...(genPromptOverride.value.trim() ? { promptOverride: genPromptOverride.value.trim() } : {}),
    })
    await load()
  } catch (err) {
    genError.value = err.message
  } finally {
    genBusy.value = false
  }
}

async function acceptImage(jobId) {
  try {
    await api.post(`/characters/${genTargetId.value}/images/${jobId}/accept`)
    await load()
    toast('已采用为参考图')
  } catch (err) {
    genError.value = err.message
  }
}

async function rejectImage(jobId) {
  try {
    await api.del(`/characters/${genTargetId.value}/images/${jobId}`)
    await load()
  } catch (err) {
    genError.value = err.message
  }
}

function stopGenPolling() {
  if (genPollTimer) clearInterval(genPollTimer)
  genPollTimer = null
}

watch(pendingActive, (active) => {
  if (active.length && !genPollTimer) {
    genPollTimer = setInterval(async () => {
      try {
        await load()
      } catch {
        // 轮询期间的瞬时读取失败不打断，下一轮继续
      }
    }, 1800)
  } else if (!active.length) {
    stopGenPolling()
  }
})

onBeforeUnmount(stopGenPolling)
onMounted(load)
</script>

<template>
  <div class="page">
    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <section class="card">
      <div class="toolbar">
        <n-input v-model:value="keyword" clearable placeholder="搜索名字、别名或外貌" aria-label="搜索角色" />
        <div class="spacer" />
        <span class="field-hint">{{ filtered.length }} / {{ characters.length }} 个角色</span>
        <div class="head-actions">
          <n-button secondary @click="showExtract = true; extractError = ''">从正文抽取</n-button>
          <n-button type="primary" @click="openCreate">新建角色</n-button>
        </div>
      </div>

      <div v-if="loading" class="skeleton" style="height: 120px" />
      <div v-else-if="!characters.length" class="empty">
        <n-empty description="把常画的角色建成卡片，之后在漫画分镜里直接引用。">
          <template #extra>
            <n-button type="primary" size="small" @click="openCreate">新建第一个角色</n-button>
          </template>
        </n-empty>
      </div>
      <div v-else-if="!filtered.length" class="empty">
        <n-empty description="换个关键词再试试。" />
      </div>
      <div v-else class="role-grid">
        <article v-for="item in filtered" :key="item.id" class="role-card">
          <div class="role-thumb">
            <img v-if="item.images?.length" :src="apiUrl(item.images[0].url)" :alt="item.name" />
            <span v-else class="role-initial">{{ item.name.slice(0, 1) }}</span>
          </div>
          <div class="role-body">
            <header class="role-head">
              <strong :title="item.name">{{ item.name }}</strong>
              <span class="tag tag-accent">{{ item.images?.length || 0 }} 张参考图</span>
            </header>
            <p v-if="item.aliases?.length" class="role-line">别名：{{ item.aliases.join('、') }}</p>
            <p v-if="appearanceText(item)" class="role-line">{{ appearanceText(item) }}</p>
            <p v-if="item.notes" class="role-line role-note">{{ item.notes }}</p>
            <p v-if="!appearanceText(item) && !item.images?.length" class="role-line role-note">还没有外貌与参考图，生成时会缺少一致性依据</p>
            <footer class="role-actions">
              <n-button quaternary size="small" @click="openEdit(item)">编辑</n-button>
              <n-button quaternary size="small" @click="openGenerate(item)">AI 生成参考图</n-button>
              <div class="spacer" />
              <n-button type="error" size="small" @click="remove(item)">
                {{ confirmingId === item.id ? '确认移除' : '移除' }}
              </n-button>
            </footer>
          </div>
        </article>
      </div>
    </section>

    <n-modal v-model:show="showEditor" preset="card" :title="editingId ? '编辑角色' : '新建角色'" class="role-editor">
      <div class="editor-grid">
        <label class="field"><span>角色名 *</span><n-input v-model:value="form.name" placeholder="如：林夏" /></label>
        <label class="field"><span>别名（逗号分隔）</span><n-input v-model:value="form.aliases" placeholder="如：夏子，小林" /></label>
      </div>
      <div class="section-head">
        <p class="section-title">外貌设定（留空即可，只写有把握的特征）</p>
        <n-button size="tiny" secondary :loading="completing" @click="runComplete">AI 补全外貌</n-button>
      </div>
      <div class="complete-box">
        <n-input v-model:value="completeHint" size="small" placeholder="一句话设定，如：冷峻的剑客，常年风餐露宿" />
        <n-button size="tiny" quaternary @click="showCompleteSource = !showCompleteSource">
          {{ showCompleteSource ? '收起正文片段' : '贴一段原文（可选）' }}
        </n-button>
        <n-input v-if="showCompleteSource" v-model:value="completeSource" type="textarea" :rows="3" placeholder="粘贴含该角色外貌描写的原文…" />
      </div>
      <div v-if="completeSuggestions" class="suggestion-list">
        <div v-for="field in APPEARANCE_FIELDS.filter((f) => completeSuggestions[f.key])" :key="field.key" class="suggestion-row">
          <n-checkbox v-model:checked="completeSuggestions[field.key].picked" />
          <span class="suggestion-label">{{ field.label }}</span>
          <span class="suggestion-value">{{ completeSuggestions[field.key].value }}</span>
          <span v-if="String(form.appearance[field.key] || '').trim()" class="tag tag-warn">已有值，采纳时跳过</span>
        </div>
        <div class="suggestion-actions">
          <n-button size="tiny" type="primary" @click="adoptSuggestions">采纳勾选项</n-button>
          <n-button size="tiny" quaternary @click="completeSuggestions = null">放弃</n-button>
        </div>
      </div>
      <div class="appearance-grid">
        <label v-for="field in APPEARANCE_FIELDS" :key="field.key" class="field">
          <span>{{ field.label }}</span>
          <n-input v-model:value="form.appearance[field.key]" :placeholder="field.label" />
        </label>
      </div>
      <div class="section-head">
        <p class="section-title">参考图（从素材图库选，按角度标注；每个镜头只会投 1 张）</p>
        <n-button size="tiny" secondary @click="addImage">添加一张</n-button>
      </div>
      <p v-if="!form.images.length" class="field-hint">还没有参考图。可以先建档，之后到素材图库上传再补。</p>
      <div v-for="(image, index) in form.images" :key="index" class="image-row">
        <button type="button" class="image-asset" @click="openAssetPicker(index)">
          <span class="image-asset-thumb">
            <img
              v-if="image.assetId && imageAsset(image).url"
              :src="imageAsset(image).url"
              :alt="imageAsset(image).name"
              loading="lazy"
            />
            <span v-else-if="image.assetId" class="image-asset-empty">无预览</span>
            <span v-else class="image-asset-empty">+</span>
          </span>
          <span class="image-asset-text" :class="{ 'is-empty': !image.assetId }">
            {{ image.assetId ? imageAsset(image).name : '从图库选择参考图' }}
          </span>
          <span class="image-asset-cta">{{ image.assetId ? '更换' : '选择' }}</span>
        </button>
        <n-button
          v-if="image.assetId"
          size="tiny"
          quaternary
          title="清除已选参考图"
          class="image-clear"
          @click="clearImageAsset(index)"
        >清除</n-button>
        <n-select v-model:value="image.angle" :options="ANGLES" class="angle-select" />
        <n-input v-model:value="image.label" placeholder="备注" class="image-label" />
        <n-button
          size="tiny"
          :type="image.isPrimary ? 'primary' : 'default'"
          secondary
          :disabled="!image.assetId"
          :title="image.assetId ? '' : '先选一张参考图才能设为主图'"
          @click="setPrimary(index)"
        >
          {{ image.isPrimary ? '主图' : '设为主图' }}
        </n-button>
        <n-button size="tiny" quaternary type="error" class="image-remove" @click="removeImage(index)">移除</n-button>
      </div>
      <label class="field notes-field"><span>备注</span><n-input v-model:value="form.notes" type="textarea" :rows="2" /></label>
      <template #footer>
        <div class="modal-footer">
          <n-alert v-if="error" type="error" :bordered="false" class="inline-error">{{ error }}</n-alert>
          <div class="spacer" />
          <n-button @click="showEditor = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="save">保存</n-button>
        </div>
      </template>
    </n-modal>

    <CharacterAssetPicker
      v-model:show="assetPickerOpen"
      :selected-id="assetPickerSelectedId"
      :disabled-ids="assetPickerDisabledIds"
      @pick="onAssetPicked"
    />

    <n-modal v-model:show="genOpen" preset="card" :title="`AI 生成参考图 · ${genTarget?.name || ''}`" class="gen-modal">
      <p class="field-hint">有参考图时以现有图为锚保持同一张脸；一张都没有则直接文生图，采用后成为主图。生成的先进候选，采用才进参考图。</p>
      <div class="gen-controls">
        <label class="field"><span>角度</span><n-select v-model:value="genAngle" :options="ANGLES" /></label>
        <label class="field"><span>风格</span><n-select v-model:value="genStyle" :options="GEN_STYLES" /></label>
        <label class="field"><span>生图模型</span>
          <n-select
            v-model:value="genImageModelId"
            :options="genImageModelOptions"
            :loading="genImageModelsLoading"
            :disabled="genImageModelsLoading || !genImageModelOptions.length"
            :consistent-menu-width="false"
            placeholder="没有可用的生图模型"
            aria-label="选择角色参考图的生图模型"
          />
        </label>
      </div>
      <p class="field-hint">{{ genImageModelHint }}</p>
      <details class="gen-prompt-details">
        <summary>编辑最终提示词（默认按外貌设定自动合成）</summary>
        <n-input v-model:value="genPromptOverride" type="textarea" :rows="3" placeholder="留空 = 用模板合成；填写 = 整体替换" />
      </details>
      <div class="gen-actions">
        <n-button type="primary" :loading="genBusy" :disabled="genImageModelsLoading || !genImageModelOptions.length" @click="runGenerate">生成一张</n-button>
        <span v-if="pendingActive.length" class="field-hint">{{ pendingActive.length }} 张正在生成…</span>
      </div>
      <n-alert v-if="genError" type="error" :bordered="false">{{ genError }}</n-alert>
      <div v-if="genTarget?.pendingImages?.length" class="pending-list">
        <div v-for="candidate in genTarget.pendingImages" :key="candidate.jobId" class="pending-row">
          <div class="pending-thumb">
            <img v-if="candidate.status === 'succeeded' && candidate.url" :src="apiUrl(candidate.url)" alt="候选参考图" />
            <span v-else-if="['queued', 'running', 'retrying'].includes(candidate.status)" class="pending-spinner" />
            <span v-else class="pending-failed">✕</span>
          </div>
          <div class="pending-main">
            <span class="tag tag-accent">{{ ANGLES.find((a) => a.value === candidate.angle)?.label || candidate.angle }}</span>
            <span v-if="candidate.degraded" class="tag tag-warn">无锚定，可能跑脸</span>
            <span v-if="candidate.status === 'failed'" class="field-hint pending-error">{{ candidate.error || '生成失败' }}</span>
          </div>
          <template v-if="candidate.status === 'succeeded'">
            <n-button size="tiny" type="primary" @click="acceptImage(candidate.jobId)">采用</n-button>
            <n-button size="tiny" quaternary type="error" @click="rejectImage(candidate.jobId)">不要</n-button>
          </template>
          <n-button v-else-if="candidate.status === 'failed'" size="tiny" secondary @click="rejectImage(candidate.jobId)">清除</n-button>
        </div>
      </div>
      <template #footer>
        <div class="modal-footer">
          <div class="spacer" />
          <n-button @click="genOpen = false">关闭</n-button>
        </div>
      </template>
    </n-modal>

    <n-modal v-model:show="showExtract" preset="card" title="从正文抽取角色" class="extract-modal">
      <p class="field-hint">粘贴小说片段或剧本，模型只给出草稿，不写库；确认后才创建。参考图始终需要人工挑选。</p>
      <n-input v-model:value="extractText" type="textarea" :rows="7" placeholder="粘贴正文…" />
      <div class="extract-actions">
        <n-button type="primary" :loading="extracting" @click="runExtract">抽取草稿</n-button>
        <span v-if="drafts.length" class="field-hint">共 {{ drafts.length }} 个候选</span>
      </div>
      <n-alert v-if="extractError" type="error" :bordered="false">{{ extractError }}</n-alert>
      <div v-if="drafts.length" class="draft-list">
        <div v-for="(draft, index) in drafts" :key="index" class="draft-row">
          <n-checkbox v-model:checked="draft.selected" />
          <div class="draft-main">
            <n-input v-model:value="draft.name" size="small" placeholder="角色名" />
            <span class="field-hint">
              {{ (draft.aliases || []).join('、') || '无别名' }}
              <template v-if="Object.keys(draft.appearance || {}).length">
                · {{ Object.values(draft.appearance).join('，') }}
              </template>
            </span>
            <span v-if="draft.exists" class="tag tag-warn">已有同名角色</span>
          </div>
          <n-select v-model:value="draft.action" size="small" class="draft-action" :options="[
            { label: '新建', value: 'create' },
            { label: '跳过', value: 'skip' },
            ...(draft.exists ? [{ label: '合并到已有', value: 'merge' }] : []),
          ]" />
        </div>
      </div>
      <template #footer>
        <div class="modal-footer">
          <div class="spacer" />
          <n-button @click="showExtract = false">取消</n-button>
          <n-button type="primary" :disabled="!drafts.length" :loading="committing" @click="commitDrafts">确认入库</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.page-title { margin: 0; font-size: 22px; }
.page-sub { margin: 6px 0 0; color: var(--color-text-muted); font-size: 13px; line-height: 1.6; max-width: 720px; }
.head-actions { display: flex; gap: 8px; flex-shrink: 0; }
.card { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 10px; padding: 16px; margin-top: 14px; }
.toolbar { display: flex; align-items: center; gap: 12px; }
.toolbar :deep(.n-input) { max-width: 320px; }
.spacer { flex: 1; }
.field-hint { color: var(--color-text-muted); font-size: 12px; }
.empty { padding: 28px 0; }
.skeleton { margin-top: 16px; border-radius: 6px; background: linear-gradient(90deg, var(--color-surface-raised) 25%, var(--color-border) 50%, var(--color-surface-raised) 75%); background-size: 200% 100%; animation: shimmer 1.4s infinite; }
@keyframes shimmer { to { background-position: -200% 0; } }
.role-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; margin-top: 16px; }
.role-card { display: flex; gap: 12px; border: 1px solid var(--color-border); border-radius: 8px; padding: 12px; min-width: 0; }
.role-thumb { width: 72px; height: 92px; flex-shrink: 0; border-radius: 6px; overflow: hidden; background: var(--color-surface-raised); display: flex; align-items: center; justify-content: center; }
.role-thumb img { width: 100%; height: 100%; object-fit: cover; }
.role-initial { font-size: 30px; color: var(--color-text-muted); }
.role-body { min-width: 0; flex: 1; display: flex; flex-direction: column; }
.role-head { display: flex; align-items: center; gap: 8px; justify-content: space-between; }
.role-head strong { font-size: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tag { font-size: 11px; padding: 2px 6px; border-radius: 4px; white-space: nowrap; }
.tag-accent { background: var(--color-accent-soft); color: var(--color-accent); }
.tag-warn { background: color-mix(in srgb, var(--color-warning) 15%, transparent); color: var(--color-warning); }
.role-line { margin: 6px 0 0; font-size: 12px; color: var(--color-text-muted); line-height: 1.5; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.role-note { color: var(--color-text-muted); }
.role-actions { display: flex; align-items: center; margin-top: auto; padding-top: 8px; }
.editor-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.field { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--color-text-muted); min-width: 0; }
.section-title { margin: 16px 0 8px; font-size: 13px; font-weight: 600; }
.section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.appearance-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.image-row { display: grid; grid-template-columns: minmax(200px, 2fr) auto 92px minmax(80px, 1fr) auto auto; gap: 6px; align-items: center; margin-bottom: 8px; }
.image-asset { display: flex; align-items: center; gap: 8px; min-width: 0; height: 44px; padding: 4px 8px 4px 4px; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-surface); font: inherit; text-align: left; cursor: pointer; transition: border-color 0.15s ease, background 0.15s ease; }
.image-asset:hover { border-color: var(--color-border-strong); background: var(--color-surface-raised); }
.image-asset:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px; }
.image-asset-thumb { display: grid; place-items: center; width: 34px; height: 34px; flex: 0 0 34px; overflow: hidden; border-radius: 6px; background: var(--color-surface-raised); }
.image-asset-thumb img { width: 100%; height: 100%; object-fit: cover; }
.image-asset-empty { color: var(--color-text-muted); font-size: 16px; line-height: 1; }
.image-asset-text { min-width: 0; overflow: hidden; color: var(--color-text-primary); font-size: 12px; white-space: nowrap; text-overflow: ellipsis; }
.image-asset-text.is-empty { color: var(--color-text-muted); }
.image-asset-cta { margin-left: auto; flex-shrink: 0; color: var(--color-accent); font-size: 11px; }
.image-clear, .image-remove { flex-shrink: 0; }
.notes-field { margin-top: 14px; }
.modal-footer { display: flex; align-items: center; gap: 8px; }
.inline-error { padding: 4px 8px; }
.extract-actions { display: flex; align-items: center; gap: 12px; margin-top: 10px; }
.draft-list { margin-top: 12px; max-height: 280px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.draft-row { display: flex; align-items: center; gap: 10px; border: 1px solid var(--color-border); border-radius: 6px; padding: 8px; }
.draft-main { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.draft-main :deep(.n-input) { width: 140px; }
.draft-action { width: 130px; flex-shrink: 0; }
.role-editor { width: min(820px, 94vw); }
.extract-modal { width: min(720px, 94vw); }
.gen-modal { width: min(680px, 94vw); }
.complete-box { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px; }
.complete-box :deep(.n-input) { flex: 1 1 260px; }
.suggestion-list { border: 1px solid var(--color-border); border-radius: 6px; padding: 8px; margin-bottom: 10px; display: flex; flex-direction: column; gap: 6px; }
.suggestion-row { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.suggestion-label { color: var(--color-text-muted); width: 56px; flex-shrink: 0; }
.suggestion-value { flex: 1; min-width: 0; }
.suggestion-actions { display: flex; gap: 8px; margin-top: 2px; }
.gen-controls { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 10px 0; }
.gen-prompt-details { margin: 10px 0; font-size: 12px; color: var(--color-text-muted); }
.gen-prompt-details textarea { margin-top: 6px; }
.gen-actions { display: flex; align-items: center; gap: 12px; margin: 10px 0; }
.pending-list { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
.pending-row { display: flex; align-items: center; gap: 10px; border: 1px solid var(--color-border); border-radius: 6px; padding: 8px; }
.pending-thumb { width: 56px; height: 74px; flex-shrink: 0; border-radius: 4px; overflow: hidden; background: var(--color-surface-raised); display: flex; align-items: center; justify-content: center; }
.pending-thumb img { width: 100%; height: 100%; object-fit: cover; }
.pending-spinner { width: 18px; height: 18px; border: 2px solid var(--color-border-strong); border-top-color: var(--color-text-muted); border-radius: 50%; animation: spin 0.9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.pending-failed { color: var(--color-danger); font-size: 16px; }
.pending-main { flex: 1; min-width: 0; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.pending-error { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
/* Portrait-led catalog, while keeping the editing workflows unchanged. */
.page-head { align-items: center; padding-bottom: 22px; border-bottom: 1px solid var(--color-border); }
.page-title { font-size: 26px; letter-spacing: .02em; }
.page-sub { margin-top: 8px; line-height: 1.7; }
.head-actions { flex-wrap: wrap; }
.card { padding: 20px; border-radius: 14px; }
.toolbar { flex-wrap: wrap; }
.role-grid { grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr)); gap: 16px; margin-top: 20px; }
.role-card { gap: 16px; padding: 16px; border-radius: 14px; background: var(--color-surface-raised); transition: box-shadow .18s ease, transform .18s ease; }
.role-card:hover { box-shadow: var(--shadow-md); transform: translateY(-1px); }
.role-thumb { width: 92px; height: 120px; border-radius: 9px; background: var(--color-accent-soft); }
.role-initial { color: var(--color-accent); font-family: Georgia, serif; font-size: 36px; }
.role-head { flex-wrap: wrap; padding-bottom: 10px; border-bottom: 1px solid var(--color-border); }
.role-head strong { font-size: 16px; }
.role-line { line-height: 1.7; }
.role-actions { flex-wrap: wrap; gap: 2px; padding-top: 12px; }
.head-actions :deep(.n-button) { border-radius: 999px; }
.card { padding: 0; border: 0; background: transparent; }
.role-grid { grid-template-columns: repeat(auto-fill, minmax(min(100%, 280px), 1fr)); gap: 10px; margin-top: 14px; }
.role-card { gap: 12px; padding: 16px; border-radius: 16px; background: var(--color-surface); box-shadow: var(--shadow-sm); }
.role-thumb { width: 64px; height: 80px; border-radius: 12px; }
.role-head { padding-bottom: 0; border: 0; }
.role-head strong { font-size: 15px; }
.role-actions { padding-top: 8px; }
@media (prefers-reduced-motion: reduce) { .role-card { transition: none; } }
@media (max-width: 800px) {
  .page-head { flex-direction: column; align-items: flex-start; }
  .editor-grid, .appearance-grid, .gen-controls { grid-template-columns: 1fr 1fr; }
  /* 窄屏：参考图占满整行，其余控件两列排布 */
  .image-row { grid-template-columns: 1fr 1fr; }
  .image-asset { grid-column: 1 / -1; }
}
</style>
