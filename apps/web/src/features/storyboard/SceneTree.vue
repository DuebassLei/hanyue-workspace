<script setup>
import { hasSceneStyleOverride } from './sceneStyle.js'

const props = defineProps({
  scenes: { type: Array, default: () => [] },
  activeSceneId: { type: String, default: '' },
})
const emit = defineEmits(['select', 'create', 'delete', 'create-shot', 'style'])
</script>

<template>
  <aside class="scene-tree">
    <div class="tree-head">
      <div>
        <span class="eyebrow">STORY MAP</span>
        <h2>场景目录</h2>
      </div>
      <button class="icon-button" type="button" title="新建场景" @click="emit('create')">+</button>
    </div>
    <div v-if="!props.scenes.length" class="tree-empty">先添加一个场景，开始搭建镜头。</div>
    <div v-for="scene in props.scenes" :key="scene.id" class="scene-node" :class="{ active: scene.id === props.activeSceneId }">
      <button class="scene-select" type="button" @click="emit('select', scene.id)">
        <span class="scene-index">{{ String(scene.sortOrder + 1).padStart(2, '0') }}</span>
        <span class="scene-copy"><strong>{{ scene.title }}<span v-if="hasSceneStyleOverride(scene.styleConfig)" class="style-flag" title="该场景已单独设置画风，出图优先使用场景风格">覆盖</span></strong><small>{{ scene.chapter }} · {{ scene.shots?.length || 0 }} 镜头</small></span>
      </button>
      <div class="scene-actions">
        <button type="button" title="场景画风" @click="emit('style', scene.id)">◈</button>
        <button type="button" title="新建镜头" @click="emit('create-shot', scene.id)">＋</button>
        <button type="button" title="删除场景" @click="emit('delete', scene.id)">×</button>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.scene-tree { border-right: 1px solid var(--paper-line-strong); padding: 22px 14px; background: var(--paper-sunken); min-height: 560px; }
.tree-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:20px; }
.eyebrow { color:var(--paper-ink-muted); font-size:10px; letter-spacing:1.4px; }
h2 { margin:4px 0 0; font-size:18px; font-weight:650; color:var(--paper-ink); }
.icon-button, .scene-actions button { border:0; background:transparent; cursor:pointer; color:var(--paper-ink-soft); font-size:20px; }
.icon-button { width:32px; height:32px; border:1px solid var(--paper-line-strong); background:var(--paper-surface); }
.tree-empty { color:var(--paper-ink-muted); font-size:13px; line-height:1.6; padding:16px 8px; }
.scene-node { display:flex; align-items:center; gap:4px; border-left:2px solid transparent; padding:9px 4px 9px 8px; }
.scene-node.active { border-left-color:var(--paper-accent); background:var(--paper-accent-soft); }
.scene-select { display:flex; gap:10px; flex:1; min-width:0; text-align:left; border:0; background:transparent; color:inherit; cursor:pointer; }
.scene-index { font:11px ui-monospace, SFMono-Regular, Consolas, monospace; color:var(--paper-ink-muted); padding-top:2px; }
.scene-copy { min-width:0; display:flex; flex-direction:column; gap:3px; }
.scene-copy strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--paper-ink); font-size:13px; }
.style-flag { margin-left:6px; padding:1px 4px; border:1px solid var(--paper-accent-line); background:var(--paper-accent-tint); color:var(--paper-accent); font-size:9px; font-weight:400; white-space:nowrap; }
.scene-copy small { color:var(--paper-ink-muted); font-size:11px; }
.scene-actions { display:flex; gap:2px; opacity:0; }
.scene-node:hover .scene-actions, .scene-node.active .scene-actions { opacity:1; }
.scene-actions button { font-size:15px; width:22px; height:22px; }
</style>
