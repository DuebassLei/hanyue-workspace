<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { NButton, NInput, NSelect } from 'naive-ui'
import { api } from '../../api/client'
import { applyGenerationQueued, hasActiveGeneration } from './generationState.js'
import { providerOptions, resolveSelectedModelId } from '../../composables/imageProviders.js'
import { copyText, shotImages } from './shotImages.js'

const props = defineProps({ shot: { type: Object, default: null }, project: { type: Object, default: null }, saving: Boolean })
const emit = defineEmits(['save', 'generate-sketch', 'generate-final', 'generation-queued', 'retry', 'preview'])
const promptOpen = ref(false)
const draft = ref({})
const busy = ref('')
const error = ref('')
const imageProviders = ref([])
const imageModelId = ref('')
const imageModelsLoading = ref(false)
const imageModelOptions = computed(() => providerOptions(imageProviders.value))
const selectedImageProvider = computed(
  () => imageProviders.value.find((item) => (item.modelId || '') === imageModelId.value) || null,
)
const imageModelHint = computed(() => {
  if (!imageModelOptions.value.length) {
    return '还没有可用的生图模型。到设置里启用带生图能力的连接后再来。'
  }
  return `${selectedImageProvider.value?.label || '未选择'}。草图和正式图都用这个模型。`
})
const copyState = ref('')
let copyTimer
const generationActive = computed(() => hasActiveGeneration(props.shot))
const images = computed(() => shotImages(props.shot))
const generatedPrompt = computed(() => props.shot?.finalPrompt || props.shot?.sketchPrompt || '')
const activeStyleLabel = computed(() => {
  const config = props.project?.styleConfig || {}
  return config.styleName || (config.promptSuffix ? '自定义风格' : '')
})
const framePrompt = computed(() => draft.value.prompt || props.shot?.frameDescription || '')
const assetOptions = computed(() => {
  const refs = props.project?.assets || {}
  return ['characters', 'locations', 'styles'].flatMap((key) => (refs[key] || []).map((item) => ({
    label: item.name, value: item.id,
  }))).filter((item) => item.value)
})
const sizes = [{ label: '遠景', value: 'wide' }, { label: '全景', value: 'full' }, { label: '中景', value: 'medium' }, { label: '近景', value: 'close' }, { label: '特寫', value: 'extreme_close' }]
const angles = [{ label: '平視', value: 'eye_level' }, { label: '俯視', value: 'high_angle' }, { label: '仰視', value: 'low_angle' }, { label: '過肩', value: 'over_shoulder' }, { label: '主觀', value: 'point_of_view' }]
watch(() => props.shot, (shot) => { draft.value = shot ? { ...shot } : {}; copyState.value = '' }, { immediate: true })

async function loadImageProviders() {
  imageModelsLoading.value = true
  try {
    const status = await api.get('/ai/image-provider/status')
    imageProviders.value = status.providers || []
    imageModelId.value = resolveSelectedModelId(imageProviders.value, imageModelId.value)
  } catch (err) {
    error.value = err.message
  } finally {
    imageModelsLoading.value = false
  }
}

onMounted(loadImageProviders)
let saveTimer
watch(draft, () => {
  if (!props.shot?.id) return
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => emit('save', props.shot.id, {
    frameDescription: draft.value.frameDescription || '', action: draft.value.action || '', dialogue: draft.value.dialogue || '',
    shotSize: draft.value.shotSize || 'medium', viewAngle: draft.value.viewAngle || 'eye_level',
    cameraMotion: draft.value.cameraMotion || 'static', durationSeconds: Number(draft.value.durationSeconds || 3), prompt: draft.value.prompt || '',
    assetRefs: draft.value.assetRefs || [],
  }), 500)
}, { deep: true })

async function generate(stage) {
  if (!props.shot || generationActive.value) return
  busy.value = stage
  error.value = ''
  try {
    const response = await api.post(`/storyboards/shots/${props.shot.id}/generate-${stage}`, {
      modelId: imageModelId.value || null,
    })
    emit(stage === 'sketch' ? 'generate-sketch' : 'generate-final', response)
    emit('generation-queued', applyGenerationQueued(props.shot, stage, response))
  } catch (err) {
    error.value = err.message
  } finally { busy.value = '' }
}

function openPreview(index) {
  if (!props.shot || !images.value.length) return
  emit('preview', { shotId: props.shot.id, images: images.value, index })
}

function candidateIndex(url) {
  const found = images.value.findIndex((item) => item.url === url)
  return found < 0 ? 0 : found
}

async function copy(kind) {
  const text = kind === 'full' ? generatedPrompt.value : framePrompt.value
  const ok = await copyText(text)
  copyState.value = ok ? kind : 'failed'
  clearTimeout(copyTimer)
  copyTimer = setTimeout(() => { copyState.value = '' }, 1800)
}
</script>

<template>
  <aside class="inspector">
    <div class="inspector-head"><div><span class="eyebrow">SHOT DESIGN</span><h2>镜头设定</h2></div><span v-if="props.saving" class="save-state">保存中</span></div>
    <div v-if="!props.shot" class="inspector-empty">选择镜头后编辑画面、动作和对白。</div>
    <template v-else>
      <div class="field"><label>画面描述</label><n-input v-model:value="draft.frameDescription" type="textarea" :autosize="{ minRows: 3, maxRows: 6 }" placeholder="描述画面中看见什么" /></div>
      <div class="field"><label>角色动作</label><n-input v-model:value="draft.action" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" placeholder="角色如何移动或反应" /></div>
      <div class="field"><label>对白 / 旁白</label><n-input v-model:value="draft.dialogue" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" placeholder="留空表示无对白" /></div>
      <div class="field-grid"><div class="field"><label>景别</label><n-select v-model:value="draft.shotSize" :options="sizes" /></div><div class="field"><label>视角</label><n-select v-model:value="draft.viewAngle" :options="angles" /></div></div>
      <div class="field"><label>本镜视觉资产</label><n-select v-model:value="draft.assetRefs" :options="assetOptions" multiple clearable placeholder="地点和风格在这里选；角色优先按名字匹配，对不上才用这里的勾选" /></div>
      <button class="prompt-toggle" type="button" @click="promptOpen = !promptOpen">{{ promptOpen ? '收起提示词' : '查看 / 复制提示词' }} <span>{{ promptOpen ? '−' : '+' }}</span></button>
      <div v-if="promptOpen" class="prompt-panel">
        <div class="field prompt-field"><label>生成提示词（可编辑，只写画面）</label><n-input v-model:value="draft.prompt" type="textarea" :autosize="{ minRows: 4, maxRows: 10 }" placeholder="这里只写画面。角色一致性和参考图标注会自动附在后面" />
          <div class="prompt-tools"><n-button size="tiny" quaternary :disabled="!framePrompt" @click="copy('frame')">{{ copyState === 'frame' ? '已复制 ✓' : '复制画面提示词' }}</n-button><small>角色一致性和参考图标注会自动附在后面</small></div>
        </div>
        <div class="field generated-prompt">
          <label>实际出图提示词（只读）</label>
          <template v-if="generatedPrompt">
            <pre class="generated-text">{{ generatedPrompt }}</pre>
            <n-button size="tiny" secondary @click="copy('full')">{{ copyState === 'full' ? '已复制 ✓' : '复制完整提示词' }}</n-button>
            <small>按最近一次生成阶段（{{ props.shot.finalPrompt ? '正式图' : '草图' }}）的入队原文展示，重新生成后会更新。</small>
          </template>
          <p v-else class="field-hint">这一镜还没有生成记录。生成草图或正式图后，这里会出现实际送进生图模型的完整提示词，可直接复制。</p>
        </div>
        <p v-if="copyState === 'failed'" class="inline-error">复制失败，请选中文本手动复制</p>
      </div>
      <div class="field image-model-field">
        <label>生图模型</label>
        <n-select
          v-model:value="imageModelId"
          :options="imageModelOptions"
          :loading="imageModelsLoading"
          :disabled="imageModelsLoading || !imageModelOptions.length || generationActive"
          :consistent-menu-width="false"
          placeholder="没有可用的生图模型"
          aria-label="选择本镜生图模型"
        />
        <p class="field-hint">{{ imageModelHint }}</p>
      </div>
      <div v-if="error" class="inline-error">{{ error }}</div>
      <p v-if="activeStyleLabel" class="field-hint">当前组级统一风格：{{ activeStyleLabel }}；新生成的镜头会套用此风格，已有镜头需重新生成才会变化。</p>
      <div v-if="generationActive" class="generation-status" role="status" aria-live="polite"><span class="generation-spinner" aria-hidden="true"></span><span>{{ props.shot.sketchStatus === 'queued' ? '草图排队中' : props.shot.finalStatus === 'queued' ? '正式图排队中' : props.shot.sketchStatus === 'generating' ? '草图绘制中' : '正式图绘制中' }}</span></div>
      <div class="generate-actions"><n-button type="primary" :loading="busy === 'sketch' || ['queued', 'generating'].includes(props.shot.sketchStatus)" :disabled="busy !== '' || generationActive || !imageModelOptions.length" @click="generate('sketch')">{{ props.shot.sketchStatus === 'queued' ? '草图排队中' : props.shot.sketchStatus === 'generating' ? '草图绘制中' : '生成草图' }}</n-button><n-button secondary :loading="busy === 'final' || ['queued', 'generating'].includes(props.shot.finalStatus)" :disabled="busy !== '' || generationActive || !props.shot.sketchAssetId || !imageModelOptions.length" @click="generate('final')">{{ props.shot.finalStatus === 'queued' ? '正式图排队中' : props.shot.finalStatus === 'generating' ? '正式图绘制中' : '生成正式候选' }}</n-button></div>
      <p class="field-hint">{{ props.shot.sketchStatus === 'failed' ? props.shot.sketchError : props.shot.finalStatus === 'failed' ? props.shot.finalError : '正式图会锁住草图构图，只提高完成度。' }}</p>
      <button v-if="images.length" class="open-viewer" type="button" @click="openPreview(0)">⤢ 放大查看本镜 {{ images.length }} 张图片与提示词</button>
      <div v-if="props.shot.finalUrls?.length" class="candidate-grid">
        <button v-for="(url, index) in props.shot.finalUrls" :key="url" type="button" :title="`放大正式候选 ${index + 1} 并查看提示词`" @click="openPreview(candidateIndex(url))"><img :src="url" :alt="`正式候选 ${index + 1}`" /></button>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.inspector { border-left:1px solid var(--paper-line-strong); background:var(--paper-bg); padding:22px 18px; min-height:560px; overflow:auto; }
.inspector-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:18px; }.eyebrow { color:var(--paper-ink-muted); font-size:10px; letter-spacing:1.4px; } h2 { margin:4px 0 0; font-size:18px; color:var(--paper-ink); }
.save-state,.field-hint { color:var(--paper-ink-muted); font-size:11px; }.inspector-empty { color:var(--paper-ink-muted); font-size:13px; line-height:1.6; padding:20px 4px; }
.field { display:flex; flex-direction:column; gap:6px; margin-bottom:14px; } .image-model-field { margin-top:4px; padding-top:12px; border-top:1px solid var(--paper-line); }.field label { color:var(--paper-ink-soft); font-size:11px; }.field-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.prompt-toggle { display:flex; justify-content:space-between; width:100%; padding:10px 0; border:0; border-top:1px solid var(--paper-line); background:transparent; text-align:left; color:var(--paper-accent); cursor:pointer; font-size:12px; }.prompt-field { padding-top:8px; }
.prompt-tools { display:flex; align-items:center; flex-wrap:wrap; gap:6px; }.prompt-tools small,.generated-prompt small { color:var(--paper-ink-muted); font-size:11px; line-height:1.5; }
.prompt-panel { border-bottom:1px solid var(--paper-line); padding-bottom:6px; }
.generated-text { margin:0 0 4px; padding:9px; max-height:210px; overflow:auto; white-space:pre-wrap; word-break:break-word; background:var(--paper-hover); border:1px solid var(--paper-line); color:var(--paper-ink-soft); font:11px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace; user-select:text; }
.generate-actions { display:flex; flex-direction:column; gap:8px; margin-top:12px; }.generation-status { display:flex; align-items:center; gap:8px; margin-top:14px; padding:9px 10px; border:1px solid var(--paper-warning-line); background:var(--paper-warning-soft); color:var(--paper-warning); font-size:12px; }.generation-spinner { width:12px; height:12px; border:2px solid var(--paper-warning-line); border-top-color:var(--paper-warning); border-radius:50%; animation:storyboard-spin 1s linear infinite; }.inline-error { color:var(--paper-danger); font-size:12px; padding:8px 0; }
.open-viewer { width:100%; margin-top:12px; padding:9px 10px; border:1px solid var(--paper-accent-line); background:var(--paper-accent-tint); color:var(--paper-accent); font-size:12px; text-align:left; cursor:pointer; }.open-viewer:hover { border-color:var(--paper-accent); }
.candidate-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; margin-top:10px; }.candidate-grid button { padding:0; border:1px solid var(--paper-line-strong); background:var(--paper-sunken); cursor:zoom-in; }.candidate-grid button:hover { border-color:var(--paper-accent); }.candidate-grid img { width:100%; aspect-ratio:3/4; object-fit:cover; display:block; }
@keyframes storyboard-spin { to { transform:rotate(360deg); } }
@media(prefers-reduced-motion:reduce) { .generation-spinner { animation:none; } }
</style>
