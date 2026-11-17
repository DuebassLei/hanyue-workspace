<script setup>
import { computed } from 'vue'
import { generationStatusLabel } from './generationState.js'
import { shotImages } from './shotImages.js'

const props = defineProps({ shot: { type: Object, required: true }, index: { type: Number, default: 0 }, active: Boolean })
const emit = defineEmits(['select', 'move-up', 'move-down', 'delete', 'preview'])
const images = computed(() => shotImages(props.shot))
const image = computed(() => images.value[0]?.url || '')
const generating = computed(() => ['queued', 'generating'].includes(props.shot.sketchStatus) || ['queued', 'generating'].includes(props.shot.finalStatus))
const statusLabel = computed(() => generationStatusLabel(props.shot))

function select() {
  emit('select', props.shot.id)
}

function openPreview() {
  if (!images.value.length) {
    select()
    return
  }
  emit('preview', { shotId: props.shot.id, images: images.value, index: 0 })
}
</script>

<template>
  <article class="shot-card" :class="{ selected: props.active }">
    <div class="shot-main" role="button" tabindex="0" aria-label="选择镜头" @click="select" @keydown.enter.prevent="select" @keydown.space.prevent="select">
      <button
        class="frame-preview"
        type="button"
        :title="images.length ? '点击放大，查看并复制提示词' : '选中镜头后生成画面'"
        @click.stop="openPreview"
      >
        <img v-if="image" :src="image" :alt="`镜头 ${props.index + 1}`" />
        <span v-else class="placeholder" :class="{ generating }"><span class="crosshair">{{ generating ? '◌' : '＋' }}</span><small>{{ generating ? '正在绘制…' : '尚无画面' }}</small></span>
        <span class="frame-number">{{ String(props.index + 1).padStart(2, '0') }}</span>
        <span v-if="images.length" class="zoom-mark" aria-hidden="true">⤢</span>
      </button>
      <div class="shot-copy">
        <div class="shot-meta"><span>{{ props.shot.shotSize }}</span><span>{{ props.shot.viewAngle }}</span><span>{{ props.shot.durationSeconds }}s</span></div>
        <p>{{ props.shot.frameDescription || '添加画面描述' }}</p>
        <small v-if="props.shot.dialogue">“{{ props.shot.dialogue }}”</small>
      </div>
      <span class="status-mark" :class="props.shot.sketchStatus">{{ statusLabel }}</span>
    </div>
    <div class="order-actions"><button type="button" title="上移镜头" @click="emit('move-up')">↑</button><button type="button" title="下移镜头" @click="emit('move-down')">↓</button><button type="button" title="删除镜头" @click="emit('delete', props.shot.id)">×</button></div>
  </article>
</template>

<style scoped>
.shot-card { display:flex; position:relative; border:1px solid var(--paper-line-strong); background:var(--paper-surface); transition:border-color .15s ease; }
.shot-card.selected { border-color:var(--paper-accent); box-shadow:inset 3px 0 var(--paper-accent); }
.shot-main { display:grid; grid-template-columns:154px minmax(0,1fr) auto; align-items:center; gap:16px; width:100%; padding:12px 14px; border:0; background:transparent; text-align:left; color:inherit; font:inherit; cursor:pointer; }
.shot-main:focus-visible { outline:2px solid var(--paper-accent); outline-offset:-2px; }
.frame-preview { display:block; position:relative; width:100%; padding:0; aspect-ratio:4/3; overflow:hidden; background:var(--paper-sunken); border:1px solid var(--paper-line-strong); cursor:zoom-in; }
.frame-preview img { width:100%; height:100%; object-fit:cover; display:block; }
.zoom-mark { position:absolute; right:5px; bottom:5px; display:grid; place-items:center; width:22px; height:22px; background:var(--paper-scrim-strong); color:var(--paper-on-scrim); font-size:13px; opacity:0; transition:opacity .15s ease; }
.frame-preview:hover .zoom-mark,.frame-preview:focus-visible .zoom-mark { opacity:1; }
.frame-preview:focus-visible { outline:2px solid var(--paper-accent); outline-offset:2px; }
.placeholder { width:100%; height:100%; display:flex; align-items:center; justify-content:center; flex-direction:column; gap:4px; color:var(--paper-ink-muted); }
.crosshair { font-size:24px; line-height:1; color:var(--paper-ink-muted); }
.placeholder small { font-size:10px; }.placeholder.generating { color:var(--paper-warning); }.placeholder.generating .crosshair { animation: storyboard-spin 1.1s linear infinite; }.frame-number { position:absolute; left:6px; top:6px; background:var(--paper-scrim-strong); color:var(--paper-on-scrim); padding:2px 6px; font:11px ui-monospace,monospace; }
.shot-copy { min-width:0; }
.shot-meta { display:flex; gap:10px; font-size:10px; text-transform:capitalize; color:var(--paper-ink-muted); }
.shot-copy p { margin:7px 0 4px; color:var(--paper-ink); font-size:14px; line-height:1.45; }
.shot-copy small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--paper-ink-muted); font-size:12px; }
.status-mark { align-self:start; color:var(--paper-ink-muted); font-size:10px; white-space:nowrap; }
.status-mark.succeeded { color:var(--paper-success); }.status-mark.failed { color:var(--paper-danger); }.status-mark.queued,.status-mark.generating { color:var(--paper-warning); font-weight:650; }
.order-actions { position:absolute; right:9px; bottom:6px; display:flex; opacity:0; }
.shot-card:hover .order-actions,.shot-card.selected .order-actions { opacity:1; }
.order-actions button { border:0; background:transparent; color:var(--paper-ink-muted); cursor:pointer; }
@media(max-width:720px) { .shot-main { grid-template-columns:112px minmax(0,1fr); gap:10px; padding:10px; }.status-mark { position:absolute; right:10px; bottom:8px; }.shot-copy p { font-size:13px; padding-right:54px; } }
@keyframes storyboard-spin { to { transform:rotate(360deg); } }
@media(prefers-reduced-motion:reduce) { .placeholder.generating .crosshair { animation:none; } }
</style>
