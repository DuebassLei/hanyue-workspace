<script setup>
/**
 * 段落下方的批注条：告诉作者这一段被判定成了什么、能做什么。
 *
 * 设计取向是「贴着段落的细条」而不是卡片 —— 一篇文章有几十段，
 * 卡片会把稿纸撑得很散，细条只占一行，扫读时不会打断正文。
 *
 * 分支顺序严格照参考项目：改写中 → 未检测 → 已通过 / 保留 / 已改写 → 标红。
 * 文案（「改这段」「保留」「退回上一版」「看改动」）也跟参考项目保持一致。
 */
import { computed, ref } from 'vue'
import PaperDiffDialog from './PaperDiffDialog.vue'

const props = defineProps({
  block: { type: Object, required: true },
  job: { type: Object, default: null },
  number: { type: Number, required: true },
})
const emit = defineEmits(['fix', 'keep', 'revert'])

const showDiff = ref(false)

const check = computed(() => props.block?.check || null)
// 后端用 null 表示「没有上一版」，空字符串是合法的上一版内容，所以不能用真值判断
const prevText = computed(() => (props.block?.prevText === undefined ? null : props.block.prevText))
const hasPrev = computed(() => prevText.value !== null)
const status = computed(() => check.value?.status || '')

// 后台任务：单段改写看 target，批量改写看 targets 里有没有本段
const busyKind = computed(() => {
  const job = props.job
  if (!job) return ''
  if (job.kind === 'fix' && job.target === props.block.id) return 'fix'
  if (job.kind === 'fixall' && (job.targets || []).includes(props.block.id)) return 'fixall'
  return ''
})

const STATUS_TEXT = { pass: '已通过', kept: '保留原样', fixed: '已改写，等下一轮检测' }

// 标红段的标题：朱雀 API / PDF 分别标注来源，本地规则单独叫名字，避免被误当成朱雀结论
const flagTitle = computed(() => {
  const current = check.value
  if (current?.source === 'local') return '本地规则：疑似 AI'
  const prefix = current?.source === 'pdf' ? '朱雀 PDF' : '朱雀 API'
  return `${prefix}：${current?.level === 'ai' ? 'AI 特征' : '疑似 AI'}`
})

const markText = computed(() => {
  if (!status.value) return ''
  if (status.value === 'flag') return flagTitle.value
  return STATUS_TEXT[status.value] || ''
})

const scoreText = computed(() => {
  if (status.value !== 'flag') return ''
  const current = check.value
  if (current?.source === 'api') return `分类置信度 ${Math.round(Number(current.score || 0) * 100)}%`
  return `报告得分 ${current?.score}`
})

// 建议只出一条：三条同时命中时作者也只会照第一条做，堆在一起反而没人看
const suggestion = computed(() => {
  const current = check.value
  if (status.value !== 'flag' || !current) return ''
  if (current.worse) return '改完分数反而升了，建议退回上一版的写法。'
  if (Number(current.fixes) >= 2) return '这一段已经改过两轮。建议你用自己平时说话的方式重说一遍，点正文就能直接改。'
  if (current.source !== 'api' && Number(current.score) < 0.6) return '0.5–0.6 是噪声带，每次重测都会浮动，可以不改。'
  return ''
})

const revertFirst = computed(() => Boolean(check.value?.worse) && hasPrev.value)

const tone = computed(() => {
  if (busyKind.value) return 'busy'
  if (status.value === 'flag') return 'flag'
  if (status.value === 'fixed') return 'fixed'
  if (status.value === 'pass') return 'pass'
  return 'quiet'
})

// 没检测、没上一版、也不在改写中 —— 整条不渲染，不给稿纸留空占位
const visible = computed(() => Boolean(busyKind.value || markText.value || hasPrev.value))
</script>

<template>
  <div v-if="visible" class="ann" :class="[tone, { worse: check?.worse }]">
    <span v-if="busyKind" class="ann-shimmer">正在改写这一段…</span>

    <template v-else-if="markText">
      <span class="ann-mark">{{ markText }}</span>
      <span v-if="scoreText" class="ann-score">{{ scoreText }}</span>
      <span class="ann-actions">
        <button v-if="hasPrev" type="button" class="ann-act" @click="showDiff = true">看改动</button>
        <template v-if="status === 'flag'">
          <button v-if="revertFirst" type="button" class="ann-act is-primary" @click="emit('revert')">退回上一版</button>
          <button v-else type="button" class="ann-act is-primary" :disabled="Boolean(job)" @click="emit('fix')">改这段</button>
          <button type="button" class="ann-act" @click="emit('keep')">保留</button>
        </template>
        <button v-else-if="status === 'fixed' && hasPrev" type="button" class="ann-act" @click="emit('revert')">退回上一版</button>
      </span>
      <p v-if="suggestion" class="ann-tip">{{ suggestion }}</p>
    </template>

    <span v-else class="ann-actions">
      <button type="button" class="ann-act" @click="showDiff = true">看改动</button>
    </span>

    <PaperDiffDialog
      v-model:show="showDiff"
      :before="prevText || ''"
      :after="block.text || ''"
      :number="number"
    />
  </div>
</template>

<style scoped>
.ann {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
  margin-top: 8px;
  padding: 3px 0 3px 10px;
  /* 左侧竖线就是风险等级：一眼扫过去先看到颜色，再读文字 */
  border-left: 2px solid var(--paper-line-strong, #d8cdb6);
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-tertiary, #8a8a8a);
}
.ann.flag { border-left-color: var(--paper-danger, #a3271f); }
.ann.fixed { border-left-color: var(--color-warning, #b26a00); }
.ann.pass { border-left-color: var(--paper-success, #3f7a4f); }
.ann.busy {
  border-left-color: var(--paper-accent, #b08d57);
  background: var(--paper-accent-tint, #f7f3e8);
  padding-right: 8px;
  border-radius: 0 6px 6px 0;
}

.ann-mark { font-weight: 600; color: var(--paper-ink-soft, #9a8f7a); }
.ann.flag .ann-mark { color: var(--paper-danger, #a3271f); }
.ann.fixed .ann-mark { color: var(--color-warning, #b26a00); }
.ann.pass .ann-mark { color: var(--paper-success, #3f7a4f); }
/* 改完反而更重：标题直接变红加粗，不和普通标红混在一起 */
.ann.worse .ann-mark { color: var(--paper-danger, #a3271f); }

.ann-score { font-variant-numeric: tabular-nums; }
.ann-tip { flex-basis: 100%; margin: 0; color: var(--color-text-tertiary, #8a8a8a); }

.ann-actions {
  margin-left: auto;
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.ann-act {
  border: none;
  background: none;
  padding: 0;
  font-size: 12px;
  color: var(--paper-accent, #b08d57);
  cursor: pointer;
}
.ann-act.is-primary { font-weight: 600; }
.ann.flag .ann-act.is-primary { color: var(--paper-danger, #a3271f); }
.ann.fixed .ann-act.is-primary { color: var(--color-warning, #b26a00); }
.ann-act:disabled { color: var(--color-text-tertiary, #aaa); cursor: not-allowed; }
.ann-act:hover:not(:disabled) { text-decoration: underline; }

.ann-shimmer { animation: shimmer 1.4s ease-in-out infinite; }
@keyframes shimmer {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
</style>
