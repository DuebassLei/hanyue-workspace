<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { NAlert, NButton, NInput } from 'naive-ui'
import { confirm, toast } from '../ui/feedback'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import { useStoryboard } from '../composables/useStoryboard'
import { useStoryboardJobs } from '../composables/useStoryboardJobs'
import StoryboardWorkspace from '../features/storyboard/StoryboardWorkspace.vue'

const route = useRoute()
const router = useRouter()
const store = useStoryboard()
const newTitle = ref('')
const listLoading = ref(true)
const listError = ref('')
const generating = useStoryboardJobs(store.project, store.refresh)
const showTrash = ref(false)
const trash = ref({ projects: [], scenes: [], shots: [] })

async function loadTrash() {
  trash.value = await api.get('/storyboards/trash')
}

async function openTrash() {
  showTrash.value = true
  await loadTrash()
}

async function deleteProject() {
  if (!await confirm('项目、场景和镜头会移入回收站，生成中的任务会被取消。确定删除吗？', '删除分镜项目')) return
  await store.deleteProject(store.project.value.id)
  toast('项目已移入回收站')
  router.replace({ query: {} })
}

async function deleteScene(id) {
  if (!await confirm('场景及其镜头会移入回收站，确定删除吗？', '删除场景')) return
  await store.deleteScene(id)
  toast('场景已移入回收站')
}

async function deleteShot(id) {
  if (!await confirm('镜头会移入回收站，确定删除吗？', '删除镜头')) return
  await store.deleteShot(id)
  toast('镜头已移入回收站')
}

async function restoreTrash(type, id) {
  await store.restore(type, id)
  await loadTrash()
  await store.loadProjects()
  toast('已恢复')
}

async function purgeTrash(type, id) {
  if (!await confirm('永久删除后无法恢复，关联生成图也可能被清理。确定继续吗？', '永久删除')) return
  await store.deletePermanently(type, id)
  await loadTrash()
  toast('已永久删除')
}

async function loadProjects() {
  listLoading.value = true
  listError.value = ''
  try {
    await store.loadProjects()
    const requested = String(route.query.id || '')
    const id = store.projects.value.some((item) => item.id === requested) ? requested : store.projects.value[0]?.id
    if (id) await store.loadProject(id)
  } catch (err) {
    listError.value = err.message
  } finally { listLoading.value = false }
}

async function createProject() {
  const project = await store.createProject({ title: newTitle.value.trim() || '未命名分镜' })
  newTitle.value = ''
  router.replace({ query: { id: project.id } })
}

async function selectProject(id) {
  await store.loadProject(id)
  router.replace({ query: { id } })
}

async function exportBoard() {
  const data = await api.get(`/storyboards/${store.project.value.id}/exports/board`)
  const scenes = data.scenes.map((scene) => `<section><h2>${escapeHtml(scene.chapter)} / ${escapeHtml(scene.title)}</h2>${scene.shots.map((shot) => `<article><h3>鏡頭 ${shot.sortOrder + 1}</h3><div class="panel">${shot.sketchUrl ? `<img src="${escapeAttr(shot.sketchUrl)}">` : '<div class="blank">待構圖</div>'}<p>${escapeHtml(shot.frameDescription)}</p><p>${escapeHtml(shot.dialogue)}</p></div></article>`).join('')}</section>`).join('')
  const html = `<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>${escapeHtml(data.title)} - 分鏡板</title><style>body{font:14px Arial,sans-serif;color:#292722;margin:32px}h1{border-bottom:2px solid #333;padding-bottom:12px}section{break-inside:avoid;margin:28px 0}article{display:inline-block;vertical-align:top;width:30%;margin:1%;break-inside:avoid}.panel{border:1px solid #999;padding:8px;min-height:180px}.panel img{width:100%;aspect-ratio:4/3;object-fit:cover}.blank{aspect-ratio:4/3;background:#eee;display:grid;place-items:center;color:#777}@media print{body{margin:12mm}}</style><h1>${escapeHtml(data.title)}</h1>${scenes}</html>`
  const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }))
  window.open(url, '_blank', 'noopener')
}

function escapeHtml(value) { return String(value || '').replace(/[&<>"']/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[c]) }
function escapeAttr(value) { return escapeHtml(value) }
async function exportPrompts() {
  const response = await fetch(`/api/v1/storyboards/${store.project.value.id}/exports/prompt-pack`, { headers: { Authorization: `Bearer ${localStorage.getItem('hanyue-token') || ''}` } })
  if (!response.ok) { listError.value = '提示词包导出失败'; return }
  const blob = await response.blob()
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = 'storyboard-prompts.md'
  link.click()
  URL.revokeObjectURL(link.href)
}

onMounted(loadProjects)
onBeforeUnmount(() => generating.stop())
</script>

<template>
  <div class="storyboard-page">
    <header class="page-head"><div><h1>漫画分镜</h1><p>从故事场景拆出镜头，逐格确认构图与画面。</p></div><n-button v-if="store.project.value" secondary @click="store.project.value = null; router.replace({ query: {} })">项目列表</n-button></header>
    <n-alert v-if="listError || store.error.value" type="error" closable @close="listError = ''; store.error.value = ''">{{ listError || store.error.value }}</n-alert>
    <template v-if="store.project.value"><StoryboardWorkspace :store="store" @export-board="exportBoard" @export-prompts="exportPrompts" @delete-project="deleteProject" @delete-scene="deleteScene" @delete-shot="deleteShot" /></template>
    <section v-else class="project-list">
      <div class="list-toolbar"><div class="create-row"><n-input v-model:value="newTitle" placeholder="新分镜项目名称" @keyup.enter="createProject" /><n-button type="primary" @click="createProject">新建分镜</n-button></div><n-button secondary @click="showTrash ? (showTrash = false) : openTrash()">{{ showTrash ? '返回项目' : '回收站' }}</n-button></div>
      <section v-if="showTrash" class="trash-list">
        <h2>回收站</h2>
        <div v-if="!trash.projects.length && !trash.scenes.length && !trash.shots.length" class="list-loading">回收站为空</div>
        <div v-for="item in trash.projects" :key="`project-${item.id}`" class="trash-row"><span><strong>{{ item.title }}</strong><small>整个项目</small></span><n-button size="small" @click="restoreTrash('project', item.id)">恢复</n-button><n-button size="small" type="error" @click="purgeTrash('project', item.id)">永久删除</n-button></div>
        <div v-for="item in trash.scenes" :key="`scene-${item.id}`" class="trash-row"><span><strong>{{ item.title }}</strong><small>场景</small></span><n-button size="small" @click="restoreTrash('scenes', item.id)">恢复</n-button><n-button size="small" type="error" @click="purgeTrash('scenes', item.id)">永久删除</n-button></div>
        <div v-for="item in trash.shots" :key="`shot-${item.id}`" class="trash-row"><span><strong>{{ item.frameDescription || '未命名镜头' }}</strong><small>镜头</small></span><n-button size="small" @click="restoreTrash('shots', item.id)">恢复</n-button><n-button size="small" type="error" @click="purgeTrash('shots', item.id)">永久删除</n-button></div>
      </section>
      <div v-if="showTrash"></div>
      <div v-else-if="listLoading" class="list-loading">正在读取分镜项目…</div>
      <div v-else-if="!store.projects.value.length" class="empty-project"><div class="empty-board">▱</div><h2>从一个故事场景开始</h2><p>新建项目后，先建立场景，再把动作拆成一格格镜头。</p><n-button type="primary" @click="createProject">创建第一个分镜</n-button></div>
      <button v-for="item in store.projects.value" v-else :key="item.id" class="project-row" type="button" @click="selectProject(item.id)"><span class="project-icon">▧</span><span><strong>{{ item.title }}</strong><small>{{ item.scenes?.length || 0 }} 个场景 · 更新于 {{ new Date(item.updatedAt).toLocaleDateString('zh-CN') }}</small></span><span class="open-arrow">›</span></button>
    </section>
  </div>
</template>

<style scoped>
.storyboard-page { padding:22px clamp(12px,2vw,26px) 40px; color:var(--paper-ink); }.page-head { display:flex; align-items:center; justify-content:space-between; gap:14px; margin-bottom:18px; }.page-head h1 { margin:0; font-size:24px; font-weight:680; }.page-head p { margin:5px 0 0; color:var(--paper-ink-muted); font-size:13px; }.project-list { max-width:940px; border-top:1px solid var(--paper-line-strong); }.list-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; }.create-row { display:flex; gap:8px; max-width:580px; margin:18px 0 28px; }.trash-list { border-top:1px solid var(--paper-line); }.trash-list h2 { margin:20px 0 10px; font-size:18px; }.trash-row { display:flex; align-items:center; gap:8px; padding:12px 8px; border-bottom:1px solid var(--paper-line); }.trash-row span { flex:1; min-width:0; }.trash-row strong,.trash-row small { display:block; }.trash-row small { margin-top:3px; color:var(--paper-ink-muted); font-size:11px; }.list-loading { padding:30px 0; color:var(--paper-ink-muted); }.empty-project { text-align:center; padding:50px 16px 65px; border-top:1px solid var(--paper-line); }.empty-board { font-size:50px; line-height:1; color:var(--paper-accent); }.empty-project h2 { margin:14px 0 5px; font-size:19px; }.empty-project p { color:var(--paper-ink-muted); font-size:13px; margin:0 0 18px; }.project-row { display:flex; align-items:center; gap:14px; width:100%; padding:14px 8px; border:0; border-bottom:1px solid var(--paper-line); background:transparent; color:inherit; text-align:left; cursor:pointer; }.project-row:hover { background:var(--paper-hover); }.project-icon { display:grid; place-items:center; width:38px; height:38px; background:var(--paper-accent-soft); color:var(--paper-accent); font-size:20px; }.project-row strong,.project-row small { display:block; }.project-row strong { font-size:14px; }.project-row small { margin-top:4px; color:var(--paper-ink-muted); font-size:11px; }.open-arrow { margin-left:auto; color:var(--paper-ink-muted); font-size:22px; }.storyboard-page :deep(.n-alert) { margin-bottom:12px; }
</style>
