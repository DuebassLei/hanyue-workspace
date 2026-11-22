<script setup>
/**
 * 成稿台右栏「发布 · 复盘」。
 *
 * 受控组件：排期、存入文章管理、立即发布都回整篇快照（emit('saved')），复盘是只读派生数据。
 *
 * 平台勾选项来自 `GET /platforms/capabilities` 的真实目录——后端目前只接入了
 * `wechat_official`（草稿箱推送），所以不能在前端写死平台名；接口拿不到时退化为
 * 微信公众号一项（FALLBACK_PLATFORMS），并在提示里说明。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NCheckbox, NDatePicker } from 'naive-ui'
import { FALLBACK_PLATFORMS, studioExtraApi } from '../../api/studioExtra'

const props = defineProps({
  article: { type: Object, default: null },
  busy: { type: String, default: '' },
})
const emit = defineEmits(['saved', 'notify'])

const platforms = ref([])
const selected = ref([])
const scheduleAt = ref(null)
const review = ref(null)
const loading = ref(false)
const action = ref('')
const error = ref('')
const platformFallback = ref(false)

let timer = null
let disposed = false
let sequence = 0

const busyAction = computed(() => Boolean(action.value) || Boolean(props.busy))
const isAction = (label) => action.value === label
const platformLabel = computed(() => Object.fromEntries(platforms.value.map((item) => [item.platform, item.label || item.platform])))
const availablePlatforms = computed(() => platforms.value.filter((item) => item.available !== false))
const reviewRows = computed(() => review.value?.platforms || [])
const reviewStats = computed(() => review.value?.stats || null)
const publishedAt = computed(() => props.article?.publishedAt || review.value?.publishedAt || '')
const reviewed = computed(() => Boolean(reviewRows.value.length || publishedAt.value))
const scheduleText = computed(() => (props.article?.scheduledFor ? formatTime(props.article.scheduledFor) : '未排期'))

const statusLabel = { queued: '排队中', running: '发布中', succeeded: '成功', failed: '失败' }
const statusClass = { queued: 'idle', running: 'idle', succeeded: 'ok', failed: 'bad' }

function formatTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  const pad = (num) => String(num).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function stopPolling() {
  if (timer) clearInterval(timer)
  timer = null
}

function hasActiveReview() {
  return reviewRows.value.some((row) => ['queued', 'running'].includes(row.status))
}

function ensurePolling() {
  if (disposed || !hasActiveReview()) return stopPolling()
  if (timer) return
  timer = setInterval(loadReview, 5000)
}

async function loadReview() {
  const articleId = props.article?.id
  if (!articleId) return
  try {
    const data = await studioExtraApi.review(articleId)
    if (disposed) return
    review.value = data || null
    ensurePolling()
  } catch {
    // 复盘是只读附加信息，失败不打断发布流程
  }
}

async function load() {
  const articleId = props.article?.id
  if (!articleId) return
  const seq = ++sequence
  stopPolling()
  loading.value = true
  error.value = ''
  review.value = null
  const parsed = props.article?.scheduledFor ? Date.parse(props.article.scheduledFor) : NaN
  scheduleAt.value = Number.isNaN(parsed) ? null : parsed
  const [caps, rev] = await Promise.allSettled([studioExtraApi.platformCapabilities(), studioExtraApi.review(articleId)])
  if (seq !== sequence || disposed) return
  if (caps.status === 'fulfilled') {
    const list = caps.value?.platforms || []
    platforms.value = list.length ? list : FALLBACK_PLATFORMS
    platformFallback.value = !list.length
  } else {
    platforms.value = FALLBACK_PLATFORMS
    platformFallback.value = true
  }
  // 只保留还可用且在目录里的勾选，默认选第一个可用平台
  const usable = availablePlatforms.value.map((item) => item.platform)
  const kept = selected.value.filter((platform) => usable.includes(platform))
  selected.value = kept.length ? kept : usable.slice(0, 1)
  if (rev.status === 'fulfilled') review.value = rev.value || null
  loading.value = false
  ensurePolling()
}

async function saveSchedule() {
  const articleId = props.article?.id
  if (!articleId || busyAction.value) return
  if (!scheduleAt.value) {
    emit('notify', '先选一个发布时间')
    return
  }
  if (scheduleAt.value < Date.now()) {
    emit('notify', '计划发布时间不能早于现在')
    return
  }
  action.value = 'schedule'
  error.value = ''
  try {
    const snapshot = await studioExtraApi.schedule(articleId, new Date(scheduleAt.value).toISOString())
    if (snapshot) emit('saved', snapshot)
    emit('notify', `已排期到 ${formatTime(scheduleAt.value)}`)
  } catch (err) {
    error.value = err.message || '排期保存失败'
    emit('notify', error.value)
  } finally {
    action.value = ''
  }
}

async function clearSchedule() {
  const articleId = props.article?.id
  if (!articleId || busyAction.value) return
  action.value = 'schedule-clear'
  error.value = ''
  try {
    const snapshot = await studioExtraApi.schedule(articleId, null)
    if (snapshot) emit('saved', snapshot)
    scheduleAt.value = null
    emit('notify', '已取消排期')
  } catch (err) {
    error.value = err.message || '取消失败'
    emit('notify', error.value)
  } finally {
    action.value = ''
  }
}

async function publish(dryRun) {
  const articleId = props.article?.id
  if (!articleId || busyAction.value) return
  if (!selected.value.length) {
    emit('notify', '先勾选至少一个发布平台')
    return
  }
  action.value = dryRun ? 'store' : 'publish'
  error.value = ''
  try {
    const result = await studioExtraApi.publish(articleId, {
      platforms: [...selected.value],
      scheduledAt: null,
      dryRun,
    })
    // publish 路由回的是任务结果，不是稿件快照；稿件状态（published_at 等）靠复盘与父级刷新兜住
    if (dryRun) {
      emit('notify', `已存入文章管理${result?.versionId ? `（版本 ${result.versionId}）` : ''}`)
    } else {
      const count = result?.publishJobIds?.length || 0
      emit('notify', count ? `已创建 ${count} 个发布任务` : '已提交发布')
    }
    await loadReview()
  } catch (err) {
    error.value = err.message || '发布失败'
    emit('notify', error.value)
  } finally {
    action.value = ''
  }
}

function togglePlatform(platform, checked) {
  const next = new Set(selected.value)
  if (checked) next.add(platform)
  else next.delete(platform)
  selected.value = [...next]
}

watch(() => props.article?.id, load, { immediate: true })

onBeforeUnmount(() => {
  disposed = true
  sequence += 1
  stopPolling()
})
</script>

<template>
  <section class="publish-panel" aria-label="发布与复盘">
    <header class="panel-head">
      <span>发布 · 复盘</span>
      <span class="panel-note">{{ loading ? '载入中…' : scheduleText }}</span>
    </header>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="!article" class="panel-empty">左侧选一篇稿件。</p>

    <template v-else>
      <section class="block-section">
        <div class="setting-head">
          <h3>排期</h3>
          <span class="state" :class="article.scheduledFor ? 'ok' : 'idle'">{{ article.scheduledFor ? '已排期' : '未排期' }}</span>
        </div>
        <NDatePicker
          :value="scheduleAt"
          type="datetime"
          size="small"
          clearable
          placeholder="选一个计划发布的时间"
          :disabled="busyAction"
          class="date-picker"
          @update:value="(value) => (scheduleAt = value)"
        />
        <div class="row">
          <NButton size="small" :disabled="busyAction" :loading="isAction('schedule')" @click="saveSchedule">保存排期</NButton>
          <NButton
            v-if="article.scheduledFor"
            size="small"
            quaternary
            :disabled="busyAction"
            :loading="isAction('schedule-clear')"
            @click="clearSchedule"
          >取消排期</NButton>
          <span class="hint">排期只记录计划时间，真正的队列在发布模块里。</span>
        </div>
      </section>

      <section class="block-section">
        <div class="setting-head">
          <h3>发布平台</h3>
          <span v-if="platformFallback" class="state idle">默认微信</span>
        </div>
        <div class="platform-list">
          <label v-for="item in platforms" :key="item.platform" class="platform-item" :class="{ disabled: item.available === false }">
            <NCheckbox
              :checked="selected.includes(item.platform)"
              :disabled="busyAction || item.available === false"
              @update:checked="(checked) => togglePlatform(item.platform, checked)"
            />
            <span class="platform-name">{{ item.label || item.platform }}</span>
            <span v-if="item.available === false" class="platform-note">未接入</span>
            <span v-else-if="item.draftUpload" class="platform-note">草稿箱</span>
          </label>
        </div>
        <p v-if="platformFallback" class="hint">平台能力接口不可用，暂按微信公众号处理。</p>

        <div class="row actions">
          <NButton size="small" type="primary" :disabled="busyAction" :loading="isAction('publish')" @click="publish(false)">立即发布</NButton>
        </div>
        <p class="hint">存档请用顶部「文章存档」。这里只负责排期和发布。</p>
      </section>

      <section class="block-section">
        <div class="setting-head">
          <h3>复盘</h3>
          <span class="state" :class="reviewed ? 'ok' : 'idle'">
            {{ reviewed ? '已刷新' : '未发布' }}
          </span>
        </div>
        <p v-if="reviewStats" class="hint">
          {{ reviewStats.blocks }} 段 · {{ reviewStats.chars }} 字<template v-if="reviewStats.checked"> · 质检 {{ reviewStats.flagged }} 处待改</template>
        </p>
        <p v-if="publishedAt" class="hint">已发布：{{ formatTime(publishedAt) }}</p>

        <table v-if="reviewRows.length" class="review-table">
          <thead>
            <tr><th>平台</th><th>状态</th><th>链接</th></tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in reviewRows" :key="`${row.platform}-${index}`">
              <td>{{ row.platformLabel || platformLabel[row.platform] || row.platform }}</td>
              <td>
                <span class="state" :class="statusClass[row.status] || 'idle'">{{ statusLabel[row.status] || row.status }}</span>
                <small v-if="row.error" class="row-error" :title="row.error">{{ row.error }}</small>
              </td>
              <td>
                <a v-if="row.url" :href="row.url" target="_blank" rel="noopener noreferrer">打开</a>
                <span v-else class="hint">—</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="hint">还没有发布记录，发布后这里会按平台列出结果。</p>
        <div class="row">
          <NButton size="tiny" quaternary :disabled="loading" @click="loadReview">刷新复盘</NButton>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.publish-panel {
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
.state.ok { color: var(--paper-success); border-color: var(--paper-success); }
.state.bad { color: var(--paper-danger); border-color: var(--paper-danger-line); background: var(--paper-danger-soft); }
.state.idle { color: var(--color-text-tertiary); }

.date-picker { width: 100%; }
.row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.row.actions { justify-content: flex-start; }

.platform-list { display: flex; flex-direction: column; gap: 4px; }
.platform-item { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--color-text-secondary); }
.platform-item.disabled { opacity: 0.55; }
.platform-name { flex: none; }
.platform-note { font-size: 10px; color: var(--color-text-tertiary); }

.review-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.review-table th {
  text-align: left;
  padding: 4px 6px;
  font-weight: 500;
  color: var(--color-text-tertiary);
  border-bottom: 1px solid var(--color-border);
}
.review-table td { padding: 5px 6px; border-bottom: 1px solid var(--color-border); vertical-align: top; color: var(--color-text-secondary); }
.review-table a { color: var(--paper-accent); text-decoration: none; }
.review-table a:hover { text-decoration: underline; }
.row-error {
  display: block;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--paper-danger);
}

.hint { margin: 0; font-size: 11px; line-height: 1.6; color: var(--color-text-tertiary); }
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
</style>
