<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton, NSpin, NStep, NSteps } from 'naive-ui'
import { api } from '../../api/client'
import { toast } from '../../ui/feedback'
import StickerIdeateStep from './StickerIdeateStep.vue'
import StickerOutlineStep from './StickerOutlineStep.vue'
import StickerReviewStep from './StickerReviewStep.vue'

const props = defineProps({
  // 新建：{ theme?, track?, hotClusterId? }；编辑：传 sticker（列表行，挂载时拉全量详情）
  preset: { type: Object, default: null },
  sticker: { type: Object, default: null },
})

const emit = defineEmits(['close', 'saved'])

const route = useRoute()
const router = useRouter()

const stylesData = ref({ styles: [], trackDefaults: {}, narratives: [] })
const step = ref(1)
const detail = ref(null)
// Step2 的行数据来源：AI 构思结果或「再补一张」拼出来的列表；null = 用集合已有 images
const outlineRows = ref(null)
const busy = ref(false)
const error = ref('')
const loading = ref(false)

let pollTimer = null
let pollRounds = 0
const MAX_POLL_ROUNDS = 150 // 2s × 150 = 5 分钟

const stepStatus = computed(() => (step.value === 2 ? 'process' : 'finish'))

/** Step1 的 initial：编辑回填集合字段；新建时带预填主题/赛道与赛道默认风格 */
const initialForStep1 = computed(() => {
  if (detail.value) {
    return {
      theme: detail.value.theme,
      styleId: detail.value.styleId,
      narrative: detail.value.narrative,
      images: detail.value.images,
    }
  }
  const preset = props.preset || {}
  return {
    theme: preset.theme || '',
    trackStyleId: stylesData.value.trackDefaults[preset.track] || '',
  }
})

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

function isGenerating(set) {
  return (set?.images || []).some((item) => ['queued', 'generating'].includes(item.status))
}

function startPolling() {
  if (pollTimer) return
  pollRounds = 0
  pollTimer = setInterval(async () => {
    pollRounds += 1
    if (pollRounds > MAX_POLL_ROUNDS) {
      stopPolling()
      error.value = '生成状态轮询超时（5 分钟），稍后刷新本页查看结果'
      return
    }
    try {
      await refreshDetail()
    } catch (err) {
      stopPolling()
      error.value = `生成状态查询中断：${err.message}`
    }
  }, 2000)
}

async function refreshDetail() {
  if (!detail.value) return
  const fresh = await api.get(`/stickers/${detail.value.id}`)
  detail.value = fresh
  emit('saved', fresh)
  if (!isGenerating(fresh)) stopPolling()
}

/** 生成动作共用的乐观更新：把目标图先标 queued，立刻进轮询 */
function markQueued(imageIds) {
  if (!detail.value) return
  const ids = new Set(imageIds)
  detail.value = {
    ...detail.value,
    status: 'generating',
    images: (detail.value.images || []).map((item) =>
      // 已在跑的行保留真实状态，只把 pending/failed 的行标成排队中
      ids.has(item.id) && ['pending', 'failed'].includes(item.status)
        ? { ...item, status: 'queued', errorCode: null }
        : item,
    ),
  }
  startPolling()
}

async function runAction(fn, failText) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await fn()
  } catch (err) {
    error.value = `${failText}：${err.message}`
  } finally {
    busy.value = false
  }
}

/** 守卫：没有分镜就不许进 Step3——先补 outline 再生图 */
function gotoStep(target) {
  if (target === 3 && !(detail.value?.images || []).length) return
  step.value = target
}

let booted = false

/** 容器挂载时加载风格库；编辑态由父级带入 sticker（拉全量详情），新建态可带 preset */
async function boot() {
  if (booted) return
  booted = true
  loading.value = true
  error.value = ''
  try {
    const [styles, full] = await Promise.all([
      api.get('/sticker-styles'),
      props.sticker ? api.get(`/stickers/${props.sticker.id}`) : Promise.resolve(null),
    ])
    stylesData.value = styles
    if (full) {
      detail.value = full
      // 按 status 定位步骤：未出分镜回 Step1，有分镜的（outlined/generating/review/done）进 Step3
      step.value = (full.images || []).length ? 3 : 1
      if (isGenerating(full)) startPolling()
    }
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

// 列表页点开的集合可能晚于挂载才就位（同一组件实例复用）
watch(() => props.sticker, boot)

boot()

// —— Step1 → Step2 ——
async function onIdeateSubmit(event) {
  await runAction(async () => {
    let set = detail.value
    const payload = event.payload
    if (set) {
      set = await api.patch(`/stickers/${set.id}`, {
        title: payload.title,
        theme: payload.theme,
        styleId: payload.styleId,
        narrative: payload.narrative,
      })
    } else {
      set = await api.post('/stickers', {
        theme: payload.theme,
        title: payload.title,
        styleId: payload.styleId,
        track: props.preset?.track || null,
        hotClusterId: props.preset?.hotClusterId || null,
        narrative: payload.narrative,
      })
    }
    detail.value = set
    outlineRows.value = event.mode === 'ideate' ? event.items : null
    step.value = 2
  }, '保存构思失败')
}

// —— Step2 → Step3 ——
async function onOutlineConfirm(items) {
  await runAction(async () => {
    const saved = await api.put(`/stickers/${detail.value.id}/outline`, { items })
    detail.value = saved
    outlineRows.value = null
    step.value = 3
    if (saved.status === 'generating') {
      // done 集合「再补一张」回来的路径：旧 asset 已随重排带回，
      // 走 generate-rest——未变行秒过复用，只有新行真正入队。
      const updated = await api.post(`/stickers/${saved.id}/generate-rest`, {})
      detail.value = updated
      markQueued((updated.images || []).map((item) => item.id))
      return
    }
    // 首次确认方案即启动锚点生成（契约：PUT outline → generate-anchor）
    await api.post(`/stickers/${saved.id}/generate-anchor`, {})
    markQueued((saved.images || []).slice(0, 1).map((item) => item.id))
  }, '确认分镜失败')
}

// —— Step3 操作 ——
function onGenerateAnchor() {
  runAction(async () => {
    const updated = await api.post(`/stickers/${detail.value.id}/generate-anchor`, {})
    detail.value = updated
    markQueued([updated.images?.[0]?.id].filter(Boolean))
  }, '锚点生成请求失败')
}

function onGenerateRest() {
  runAction(async () => {
    const updated = await api.post(`/stickers/${detail.value.id}/generate-rest`, {})
    detail.value = updated
    markQueued((updated.images || []).slice(1).map((item) => item.id))
  }, '批量生成请求失败')
}

function onRetryImage(image) {
  runAction(async () => {
    await api.post(`/stickers/${detail.value.id}/images/${image.id}/retry`, {})
    markQueued([image.id])
  }, '重试请求失败')
}

function onRetryFailed() {
  runAction(async () => {
    const updated = await api.post(`/stickers/${detail.value.id}/retry-failed`, {})
    detail.value = updated
    markQueued((updated.images || []).map((item) => item.id))
  }, '批量重试失败')
}

function onUpdateCaption({ id, caption }) {
  runAction(async () => {
    // 配文是行级数据：PATCH 契约未声明 images 字段（会被静默忽略），
    // 所以走 outline 全量覆盖，目标行换配文、其余字段原样带回。
    const items = (detail.value.images || []).map((item) => ({
      caption: item.id === id ? caption : item.caption,
      framePrompt: item.framePrompt,
      slotType: item.slotType,
    }))
    detail.value = await api.put(`/stickers/${detail.value.id}/outline`, { items })
  }, '配文保存失败')
}

function onAppend(row) {
  // 「再补一张」：带着当前分镜 + 新行回到 Step2 编辑确认
  outlineRows.value = [
    ...(detail.value.images || []).map(({ caption, framePrompt, slotType }) => ({
      caption,
      framePrompt,
      slotType,
    })),
    row,
  ]
  step.value = 2
}

async function onFinalize() {
  await runAction(async () => {
    const updated = await api.post(`/stickers/${detail.value.id}/finalize`, {})
    detail.value = updated
    toast('贴图集已归档')
    close()
  }, '归档失败')
}

function goBack() {
  gotoStep(step.value - 1)
}

function close() {
  // 清掉 ?new=1&clusterId=... 之类的入口参数，避免刷新重放新建流程
  if (route.query.new || route.query.clusterId || route.query.id) {
    router.replace({ name: 'stickers' })
  }
  emit('close')
}

// 离开向导必须停表，否则后台每 2 秒打一次接口
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="wizard stack">
    <div class="row wizard-head">
      <n-button quaternary size="small" @click="close">← 返回列表</n-button>
      <span v-if="detail" class="wizard-title" :title="detail.title">{{ detail.title || '未命名贴图' }}</span>
      <div class="spacer" />
      <span v-if="detail" class="field-hint">{{ detail.succeeded }}/{{ detail.total }} 张</span>
    </div>

    <n-steps :current="step" :status="stepStatus" size="small">
      <n-step title="主题与画风" @click="gotoStep(1)" />
      <n-step title="分镜方案" @click="gotoStep(2)" />
      <n-step title="生成与检查" @click="gotoStep(3)" />
    </n-steps>

    <n-alert v-if="error" type="error" closable @close="error = ''">{{ error }}</n-alert>

    <div v-if="loading || (props.sticker && !detail)" class="wizard-spin"><n-spin size="small" /></div>

    <StickerIdeateStep
      v-else-if="step === 1"
      :styles="stylesData.styles"
      :track-defaults="stylesData.trackDefaults"
      :narratives="stylesData.narratives"
      :initial="initialForStep1"
      :busy="busy"
      @submit="onIdeateSubmit"
    />
    <StickerOutlineStep
      v-else-if="step === 2"
      :images="outlineRows || detail?.images || []"
      :busy="busy"
      @confirm="onOutlineConfirm"
    />
    <StickerReviewStep
      v-else-if="detail"
      :detail="detail"
      :busy="busy"
      @generate-anchor="onGenerateAnchor"
      @generate-rest="onGenerateRest"
      @retry-image="onRetryImage"
      @retry-failed="onRetryFailed"
      @update-caption="onUpdateCaption"
      @download-error="error = $event"
      @append="onAppend"
      @finalize="onFinalize"
    />

    <div v-if="!loading && step > 1 && detail?.status !== 'done'" class="row wizard-back">
      <n-button quaternary size="small" :disabled="busy" @click="goBack">上一步</n-button>
    </div>
  </div>
</template>

<style scoped>
.wizard-head {
  gap: var(--space-2);
}
.wizard-title {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.wizard-spin {
  display: flex;
  justify-content: center;
  padding: var(--space-6);
}
.wizard-back {
  border-top: 1px dashed var(--color-border);
  padding-top: var(--space-2);
}
</style>
