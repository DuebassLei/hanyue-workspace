<script setup>
import { computed, ref } from 'vue'
import { NAlert, NButton } from 'naive-ui'
import { confirm, toast } from '../../ui/feedback'
import VideoSettingsPanel from './VideoSettingsPanel.vue'
import VideoShotCard from './VideoShotCard.vue'
import VideoRenderProgress from './VideoRenderProgress.vue'

const props = defineProps({ store: { type: Object, required: true }, modelOptions: { type: Array, default: () => [] } })
const emit = defineEmits(['back'])

const project = computed(() => props.store.project.value)
const rendering = computed(() => props.store.rendering.value)
const settings = computed(() => project.value.settings || {})
const hoveredPreviewId = ref(null)
const activePreviewId = computed(() => hoveredPreviewId.value || project.value.shots[0]?.id || null)

const breakdownModel = ref('')
const showScript = ref(false)
const renderErrorText = computed(() => {
  const t = props.store.renderTask.value
  if (!t) return project.value.renderError || ''
  return t.status === 'failed' ? (t.errorMessage || project.value.renderError || '成片失败，请重试') : ''
})

const totalText = computed(() => {
  const ms = project.value.totalDurationMs
  if (!ms) return `${project.value.shots.length} 个镜头`
  const s = Math.round(ms / 1000)
  return `${project.value.shots.length} 个镜头 · 约 ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
})

async function saveSettings(next) {
  try {
    await props.store.patchProject({ settings: next })
  } catch (err) { toast(err.message, 'error') }
}

async function renameTitle() {
  const title = project.value.title.trim()
  if (!title) return
  try { await props.store.patchProject({ title }) } catch (err) { toast(err.message, 'error') }
}

async function runBreakdown() {
  if (project.value.shots.length) {
    const ok = await confirm('拆分将覆盖现有全部镜头（含已生成的配图与配音），确定重新拆分吗？', '重新 AI 拆分镜')
    if (!ok) return
  }
  try {
    await props.store.breakdown(breakdownModel.value || undefined)
    toast('分镜已生成，逐镜头确认后点「生成成片」')
  } catch (err) { toast(err.message, 'error') }
}

async function addShot() {
  try { await props.store.addShot({ narration: '', imagePrompt: '', motion: 'zoom_in' }) } catch (err) { toast(err.message, 'error') }
}

async function saveShot(id, patch) {
  try { await props.store.patchShot(id, patch) } catch (err) { toast(err.message, 'error') }
}

async function removeShot(id) {
  if (!await confirm('删除该镜头？', '删除镜头')) return
  try { await props.store.deleteShot(id) } catch (err) { toast(err.message, 'error') }
}

async function moveShot(index, delta) {
  const ids = project.value.shots.map((s) => s.id)
  const to = index + delta
  if (to < 0 || to >= ids.length) return
  ;[ids[index], ids[to]] = [ids[to], ids[index]]
  try { await props.store.reorderShots(ids) } catch (err) { toast(err.message, 'error') }
}

async function startRender(reset = false) {
  try {
    await props.store.render(reset)
  } catch (err) { toast(err.message, 'error') }
}

async function cancelRender() {
  try { await props.store.cancelRender(); toast('已请求取消') } catch (err) { toast(err.message, 'error') }
}

const providerNotes = computed(() => {
  const p = props.store.provider.value
  if (!p) return []
  const notes = []
  if (!p.images?.available) notes.push('未接入生图供应商：镜头配图请在卡片中手动选择素材')
  if (!p.ffmpeg?.available) notes.push(p.ffmpeg.note || '本机未检测到 ffmpeg，无法成片')
  return notes
})
</script>

<template>
  <div class="video-workspace">
    <header class="ws-head">
      <n-button size="small" tertiary @click="emit('back')">‹ 项目列表</n-button>
      <input class="ws-title" :value="project.title" maxlength="200" @change="project.title = $event.target.value; renameTitle()" />
      <span class="ws-meta">{{ totalText }}</span>
    </header>

    <n-alert v-if="renderErrorText" type="error" :bordered="false">{{ renderErrorText }}</n-alert>

    <VideoRenderProgress v-if="rendering || store.renderTask.value?.status === 'rendering'" :task="store.renderTask.value" error="" @cancel="cancelRender" />

    <div class="ws-grid">
      <aside class="ws-side">
        <section class="side-card">
          <h3>全局设置</h3>
          <VideoSettingsPanel :settings="settings" :busy="rendering" @save="saveSettings" />
        </section>
        <section class="side-card">
          <h3>创作流程</h3>
          <template v-if="modelOptions.length">
            <select v-model="breakdownModel" class="model-select" :disabled="rendering">
              <option value="">自动选模型</option>
              <option v-for="m in modelOptions" :key="m.value" :value="m.value">{{ m.label }}</option>
            </select>
          </template>
          <n-button type="primary" block :loading="project.status === 'draft' && rendering" :disabled="rendering" @click="runBreakdown">
            {{ project.shots.length ? '重新 AI 拆分镜' : 'AI 拆分镜' }}
          </n-button>
          <n-button block secondary :disabled="rendering || !project.shots.length" @click="startRender(false)">
            {{ project.status === 'rendered' ? '重新生成成片' : '生成成片' }}
          </n-button>
          <n-button block text size="small" :disabled="rendering || !project.renderAssetId" @click="startRender(true)">清空缓存并完整重做</n-button>
          <p v-if="project.status === 'draft'" class="side-tip">第一步：把文案拆成解说稿 + 分镜。</p>
          <p v-else-if="!project.renderAssetId" class="side-tip">第二步：确认镜头后生成成片（配图 → 配音 → 合成）。</p>
        </section>
        <section v-if="project.renderUrl" class="side-card preview-card">
          <h3>成片预览</h3>
          <video :src="project.renderUrl" controls playsinline :class="{ landscape: settings.aspectRatio === '16:9' }" />
          <a class="download-link" :href="project.renderUrl" download>下载 MP4</a>
        </section>
        <section v-if="providerNotes.length" class="side-card warn-card">
          <p v-for="note in providerNotes" :key="note">⚠ {{ note }}</p>
        </section>
        <section class="side-card">
          <details :open="showScript" @toggle="showScript = $event.target.open">
            <summary>原文案（{{ project.sourceText.length }} 字）</summary>
            <pre class="source-text">{{ project.sourceText }}</pre>
          </details>
        </section>
      </aside>

      <main class="ws-main">
        <div class="shots-toolbar">
          <span>镜头列表</span>
          <n-button size="tiny" secondary :disabled="rendering" @click="addShot">+ 添加镜头</n-button>
        </div>
        <div v-if="!project.shots.length" class="shots-empty">还没有镜头。点左侧「AI 拆分镜」自动拆分，或手动添加。</div>
        <VideoShotCard
          v-for="(shot, i) in project.shots" :key="shot.id"
          :shot="shot" :index="i" :aspect-ratio="settings.aspectRatio || '9:16'" :settings="settings" :active-preview="activePreviewId === shot.id" :busy="rendering"
          @save="saveShot" @remove="removeShot" @move="moveShot" @preview="hoveredPreviewId = $event"
        />
      </main>
    </div>
  </div>
</template>

<style scoped>
.video-workspace { display:flex; flex-direction:column; gap:14px; }.ws-head { display:flex; align-items:center; gap:12px; }.ws-title { flex:1; min-width:0; padding:6px 10px; border:1px solid transparent; border-radius:8px; background:transparent; font-size:17px; font-weight:650; color:var(--paper-ink); }.ws-title:hover, .ws-title:focus { border-color:var(--paper-line); background:var(--paper-surface); outline:none; }.ws-meta { color:var(--paper-ink-muted); font-size:12px; white-space:nowrap; }.ws-grid { display:grid; grid-template-columns:290px 1fr; gap:16px; align-items:start; }.ws-side { display:flex; flex-direction:column; gap:12px; position:sticky; top:14px; }.side-card { padding:13px 14px; border:1px solid var(--paper-line); border-radius:10px; background:var(--paper-surface); }.side-card h3 { margin:0 0 10px; font-size:13px; color:var(--paper-ink-soft); }.side-card + .side-card { }.side-tip { margin:8px 0 0; color:var(--paper-ink-muted); font-size:11px; line-height:1.5; }.side-card .n-button + .n-button { margin-top:7px; }.model-select { width:100%; margin-bottom:8px; padding:5px 8px; border:1px solid var(--paper-line); border-radius:7px; background:var(--paper-surface); font-size:12px; color:var(--paper-ink-soft); }.preview-card video { width:100%; max-height:340px; border-radius:8px; background:#000; }.preview-card video.landscape { aspect-ratio:16/9; height:auto; }.download-link { display:inline-block; margin-top:8px; color:var(--paper-accent); font-size:12px; }.warn-card p { margin:0 0 5px; color:var(--paper-warning); font-size:12px; line-height:1.5; }.warn-card p:last-child { margin:0; }.ws-side details summary { cursor:pointer; color:var(--paper-ink-muted); font-size:12px; }.source-text { max-height:220px; overflow:auto; margin:8px 0 0; padding:9px; border-radius:7px; background:var(--paper-sunken); color:var(--paper-ink-soft); font-size:12px; line-height:1.7; white-space:pre-wrap; word-break:break-word; }.ws-main { display:flex; flex-direction:column; gap:10px; min-width:0; }.shots-toolbar { display:flex; align-items:center; justify-content:space-between; color:var(--paper-ink-soft); font-size:13px; font-weight:600; }.shots-empty { padding:44px 16px; border:1px dashed var(--paper-line-strong); border-radius:10px; text-align:center; color:var(--paper-ink-muted); font-size:13px; }.video-workspace :deep(.n-alert) { margin-bottom:0; }
@media (max-width:900px){ .ws-grid { grid-template-columns:1fr; }.ws-side { position:static; } }
</style>
