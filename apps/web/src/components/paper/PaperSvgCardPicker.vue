<script setup>
import { computed, ref, watch } from 'vue'
import { NButton } from 'naive-ui'

const props = defineProps({
  catalog: { type: Array, default: () => [] },
  selectedBlockId: { type: String, default: '' },
  // 正文里多选出的素材段落（按文档顺序由父级传入）；为空时退回当前段。
  selectedBlockIds: { type: Array, default: () => [] },
  blocks: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['confirm', 'pick-in-paper'])
const selected = ref('')
const cards = computed(() => props.catalog.filter((item) => item.id || item.kind))
const current = computed(() => cards.value.find((item) => (item.id || item.kind) === selected.value))
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

// 素材来源：优先用正文多选结果，没多选时用当前点中的那一段，保证「只选一段」也能直接生成。
const effectiveIds = computed(() =>
  props.selectedBlockIds.length ? props.selectedBlockIds : props.selectedBlockId ? [props.selectedBlockId] : [],
)
const pickedBlocks = computed(() => props.blocks.filter((block) => effectiveIds.value.includes(block.id)))
const blockNumber = (block) => props.blocks.findIndex((item) => item.id === block.id) + 1
const excerpt = (text) => {
  const plain = String(text || '').replace(/\s+/g, ' ').trim()
  return plain.length > 34 ? `${plain.slice(0, 34)}…` : plain || '（空段落）'
}
const mergedText = computed(() => pickedBlocks.value.map((block) => String(block.text || '').trim()).join('\n\n'))
const enoughText = computed(() => mergedText.value.trim().length >= 8)
const canSubmit = computed(() => Boolean(selected.value) && pickedBlocks.value.length > 0 && enoughText.value)

watch(cards, (list) => {
  if (selected.value && !list.some((item) => (item.id || item.kind) === selected.value)) selected.value = ''
})

function previewDocument(html) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#fff;overflow:hidden}body{padding:4px;box-sizing:border-box}svg{max-width:100%;height:auto}</style></head><body>${html}</body></html>`
}
</script>

<template>
  <section class="svg-picker" aria-label="SVG 卡">
    <div class="heading"><h3>SVG 卡</h3><span>插在所选最后一处之后</span></div>
    <p class="hint">选择卡型查看引擎样例；生成内容以正文所选段落为准。</p>
    <div v-if="cards.length" class="cards">
      <button
        v-for="card in cards"
        :key="card.id || card.kind"
        type="button"
        class="card"
        :class="{ active: selected === (card.id || card.kind) }"
        :aria-pressed="selected === (card.id || card.kind)"
        @click="selected = card.id || card.kind"
      >
        <div class="thumbnail">
          <iframe v-if="card.previewHtml" tabindex="-1" sandbox="" :srcdoc="previewDocument(card.previewHtml)" title="卡片样例缩略图" />
          <span v-else>预览不可用</span>
        </div>
        <span class="card-copy"><b>{{ card.name }}</b><small>{{ card.lead || card.desc }}</small></span>
      </button>
    </div>
    <p v-else class="hint">卡片目录暂不可用。</p>
    <div v-if="current" class="detail">
      <b>{{ current.name }} · 样例预览</b>
      <iframe v-if="current.previewHtml" class="large-preview" sandbox="" :srcdoc="previewDocument(current.previewHtml)" :title="`${current.name}样例预览`" />
      <p v-else class="hint">暂时无法展示引擎样例。</p>
      <p class="hint">{{ requirements[selected] || current.lead }}</p>

      <div class="source">
        <div class="source-head">
          <b>素材段落</b>
          <NButton size="tiny" :disabled="busy" @click="emit('pick-in-paper')">
            {{ pickedBlocks.length ? '去正文改选' : '去正文选段落' }}
          </NButton>
        </div>
        <ul v-if="pickedBlocks.length" class="chips">
          <li v-for="block in pickedBlocks" :key="block.id">
            <b>{{ blockNumber(block) }}</b><span>{{ excerpt(block.text) }}</span>
          </li>
        </ul>
        <p v-else class="hint">还没有选段落：点「去正文选段落」后在正文里点要用的段落。</p>
        <p v-if="pickedBlocks.length && !enoughText" class="hint strong">所选段落合并后至少需要 8 个字。</p>
        <p v-else-if="pickedBlocks.length > 1" class="hint">已选 {{ pickedBlocks.length }} 段，将合并成一个素材源生成一张卡。</p>
      </div>

      <p class="hint">样例仅展示卡型，不是生成结果。使用 AI 根据段落生成并直接插入；结果请核对原文。</p>
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
.svg-picker { display:flex; flex-direction:column; gap:8px; padding-top:10px; border-top:1px solid var(--color-border); }
.heading { display:flex; justify-content:space-between; align-items:baseline; gap:8px; }
.heading h3 { margin:0; font-size:12px; font-weight:600; }
.heading span, .hint { font-size:11px; color:var(--color-text-tertiary); }
.hint { margin:0; line-height:1.6; }
.hint.strong { color:var(--color-text-secondary); }
.cards { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; }
.card { min-width:0; padding:5px; text-align:left; background:var(--color-surface); color:var(--color-text-secondary); border:1px solid var(--color-border); border-radius:8px; cursor:pointer; }
.card.active { border-color:var(--color-primary, #c9643e); }
.card:focus-visible { outline:2px solid var(--color-primary, #c9643e); outline-offset:2px; }
.thumbnail { height:70px; overflow:hidden; background:#fff; border-radius:4px; display:flex; justify-content:center; align-items:center; font-size:10px; color:#777; }
.thumbnail iframe { width:200%; height:140px; flex:none; border:0; transform:scale(.5); pointer-events:none; }
.card-copy { display:flex; flex-direction:column; gap:2px; padding:4px 1px 1px; }
.card-copy b { font-size:11px; }
.card-copy small { font-size:10px; color:var(--color-text-tertiary); }
.detail { display:flex; flex-direction:column; gap:7px; padding:9px; border:1px solid var(--color-border); border-radius:8px; font-size:12px; }
.large-preview { width:100%; height:220px; border:0; border-radius:6px; background:#fff; }
.source { display:flex; flex-direction:column; gap:6px; }
.source-head { display:flex; justify-content:space-between; align-items:center; gap:8px; font-size:11px; }
.chips { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:3px; max-height:132px; overflow:auto; }
.chips li { display:flex; align-items:baseline; gap:6px; font-size:11px; padding:3px 6px; border-radius:5px; background:var(--color-hover, rgba(0,0,0,.04)); min-width:0; }
.chips b { flex:none; min-width:16px; text-align:center; font-size:10px; color:var(--color-text-tertiary); }
.chips span { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--color-text-secondary); }
</style>
