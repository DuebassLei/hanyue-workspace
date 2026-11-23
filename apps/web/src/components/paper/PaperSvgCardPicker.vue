<script setup>
import { computed, ref, watch } from 'vue'
import { NButton, NSelect } from 'naive-ui'
import PaperParagraphPickControls from './PaperParagraphPickControls.vue'

const props = defineProps({
  catalog: { type: Array, default: () => [] },
  selectedBlockIds: { type: Array, default: () => [] },
  picking: Boolean,
  blocks: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['confirm', 'pick-in-paper', 'clear-pick', 'finish-pick'])
const selected = ref(null)
const cards = computed(() => props.catalog.filter((item) => item.id || item.kind))
const current = computed(() => cards.value.find((item) => (item.id || item.kind) === selected.value))
const cardOptions = computed(() => cards.value.map((item) => ({ label: item.name, value: item.id || item.kind })))
const requirements = {
  'svg-table': '适合多列信息；需要表头和 1–7 行数据。',
  'svg-bars': '适合数值对比；1–6 项，数值必须出现在所选段落中，不支持编造或换算。',
  'svg-steps': '适合操作流程；需要 2–6 个步骤。',
  'svg-timeline': '适合时间顺序；需要 1–6 个时间与事件。',
  'svg-breakdown': '适合解释词条；需要 1–5 组词条和解释。',
  'svg-compare': '适合左右对照；需要表头和 1–6 组对比。',
  'svg-concept': '适合呈现概念变化；需要 1–2 条轨道，每条 1–4 个节点。',
  'svg-phone': '适合展示对话场景；支持 1–5 幕，不支持金句。',
}

const pickedBlocks = computed(() => props.blocks.filter((block) => props.selectedBlockIds.includes(block.id)))
const blockNumber = (block) => props.blocks.findIndex((item) => item.id === block.id) + 1
const excerpt = (text) => {
  const plain = String(text || '').replace(/\s+/g, ' ').trim()
  return plain.length > 34 ? `${plain.slice(0, 34)}…` : plain || '（空段落）'
}
const mergedText = computed(() => pickedBlocks.value.map((block) => String(block.text || '').trim()).join('\n\n'))
const enoughText = computed(() => mergedText.value.trim().length >= 8)
const canSubmit = computed(() => Boolean(selected.value) && pickedBlocks.value.length > 0 && enoughText.value)

watch(cards, (list) => {
  if (selected.value && !list.some((item) => (item.id || item.kind) === selected.value)) selected.value = null
})

function previewDocument(html) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#fff;overflow:hidden}body{padding:4px;box-sizing:border-box}svg{max-width:100%;height:auto}</style></head><body>${html}</body></html>`
}
</script>

<template>
  <section class="svg-picker" aria-label="SVG 卡">
    <NSelect v-if="cards.length" v-model:value="selected" size="small" :options="cardOptions" placeholder="选择卡型" aria-label="卡型" />
    <p v-else class="hint">卡片目录暂不可用。</p>
    <div v-if="cards.length" class="detail">
      <p v-if="current" class="hint">{{ requirements[selected] || current.lead }}</p>
      <details v-if="current?.previewHtml" :key="selected" class="sample-preview">
        <summary>查看卡型样例</summary>
        <iframe loading="lazy" class="large-preview" sandbox="" :srcdoc="previewDocument(current.previewHtml)" :title="`${current.name}样例预览`" />
      </details>

      <div class="source">
        <b class="source-head">素材段落</b>
        <PaperParagraphPickControls
          :active="picking"
          :count="pickedBlocks.length"
          :disabled="busy || !blocks.length"
          @start="emit('pick-in-paper')"
          @clear="emit('clear-pick')"
          @finish="emit('finish-pick')"
        />
        <ul v-if="pickedBlocks.length" class="chips">
          <li v-for="block in pickedBlocks" :key="block.id">
            <b>{{ blockNumber(block) }}</b><span>{{ excerpt(block.text) }}</span>
          </li>
        </ul>
        <p v-if="pickedBlocks.length && !enoughText" class="hint strong">所选段落合并后至少需要 8 个字。</p>
        <p v-else-if="pickedBlocks.length > 1" class="hint">所选段落将合并生成一张卡。</p>
      </div>

      <p v-if="canSubmit" class="hint">卡片将插入最后一个所选段落之后，生成后请核对原文。</p>
      <NButton
        size="small"
        type="primary"
        :disabled="busy || !canSubmit"
        :loading="loading"
        @click="emit('confirm', selected, pickedBlocks.map((block) => block.id))"
      >确认生成并插入</NButton>
    </div>
  </section>
</template>

<style scoped>
.svg-picker, .detail { display:flex; flex-direction:column; gap:8px; }
.hint { margin:0; line-height:1.6; font-size:11px; color:var(--color-text-tertiary); }
.hint.strong { color:var(--color-text-secondary); }
.sample-preview { font-size:12px; }
.sample-preview summary { cursor:pointer; color:var(--color-text-secondary); }
.sample-preview[open] summary { margin-bottom:6px; }
.large-preview { width:100%; height:220px; border:0; border-radius:6px; background:#fff; }
.source { display:flex; flex-direction:column; gap:6px; }
.source-head { font-size:12px; }
.chips { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:3px; max-height:132px; overflow:auto; }
.chips li { display:flex; align-items:baseline; gap:6px; font-size:11px; padding:3px 6px; border-radius:5px; background:var(--color-hover, rgba(0,0,0,.04)); min-width:0; }
.chips b { flex:none; min-width:16px; text-align:center; font-size:10px; color:var(--color-text-tertiary); }
.chips span { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--color-text-secondary); }
</style>
