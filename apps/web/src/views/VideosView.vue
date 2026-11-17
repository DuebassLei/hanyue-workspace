<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton, NInput } from 'naive-ui'
import { confirm, toast } from '../ui/feedback'
import { useVideo } from '../composables/useVideo'
import { useModelSelectionStore } from '../stores/modelSelection'
import VideoWorkspace from '../features/video/VideoWorkspace.vue'

const route = useRoute()
const router = useRouter()
const store = useVideo()
const models = useModelSelectionStore()

const newTitle = ref('')
const sourceText = ref('')
const listLoading = ref(true)
const listError = ref('')
const creating = ref(false)

const modelOptions = computed(() => models.options.filter((o) => o.value))

const STATUS_LABELS = { draft: '待拆分', scripted: '已拆分', rendering: '成片中', rendered: '已出片', dirty: '有改动' }

async function loadProjects() {
  listLoading.value = true
  listError.value = ''
  try {
    await store.loadProjects()
    const requested = String(route.query.id || '')
    const id = store.projects.value.some((item) => item.id === requested) ? requested : (store.projects.value.length && !history.state?.videoSource ? store.projects.value[0].id : '')
    if (id) await store.loadProject(id)
  } catch (err) {
    listError.value = err.message
  } finally {
    listLoading.value = false
  }
}

/** 从创作空间「转视频」带入的正文：自动建项目并直接拆分镜。 */
async function consumeIncoming() {
  const incoming = history.state?.videoSource
  if (!incoming) return false
  history.replaceState({ ...history.state, videoSource: null }, '')
  creating.value = true
  try {
    const created = await store.createProject({ title: history.state?.videoTitle || '', sourceText: String(incoming) })
    router.replace({ query: { id: created.id } })
    await store.breakdown().catch((err) => { toast(err.message, 'error') })
  } catch (err) {
    listError.value = err.message
  } finally {
    creating.value = false
  }
  return true
}

async function createProject() {
  if (!sourceText.value.trim()) {
    listError.value = '请先粘贴文案内容'
    return
  }
  creating.value = true
  try {
    const created = await store.createProject({ title: newTitle.value.trim(), sourceText: sourceText.value })
    newTitle.value = ''
    sourceText.value = ''
    router.replace({ query: { id: created.id } })
  } catch (err) {
    listError.value = err.message
  } finally {
    creating.value = false
  }
}

async function selectProject(id) {
  try {
    await store.loadProject(id)
    router.replace({ query: { id } })
  } catch (err) { toast(err.message, 'error') }
}

function backToList() {
  store.reset()
  router.replace({ query: {} })
  store.loadProjects().catch((err) => { listError.value = err.message })
}

async function removeProject(item) {
  if (!await confirm(`删除「${item.title}」？镜头与成片缓存会一并清理，素材库中的成品保留。`, '删除视频项目')) return
  try {
    await store.deleteProject(item.id)
    toast('已删除')
  } catch (err) { toast(err.message, 'error') }
}

onMounted(async () => {
  models.load().catch(() => {})
  store.loadProvider()
  if (await consumeIncoming()) return
  await loadProjects()
})
onBeforeUnmount(store.stopPolling)
</script>

<template>
  <div class="videos-page">
    <header class="page-head">
      <div><h1>视频创作</h1><p>粘贴文案或导入稿件，AI 拆解说稿分镜，自动配图配音合成竖屏短视频。</p></div>
      <n-button v-if="store.project.value" secondary @click="backToList">项目列表</n-button>
    </header>
    <n-alert v-if="listError || store.error.value" type="error" closable @close="listError = ''; store.error.value = ''">{{ listError || store.error.value }}</n-alert>

    <template v-if="store.project.value">
      <VideoWorkspace :store="store" :model-options="modelOptions" @back="backToList" />
    </template>

    <section v-else class="project-list">
      <div class="create-box">
        <n-input v-model:value="newTitle" placeholder="视频标题（留空取文案开头）" />
        <n-input v-model:value="sourceText" type="textarea" :rows="4" placeholder="粘贴要做成视频的文案…" maxlength="200000" />
        <div class="create-foot">
          <span class="create-tip">也可以到「创作空间」打开稿件点「转为视频」</span>
          <n-button type="primary" :loading="creating" @click="createProject">创建并开始</n-button>
        </div>
      </div>
      <div v-if="listLoading" class="list-loading">正在读取视频项目…</div>
      <template v-else>
        <div v-if="!store.projects.value.length" class="empty-video"><div class="empty-icon">▶</div><h2>从一段文案开始</h2><p>文案 → AI 解说分镜 → 逐镜配图运镜 → 旁白字幕 → 一条成片。</p></div>
        <div v-for="item in store.projects.value" :key="item.id" class="project-row">
          <button type="button" class="row-main" @click="selectProject(item.id)">
            <span class="project-icon">▶</span>
            <span class="row-text"><strong>{{ item.title }}</strong><small>{{ item.shotCount }} 个镜头 · {{ STATUS_LABELS[item.status] || item.status }} · 更新于 {{ new Date(item.updatedAt).toLocaleDateString('zh-CN') }}</small></span>
            <span class="open-arrow">›</span>
          </button>
          <n-button size="tiny" quaternary type="error" @click.stop="removeProject(item)">删除</n-button>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.videos-page { padding:22px clamp(12px,2vw,26px) 40px; color:var(--paper-ink); }.page-head { display:flex; align-items:center; justify-content:space-between; gap:14px; margin-bottom:18px; }.page-head h1 { margin:0; font-size:24px; font-weight:680; }.page-head p { margin:5px 0 0; color:var(--paper-ink-muted); font-size:13px; }.project-list { max-width:940px; border-top:1px solid var(--paper-line-strong); }.create-box { display:flex; flex-direction:column; gap:9px; margin:18px 0 26px; }.create-foot { display:flex; align-items:center; justify-content:space-between; gap:12px; }.create-tip { color:var(--paper-ink-muted); font-size:12px; }.list-loading { padding:30px 0; color:var(--paper-ink-muted); }.empty-video { text-align:center; padding:44px 16px 60px; border-top:1px solid var(--paper-line); }.empty-icon { display:inline-grid; place-items:center; width:56px; height:56px; border-radius:50%; background:var(--paper-accent-soft); color:var(--paper-accent); font-size:22px; }.empty-video h2 { margin:14px 0 5px; font-size:19px; }.empty-video p { margin:0; color:var(--paper-ink-muted); font-size:13px; }.project-row { display:flex; align-items:center; gap:6px; border-bottom:1px solid var(--paper-line); }.row-main { display:flex; align-items:center; gap:14px; flex:1; min-width:0; padding:13px 8px; border:0; background:transparent; color:inherit; text-align:left; cursor:pointer; }.row-main:hover { background:var(--paper-hover); }.project-icon { display:grid; place-items:center; width:38px; height:38px; flex:none; border-radius:8px; background:var(--paper-accent-soft); color:var(--paper-accent); font-size:15px; }.row-text { min-width:0; }.row-text strong,.row-text small { display:block; }.row-text strong { font-size:14px; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }.row-text small { margin-top:4px; color:var(--paper-ink-muted); font-size:11px; }.open-arrow { margin-left:auto; color:var(--paper-ink-muted); font-size:22px; }.videos-page :deep(.n-alert) { margin-bottom:12px; }
</style>
