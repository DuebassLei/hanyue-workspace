<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton } from 'naive-ui'
import { confirm } from '../ui/feedback'
import { useNovel } from '../composables/useNovel'
import NovelList from '../features/novel/NovelList.vue'
import NovelChapterList from '../features/novel/NovelChapterList.vue'
import NovelEditor from '../features/novel/NovelEditor.vue'
import NovelCompare from '../features/novel/NovelCompare.vue'
import NovelBible from '../features/novel/NovelBible.vue'
import NovelContinue from '../features/novel/NovelContinue.vue'
import ModelSelector from '../components/ModelSelector.vue'

const route = useRoute()
const router = useRouter()
const store = useNovel()
const listRef = ref(null)
const listLoading = ref(true)
const listError = ref('')
const creating = ref(false)
const splitting = ref(false)
const accepting = ref(false)
const actionError = ref('')

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
const sourcePreview = computed(() => String(store.project.value?.sourceText || '').slice(0, 200))
const impactChapters = computed(() => store.selectedChapter.value?.impact?.chapters || [])

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
  actionError.value = ''
  try {
    const project = await store.createProject(payload)
    listRef.value?.reset()
    router.replace({ query: { id: project.id } })
  } catch (err) {
    actionError.value = err.message
  } finally {
    creating.value = false
  }
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
  <div class="novel-page">
    <header class="page-head">
      <div>
        <h1>小说创作</h1>
        <p>从一份你有权改写的文本切章、改写并续写。</p>
      </div>
    </header>

    <NAlert v-if="pageError" type="error" closable @close="clearError">{{ pageError }}</NAlert>
    <NAlert v-if="failedJobMessage" type="error">
      {{ failedJobMessage }}<template v-if="store.job.value?.errorCode !== 'provider_unavailable'">。可以再点一次。</template>
    </NAlert>
    <p v-if="store.jobBusy.value" class="job-note">任务进行中，章节仍可查看。</p>
    <p v-if="jobModelInfo" class="job-note">{{ jobModelInfo }}</p>

    <div v-if="listLoading" class="list-loading">正在读取小说…</div>

    <div v-else class="novel-layout">
      <NovelList
        ref="listRef"
        :projects="store.projects.value"
        :active-id="store.project.value?.id || ''"
        :creating="creating"
        @create="createProject"
        @select="selectProject"
        @delete="deleteProject"
      />

      <div v-if="store.project.value" class="novel-main">
        <div class="toolbar">
          <strong class="project-title">{{ store.project.value.title }}</strong>
          <div class="novel-model-slot">
            <ModelSelector :disabled="store.jobBusy.value" label="模型" />
          </div>
          <NButton size="small" :loading="splitting" :disabled="store.jobBusy.value" @click="split">切章</NButton>
          <NButton size="small" :disabled="store.jobBusy.value" @click="run(() => store.extract())">抽取人名</NButton>
          <NButton size="small" secondary @click="run(() => store.exportFile('md'))">导出 md</NButton>
          <NButton size="small" secondary @click="run(() => store.exportFile('txt'))">导出 txt</NButton>
        </div>

        <section v-if="!store.chapters.value.length" class="no-chapters">
          <h2>还没有章节</h2>
          <pre v-if="sourcePreview" class="plain">{{ sourcePreview }}</pre>
          <NButton type="primary" :loading="splitting" @click="split">切章</NButton>
        </section>

        <div class="workbench">
          <NovelChapterList
            :chapters="store.chapters.value"
            :active-id="store.selectedChapter.value?.id || ''"
            @select="store.selectChapter"
          />
          <div class="work-col">
            <NovelEditor
              v-if="store.selectedChapter.value"
              :chapter="store.selectedChapter.value"
              :busy="store.jobBusy.value"
              :accepting="accepting"
              @rewrite="run(() => store.rewrite(store.selectedChapter.value.id))"
              @accept="accept"
              @revert="run(() => store.revert(store.selectedChapter.value.id))"
            />
            <p v-else class="muted">切章后在这里看原文、草稿和已接受正文。</p>
            <NovelCompare :chapter="store.selectedChapter.value" />
            <NovelBible
              :rules="store.project.value.rules"
              :characters="store.characters.value"
              :aliases="store.aliases.value"
              @patch-rules="(rules) => run(() => store.patchProject({ rules }))"
              @patch-character="(id, payload) => run(() => store.patchCharacter(id, payload))"
              @patch-alias="(id, payload) => run(() => store.patchAlias(id, payload))"
            />
            <NovelContinue
              :busy="store.jobBusy.value"
              :warnings="store.warnings.value"
              :impact-chapters="impactChapters"
              @continue="run(() => store.continueNext())"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.novel-page { padding: 22px clamp(12px, 2vw, 26px) 40px; color: var(--paper-ink); max-width: 100%; overflow-x: hidden; box-sizing: border-box; }
.page-head { margin-bottom: 16px; }
.page-head h1 { margin: 0; font-size: 24px; font-weight: 680; }
.page-head p { margin: 5px 0 0; color: var(--paper-ink-muted); font-size: 13px; }
.job-note, .list-loading { margin: 8px 0 12px; color: var(--paper-ink-muted); font-size: 13px; }
.novel-layout { display: grid; grid-template-columns: minmax(220px, 280px) minmax(0, 1fr); gap: 20px; align-items: start; }
.novel-main { min-width: 0; display: flex; flex-direction: column; gap: 16px; }
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.project-title { margin-right: auto; overflow-wrap: anywhere; }
.novel-model-slot { flex: 0 1 420px; min-width: 240px; }
.no-chapters { min-width: 0; }
.no-chapters h2 { margin: 0 0 8px; font-size: 16px; }
.plain { margin: 0 0 12px; white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.6 inherit; background: var(--paper-sunken); padding: 10px; }
.muted { margin: 0; color: var(--paper-ink-muted); font-size: 13px; }
.workbench { display: grid; grid-template-columns: minmax(160px, 220px) minmax(0, 1fr); gap: 16px; align-items: start; }
.work-col { min-width: 0; display: flex; flex-direction: column; gap: 18px; }
.novel-page :deep(.n-alert) { margin-bottom: 12px; }
@media (max-width: 800px) {
  .novel-layout, .workbench { grid-template-columns: 1fr; }
}
</style>
