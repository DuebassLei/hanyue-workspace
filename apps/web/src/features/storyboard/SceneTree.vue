<script setup>
const props = defineProps({
  scenes: { type: Array, default: () => [] },
  activeSceneId: { type: String, default: '' },
})
const emit = defineEmits(['select', 'create', 'delete', 'create-shot'])
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
        <span class="scene-copy"><strong>{{ scene.title }}</strong><small>{{ scene.chapter }} · {{ scene.shots?.length || 0 }} 镜头</small></span>
      </button>
      <div class="scene-actions">
        <button type="button" title="新建镜头" @click="emit('create-shot', scene.id)">＋</button>
        <button type="button" title="删除场景" @click="emit('delete', scene.id)">×</button>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.scene-tree { border-right: 1px solid var(--border-color, #d9d6cf); padding: 22px 14px; background: #f5f2eb; min-height: 560px; }
.tree-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:20px; }
.eyebrow { color:#8c8175; font-size:10px; letter-spacing:1.4px; }
h2 { margin:4px 0 0; font-size:18px; font-weight:650; color:#26231f; }
.icon-button, .scene-actions button { border:0; background:transparent; cursor:pointer; color:#5b4e45; font-size:20px; }
.icon-button { width:32px; height:32px; border:1px solid #cfc7bd; background:#fffdf9; }
.tree-empty { color:#8c8175; font-size:13px; line-height:1.6; padding:16px 8px; }
.scene-node { display:flex; align-items:center; gap:4px; border-left:2px solid transparent; padding:9px 4px 9px 8px; }
.scene-node.active { border-left-color:#2f6170; background:#e7eeed; }
.scene-select { display:flex; gap:10px; flex:1; min-width:0; text-align:left; border:0; background:transparent; cursor:pointer; }
.scene-index { font:11px ui-monospace, SFMono-Regular, Consolas, monospace; color:#9d8f83; padding-top:2px; }
.scene-copy { min-width:0; display:flex; flex-direction:column; gap:3px; }
.scene-copy strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#2f2b27; font-size:13px; }
.scene-copy small { color:#978d83; font-size:11px; }
.scene-actions { display:flex; gap:2px; opacity:0; }
.scene-node:hover .scene-actions, .scene-node.active .scene-actions { opacity:1; }
.scene-actions button { font-size:15px; width:22px; height:22px; }
</style>
