<script setup>
import { computed, onMounted, ref } from 'vue'
import { NButton, NSelect, NSlider, NSwitch } from 'naive-ui'
import { api } from '../../api/client'
import { providerOptions, resolveSelectedModelId } from '../../composables/imageProviders.js'
import AssetPickerModal from './AssetPickerModal.vue'
import { DEFAULT_MOTION_FOCUS, DEFAULT_MOTION_INTENSITY, MAX_MOTION_INTENSITY } from './motionPreview.js'

/** 全局设置：画幅 / 生图模型 / 音色 / 语速 / 字幕 / BGM。改动即保存（防抖由父级处理 @save）。 */
const props = defineProps({ settings: { type: Object, required: true }, busy: Boolean })
const emit = defineEmits(['save'])

const showBgmPicker = ref(false)
const advancedMotion = ref(false)
const imageProviders = ref([])
const imageModelsLoading = ref(false)
const imageModelOptions = computed(() => providerOptions(imageProviders.value))
const imageModelId = computed(() => resolveSelectedModelId(imageProviders.value, props.settings.imageModelId || ''))

const rateOptions = ['-25%', '-10%', '+0%', '+10%', '+25%']
const motionIntensity = computed(() => props.settings.motionIntensity ?? DEFAULT_MOTION_INTENSITY)
const motionFocus = computed(() => props.settings.motionFocus ?? DEFAULT_MOTION_FOCUS)

function updateMotionFocus(axis, value) {
  patch({ motionFocus: { ...motionFocus.value, [axis]: value } })
}
const voiceHint = computed(() => (props.settings.voice || '').replace(/^zh-CN-/, '').replace(/Neural$/, '') || '小晓')

function patch(part) {
  emit('save', { ...props.settings, ...part })
}

function onBgmPicked(asset) {
  patch({ bgmAssetId: asset.id })
}

async function loadImageProviders() {
  imageModelsLoading.value = true
  try {
    const status = await api.get('/ai/image-provider/status')
    imageProviders.value = status.providers || []
  } finally {
    imageModelsLoading.value = false
  }
}

onMounted(loadImageProviders)
</script>

<template>
  <section class="video-settings">
    <div class="setting-row">
      <span class="setting-label">生图模型</span>
      <n-select
        class="image-model-select"
        :value="imageModelId"
        :options="imageModelOptions"
        :loading="imageModelsLoading"
        :disabled="busy || imageModelsLoading || !imageModelOptions.length"
        :consistent-menu-width="false"
        placeholder="没有可用的生图模型"
        aria-label="选择镜头配图的生图模型"
        @update:value="(value) => patch({ imageModelId: value || null })"
      />
    </div>
    <p class="settings-tip">只影响还没有配图的镜头，已有图的镜头不会重画。</p>
    <div class="setting-row">
      <span class="setting-label">画幅</span>
      <div class="seg">
        <button type="button" :class="{ on: settings.aspectRatio === '9:16' }" :disabled="busy" @click="patch({ aspectRatio: '9:16' })">竖屏 9:16</button>
        <button type="button" :class="{ on: settings.aspectRatio === '16:9' }" :disabled="busy" @click="patch({ aspectRatio: '16:9' })">横屏 16:9</button>
      </div>
    </div>
    <div class="setting-row">
      <span class="setting-label">旁白音色</span>
      <input v-model.trim="settings.voice" class="text-input" maxlength="64" spellcheck="false" :disabled="busy" title="edge-tts 音色，如 zh-CN-XiaoxiaoNeural / zh-CN-YunxiNeural" @change="patch({ voice: settings.voice })" />
      <span class="setting-note">{{ voiceHint }}</span>
    </div>
    <div class="setting-row">
      <span class="setting-label">语速</span>
      <div class="seg">
        <button v-for="r in rateOptions" :key="r" type="button" :class="{ on: settings.rate === r }" :disabled="busy" @click="patch({ rate: r })">{{ r }}</button>
      </div>
    </div>
    <div class="setting-row">
      <span class="setting-label">硬字幕</span>
      <n-switch size="small" :value="settings.subtitles" :disabled="busy" @update:value="(v) => patch({ subtitles: v })" />
    </div>
    <div class="setting-row motion-setting">
      <span class="setting-label">动态幅度</span>
      <n-slider
        :value="motionIntensity" :min="0" :max="MAX_MOTION_INTENSITY" :step="0.01" :disabled="busy"
        :format-tooltip="(v) => `${Math.round(v * 100)}%`"
        @update:value="(value) => patch({ motionIntensity: value })"
      />
      <span class="motion-value">{{ Math.round(motionIntensity * 100) }}%</span>
    </div>
    <details class="motion-advanced" :open="advancedMotion" @toggle="advancedMotion = $event.target.open">
      <summary>高级动态设置</summary>
      <p class="motion-help">控制画面移动时优先保留的焦点位置；每个镜头可单独覆盖。</p>
      <label class="focus-row">
        <span>水平</span>
        <n-slider :value="motionFocus.x" :min="0" :max="1" :step="0.01" :disabled="busy" :format-tooltip="(v) => `${Math.round(v * 100)}%`" @update:value="(value) => updateMotionFocus('x', value)" />
        <span>{{ Math.round(motionFocus.x * 100) }}%</span>
      </label>
      <label class="focus-row">
        <span>垂直</span>
        <n-slider :value="motionFocus.y" :min="0" :max="1" :step="0.01" :disabled="busy" :format-tooltip="(v) => `${Math.round(v * 100)}%`" @update:value="(value) => updateMotionFocus('y', value)" />
        <span>{{ Math.round(motionFocus.y * 100) }}%</span>
      </label>
    </details>
    <div class="setting-row">
      <span class="setting-label">背景音乐</span>
      <template v-if="settings.bgmAssetId">
        <n-button size="tiny" secondary :disabled="busy" @click="showBgmPicker = true">已选 · 更换</n-button>
        <n-button size="tiny" text type="error" :disabled="busy" @click="patch({ bgmAssetId: null })">移除</n-button>
      </template>
      <n-button v-else size="tiny" secondary :disabled="busy" @click="showBgmPicker = true">选择音频素材</n-button>
    </div>
    <div v-if="settings.bgmAssetId" class="setting-row">
      <span class="setting-label">音乐音量</span>
      <n-slider class="bgm-slider" :value="settings.bgmVolume ?? 0.18" :min="0.02" :max="0.6" :step="0.02" :disabled="busy" :format-tooltip="(v) => `${Math.round(v * 100)}%`" @update:value="(v) => patch({ bgmVolume: v })" />
    </div>
    <p class="settings-tip">修改音色或语速会使已有配音作废，成片时自动重配。</p>
    <AssetPickerModal v-model:show="showBgmPicker" kind="audio" @pick="onBgmPicked" />
  </section>
</template>

<style scoped>
.video-settings { display:flex; flex-direction:column; gap:11px; }.setting-row { display:flex; align-items:center; gap:9px; }.setting-label { width:64px; flex:none; color:var(--paper-ink-muted); font-size:12px; }.setting-note { color:var(--paper-ink-muted); font-size:11px; }.seg { display:flex; border:1px solid var(--paper-line); border-radius:7px; overflow:hidden; }.seg button { padding:4px 10px; border:0; background:var(--paper-surface); color:var(--paper-ink-soft); font-size:12px; cursor:pointer; }.seg button + button { border-left:1px solid var(--paper-line); }.seg button.on { background:var(--paper-accent); color:var(--paper-on-accent); }.seg button:disabled { opacity:.55; cursor:not-allowed; }.text-input { flex:1; min-width:0; padding:5px 9px; border:1px solid var(--paper-line); border-radius:7px; background:var(--paper-surface); font-size:12px; color:var(--paper-ink-soft); }.bgm-slider,.image-model-select { flex:1; min-width:0; }.motion-setting .n-slider { flex:1; }.motion-value { width:34px; text-align:right; color:var(--paper-ink-muted); font-size:11px; }.motion-advanced { padding:0 0 0 73px; color:var(--paper-ink-soft); font-size:12px; }.motion-advanced summary { cursor:pointer; }.motion-help { margin:7px 0; color:var(--paper-ink-muted); font-size:11px; line-height:1.5; }.focus-row { display:flex; align-items:center; gap:8px; margin-top:7px; }.focus-row > span:first-child { width:24px; }.focus-row .n-slider { flex:1; }.focus-row > span:last-child { width:34px; text-align:right; color:var(--paper-ink-muted); font-size:11px; }.settings-tip { margin:2px 0 0; color:var(--paper-ink-muted); font-size:11px; }
@media (max-width:520px) { .setting-row { flex-wrap:wrap; }.motion-advanced { padding-left:0; } }
@media (prefers-reduced-motion: reduce) { .video-settings :deep(*) { scroll-behavior:auto; } }
</style>
