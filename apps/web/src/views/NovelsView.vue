<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton } from 'naive-ui'
import { confirm } from '../ui/feedback'
import { useNovel } from '../composables/useNovel'
import AppModal from '../components/AppModal.vue'
import ModelSelector from '../components/ModelSelector.vue'
import NovelList from '../features/novel/NovelList.vue'
import NovelCreateForm from '../features/novel/NovelCreateForm.vue'
import NovelChapterList from '../features/novel/NovelChapterList.vue'
import NovelEditor from '../features/novel/NovelEditor.vue'
import NovelBible from '../features/novel/NovelBible.vue'
import NovelContinue from '../features/novel/NovelContinue.vue'

const route = useRoute()
const router = useRouter()
const store = useNovel()
const listLoading = ref(true)
const listError = ref('')
const creating = ref(false)
const splitting = ref(false)
const accepting = ref(false)
const actionError = ref('')
const showCreate = ref(false)
const createError = ref('')

// 任务错误 → 人话。映射不到的原样显示 errorMessage，方便按 errorCode 查供应商日志。
const JOB_ERROR_TEXT = {
  provider_unavailable: '文本模型未配置或订阅已失效，请到「模型管理」检查并测试通过后重试',
}

const failedJobMessage = computed(() => {
  const failed = store.job.value?.status === 'failed' ? store.job.value : null
  if (!failed) return ''
  const hint = JOB_ERROR_TEXT[failed.errorCode]
  const detail = String(failed.errorMessage || '').trim()
  if (!hint) return detail || '任务失败'
  if (!detail || detail === hint) return hint
  // 固定指引之外附上供应商真实原因（订阅过期、模型不存在、400 响应体），
  // 否则页面只会笼统说「模型未配置」，无从判断是哪个连接出了问题。
  const brief = detail.length > 220 ? `${detail.slice(0, 220)}…` : detail
  return `${hint}（${brief}）`
})
const pageError = computed(() => actionError.value || listError.value || store.error.value)
// 任务结果如实回报实际模型；降级 / 默认失效都要看得见
const jobModelInfo = computed(() => {
  const result = store.job.value?.result || {}
  if (!result.modelCode) return ''
  const parts = [`本次生成模型：${result.modelCode}`]
  if (result.fallbackUsed) parts.push('（已自动降级）')
  if (result.defaultModelIgnored) parts.push(`默认模型失效：${result.defaultModelIgnored.message}`)
  return parts.join('，')
})
const sourcePreview = computed(() => String(store.project.value?.sourceText || '').slice(0, 260))
const impactChapters = computed(() => store.selectedChapter.value?.impact?.chapters || [])

// 稿件台顶栏的一句话概况：多少章、写到哪一步
const stats = computed(() => {
  const list = store.chapters.value
  const count = (status) => list.filter((item) => item.status === status).length
  return { total: list.length, accepted: count('accepted'), drafted: count('drafted'), pending: count('pending') }
})

async function loadProjects() {
  listLoading.value = true
  listError.value = ''
  try {
    await store.loadProjects()
    const requested = String(route.query.id || '')
    const id = store.projects.value.some((item) => item.id === requested) ? requested : ''
    if (id) await store.loadProject(id)
  } catch (err) {
    listError.value = err.message
  } finally {
    listLoading.value = false
  }
}

async function selectProject(id) {
  actionError.value = ''
  try {
    await store.loadProject(id)
    router.replace({ query: { id } })
  } catch (err) {
    actionError.value = err.message
  }
}

async function createProject(payload) {
  creating.value = true
  createError.value = ''
  try {
    const project = await store.createProject(payload)
    showCreate.value = false
    router.replace({ query: { id: project.id } })
  } catch (err) {
    // 弹层开着的时候错误要留在弹层里，否则提示会藏在遮罩后面
    createError.value = err.message
    actionError.value = err.message
  } finally {
    creating.value = false
  }
}

function openCreate() {
  createError.value = ''
  showCreate.value = true
}

function closeCreate() {
  showCreate.value = false
  createError.value = ''
}

async function deleteProject(id) {
  if (!await confirm('删除后项目、章节和人物都会去掉。确定删除吗？', '删除小说')) return
  actionError.value = ''
  try {
    await store.deleteProject(id)
    if (!store.project.value) router.replace({ query: {} })
  } catch (err) {
    actionError.value = err.message
  }
}

function backToLibrary() {
  store.project.value = null
  router.replace({ query: {} })
}

async function run(action) {
  actionError.value = ''
  try {
    await action()
  } catch (err) {
    actionError.value = err.message
  }
}

async function split() {
  splitting.value = true
  try {
    await run(() => store.split())
  } finally {
    splitting.value = false
  }
}

async function accept() {
  const chapter = store.selectedChapter.value
  if (!chapter) return
  if (!await confirm('接受后会成为正式正文。', '接受本章')) return
  accepting.value = true
  try {
    await run(() => store.accept(chapter.id))
  } finally {
    accepting.value = false
  }
}

function clearError() {
  actionError.value = ''
  listError.value = ''
  store.error.value = ''
}

onMounted(loadProjects)
</script>

<template>
  <div class="page novel-page">
    <header class="page-head">
      <div>
        <h1 class="page-title">小说创作</h1>
        <p class="page-sub">从一份你有权改写的文本切章、改写并续写。</p>
      </div>
      <div class="head-actions">
        <NButton v-if="store.project.value" size="small" tertiary @click="backToLibrary">作品列表</NButton>
        <NButton type="primary" size="small" @click="openCreate">+ 新建作品</NButton>
      </div>
    </header>

    <NAlert v-if="pageError" type="error" closable @close="clearError">{{ pageError }}</NAlert>
    <NAlert v-if="failedJobMessage" type="error">
      {{ failedJobMessage }}<template v-if="store.job.value?.errorCode !== 'provider_unavailable'">。可以再点一次。</template>
    </NAlert>

    <div v-if="listLoading" class="loading-card">正在读取小说…</div>

    <NovelList
      v-else-if="!store.project.value"
      :projects="store.projects.value"
      @create="openCreate"
      @select="selectProject"
      @delete="deleteProject"
    />

    <div v-else class="workspace">
      <div class="project-bar">
        <span class="project-mark" aria-hidden="true">
          <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.4">
            <path d="M10 5.6C8 4.3 5.4 4.1 3 4.3v11c2.4-.2 5 0 7 1.3 2-1.3 4.6-1.5 7-1.3v-11c-2.4-.2-5 0-7 1.3Z" stroke-linejoin="round" />
            <path d="M10 5.6v11" />
          </svg>
        </span>
        <div class="project-id">
          <h2 class="project-name">{{ store.project.value.title || '未命名' }}</h2>
          <p class="project-meta">
            {{ stats.total }} 章
            <span class="meta-sep">·</span> 已接受 {{ stats.accepted }}
            <span class="meta-sep">·</span> 有草稿 {{ stats.drafted }}
            <span class="meta-sep">·</span> 待改写 {{ stats.pending }}
          </p>
        </div>
        <div class="project-actions">
          <NButton size="small" :loading="splitting" :disabled="store.jobBusy.value" @click="split">切章</NButton>
          <NButton size="small" :disabled="store.jobBusy.value" @click="run(() => store.extract())">抽取人名</NButton>
          <span class="act-sep" aria-hidden="true" />
          <NButton size="small" tertiary @click="run(() => store.exportFile('md'))">导出 md</NButton>
          <NButton size="small" tertiary @click="run(() => store.exportFile('txt'))">导出 txt</NButton>
          <NButton size="small" quaternary type="error" @click="deleteProject(store.project.value.id)">删除</NButton>
        </div>
      </div>

      <div class="model-strip">
        <ModelSelector :disabled="store.jobBusy.value" label="生成模型" />
      </div>

      <div v-if="store.jobBusy.value || jobModelInfo" class="job-strip">
        <span v-if="store.jobBusy.value" class="job-spinner" aria-hidden="true" />
        <span v-if="store.jobBusy.value">任务进行中，章节仍可查看。</span>
        <span v-if="jobModelInfo">{{ jobModelInfo }}</span>
      </div>

      <div class="work-grid">
        <NovelChapterList
          :chapters="store.chapters.value"
          :active-id="store.selectedChapter.value?.id || ''"
          @select="store.selectChapter"
        />

        <div class="editor-col">
          <section v-if="!store.chapters.value.length" class="pane-empty">
            <h3>还没有章节</h3>
            <p>把原文切成章节后，可以逐章改写、并排对照和续写。</p>
            <pre v-if="sourcePreview" class="source-peek">{{ sourcePreview }}</pre>
            <NButton type="primary" :loading="splitting" @click="split">切章</NButton>
          </section>
          <NovelEditor
            v-else-if="store.selectedChapter.value"
            :chapter="store.selectedChapter.value"
            :busy="store.jobBusy.value"
            :accepting="accepting"
            @rewrite="run(() => store.rewrite(store.selectedChapter.value.id))"
            @accept="accept"
            @revert="run(() => store.revert(store.selectedChapter.value.id))"
            @split="split"
          />
          <section v-else class="pane-empty">
            <h3>选一章开始</h3>
            <p>左侧点开任意章节，看原文、草稿和已接受正文。</p>
          </section>
        </div>

        <aside class="side-col">
          <NovelContinue
            :busy="store.jobBusy.value"
            :warnings="store.warnings.value"
            :impact-chapters="impactChapters"
            @continue="run(() => store.continueNext())"
          />
          <NovelBible
            :rules="store.project.value.rules"
            :characters="store.characters.value"
            :aliases="store.aliases.value"
            @patch-rules="(rules) => run(() => store.patchProject({ rules }))"
            @patch-character="(id, payload) => run(() => store.patchCharacter(id, payload))"
            @patch-alias="(id, payload) => run(() => store.patchAlias(id, payload))"
          />
        </aside>
      </div>
    </div>

    <AppModal
      v-if="showCreate"
      title="新建小说"
      description="粘一段你有权改写的原文，切章后逐章改写；没有章节标题时会按段落兜底切分。"
      @close="closeCreate"
    >
      <NovelCreateForm :creating="creating" :error="createError" @submit="createProject" />
    </AppModal>
  </div>
</template>

<style scoped>
.head-actions { display: flex; align-items: center; gap: 8px; }

.loading-card { padding: 40px 20px; border: 1px solid var(--paper-line-strong); border-radius: 10px; background: var(--paper-bg); color: var(--paper-ink-muted); font-size: 13px; }

.workspace { display: flex; flex-direction: column; border: 1px solid var(--paper-line-strong); border-radius: 10px; background: var(--paper-bg); overflow: hidden; }

.project-bar { display: flex; align-items: center; gap: 14px; padding: 13px 18px; background: var(--paper-header); border-bottom: 1px solid var(--paper-line); }
.project-mark { flex: 0 0 auto; display: grid; place-items: center; width: 34px; height: 34px; border-radius: 6px; background: var(--paper-accent-soft); color: var(--paper-accent); }
.project-id { min-width: 0; }
.project-name { margin: 0; font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif; font-size: 17px; font-weight: 650; color: var(--paper-ink); overflow-wrap: anywhere; }
.project-meta { margin: 3px 0 0; font-size: 11.5px; color: var(--paper-ink-muted); font-variant-numeric: tabular-nums; }
.meta-sep { margin: 0 3px; opacity: 0.6; }
.project-actions { margin-left: auto; display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 6px; }
.act-sep { width: 1px; height: 16px; margin: 0 2px; background: var(--paper-line-strong); }

.model-strip { padding: 8px 18px 10px; border-bottom: 1px solid var(--paper-line); background: var(--paper-surface); }
.model-strip :deep(.model-selector-select) { max-width: 380px; }

.job-strip { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 7px 18px; border-bottom: 1px solid var(--paper-line); background: var(--paper-accent-tint); color: var(--paper-ink-soft); font-size: 11.5px; }
.job-spinner { width: 10px; height: 10px; border-radius: 50%; border: 2px solid var(--paper-accent-line); border-top-color: var(--paper-accent); animation: spin 0.9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.work-grid { min-height: 500px; height: calc(100vh - 380px); display: grid; grid-template-columns: 216px minmax(0, 1fr) 316px; align-items: stretch; }
.editor-col { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.side-col { min-width: 0; min-height: 0; display: flex; flex-direction: column; gap: 12px; padding: 14px; border-left: 1px solid var(--paper-line); overflow-y: auto; scrollbar-width: thin; }

.pane-empty { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; padding: 44px clamp(18px, 3vw, 40px) 56px; }
.pane-empty h3 { margin: 0; font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif; font-size: 19px; font-weight: 650; color: var(--paper-ink); }
.pane-empty p { margin: 0 0 4px; color: var(--paper-ink-muted); font-size: 13px; line-height: 1.7; }
.source-peek { width: 100%; max-height: 190px; margin: 0 0 6px; overflow: auto; padding: 12px 14px; border: 1px dashed var(--paper-line-strong); border-radius: 8px; background: var(--paper-surface); color: var(--paper-ink-soft); font: 13px/1.85 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif; white-space: pre-wrap; overflow-wrap: anywhere; }

@media (max-width: 1200px) {
  .work-grid { grid-template-columns: 200px minmax(0, 1fr); height: auto; min-height: 0; }
  .side-col { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; border-left: 0; border-top: 1px solid var(--paper-line); overflow: visible; }
}

@media (max-width: 980px) {
  .work-grid { grid-template-columns: 1fr; }
  .side-col { grid-column: auto; }
  .project-bar { flex-wrap: wrap; }
  .project-actions { width: 100%; justify-content: flex-start; }
}

@media (max-width: 640px) {
  .side-col { grid-template-columns: 1fr; }
  .head-actions { width: 100%; justify-content: flex-start; }
  .page-head { align-items: flex-start; }
}
</style>
