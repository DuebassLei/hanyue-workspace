<script setup>
import { computed, ref, watch } from 'vue'
import { NButton } from 'naive-ui'
import NovelCompare from './NovelCompare.vue'

const props = defineProps({
  chapter: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  accepting: { type: Boolean, default: false },
})

const emit = defineEmits(['rewrite', 'accept', 'revert', 'split'])

const isContinuation = computed(() => props.chapter?.source === 'continuation')
const hasDraft = computed(() => Boolean(String(props.chapter?.draftText || '').trim()))

const STATUS_LABEL = { pending: '待改写', drafted: '有草稿', accepted: '已接受' }

const texts = computed(() => ({
  source: String(props.chapter?.sourceText || ''),
  draft: String(props.chapter?.draftText || ''),
  accepted: String(props.chapter?.acceptedText || ''),
  pending: String(props.chapter?.pendingCandidate || ''),
}))

function defaultTab(chapter) {
  if (!chapter) return 'source'
  if (String(chapter.pendingCandidate || '').trim()) return 'pending'
  if (String(chapter.draftText || '').trim()) return 'draft'
  if (String(chapter.acceptedText || '').trim()) return 'accepted'
  return 'source'
}

const activeTab = ref(defaultTab(props.chapter))
watch(() => props.chapter?.id, () => { activeTab.value = defaultTab(props.chapter) })

const tabs = computed(() =>
  [
    { key: 'source', label: '原文' },
    { key: 'draft', label: '草稿' },
    { key: 'accepted', label: '已接受' },
    ...(texts.value.pending ? [{ key: 'pending', label: '待选' }] : []),
  ],
)

const currentText = computed(() => texts.value[activeTab.value] || '')
const charCount = computed(() => currentText.value.replace(/\s/g, '').length)

const EMPTY_TEXT = {
  source: '这一章没有原文（续写章节只保留生成结果）。',
  draft: '还没有草稿，点右上角「改写本章」生成。',
  accepted: '还没有接受过正文，草稿满意后点「接受」。',
  pending: '没有待选正文。',
}

function split() {
  emit('split')
}
</script>

<template>
  <section class="editor">
    <header class="editor-head">
      <div class="head-main">
        <h2 class="chapter-title">{{ chapter?.title || '未命名' }}</h2>
        <span class="status" :class="`is-${chapter?.status}`">{{ STATUS_LABEL[chapter?.status] || chapter?.status }}</span>
      </div>
      <div class="head-actions">
        <NButton
          v-if="!isContinuation"
          size="small"
          type="primary"
          :disabled="busy"
          :loading="busy"
          @click="emit('rewrite')"
        >改写本章</NButton>
        <NButton size="small" :disabled="busy || accepting || !hasDraft" :loading="accepting" @click="emit('accept')">接受</NButton>
        <NButton size="small" tertiary :disabled="busy || accepting || chapter?.status === 'pending'" @click="emit('revert')">退回</NButton>
      </div>
    </header>

    <div class="tabs" role="tablist" aria-label="正文视图">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        role="tab"
        class="tab"
        :class="{ 'is-active': activeTab === tab.key }"
        :aria-selected="activeTab === tab.key"
        @click="activeTab = tab.key"
      >{{ tab.label }}</button>
      <button
        type="button"
        class="tab tab-compare"
        :class="{ 'is-active': activeTab === 'compare' }"
        :aria-selected="activeTab === 'compare'"
        @click="activeTab = activeTab === 'compare' ? defaultTab(chapter) : 'compare'"
      >并排对照</button>
    </div>

    <div class="pane">
      <NovelCompare v-if="activeTab === 'compare'" :chapter="chapter" />
      <pre v-else-if="currentText" class="reading">{{ currentText }}</pre>
      <div v-else class="pane-empty">
        <p>{{ EMPTY_TEXT[activeTab] }}</p>
        <NButton v-if="activeTab === 'draft' && !isContinuation" size="small" :disabled="busy" :loading="busy" @click="emit('rewrite')">改写本章</NButton>
        <NButton v-if="activeTab === 'source' && isContinuation" size="small" tertiary @click="split">去切章</NButton>
      </div>
    </div>

    <footer class="editor-foot">
      <span v-if="activeTab !== 'compare'">{{ charCount.toLocaleString('zh-CN') }} 字</span>
      <span v-if="chapter?.status === 'pending' && !isContinuation" class="foot-note">尚未改写</span>
      <span v-if="activeTab === 'draft'" class="foot-note">草稿为只读快照：章节只接受标题与原文，改原文会清空改写稿。</span>
      <span v-if="activeTab === 'compare'" class="foot-note">左为原文，右为草稿或已接受正文。</span>
    </footer>
  </section>
</template>

<style scoped>
.editor { min-width: 0; height: 100%; min-height: 0; display: flex; flex-direction: column; background: var(--paper-bg); }
.editor-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; padding: 13px 20px; background: var(--paper-header); border-bottom: 1px solid var(--paper-line); }
.head-main { display: flex; align-items: center; gap: 10px; min-width: 0; }
.chapter-title { margin: 0; font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif; font-size: 17px; font-weight: 650; color: var(--paper-ink); overflow-wrap: anywhere; }
.status { flex: 0 0 auto; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--paper-line-strong); color: var(--paper-ink-muted); background: var(--paper-surface); font-size: 11px; }
.status.is-drafted { color: var(--paper-warning); border-color: var(--paper-warning-line); background: var(--paper-warning-soft); }
.status.is-accepted { color: var(--paper-success); border-color: color-mix(in srgb, var(--paper-success) 35%, transparent); }
.head-actions { display: flex; flex-wrap: wrap; gap: 6px; }

.tabs { display: flex; align-items: stretch; gap: 2px; padding: 0 12px; border-bottom: 1px solid var(--paper-line); background: var(--paper-surface); overflow-x: auto; }
.tab { position: relative; padding: 10px 12px 9px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--paper-ink-muted); font: inherit; font-size: 12.5px; white-space: nowrap; cursor: pointer; }
.tab:hover { color: var(--paper-ink); }
.tab.is-active { color: var(--paper-accent); border-bottom-color: var(--paper-accent); font-weight: 600; }
.tab-compare { margin-left: auto; color: var(--paper-ink-muted); }
.tab-compare::before { content: '⇹'; margin-right: 5px; }

.pane { flex: 1; min-height: 280px; overflow: auto; padding: 24px clamp(18px, 3vw, 38px) 28px; }
.reading {
  margin: 0 auto;
  max-width: 40em;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif;
  font-size: 15px;
  line-height: 2;
  letter-spacing: 0.01em;
  color: var(--paper-ink-soft);
}
.pane-empty { display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 64px 16px; text-align: center; }
.pane-empty p { margin: 0; color: var(--paper-ink-muted); font-size: 13px; }

.editor-foot { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; padding: 8px 20px; border-top: 1px solid var(--paper-line); background: var(--paper-surface); color: var(--paper-ink-muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.foot-note { color: var(--paper-ink-muted); }

@media (max-width: 640px) {
  .editor-head { padding: 12px 14px; }
  .pane { padding: 18px 14px 22px; }
  .tab-compare { margin-left: 0; }
}
</style>
