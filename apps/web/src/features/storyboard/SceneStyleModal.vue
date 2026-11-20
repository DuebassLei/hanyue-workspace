<script setup>
import { computed } from 'vue'
import { NButton } from 'naive-ui'
import HanddrawStylePicker from './HanddrawStylePicker.vue'
import { hasSceneStyleOverride, sceneStyleNumber } from './sceneStyle.js'

const props = defineProps({
  scene: { type: Object, required: true },
  projectStyleName: { type: String, default: '' },
  styles: { type: Array, default: () => [] },
  groups: { type: Array, default: () => [] },
  loading: Boolean,
  saving: Boolean,
})
const emit = defineEmits(['close', 'save', 'clear'])

const overridden = computed(() => hasSceneStyleOverride(props.scene.styleConfig))
const overrideNumber = computed(() => sceneStyleNumber(props.scene.styleConfig))
</script>

<template>
  <div class="modal-scrim" @click.self="emit('close')">
    <section class="modal-card" role="dialog" aria-modal="true" :aria-label="`场景画风 · ${props.scene.title}`">
      <header class="modal-head">
        <div><span class="eyebrow">SCENE STYLE</span><h2>场景画风 · {{ props.scene.title }}</h2></div>
        <NButton quaternary size="small" @click="emit('close')">关闭</NButton>
      </header>
      <div class="follow-note" :class="{ covered: overridden }" role="status">
        <span v-if="overridden">该场景已单独设置画风，出图优先使用场景风格，不再跟随项目。</span>
        <span v-else>跟随项目风格：{{ props.projectStyleName || '未设置（沿用原有出图方式）' }}</span>
        <NButton v-if="overridden" size="tiny" quaternary :disabled="props.saving" @click="emit('clear')">清除覆盖，跟随项目</NButton>
      </div>
      <p v-if="saving" class="modal-state" role="status">正在保存场景画风…</p>
      <HanddrawStylePicker
        :model-value="overrideNumber"
        :styles="props.styles"
        :groups="props.groups"
        :loading="props.loading"
        :disabled="props.saving"
        title="场景统一画风"
        description="仅影响该场景下新生成的镜头"
        foot-note="未选择时该场景跟随项目画风。更换画风只影响之后新生成的镜头。"
        @update:model-value="emit('save', $event)"
      />
    </section>
  </div>
</template>

<style scoped>
.modal-scrim { position:fixed; inset:0; z-index:60; display:flex; align-items:center; justify-content:center; padding:24px; background:var(--paper-scrim-strong); }
.modal-card { display:flex; width:100%; max-width:760px; max-height:86vh; flex-direction:column; overflow:auto; border:1px solid var(--paper-line-strong); background:var(--paper-bg); box-shadow:var(--paper-image-shadow); }
.modal-head { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:14px 16px; border-bottom:1px solid var(--paper-line-strong); background:var(--paper-header); }
.modal-head h2 { margin:2px 0 0; color:var(--paper-ink); font-size:15px; font-weight:650; }
.eyebrow { color:var(--paper-ink-muted); font-size:10px; letter-spacing:1.4px; }
.follow-note { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:8px 16px; border-bottom:1px solid var(--paper-line); background:var(--paper-accent-tint); color:var(--paper-ink-soft); font-size:12px; }
.follow-note.covered { background:var(--paper-warning-soft); color:var(--paper-warning); }
.modal-state { margin:0; padding:6px 16px; color:var(--paper-ink-muted); font-size:11px; }
</style>
