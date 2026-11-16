<script setup>
import { computed, ref, watch } from 'vue'
import { NButton } from 'naive-ui'
import AssetPickerModal from './AssetPickerModal.vue'

/** 单个镜头卡片：缩略图 + 旁白编辑 + 配图描述 + 运镜 + 试听。 */
const props = defineProps({
  shot: { type: Object, required: true },
  index: Number,
  aspectRatio: { type: String, default: '9:16' },
  busy: Boolean,
})
const emit = defineEmits(['save', 'remove', 'move'])

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
]

const showPicker = ref(false)
const audioRef = ref(null)
const playing = ref(false)

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
  <article class="shot-card">
    <div class="shot-thumb" :class="{ wide: aspectRatio === '16:9' }">
      <img v-if="shot.imageUrl" :src="shot.imageUrl" :alt="`镜头 ${index + 1}`" loading="lazy" />
      <div v-else class="thumb-blank">
        <span>待配图</span>
        <button type="button" class="thumb-pick" @click="showPicker = true">选素材</button>
      </div>
      <span class="thumb-index">{{ index + 1 }}</span>
      <span v-if="durationText" class="thumb-duration">{{ durationText }}</span>
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
.shot-card { display:flex; gap:12px; padding:12px; border:1px solid #e0dcd4; border-radius:10px; background:#fdfcfa; }.shot-thumb { position:relative; flex:none; width:96px; border-radius:8px; overflow:hidden; background:#eceae4; }.shot-thumb:not(.wide) { aspect-ratio:9/16; }.shot-thumb.wide { width:150px; aspect-ratio:16/9; align-self:center; }.shot-thumb img { width:100%; height:100%; object-fit:cover; }.thumb-blank { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:6px; height:100%; color:#9b9287; font-size:11px; }.thumb-pick { padding:2px 8px; border:1px solid #cfc9bf; border-radius:5px; background:#fff; font-size:11px; color:#5d7f86; cursor:pointer; }.thumb-index { position:absolute; top:5px; left:5px; padding:0 6px; border-radius:4px; background:rgba(30,28,25,.6); color:#fff; font-size:11px; }.thumb-duration { position:absolute; right:5px; bottom:5px; padding:0 5px; border-radius:4px; background:rgba(30,28,25,.6); color:#fff; font-size:10px; }.shot-body { display:flex; flex-direction:column; gap:7px; flex:1; min-width:0; }.shot-narration { padding:7px 9px; border:1px solid #dcd7ce; border-radius:7px; background:#fff; font:inherit; font-size:13px; line-height:1.6; color:#37322c; resize:vertical; }.shot-prompt { padding:5px 9px; border:1px solid #e6e2da; border-radius:7px; background:#faf9f6; font-size:12px; color:#6d655b; }.shot-actions { display:flex; align-items:center; gap:5px; flex-wrap:wrap; }.action-spacer { flex:1; }.seg { display:flex; border:1px solid #dcd7ce; border-radius:7px; overflow:hidden; }.seg button { padding:3px 8px; border:0; background:#fbfaf7; color:#6d655b; font-size:11px; cursor:pointer; }.seg button + button { border-left:1px solid #dcd7ce; }.seg button.on { background:#41616a; color:#f5f3ee; }.seg button:disabled { opacity:.55; }
@media (max-width:640px){ .shot-card { flex-direction:column; }.shot-thumb, .shot-thumb.wide { width:100%; }.shot-thumb:not(.wide) { aspect-ratio:16/9; } }
</style>
