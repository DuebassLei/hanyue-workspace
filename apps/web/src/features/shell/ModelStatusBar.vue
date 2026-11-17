<script setup>
/**
 * ModelStatusBar — 顶栏里「当前用哪个文本模型 / 生图模型」的状态条。
 *
 * 纯展示 + 诚实：状态来自 /models/status 的配置链路检查，不发真实请求，
 * 所以「可用」只代表链路齐且连接测过；没测过就显示灰色，不冒充绿色。
 *
 * 点整条状态、或浮层底部那行「模型管理」，都直接进「模型管理」页，
 * 而不是停在设置里让人自己再找一次。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NPopover } from 'naive-ui'
import { api } from '../../api/client'
import { useSessionStore } from '../../stores/session'

// 设置页测完连接会播这个事件，立即刷新，不必等下一轮轮询
const REFRESH_EVENT = 'hanyue:model-status-changed'
const POLL_MS = 60_000

const emit = defineEmits(['manage-models'])

const session = useSessionStore()

const items = ref([])
const error = ref('')
const loading = ref(false)
const updatedAt = ref('')
// 受控显示：浮层里的「模型设置」点完要立刻收起，不能让鼠标悬停把它留在这儿
const open = ref(false)

const STATE_HINT = {
  ok: '可用',
  blocked: '不可用',
  unchecked: '待确认',
  none: '未配置',
}

const KIND = {
  text: { label: '文本' },
  image: { label: '生图' },
}

const rows = computed(() =>
  items.value.map((item) => ({
    ...item,
    kindLabel: KIND[item.kind]?.label || item.kind,
    hint: STATE_HINT[item.state] || item.state,
    name: item.displayName || item.modelCode || '未配置',
  })),
)

const hidden = computed(() => {
  // 错误已在顶栏提示条里说过；这里只剩「没拿到状态」时也不要打架
  if (!session.token) return true
  return !rows.value.length && !error.value && !loading.value
})

async function load() {
  if (!session.token) return
  loading.value = true
  try {
    items.value = (await api.get('/models/status')) || []
    error.value = ''
    updatedAt.value = new Date().toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch (err) {
    // 后端不可达时保留上一次结果，只记错误，不让状态条忽隐忽现
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function onVisibility() {
  if (!document.hidden) load()
}

function goSettings() {
  open.value = false
  emit('manage-models')
}

let timer = null

onMounted(() => {
  load()
  timer = setInterval(() => {
    if (!document.hidden) load()
  }, POLL_MS)
  window.addEventListener(REFRESH_EVENT, load)
  document.addEventListener('visibilitychange', onVisibility)
})

onBeforeUnmount(() => {
  clearInterval(timer)
  window.removeEventListener(REFRESH_EVENT, load)
  document.removeEventListener('visibilitychange', onVisibility)
})
</script>

<template>
  <n-popover v-if="!hidden" v-model:show="open" trigger="hover" placement="bottom-end" :show-arrow="false">
    <template #trigger>
      <button
        type="button"
        class="model-status"
        :aria-label="`${rows.map((row) => `${row.kindLabel}模型 ${row.name}：${row.hint}`).join('；')}；点击进入模型管理`"
        @click="goSettings"
      >
        <span
          v-for="row in rows"
          :key="row.kind"
          class="model-status-item"
          :class="`is-${row.state}`"
        >
          <span class="model-status-kind">{{ row.kindLabel }}</span>
          <span class="model-status-name">{{ row.name }}</span>
        </span>
        <span v-if="!rows.length" class="model-status-item is-none">
          <span class="model-status-name">{{ loading ? '读取模型…' : '模型状态未知' }}</span>
        </span>
      </button>
    </template>
    <div class="model-status-pop">
      <div v-for="row in rows" :key="row.kind" class="model-status-row">
        <div class="model-status-row-head">
          <span class="model-status-dot" :class="`is-${row.state}`" aria-hidden="true" />
          <strong>{{ row.kindLabel }}模型</strong>
          <span class="model-status-state" :class="`is-${row.state}`">{{ row.hint }}</span>
        </div>
        <p class="model-status-line">{{ row.name }}</p>
        <p v-if="row.modelCode && row.modelCode !== row.name" class="model-status-meta">
          代号 {{ row.modelCode }}
        </p>
        <p v-if="row.connectionName" class="model-status-meta">连接 {{ row.connectionName }}</p>
        <p class="model-status-msg">{{ row.message }}</p>
      </div>
      <p v-if="error" class="model-status-msg is-blocked">状态读取失败：{{ error }}</p>
      <button type="button" class="model-status-cta" @click="goSettings">
        <span v-if="updatedAt" class="model-status-cta-time">检查于 {{ updatedAt }}</span>
        <span class="model-status-cta-link">模型管理 →</span>
      </button>
    </div>
  </n-popover>
</template>

<style scoped>
.model-status {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 30px;
  max-width: 380px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.model-status:hover {
  border-color: var(--color-border-strong);
}

/* 绿（可用）/ 红（断链）/ 灰（未验证、未配置），另外浮层里带文字状态，不只靠颜色 */
.is-ok {
  color: var(--color-success);
}

.is-blocked {
  color: var(--color-danger);
}

.is-unchecked,
.is-none {
  color: var(--color-text-muted);
}

.model-status-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--color-text-muted);
}

.model-status-item.is-ok {
  color: var(--color-success);
}

.model-status-item.is-blocked {
  color: var(--color-danger);
}

.model-status-item::before {
  content: '';
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
  flex: 0 0 auto;
}

.model-status-kind {
  color: var(--color-text-muted);
}

.model-status-name {
  max-width: 130px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-status-pop {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 280px;
  margin: -2px;
}

.model-status-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.model-status-row-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.model-status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
}

.model-status-state {
  margin-left: auto;
  font-size: 12px;
}

.model-status-line {
  margin: 0;
  font-size: 13px;
}

.model-status-meta {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-muted);
}

.model-status-msg {
  margin: 2px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-secondary);
}

/* 浮层底部：整行就是入口，别只做一行小字提示 */
.model-status-cta {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-raised);
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}

.model-status-cta:hover {
  border-color: var(--color-accent);
  color: var(--color-text-primary);
}

.model-status-cta:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.model-status-cta-time {
  color: var(--color-text-muted);
}

.model-status-cta-link {
  margin-left: auto;
  color: var(--color-accent);
  font-weight: 600;
}

@media (max-width: 980px) {
  /* 中等宽度只留色点，名字进浮层；不然顶栏图标会被挤下去 */
  .model-status-name {
    display: none;
  }

  .model-status {
    gap: 8px;
    padding: 0 8px;
  }
}

@media (max-width: 640px) {
  .model-status {
    display: none;
  }
}
</style>
