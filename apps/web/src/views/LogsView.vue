<script setup>
/**
 * LogsView — 系统日志（运维查看器）：直读服务端日志文件，按服务 / 级别 / 关键词 / requestId 过滤，
 * 支持按字节偏移增量拉取与可调频率的自动刷新。
 *
 * 从设置页的「系统日志」分栏独立出来，和模型管理一样归入「系统」菜单组。
 * 日志不入库、不建索引：后端按需反向 tail + 内存过滤，页内只负责展示与轮询。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { NAlert, NButton, NCheckbox, NInput, NSelect, NTag } from 'naive-ui'
import { api } from '../api/client'

const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
// 服务端只认 api / worker；日志目录里的 *.stderr / *.stdout 不能作为可选服务，否则查询必 400
const LOG_SERVICES = ['api', 'worker']
// 自动刷新频率：只允许预设档位；选择写入 localStorage，下次进页面沿用
const LOG_POLL_OPTIONS = [
  { label: '1 秒', value: 1000 },
  { label: '2.5 秒', value: 2500 },
  { label: '5 秒', value: 5000 },
  { label: '10 秒', value: 10000 },
  { label: '30 秒', value: 30000 },
]
const LOG_POLL_KEY = 'hanyue-log-poll-ms'

const logService = ref('api')
const logMinLevel = ref('')
const logKeyword = ref('')
const logRequestId = ref('')
const logEntries = ref([])
const logOffset = ref(null) // null = tail 模式；数字 = 增量轮询偏移
const logLoading = ref(false)
const logError = ref('')
const logAuto = ref(true)
const logTruncated = ref(false)
const logSummary = ref({})
const logStreamRef = ref(null)
let logTimer = null

const logLevelOptions = LOG_LEVELS.map((level) => ({ label: level, value: level }))
const logServiceOptions = LOG_SERVICES.map((name) => ({ label: name, value: name }))

function readLogPollMs() {
  const saved = Number(localStorage.getItem(LOG_POLL_KEY))
  return LOG_POLL_OPTIONS.some((item) => item.value === saved) ? saved : 2500
}
const logPollMs = ref(readLogPollMs())

const logErrorCount = computed(() =>
  Object.values(logSummary.value).reduce((sum, item) => sum + (item.errorCount || 0), 0),
)

// 用户向上翻阅历史时暂停自动滚底，回到底部后恢复
const logPinnedToBottom = ref(true)

function onLogStreamScroll() {
  const el = logStreamRef.value
  if (!el) return
  logPinnedToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

async function scrollLogToBottom(force = false) {
  await nextTick()
  const el = logStreamRef.value
  if (el && (force || logPinnedToBottom.value)) el.scrollTop = el.scrollHeight
}

function resetLogFilters() {
  logMinLevel.value = ''
  logKeyword.value = ''
  logRequestId.value = ''
  loadLogsTail()
}

function filterByRequestId(requestId) {
  logRequestId.value = requestId
  loadLogsTail()
}

function logQuery(extra = {}) {
  const params = new URLSearchParams()
  params.set('service', extra.service ?? logService.value)
  if (extra.offset != null) params.set('offset', String(extra.offset))
  if (logMinLevel.value) params.set('level', logMinLevel.value)
  const keyword = String(logKeyword.value || '').trim()
  const requestId = String(logRequestId.value || '').trim()
  if (keyword) params.set('keyword', keyword)
  if (requestId) params.set('requestId', requestId)
  return `/logs?${params.toString()}`
}

async function loadLogsTail() {
  logLoading.value = true
  logError.value = ''
  try {
    const data = await api.get(logQuery({ offset: null }))
    logEntries.value = data.entries || []
    logOffset.value = data.nextOffset ?? 0
    logTruncated.value = Boolean(data.hasMore)
    await scrollLogToBottom(true)
  } catch (err) {
    logError.value = err.message
  } finally {
    logLoading.value = false
  }
}

async function pollLogs() {
  if (document.hidden || logLoading.value) return
  logLoading.value = true
  try {
    if (logOffset.value == null) {
      await loadLogsTail()
      return
    }
    const data = await api.get(logQuery({ offset: logOffset.value }))
    if (data.entries?.length) {
      logEntries.value = [...logEntries.value, ...data.entries].slice(-2000)
      await scrollLogToBottom()
    }
    // nextOffset 归零说明日志轮转/清空：重置为 tail 重新拉尾部
    logOffset.value = data.nextOffset === 0 ? null : data.nextOffset
    if (data.truncated) await loadLogsTail()
  } catch {
    // 轮询失败静默重试，不打断页面；连续失败由下一次手动刷新暴露
  } finally {
    logLoading.value = false
  }
}

function startLogPolling() {
  stopLogPolling()
  if (logAuto.value) logTimer = setInterval(pollLogs, logPollMs.value)
}

function stopLogPolling() {
  if (logTimer) {
    clearInterval(logTimer)
    logTimer = null
  }
}

function toggleLogAuto() {
  logAuto.value = !logAuto.value
  startLogPolling()
}

/** 改档位后立即按新间隔重启，不必先关再开自动刷新 */
function setLogPollMs(value) {
  const next = Number(value)
  logPollMs.value = LOG_POLL_OPTIONS.some((item) => item.value === next) ? next : 2500
  try {
    localStorage.setItem(LOG_POLL_KEY, String(logPollMs.value))
  } catch {
    /* 隐私模式下 localStorage 不可写，不影响本次会话 */
  }
  startLogPolling()
}

async function refreshLogSummary() {
  try {
    const data = await api.get('/logs/summary')
    logSummary.value = data.services || {}
  } catch {
    /* 概览失败不影响主功能 */
  }
}

onMounted(() => {
  loadLogsTail()
  refreshLogSummary()
  startLogPolling()
})

onBeforeUnmount(stopLogPolling)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">系统日志</h1>
        <p class="page-sub">
          直读服务端日志文件，不入库；按服务、级别、关键词或 requestId 过滤，支持增量自动刷新。
        </p>
      </div>
      <n-tag v-if="logErrorCount" size="small" round type="error">
        最近 {{ logErrorCount }} 条错误
      </n-tag>
    </div>

    <section class="card">
      <div v-if="Object.keys(logSummary).length" class="log-toolbar">
        <span
          v-for="(item, name) in logSummary"
          :key="name"
          class="log-summary-chip"
          :class="{ 'is-error': item.errorCount, 'is-warn': !item.errorCount && item.warnCount }"
        >
          {{ name }} · 错误 {{ item.errorCount }} · 警告 {{ item.warnCount }}
        </span>
      </div>

      <div class="log-toolbar">
        <n-select
          v-model:value="logService"
          class="log-service-select"
          :options="logServiceOptions"
          size="small"
          @update:value="loadLogsTail"
        />
        <n-select
          v-model:value="logMinLevel"
          class="log-level-select"
          :options="logLevelOptions"
          size="small"
          clearable
          placeholder="全部级别"
          @update:value="loadLogsTail"
        />
        <n-input
          v-model:value="logKeyword"
          class="log-keyword-input"
          size="small"
          placeholder="关键词过滤"
          @keyup.enter="loadLogsTail"
        />
        <n-input
          v-model:value="logRequestId"
          class="log-rid-input"
          size="small"
          placeholder="requestId"
          @keyup.enter="loadLogsTail"
        />
        <n-button size="small" type="primary" @click="loadLogsTail">查询</n-button>
        <n-button size="small" quaternary @click="resetLogFilters">清空条件</n-button>
        <div class="spacer" />
        <n-checkbox :checked="logAuto" size="small" @update:checked="toggleLogAuto">
          自动刷新
        </n-checkbox>
        <n-select
          class="log-poll-select"
          :value="logPollMs"
          :options="LOG_POLL_OPTIONS"
          :disabled="!logAuto"
          size="small"
          @update:value="setLogPollMs"
        />
      </div>

      <n-alert v-if="logError" type="error" :bordered="false" class="log-alert">
        {{ logError }}
      </n-alert>
      <p v-if="logTruncated" class="log-truncated-hint">
        本次结果超过服务端单次上限，仅展示最近部分；请缩小筛选范围。
      </p>

      <div ref="logStreamRef" class="log-stream" @scroll.passive="onLogStreamScroll">
        <div v-if="!logEntries.length" class="log-empty">
          {{ logLoading ? '正在读取日志…' : '当前条件下没有日志条目' }}
        </div>
        <div
          v-for="(entry, index) in logEntries"
          :key="`${entry.ts}-${entry.requestId}-${index}`"
          class="log-line"
          :class="entry.level ? `level-${entry.level.toLowerCase()}` : 'level-continuation'"
        >
          <span v-if="entry.ts" class="log-ts">{{ entry.ts }}</span>
          <span v-if="entry.level" class="log-level">{{ entry.level }}</span>
          <span v-if="entry.logger" class="log-logger">[{{ entry.logger }}]</span>
          <span
            v-if="entry.requestId"
            class="log-rid"
            title="按该 requestId 过滤"
            @click="filterByRequestId(entry.requestId)"
          >[{{ entry.requestId }}]</span>
          <span class="log-msg">{{ entry.message }}</span>
        </div>
      </div>
      <p class="field-hint" style="margin-top: var(--space-2)">
        筛选只影响展示，不会修改日志文件；点击 requestId 可按链路过滤。自动刷新间隔可调（1～30 秒），向上翻阅时暂停自动滚底。
      </p>
    </section>
  </div>
</template>

<style scoped>
.log-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin: var(--space-3) 0;
}
.log-service-select {
  width: 120px;
}
.log-level-select {
  width: 130px;
}
.log-keyword-input {
  width: 180px;
}
.log-rid-input {
  width: 160px;
}
.log-poll-select {
  width: 96px;
}
.log-summary-chip {
  font-size: var(--font-xs);
  padding: 2px 8px;
  border-radius: 10px;
}
.log-summary-chip.is-error {
  color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 12%, transparent);
}
.log-summary-chip.is-warn {
  color: var(--color-warning);
  background: color-mix(in srgb, var(--color-warning) 14%, transparent);
}
.log-alert {
  margin-bottom: var(--space-2);
}
.log-truncated-hint {
  font-size: var(--font-xs);
  color: var(--color-warning);
  margin-bottom: var(--space-2);
}
.log-stream {
  max-height: 480px;
  overflow: auto;
  background: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: var(--space-2) var(--space-3);
  font-family: 'JetBrains Mono', Consolas, Menlo, monospace;
  font-size: 12px;
  line-height: 1.7;
}
.log-empty {
  color: var(--color-text-muted);
  padding: var(--space-4);
  text-align: center;
}
.log-line {
  white-space: pre-wrap;
  word-break: break-all;
}
.log-ts {
  color: var(--color-text-muted);
  margin-right: 6px;
}
.log-level {
  display: inline-block;
  min-width: 58px;
  font-weight: 600;
  margin-right: 6px;
}
.level-error .log-level,
.level-critical .log-level,
.level-error .log-msg,
.level-critical .log-msg {
  color: var(--color-danger);
}
.level-warning .log-level,
.level-warning .log-msg {
  color: var(--color-warning);
}
.level-debug .log-msg {
  color: var(--color-text-muted);
}
.log-logger {
  color: var(--color-info);
  margin-right: 6px;
}
.log-rid {
  color: var(--color-text-muted);
  margin-right: 6px;
  cursor: pointer;
  text-decoration: underline dotted;
}
.log-line.level-continuation {
  padding-left: 18px;
  color: var(--color-text-muted);
}
</style>
