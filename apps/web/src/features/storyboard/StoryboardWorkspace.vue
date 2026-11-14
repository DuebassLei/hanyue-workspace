<script setup>
import { computed, ref } from 'vue'
import { NButton, NInput } from 'naive-ui'
import SceneTree from './SceneTree.vue'
import ShotList from './ShotList.vue'
import ShotInspector from './ShotInspector.vue'
import AssetBoard from './AssetBoard.vue'
import ShotImageViewer from './ShotImageViewer.vue'

const props = defineProps({ store: { type: Object, required: true } })
const emit = defineEmits(['export-board', 'export-prompts', 'delete-project', 'delete-shot', 'delete-scene'])
const showMobile = ref('shots')
const sourceText = ref('')
const breaking = ref(false)
const breakError = ref('')
const preview = ref(null)
const projectTitle = computed({ get: () => props.store.project.value?.title || '', set: (title) => props.store.patchProject({ title }) })
const chapterLabel = computed(() => props.store.selectedScene.value?.chapter || '未选择场景')
// 只记住镜头 id，提示词始终从最新项目数据取：轮询刷新后弹窗里的完整提示词会同步更新
const previewShot = computed(() => {
  const id = preview.value?.shotId
  if (!id) return null
  for (const scene of props.store.project.value?.scenes || []) {
    const shot = (scene.shots || []).find((item) => item.id === id)
    if (shot) return shot
  }
  return null
})

function openPreview(payload) {
  if (payload?.images?.length) preview.value = payload
}

function setPreviewIndex(index) {
  if (preview.value) preview.value = { ...preview.value, index }
}

async function runBreakdown(scope) {
  if (!sourceText.value.trim()) return
  breaking.value = true
  breakError.value = ''
  try {
    if (scope === 'scene' && props.store.selectedScene.value) {
      await props.store.breakDownScene(props.store.selectedScene.value.id, sourceText.value.trim())
    } else {
      await props.store.breakDownProject(sourceText.value.trim())
    }
    sourceText.value = ''
  } catch (err) {
    breakError.value = err.message
  } finally { breaking.value = false }
}
</script>

<template>
  <div class="workspace">
    <header class="project-bar">
      <div class="project-title"><span class="project-mark">▧</span><n-input v-model:value="projectTitle" class="title-input" :bordered="false" aria-label="分镜项目名称" /></div>
      <div class="project-actions"><span class="save-indicator">{{ props.store.saving.value ? '保存中' : '已保存' }}</span><n-button quaternary size="small" type="error" @click="emit('delete-project')">删除项目</n-button><n-button quaternary size="small" @click="emit('export-board')">分镜板</n-button><n-button quaternary size="small" @click="emit('export-prompts')">提示词包</n-button><n-button secondary size="small" @click="props.store.createScene()">+ 场景</n-button></div>
    </header>
    <section class="breakdown">
      <n-input v-model:value="sourceText" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }" placeholder="粘贴故事正文。拆分只追加草稿，不覆盖已有场景和镜头。" />
      <div>
        <n-button size="small" type="primary" :loading="breaking" @click="runBreakdown('project')">拆成新场景</n-button>
        <n-button size="small" secondary :loading="breaking" :disabled="!props.store.selectedScene.value" @click="runBreakdown('scene')">补拆当前场</n-button>
      </div>
      <p v-if="breakError">{{ breakError }}</p>
    </section>
    <div class="mobile-tabs"><button :class="{ active: showMobile === 'scenes' }" @click="showMobile = 'scenes'">场景</button><button :class="{ active: showMobile === 'shots' }" @click="showMobile = 'shots'">镜头</button><button :class="{ active: showMobile === 'edit' }" @click="showMobile = 'edit'">设定</button></div>
    <main class="editor-grid">
      <div class="panel-scenes" :class="{ 'mobile-active': showMobile === 'scenes' }">
        <SceneTree :scenes="props.store.scenes.value" :active-scene-id="props.store.selectedSceneId.value" @select="props.store.selectScene" @create="props.store.createScene" @delete="emit('delete-scene', $event)" @create-shot="(id) => props.store.createShot(id, {})" />
      </div>
      <div class="panel-shots" :class="{ 'mobile-active': showMobile === 'shots' }">
        <div v-if="props.store.selectedScene.value" class="scene-heading"><div><span>{{ chapterLabel }}</span><n-input :value="props.store.selectedScene.value.title" size="small" :bordered="false" @update:value="props.store.patchScene(props.store.selectedScene.value.id, { title: $event })" /></div><n-button type="primary" size="small" @click="props.store.createShot(props.store.selectedScene.value.id, {})">+ 镜头</n-button></div>
        <ShotList :shots="props.store.shots.value" :selected-shot-id="props.store.selectedShotId.value" @select="props.store.selectShot" @move="props.store.reorderShots(props.store.selectedScene.value.id, $event)" @delete="emit('delete-shot', $event)" @preview="openPreview" />
      </div>
      <div class="panel-inspector" :class="{ 'mobile-active': showMobile === 'edit' }">
        <ShotInspector :shot="props.store.selectedShot.value" :project="props.store.project.value" :saving="props.store.saving.value" @save="props.store.patchShot" @generation-queued="props.store.replaceShot" @preview="openPreview" />
        <AssetBoard :project="props.store.project.value" @save="props.store.saveAssets" />
      </div>
    </main>
    <ShotImageViewer
      :shot="previewShot"
      :images="preview?.images || []"
      :index="preview?.index || 0"
      @close="preview = null"
      @update:index="setPreviewIndex"
    />
  </div>
</template>

<style scoped>
.workspace { border:1px solid #d9d6cf; background:#fbfaf6; }.project-bar { min-height:58px; display:flex; align-items:center; justify-content:space-between; gap:12px; padding:8px 16px; border-bottom:1px solid #d9d6cf; }.breakdown { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; align-items:start; padding:12px 16px; border-bottom:1px solid #e4dfd6; background:#f8f6f1; }.breakdown div { display:flex; gap:8px; }.breakdown p { grid-column:1 / -1; margin:0; color:#9b3e37; font-size:12px; }.project-title,.project-actions { display:flex; align-items:center; gap:10px; }.project-mark { display:grid; place-items:center; width:30px; height:30px; background:#dce8e7; color:#315e68; font-size:18px; }.title-input { max-width:360px; font-weight:650; }.save-indicator { color:#8a8278; font-size:11px; }.editor-grid { display:grid; grid-template-columns:220px minmax(360px,1fr) 310px; align-items:stretch; }.panel-scenes,.panel-inspector { min-width:0; }.panel-shots { min-width:0; border-right:1px solid #d9d6cf; }.scene-heading { display:flex; justify-content:space-between; align-items:center; gap:8px; padding:12px 18px; background:#f8f6f1; border-bottom:1px solid #e1ddd5; }.scene-heading span { display:block; color:#91877d; font-size:10px; }.scene-heading :deep(.n-input) { width:min(250px,52vw); font-weight:600; }.mobile-tabs { display:none; }
@media(max-width:1000px) { .editor-grid { grid-template-columns:190px minmax(330px,1fr); }.panel-inspector { grid-column:1/-1; border-top:1px solid #d9d6cf; }.panel-inspector :deep(.inspector) { min-height:0; } }
@media(max-width:720px) { .project-bar { align-items:flex-start; flex-direction:column; }.breakdown { grid-template-columns:1fr; }.project-actions { width:100%; flex-wrap:wrap; }.mobile-tabs { display:grid; grid-template-columns:repeat(3,1fr); border-bottom:1px solid #d9d6cf; }.mobile-tabs button { padding:10px; border:0; background:#f5f2eb; color:#625a51; }.mobile-tabs button.active { background:#315e68; color:white; }.editor-grid { display:block; }.panel-scenes,.panel-shots,.panel-inspector { display:none; }.panel-scenes.mobile-active,.panel-shots.mobile-active,.panel-inspector.mobile-active { display:block; }.panel-shots { border:0; }.panel-inspector { border-top:0; } }
</style>
