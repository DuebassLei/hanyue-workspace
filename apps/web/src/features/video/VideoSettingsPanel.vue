<script setup>
import { computed, ref } from 'vue'
import { NButton, NInput, NSlider, NSwitch } from 'naive-ui'
import AssetPickerModal from './AssetPickerModal.vue'

/** 全局设置：画幅 / 音色 / 语速 / 字幕 / BGM。改动即保存（防抖由父级处理 @save）。 */
const props = defineProps({ settings: { type: Object, required: true }, busy: Boolean })
const emit = defineEmits(['save'])

const showBgmPicker = ref(false)

const rateOptions = ['-25%', '-10%', '+0%', '+10%', '+25%']
const voiceHint = computed(() => (props.settings.voice || '').replace(/^zh-CN-/, '').replace(/Neural$/, '') || '小晓')

function patch(part) {
  emit('save', { ...props.settings, ...part })
}

function onBgmPicked(asset) {
  patch({ bgmAssetId: asset.id })
}
</script>

<template>
  <section class="video-settings">
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
.video-settings { display:flex; flex-direction:column; gap:11px; }.setting-row { display:flex; align-items:center; gap:9px; }.setting-label { width:64px; flex:none; color:#82796f; font-size:12px; }.setting-note { color:#a2988c; font-size:11px; }.seg { display:flex; border:1px solid #dcd7ce; border-radius:7px; overflow:hidden; }.seg button { padding:4px 10px; border:0; background:#fbfaf7; color:#6d655b; font-size:12px; cursor:pointer; }.seg button + button { border-left:1px solid #dcd7ce; }.seg button.on { background:#41616a; color:#f5f3ee; }.seg button:disabled { opacity:.55; cursor:not-allowed; }.text-input { flex:1; min-width:0; padding:5px 9px; border:1px solid #dcd7ce; border-radius:7px; background:#fdfcfa; font-size:12px; color:#3a352f; }.bgm-slider { flex:1; }.settings-tip { margin:2px 0 0; color:#a2988c; font-size:11px; }
</style>
