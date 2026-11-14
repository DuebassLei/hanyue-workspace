<script setup>
import { computed } from 'vue'
import { NButton } from 'naive-ui'

const props = defineProps({
  chapter: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  accepting: { type: Boolean, default: false },
})

const emit = defineEmits(['rewrite', 'accept', 'revert'])

const isContinuation = computed(() => props.chapter?.source === 'continuation')
const hasDraft = computed(() => Boolean(String(props.chapter?.draftText || '').trim()))
</script>

<template>
  <section v-if="chapter" class="novel-editor">
    <header class="editor-head">
      <h2>{{ chapter.title || '未命名' }}</h2>
      <div class="editor-actions">
        <NButton
          v-if="!isContinuation"
          size="small"
          type="primary"
          :disabled="busy"
          :loading="busy"
          @click="emit('rewrite')"
        >改写本章</NButton>
        <NButton size="small" :disabled="busy || accepting || !hasDraft" :loading="accepting" @click="emit('accept')">接受</NButton>
        <NButton size="small" secondary :disabled="busy || accepting || chapter.status === 'pending'" @click="emit('revert')">退回</NButton>
      </div>
    </header>

    <p v-if="chapter.status === 'pending' && !isContinuation" class="pending-hint">尚未改写</p>

    <div class="text-block">
      <h3>原文</h3>
      <p v-if="isContinuation || !chapter.sourceText" class="muted">无原文</p>
      <pre v-else class="plain">{{ chapter.sourceText }}</pre>
    </div>

    <div class="text-block">
      <h3>草稿</h3>
      <p v-if="!chapter.draftText" class="muted">还没有草稿</p>
      <pre v-else class="plain">{{ chapter.draftText }}</pre>
      <p class="note">草稿正文没有保存接口。章节更新只接受标题和原文，改原文会清空改写稿，所以这里只读。</p>
    </div>

    <div v-if="chapter.pendingCandidate" class="text-block">
      <h3>待选</h3>
      <p class="muted">这次结果和现有草稿冲突，新正文在这里，原草稿仍保留。</p>
      <pre class="plain">{{ chapter.pendingCandidate }}</pre>
    </div>

    <div class="text-block">
      <h3>已接受正文</h3>
      <p v-if="!chapter.acceptedText" class="muted">尚未接受</p>
      <pre v-else class="plain">{{ chapter.acceptedText }}</pre>
    </div>
  </section>
</template>

<style scoped>
.novel-editor { min-width: 0; }
.editor-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
.editor-head h2 { margin: 0; font-size: 16px; overflow-wrap: anywhere; }
.editor-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.pending-hint { margin: 8px 0 0; color: #9a6230; font-size: 13px; }
.text-block { margin-top: 14px; min-width: 0; }
.text-block h3 { margin: 0 0 6px; font-size: 13px; color: #5c564e; }
.plain { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.6 inherit; background: #f7f4ee; padding: 10px; max-height: 280px; overflow: auto; }
.muted, .note { margin: 0; color: #827a71; font-size: 12px; }
.note { margin-top: 6px; }
</style>
