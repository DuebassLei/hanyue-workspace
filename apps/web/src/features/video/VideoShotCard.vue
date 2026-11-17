<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { NButton, NSlider } from 'naive-ui'
import AssetPickerModal from './AssetPickerModal.vue'
import { DEFAULT_MOTION_FOCUS, DEFAULT_MOTION_INTENSITY, MAX_MOTION_INTENSITY, effectiveMotion, motionPreviewStyle } from './motionPreview.js'

/** 单个镜头卡片：缩略图 + 旁白编辑 + 配图描述 + 运镜 + 试听。 */
const props = defineProps({
  shot: { type: Object, required: true },
  index: Number,
  aspectRatio: { type: String, default: '9:16' },
  settings: { type: Object, default: () => ({}) },
  activePreview: Boolean,
  busy: Boolean,
})
const emit = defineEmits(['save', 'remove', 'move', 'preview'])

const narration = ref(props.shot.narration || '')
const imagePrompt = ref(props.shot.imagePrompt || '')
const dirty = computed(
  () => narration.value.trim() !== (props.shot.narration || '').trim()
    || imagePrompt.value.trim() !== (props.shot.imagePrompt || '').trim(),
)
watch(() => props.shot.id, () => {
  narration.value = props.shot.narration || ''
  imagePrompt.value = props.shot.imagePrompt || ''
})

const MOTIONS = [
  { value: 'zoom_in', label: '推近' },
  { value: 'zoom_out', label: '拉远' },
  { value: 'pan_left', label: '左移' },
  { value: 'pan_right', label: '右移' },
  { value: 'still', label: '静止' },
]

const showPicker = ref(false)
const advancedMotion = ref(false)
const focusDragging = ref(false)
const pendingFocus = ref(null)
const shotThumb = ref(null)
const audioRef = ref(null)
const playing = ref(false)
// animating: controls whether the motion-active CSS class is applied.
// Toggling it off then back on forces the browser to restart the keyframe animation
// (reflow trick) when the preview card becomes active or the motion type changes.
const animating = ref(false)
const effective = computed(() => effectiveMotion(props.shot, props.settings))
const previewImageStyle = computed(() => motionPreviewStyle(props.shot, props.settings))
const shotIntensity = computed(() => props.shot.intensity ?? props.settings.motionIntensity ?? DEFAULT_MOTION_INTENSITY)
const shotFocus = computed(() => props.shot.focus ?? props.settings.motionFocus ?? DEFAULT_MOTION_FOCUS)
const displayedFocus = computed(() => pendingFocus.value ?? shotFocus.value)
const hasIntensityOverride = computed(() => props.shot.intensity != null)
const hasFocusOverride = computed(() => props.shot.focus != null)

// Restart animation when: card becomes active, motion direction changes, or intensity changes.
watch(
  [() => props.activePreview, () => props.shot.motion, () => props.shot.intensity, () => props.shot.focus],
  async ([active]) => {
    if (!active) { animating.value = false; return }
    animating.value = false
    await nextTick()
    animating.value = true
  },
  { immediate: true },
)

function updateShotIntensity(value) {
  emit('save', props.shot.id, { intensity: value })
}

function updateShotFocus(axis, value) {
  emit('save', props.shot.id, { focus: { ...shotFocus.value, [axis]: value } })
}

function setFocusFromPointer(event) {
  if (!focusDragging.value || props.busy || !shotThumb.value) return
  const rect = shotThumb.value.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  const inset = effective.value.intensity / (2 * (1 + effective.value.intensity))
  const x = Math.min(1 - inset, Math.max(inset, (event.clientX - rect.left) / rect.width))
  const y = Math.min(1 - inset, Math.max(inset, (event.clientY - rect.top) / rect.height))
  pendingFocus.value = { x: Number(x.toFixed(3)), y: Number(y.toFixed(3)) }
}

function finishFocusDrag() {
  if (!focusDragging.value) return
  focusDragging.value = false
  if (pendingFocus.value) emit('save', props.shot.id, { focus: pendingFocus.value })
  pendingFocus.value = null
}

function cancelFocusDrag() {
  focusDragging.value = false
  pendingFocus.value = null
}

watch(() => props.shot.id, cancelFocusDrag)

function clearFocusOverride() {
  emit('save', props.shot.id, { focus: null })
}

function clearIntensityOverride() {
  emit('save', props.shot.id, { intensity: null })
}

function toggleAudio() {
  const el = audioRef.value
  if (!el) return
  if (playing.value) { el.pause(); return }
  el.play().catch(() => {})
}

function save() {
  emit('save', props.shot.id, {
    narration: narration.value,
    imagePrompt: imagePrompt.value,
  })
}

function setMotion(motion) {
  if (motion !== props.shot.motion) emit('save', props.shot.id, { motion })
}

function picked(asset) {
  emit('save', props.shot.id, { imageAssetId: asset.id })
}

const durationText = computed(() => {
  const ms = props.shot.durationMs
  return ms ? `${Math.round(ms / 100) / 10}s` : ''
})
</script>

<template>
  <article class="shot-card" @mouseenter="emit('preview', shot.id)" @mouseleave="emit('preview', null)">
    <div
      ref="shotThumb"
      class="shot-thumb" :class="{ wide: aspectRatio === '16:9', 'motion-active': animating && shot.imageUrl }"
    >
      <img v-if="shot.imageUrl" :src="shot.imageUrl" :alt="`镜头 ${index + 1}`" loading="lazy" :style="previewImageStyle" />
      <div v-else class="thumb-blank">
        <span>待配图</span>
        <button type="button" class="thumb-pick" @click="showPicker = true">选素材</button>
      </div>
      <span class="thumb-index">{{ index + 1 }}</span>
      <span v-if="durationText" class="thumb-duration">{{ durationText }}</span>
      <button v-if="shot.imageUrl" type="button" class="focus-target" :class="{ dragging: focusDragging }" :style="{ left: `${displayedFocus.x * 100}%`, top: `${displayedFocus.y * 100}%` }" :disabled="busy" aria-label="拖动设置画面焦点" @pointerdown="focusDragging = true; pendingFocus = shotFocus; $event.currentTarget.setPointerCapture($event.pointerId); setFocusFromPointer($event)" @pointermove="setFocusFromPointer" @pointerup="finishFocusDrag" @pointercancel="cancelFocusDrag" @click.stop="!focusDragging && emit('preview', shot.id)">+</button>
    </div>
    <div class="shot-body">
      <textarea
        v-model="narration"
        class="shot-narration"
        rows="3"
        maxlength="1000"
        placeholder="这个镜头的旁白（留空则静音画面）"
      />
      <input v-model.trim="imagePrompt" class="shot-prompt" maxlength="1000" placeholder="配图描述（AI 生图提示词）" />
      <details class="shot-motion-settings" :open="advancedMotion" @toggle="advancedMotion = $event.target.open">
        <summary>动态细节 <span>{{ Math.round(effective.intensity * 100) }}% · 焦点 {{ Math.round(effective.focus.x * 100) }}/{{ Math.round(effective.focus.y * 100) }}</span></summary>
        <div class="motion-control">
          <label>幅度 <n-slider :value="shotIntensity" :min="0" :max="MAX_MOTION_INTENSITY" :step="0.01" :disabled="busy" :format-tooltip="(v) => `${Math.round(v * 100)}%`" @update:value="updateShotIntensity" /></label>
          <button v-if="hasIntensityOverride" type="button" class="inherit-button" :disabled="busy" @click="clearIntensityOverride">使用全局</button>
        </div>
        <div class="motion-control">
          <label>水平 <n-slider :value="shotFocus.x" :min="0" :max="1" :step="0.01" :disabled="busy" :format-tooltip="(v) => `${Math.round(v * 100)}%`" @update:value="(value) => updateShotFocus('x', value)" /></label>
          <button v-if="hasFocusOverride" type="button" class="inherit-button" :disabled="busy" @click="clearFocusOverride">使用全局</button>
        </div>
        <div class="motion-control">
          <label>垂直 <n-slider :value="shotFocus.y" :min="0" :max="1" :step="0.01" :disabled="busy" :format-tooltip="(v) => `${Math.round(v * 100)}%`" @update:value="(value) => updateShotFocus('y', value)" /></label>
        </div>
        <p class="focus-hint">点按或拖动缩略图上的十字设置焦点。清除覆盖后将跟随全局设置。</p>
      </details>
      <div class="shot-actions">
        <div class="seg mini">
          <button v-for="m in MOTIONS" :key="m.value" type="button" :class="{ on: shot.motion === m.value }" :disabled="busy" :title="m.label" @click="setMotion(m.value)">{{ m.label }}</button>
        </div>
        <span class="action-spacer" />
        <audio v-if="shot.audioUrl" ref="audioRef" :src="shot.audioUrl" preload="none" @play="playing = true" @pause="playing = false" @ended="playing = false" />
        <n-button v-if="shot.audioUrl" size="tiny" round secondary @click="toggleAudio">{{ playing ? '⏸ 暂停' : '▶ 试听' }}</n-button>
        <n-button v-if="dirty" size="tiny" type="primary" :disabled="busy" @click="save">保存</n-button>
        <n-button size="tiny" quaternary :disabled="busy" title="上移" @click="emit('move', index, -1)">↑</n-button>
        <n-button size="tiny" quaternary :disabled="busy" title="下移" @click="emit('move', index, 1)">↓</n-button>
        <n-button size="tiny" quaternary type="error" :disabled="busy" @click="emit('remove', shot.id)">删</n-button>
      </div>
    </div>
    <AssetPickerModal v-model:show="showPicker" kind="image" @pick="picked" />
  </article>
</template>

<style scoped>
.shot-card { display:flex; gap:12px; padding:12px; border:1px solid var(--paper-line); border-radius:10px; background:var(--paper-surface); }.shot-thumb { position:relative; flex:none; width:96px; border-radius:8px; overflow:hidden; background:var(--paper-thumb); }.shot-thumb:not(.wide) { aspect-ratio:9/16; }.shot-thumb.wide { width:150px; aspect-ratio:16/9; align-self:center; }.shot-thumb img { width:100%; height:100%; object-fit:cover; object-position:var(--motion-focus-x, 50%) var(--motion-focus-y, 50%); }.shot-thumb.motion-active img { transform-origin:var(--motion-focus-x, 50%) var(--motion-focus-y, 50%); animation:var(--motion-animation, motion-zoom-in) var(--motion-duration, 12s) cubic-bezier(0.25, 0.1, 0.1, 1) forwards; will-change:transform; }.focus-target { position:absolute; z-index:2; width:22px; height:22px; padding:0; border:1px solid var(--paper-image-outline); border-radius:50%; background:var(--paper-scrim-strong); color:var(--paper-on-scrim); font-size:15px; line-height:19px; transform:translate(-50%,-50%); cursor:grab; touch-action:none; box-shadow:var(--paper-image-shadow); }.focus-target.dragging { cursor:grabbing; }.focus-target:focus-visible { outline:2px solid var(--paper-warning); outline-offset:2px; }.thumb-blank { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:6px; height:100%; color:var(--paper-ink-muted); font-size:11px; }.thumb-pick { padding:2px 8px; border:1px solid var(--paper-line-strong); border-radius:5px; background:var(--paper-surface); font-size:11px; color:var(--paper-accent); cursor:pointer; }.thumb-index { position:absolute; top:5px; left:5px; padding:0 6px; border-radius:4px; background:var(--paper-scrim); color:var(--paper-on-scrim); font-size:11px; }.thumb-duration { position:absolute; right:5px; bottom:5px; padding:0 5px; border-radius:4px; background:var(--paper-scrim); color:var(--paper-on-scrim); font-size:10px; }.shot-body { display:flex; flex-direction:column; gap:7px; flex:1; min-width:0; }.shot-motion-settings { padding-top:2px; color:var(--paper-ink-soft); font-size:11px; }.shot-motion-settings summary { cursor:pointer; }.shot-motion-settings summary span { margin-left:6px; color:var(--paper-ink-muted); }.motion-control { display:flex; align-items:center; gap:8px; margin-top:7px; }.motion-control label { display:flex; align-items:center; gap:8px; flex:1; min-width:0; }.motion-control label > :first-child { width:28px; flex:none; }.motion-control label .n-slider { flex:1; }.inherit-button { padding:2px 6px; border:1px solid var(--paper-line); border-radius:5px; background:var(--paper-surface); color:var(--paper-ink-soft); font-size:10px; cursor:pointer; }.inherit-button:disabled { opacity:.55; }.focus-hint { margin:7px 0 0; color:var(--paper-ink-muted); line-height:1.5; }.shot-narration { padding:7px 9px; border:1px solid var(--paper-line); border-radius:7px; background:var(--paper-surface); font:inherit; font-size:13px; line-height:1.6; color:var(--paper-ink-soft); resize:vertical; }.shot-prompt { padding:5px 9px; border:1px solid var(--paper-line); border-radius:7px; background:var(--paper-surface); font-size:12px; color:var(--paper-ink-soft); }.shot-actions { display:flex; align-items:center; gap:5px; flex-wrap:wrap; }.action-spacer { flex:1; }.seg { display:flex; border:1px solid var(--paper-line); border-radius:7px; overflow:hidden; }.seg button { padding:3px 8px; border:0; background:var(--paper-surface); color:var(--paper-ink-soft); font-size:11px; cursor:pointer; }.seg button + button { border-left:1px solid var(--paper-line); }.seg button.on { background:var(--paper-accent); color:var(--paper-on-accent); }.seg button:disabled { opacity:.55; }
@keyframes motion-zoom-in { from { transform:scale(var(--kb-scale-from, 1)) translate(calc(-1 * var(--kb-dx, 0%)), calc(-1 * var(--kb-dy, 0%))); } to { transform:scale(var(--kb-scale-to, 1.12)) translate(0, 0); } }
@keyframes motion-zoom-out { from { transform:scale(var(--kb-scale-from, 1.12)) translate(calc(-1 * var(--kb-dx, 0%)), calc(-1 * var(--kb-dy, 0%))); } to { transform:scale(var(--kb-scale-to, 1)) translate(0, 0); } }
@keyframes motion-pan-left { from { transform:scale(var(--kb-scale-from, 1)) translate(0, 0); } to { transform:scale(var(--kb-scale-to, 1)) translate(calc(-1 * var(--kb-dx, 0%)), calc(-1 * var(--kb-dy, 0%))); } }
@keyframes motion-pan-right { from { transform:scale(var(--kb-scale-from, 1)) translate(calc(-1 * var(--kb-dx, 0%)), calc(-1 * var(--kb-dy, 0%))); } to { transform:scale(var(--kb-scale-to, 1)) translate(0, 0); } }
@keyframes motion-still { from { transform:scale(var(--kb-scale-from, 0.99)); } to { transform:scale(var(--kb-scale-to, 1.01)); } }
@media (prefers-reduced-motion: reduce) { .shot-thumb.motion-active img { animation:none; transform:none; } }
@media (max-width:640px){ .shot-card { flex-direction:column; }.shot-thumb, .shot-thumb.wide { width:100%; }.shot-thumb:not(.wide) { aspect-ratio:16/9; } }
</style>
