<script setup>
/**
 * 逐段 diff 弹窗：只读展示「上一版 → 当前版」的字符级差异。
 *
 * 为什么不引 diff 库：只需要一种最长公共子序列，自己写三十行就够，
 * 而且中文按字切、英文数字按词切的规则跟公众号正文的阅读习惯对得上。
 *
 * 为什么有降级分支：LCS 的表格是 O(n*m)，两段都很长时建表会让弹窗卡住，
 * 超过阈值就退化成左右两栏原文对照 —— 看得到内容，只是不再染色。
 */
import { computed } from 'vue'
import { NModal } from 'naive-ui'

const props = defineProps({
  show: { type: Boolean, default: false },
  before: { type: String, default: '' },
  after: { type: String, default: '' },
  number: { type: Number, default: 0 },
})
const emit = defineEmits(['update:show'])

// 单侧 token 超过这个数就不再建表；4000 时表格约 32MB（Uint16），再大就不划算了
const LCS_LIMIT = 4000

function tokenize(text) {
  // 中文没有空格，必须按单字切；英文与数字连成一个词，空白单独成 token 以便保留换行位置
  return String(text ?? '').match(/[A-Za-z0-9]+|\s+|[\s\S]/g) || []
}

function diffTokens(a, b) {
  // 先剥掉公共前后缀：正常修改往往只动一小段，剩下的中间部分才是 LCS 的规模
  let start = 0
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1
  let endA = a.length
  let endB = b.length
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1
    endB -= 1
  }
  const midA = a.slice(start, endA)
  const midB = b.slice(start, endB)
  const cols = midB.length + 1
  // table[i][j] = midA[i:] 与 midB[j:] 的 LCS 长度，回溯时从左上往右下走
  const table = new Uint16Array((midA.length + 1) * cols)
  for (let i = midA.length - 1; i >= 0; i -= 1) {
    for (let j = midB.length - 1; j >= 0; j -= 1) {
      table[i * cols + j] =
        midA[i] === midB[j]
          ? table[(i + 1) * cols + j + 1] + 1
          : Math.max(table[(i + 1) * cols + j], table[i * cols + j + 1])
    }
  }

  const ops = []
  // 同类型的相邻 token 合并，渲染时少几千个节点
  const push = (type, text) => {
    const last = ops[ops.length - 1]
    if (last && last.type === type) last.text += text
    else ops.push({ type, text })
  }
  for (let k = 0; k < start; k += 1) push('same', a[k])
  let i = 0
  let j = 0
  while (i < midA.length && j < midB.length) {
    if (midA[i] === midB[j]) {
      push('same', midA[i])
      i += 1
      j += 1
    } else if (table[(i + 1) * cols + j] >= table[i * cols + j + 1]) {
      push('del', midA[i])
      i += 1
    } else {
      push('ins', midB[j])
      j += 1
    }
  }
  while (i < midA.length) push('del', midA[i++])
  while (j < midB.length) push('ins', midB[j++])
  for (let k = endA; k < a.length; k += 1) push('same', a[k])
  return ops
}

const diff = computed(() => {
  const a = tokenize(props.before)
  const b = tokenize(props.after)
  if (a.length > LCS_LIMIT || b.length > LCS_LIMIT) return { ops: [], tooLarge: true }
  return { ops: diffTokens(a, b), tooLarge: false }
})

const changed = computed(() => {
  if (diff.value.tooLarge) return (props.before || '') !== (props.after || '')
  return diff.value.ops.some((op) => op.type !== 'same')
})

const stats = computed(() => {
  if (diff.value.tooLarge) return null
  let removed = 0
  let added = 0
  for (const op of diff.value.ops) {
    if (op.type === 'same') continue
    // 只数非空白字符：单纯换行/缩进的出入不算「字」，否则数字会虚高
    const count = (op.text.match(/\S/g) || []).length
    if (op.type === 'del') removed += count
    else added += count
  }
  return { removed, added }
})
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="`第 ${number} 段的改动`"
    style="width: 720px; max-width: 94vw"
    @update:show="emit('update:show', $event)"
  >
    <div class="diff">
      <div class="diff-stats">
        <template v-if="changed && stats">
          <span class="stat del">-{{ stats.removed }}</span>
          <span class="stat ins">+{{ stats.added }}</span>
        </template>
        <span v-else-if="!changed" class="stat quiet">这一段正文没有变化</span>
      </div>

      <div v-if="changed && diff.tooLarge" class="diff-columns">
        <section>
          <h4>改动前</h4>
          <pre>{{ before }}</pre>
        </section>
        <section>
          <h4>改动后</h4>
          <pre>{{ after }}</pre>
        </section>
        <p class="hint">改动太大，已切换成原文对照，不再逐字标色。</p>
      </div>

      <p v-else-if="changed" class="diff-text">
        <template v-for="(op, index) in diff.ops" :key="index">
          <del v-if="op.type === 'del'">{{ op.text }}</del>
          <ins v-else-if="op.type === 'ins'">{{ op.text }}</ins>
          <span v-else>{{ op.text }}</span>
        </template>
      </p>
    </div>
  </NModal>
</template>

<style scoped>
.diff {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.diff-stats {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.stat {
  padding: 1px 8px;
  border-radius: 999px;
}
.stat.del {
  color: var(--paper-danger, #a3271f);
  background: var(--paper-danger-soft, #fdeceb);
}
.stat.ins {
  color: var(--paper-success, #3f7a4f);
  background: var(--paper-success-soft, rgba(57, 113, 94, 0.14));
}
.stat.quiet {
  padding: 0;
  color: var(--color-text-tertiary, #8a8a8a);
}

.diff-text {
  margin: 0;
  max-height: 56vh;
  overflow: auto;
  padding: 12px 14px;
  border: 1px solid var(--paper-line, #ece3d3);
  border-radius: 10px;
  background: var(--paper-bg, #fffdf8);
  font-family: var(--font-body, inherit);
  font-size: 15px;
  line-height: 1.85;
  /* 保留正文里的换行与缩进，否则 diff 出来是一整团 */
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--paper-ink, #2b2b2b);
}
.diff-text del {
  background: var(--paper-danger-soft, #fdeceb);
  color: var(--paper-danger, #a3271f);
  text-decoration: line-through;
}
.diff-text ins {
  background: rgba(57, 113, 94, 0.18);
  color: var(--paper-success, #3f7a4f);
  text-decoration: none;
}

.diff-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.diff-columns h4 {
  margin: 0 0 4px;
  font-size: 11px;
  font-weight: 500;
  color: var(--color-text-tertiary, #8a8a8a);
}
.diff-columns pre {
  margin: 0;
  max-height: 52vh;
  overflow: auto;
  padding: 10px 12px;
  border: 1px solid var(--paper-line, #ece3d3);
  border-radius: 8px;
  background: var(--paper-bg, #fffdf8);
  font-family: var(--font-body, inherit);
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--paper-ink, #2b2b2b);
}
.diff-columns .hint {
  grid-column: 1 / -1;
  margin: 0;
  font-size: 11px;
  color: var(--color-text-tertiary, #8a8a8a);
}

@media (max-width: 640px) {
  .diff-columns { grid-template-columns: 1fr; }
}
</style>
