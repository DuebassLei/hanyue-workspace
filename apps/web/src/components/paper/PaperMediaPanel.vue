<script setup>
/**
 * 成稿台右栏「封面 · 插图」。
 *
 * 受控组件：所有写操作都回整篇快照（emit('saved', article)），由父级替换 state；
 * 自己不拉 article、不跳路由。
 *
 * 后端封面/插图都是「入队即返回」，所以 running 时必须每 2s 调一次 sync 拉结果；
 * 轮询统一收在一个定时器里，`onBeforeUnmount` 与稿件切换都会清掉，避免离开页面还在打接口。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NInput, NSelect } from 'naive-ui'
import { apiUrl } from '../../api/client'
import { studioExtraApi } from '../../api/studioExtra'
import { SVG_CARD_PRESETS } from '../../features/layout/studioPresets'

const props = defineProps({
  article: { type: Object, default: null },
  selectedBlockId: { type: String, default: '' },
  busy: { type: String, default: '' },
})
const emit = defineEmits(['saved', 'notify'])

const ASPECTS = ['16:9', '4:3', '1:1', '3:4']
const aspectOptions = ASPECTS.map((value) => ({ label: value, value }))

const catalog = ref([])
const coverPrompt = ref('')
const coverRatio = ref('16:9')
const inlinePrompt = ref('')
const inlineRatio = ref('4:3')
const svgKind = ref('')
const action = ref('')
const error = ref('')

let timer = null
let disposed = false
let failures = 0

const busyAction = computed(() => Boolean(action.value) || Boolean(props.busy))
const isAction = (label) => action.value === label

const cover = computed(() => props.article?.cover || {})
/** cover 的 ready 判定要兼容两种历史结构：新契约写 status/assetId，老数据只有 id */
const coverStatus = computed(() => {
  const current = cover.value
  if (current.status) return current.status
  return current.assetId || current.id ? 'ready' : 'idle'
})
const coverImage = computed(() => (coverStatus.value === 'ready' && cover.value.url ? apiUrl(cover.value.url) : ''))
const coverError = computed(() => cover.value.error || '')

const blocks = computed(() => props.article?.blocks || [])
const inlineItems = computed(() => props.article?.inlineDiagrams || [])
const selectedBlock = computed(() => blocks.value.find((block) => block.id === props.selectedBlockId) || null)
const selectedExcerpt = computed(() => {
  const text = String(selectedBlock.value?.text || '').replace(/\s+/g, ' ').trim()
  return text.length > 42 ? `${text.slice(0, 42)}…` : text
})
const svgKinds = computed(() => {
  // catalog.svgCards 每项是 {id, name, lead}；旧契约写的 kind 也兜一下
  const list = catalog.value.length
    ? catalog.value
    : SVG_CARD_PRESETS.map((item) => ({ id: item.id, name: item.name, lead: item.hint }))
  return list.map((item) => ({ id: item.id || item.kind, name: item.name, desc: item.lead || item.desc || '' }))
})
const svgOptions = computed(() => svgKinds.value.map((item) => ({ label: item.name, value: item.id })))
const currentSvg = computed(() => svgKinds.value.find((item) => item.id === svgKind.value) || null)
const runningInline = computed(() => inlineItems.value.filter((item) => item.status === 'running').length)

const statusLabel = { running: '生成中', ready: '已就绪', failed: '失败', idle: '未生成' }

function blockIndex(blockId) {
  const index = blocks.value.findIndex((block) => block.id === blockId)
  return index >= 0 ? index + 1 : 0
}

function stopPolling() {
  if (timer) clearInterval(timer)
  timer = null
}

function hasRunning() {
  return coverStatus.value === 'running' || runningInline.value > 0
}

function ensurePolling() {
  if (disposed || !props.article?.id || !hasRunning()) return stopPolling()
  if (timer) return
  timer = setInterval(syncPending, 2000)
}

async function syncPending() {
  const articleId = props.article?.id
  if (!articleId) return stopPolling()
  try {
    if (coverStatus.value === 'running') {
      const snapshot = await studioExtraApi.syncCover(articleId)
      if (snapshot) emit('saved', snapshot)
    }
    if (runningInline.value > 0) {
      const snapshot = await studioExtraApi.syncInline(articleId)
      if (snapshot) emit('saved', snapshot)
    }
    failures = 0
  } catch (err) {
    // 单拍失败不断轮询：后台任务可能只是还没落库，下一拍再试。
    // 连错 10 拍（约 20s）就停表并给出可读原因，避免接口异常时一直空转。
    failures += 1
    if (failures >= 10) {
      stopPolling()
      error.value = err.message || '自动检查生成结果失败，请手动重试'
    }
  }
}

async function loadCatalog() {
  const articleId = props.article?.id
  if (!articleId) return
  try {
    const data = await studioExtraApi.renderOptions(articleId)
    if (disposed) return
    catalog.value = data?.catalog?.svgCards || []
    if (!svgKind.value) svgKind.value = svgKinds.value[0]?.id || ''
  } catch {
    // 目录拿不到就沿用本地预设，不影响封面与插图
    if (!svgKind.value) svgKind.value = svgKinds.value[0]?.id || ''
  }
}

async function run(actionName, work, message) {
  if (busyAction.value) return
  action.value = actionName
  error.value = ''
  failures = 0
  try {
    const snapshot = await work()
    if (snapshot) emit('saved', snapshot)
    if (message) emit('notify', message)
    await nextTick()
    ensurePolling()
  } catch (err) {
    error.value = err.message || '操作失败，请重试'
    emit('notify', error.value)
  } finally {
    action.value = ''
  }
}

const generateCover = () =>
  run(
    'cover',
    () =>
      studioExtraApi.generateCover(props.article.id, {
        prompt: coverPrompt.value.trim() || undefined,
        aspectRatio: coverRatio.value,
      }),
    '封面已提交生成，出图后自动刷新',
  )

const removeCover = () =>
  run('cover-remove', () => studioExtraApi.pickCover(props.article.id, null), '封面已移除')

const generateInline = () =>
  run(
    'inline',
    () =>
      studioExtraApi.generateInline(props.article.id, {
        blockId: props.selectedBlockId,
        prompt: inlinePrompt.value.trim() || undefined,
        aspectRatio: inlineRatio.value,
        position: 'after',
      }),
    '插图已提交生成，完成后自动插到该段之后',
  )

const removeInline = (item) =>
  run('inline-remove', () => studioExtraApi.removeInline(props.article.id, item.id), '插图已移除')

const makeSvgCard = () =>
  run(
    'svg',
    () => studioExtraApi.svgCard(props.article.id, props.selectedBlockId, svgKind.value),
    'SVG 卡已插入该段之后',
  )

watch(
  () => props.article?.id,
  () => {
    stopPolling()
    coverPrompt.value = props.article?.cover?.prompt || ''
    inlinePrompt.value = ''
    error.value = ''
    loadCatalog()
    ensurePolling()
  },
  { immediate: true },
)

// 每次快照替换后重算「还有没有在跑的」，没有就自动停表
watch(() => props.article, ensurePolling)

onBeforeUnmount(() => {
  disposed = true
  stopPolling()
})
</script>

<template>
  <section class="media-panel" aria-label="封面与插图">
    <header class="panel-head">
      <span>封面 · 插图</span>
      <span class="panel-note">{{ runningInline || coverStatus === 'running' ? '生成中…' : '自动刷新' }}</span>
    </header>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="!article" class="panel-empty">左侧选一篇稿件。</p>

    <template v-else>
      <section class="block-section">
        <div class="setting-head">
          <h3>封面</h3>
          <span class="state" :class="coverStatus">{{ statusLabel[coverStatus] || coverStatus }}</span>
        </div>

        <div v-if="coverImage" class="cover-preview">
          <img :src="coverImage" alt="封面预览" />
          <div class="cover-actions">
            <NButton size="tiny" :disabled="busyAction" :loading="isAction('cover')" @click="generateCover">换一张</NButton>
            <NButton size="tiny" :disabled="busyAction" :loading="isAction('cover-remove')" @click="removeCover">移除</NButton>
          </div>
        </div>
        <div v-else-if="coverStatus === 'running'" class="cover-skeleton" role="status">
          <span class="shimmer" />
          <p>正在生成封面，每 2 秒自动检查一次结果…</p>
        </div>
        <p v-else-if="coverStatus === 'failed'" class="panel-error" role="alert">
          {{ coverError || '封面生成失败，换个提示词再试。' }}
        </p>

        <label class="field">
          <span>提示词</span>
          <NInput
            :value="coverPrompt"
            size="small"
            type="textarea"
            :rows="2"
            maxlength="600"
            placeholder="留空则按标题 + 正文首段自动拟"
            @update:value="(value) => (coverPrompt = value)"
          />
        </label>
        <div class="row">
          <NSelect
            :value="coverRatio"
            size="small"
            class="ratio"
            :options="aspectOptions"
            :disabled="busyAction"
            @update:value="(value) => (coverRatio = value)"
          />
          <NButton
            size="small"
            type="primary"
            class="grow"
            :disabled="busyAction"
            :loading="isAction('cover')"
            @click="generateCover"
          >
            {{ coverStatus === 'ready' ? '重新生成' : '生成封面' }}
          </NButton>
        </div>
      </section>

      <section class="block-section">
        <div class="setting-head">
          <h3>正文插图</h3>
          <span class="state" :class="runningInline ? 'running' : inlineItems.length ? 'ready' : 'idle'">
            {{ runningInline ? `${runningInline} 张生成中` : `${inlineItems.length} 张` }}
          </span>
        </div>

        <p v-if="!selectedBlockId" class="hint strong">先在稿纸里点一段，再给这一段配图。</p>
        <p v-else class="hint">
          目标段落 <b>第 {{ blockIndex(selectedBlockId) }} 段</b>：{{ selectedExcerpt || '（空段落）' }}
        </p>

        <label class="field">
          <span>画面描述（可留空）</span>
          <NInput
            :value="inlinePrompt"
            size="small"
            type="textarea"
            :rows="2"
            maxlength="600"
            placeholder="想要的场景、风格或构图的提示词"
            @update:value="(value) => (inlinePrompt = value)"
          />
        </label>
        <div class="row">
          <NSelect
            :value="inlineRatio"
            size="small"
            class="ratio"
            :options="aspectOptions"
            :disabled="busyAction"
            @update:value="(value) => (inlineRatio = value)"
          />
          <NButton
            size="small"
            type="primary"
            class="grow"
            :disabled="busyAction || !selectedBlockId"
            :loading="isAction('inline')"
            @click="generateInline"
          >
            为这一段配图
          </NButton>
        </div>

        <ul v-if="inlineItems.length" class="diagram-list">
          <li v-for="item in inlineItems" :key="item.id" class="diagram-item">
            <img v-if="item.status === 'ready' && item.url" :src="apiUrl(item.url)" alt="正文插图" />
            <div v-else class="diagram-placeholder" :class="item.status">
              {{ statusLabel[item.status] || item.status }}
            </div>
            <div class="diagram-meta">
              <span>第 {{ blockIndex(item.blockId) || '?' }} 段</span>
              <span class="state" :class="item.status">{{ statusLabel[item.status] || item.status }}</span>
              <small v-if="item.error" class="diagram-error">{{ item.error }}</small>
            </div>
            <NButton
              size="tiny"
              quaternary
              :disabled="busyAction"
              :loading="isAction('inline-remove')"
              @click="removeInline(item)"
            >移除</NButton>
          </li>
        </ul>
        <p v-else-if="selectedBlockId" class="hint">这一段还没有插图。</p>
      </section>

      <section class="block-section">
        <div class="setting-head">
          <h3>SVG 卡</h3>
          <span class="state idle">插在该段之后</span>
        </div>
        <div class="row">
          <NSelect
            :value="svgKind"
            size="small"
            class="grow"
            :options="svgOptions"
            :disabled="busyAction || !svgOptions.length"
            placeholder="选一种卡片"
            @update:value="(value) => (svgKind = value)"
          />
          <NButton
            size="small"
            :disabled="busyAction || !selectedBlockId || !svgKind"
            :loading="isAction('svg')"
            @click="makeSvgCard"
          >生成</NButton>
        </div>
        <p v-if="!selectedBlockId" class="hint strong">先在稿纸里点一段，再生成卡片。</p>
        <p v-else-if="currentSvg?.desc" class="hint">{{ currentSvg.desc }}</p>
        <p class="hint">卡片数据只取这一段原文，生成后核对数值与原文是否一致。</p>
      </section>
    </template>
  </section>
</template>

<style scoped>
.media-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-surface);
}
.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
}
.panel-note { font-size: 11px; font-weight: 400; color: var(--color-text-tertiary); }
.block-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--color-border);
}
.block-section:first-of-type { padding-top: 0; border-top: 0; }
.setting-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.setting-head h3 { margin: 0; font-size: 12px; font-weight: 600; }
.state { font-size: 10px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--color-border); color: var(--color-text-tertiary); }
.state.running { color: var(--paper-warning); border-color: var(--paper-warning-line); background: var(--paper-warning-soft); }
.state.ready { color: var(--paper-success); border-color: var(--paper-success); }
.state.failed { color: var(--paper-danger); border-color: var(--paper-danger-line); background: var(--paper-danger-soft); }

.cover-preview { display: flex; flex-direction: column; gap: 6px; }
.cover-preview img {
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid var(--paper-line);
  background: var(--color-surface-muted);
}
.cover-actions { display: flex; gap: 6px; }
.cover-skeleton {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  border: 1px dashed var(--paper-line-strong);
  border-radius: 8px;
  font-size: 11px;
  color: var(--color-text-tertiary);
}
.cover-skeleton p { margin: 0; }
.shimmer {
  display: block;
  height: 54px;
  border-radius: 6px;
  background: var(--color-surface-muted);
  animation: media-pulse 1.2s infinite;
}

.field { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--color-text-tertiary); }
.row { display: flex; align-items: center; gap: 6px; }
.ratio { width: 92px; flex: none; }
.grow { flex: 1; }

.diagram-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.diagram-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
}
.diagram-item img {
  width: 52px;
  height: 52px;
  object-fit: cover;
  border-radius: 6px;
  flex: none;
  background: var(--color-surface-muted);
}
.diagram-placeholder {
  width: 52px;
  height: 52px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  font-size: 10px;
  color: var(--color-text-tertiary);
  background: var(--color-surface-muted);
}
.diagram-placeholder.running { animation: media-pulse 1.2s infinite; }
.diagram-placeholder.failed { color: var(--paper-danger); background: var(--paper-danger-soft); }
.diagram-meta { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; font-size: 11px; color: var(--color-text-tertiary); }
.diagram-error { color: var(--paper-danger); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.hint { margin: 0; font-size: 11px; line-height: 1.6; color: var(--color-text-tertiary); }
.hint.strong { color: var(--color-text-secondary); }
.panel-error {
  margin: 0;
  padding: 8px 10px;
  border-radius: 8px;
  font-size: 12px;
  color: var(--paper-danger);
  background: var(--paper-danger-soft);
  border: 1px solid var(--paper-danger-line);
}
.panel-empty { margin: 0; font-size: 12px; color: var(--color-text-tertiary); }
@keyframes media-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}
</style>
