<script setup>
import { computed } from 'vue'
import { NButton } from 'naive-ui'

const props = defineProps({
  busy: { type: Boolean, default: false },
  warnings: { type: Array, default: () => [] },
  impactChapters: { type: Array, default: () => [] },
  chapters: { type: Array, default: () => [] },
  saving: { type: Boolean, default: false },
})

defineEmits(['continue'])

const acceptedChapters = computed(() => props.chapters.filter((item) => item.status === 'accepted' && !item.stale && String(item.acceptedText || '').trim()))
const impactLines = computed(() =>
  props.impactChapters.map((item) => (typeof item === 'string' ? item : item?.title || item?.id || '')).filter(Boolean),
)
</script>

<template>
  <section class="panel">
    <header class="panel-head">
      <span>续写</span>
      <em>接着最后一章往下写</em>
    </header>

    <div class="panel-body">
      <NButton type="primary" block :disabled="busy || saving" :loading="busy" @click="$emit('continue')">续写下一章</NButton>
      <p class="hint">{{ saving ? '正在保存设定，请稍候。' : '失败且章节没有草稿或待选正文时，可再次点击重试；有待选正文时请先检查冲突候选。结果落成章节草稿。' }}</p>
      <div class="block">
        <h4>已接受章节参考候选（{{ acceptedChapters.length }}）</h4>
        <ul v-if="acceptedChapters.length" class="chips">
          <li v-for="chapter in acceptedChapters" :key="chapter.id">{{ chapter.title || chapter.id }}</li>
        </ul>
        <p v-else class="hint">暂无已接受章节。</p>
        <p class="hint">仅未过时的已接受正文可作为章节记忆候选；草稿和已过时章节不可参考。候选不代表实际全部纳入：历史记忆、单章摘要、人物、别名及上一章末尾受长度预算限制，可能截断或被排除，请人工核对连贯性。人物设定是项目级资料，不代表人物一定在本章出场。</p>
      </div>

      <div v-if="warnings.length" class="block is-warn">
        <h4>需要确认</h4>
        <ul class="warn-list">
          <li v-for="(item, index) in warnings" :key="`${item.code}-${index}`">
            <span class="warn-name">{{ item.name || '未命名' }}</span>
            <span class="warn-code">{{ item.code }}</span>
          </li>
        </ul>
      </div>

      <div v-if="impactLines.length" class="block">
        <h4>这些章可能过时</h4>
        <ul class="chips">
          <li v-for="(line, index) in impactLines" :key="`${line}-${index}`">{{ line }}</li>
        </ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
.panel { min-width: 0; border: 1px solid var(--paper-line); border-radius: 8px; background: var(--paper-surface); overflow: hidden; }
.panel-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; padding: 10px 14px; background: var(--paper-header); border-bottom: 1px solid var(--paper-line); }
.panel-head span { font-size: 12px; font-weight: 650; letter-spacing: 0.08em; color: var(--paper-ink-soft); }
.panel-head em { font-style: normal; font-size: 10.5px; color: var(--paper-ink-muted); }
.panel-body { padding: 12px 14px 14px; }
.hint { margin: 8px 0 0; font-size: 11px; line-height: 1.7; color: var(--paper-ink-muted); }

.block { margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--paper-line-strong); }
.block h4 { margin: 0 0 6px; font-size: 11.5px; font-weight: 600; color: var(--paper-ink-soft); }
.block.is-warn h4 { color: var(--paper-warning); }
.warn-list { list-style: none; display: flex; flex-direction: column; gap: 4px; margin: 0; padding: 0; }
.warn-list li { display: flex; align-items: baseline; gap: 6px; font-size: 11.5px; }
.warn-name { color: var(--paper-ink); }
.warn-code { color: var(--paper-ink-muted); font-size: 10px; }
.chips { list-style: none; display: flex; flex-wrap: wrap; gap: 4px; margin: 0; padding: 0; }
.chips li { padding: 1px 7px; border: 1px solid var(--paper-warning-line); border-radius: 999px; background: var(--paper-warning-soft); color: var(--paper-warning); font-size: 10.5px; }
</style>
