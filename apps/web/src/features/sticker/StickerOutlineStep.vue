<script setup>
import { computed, ref } from 'vue'
import { NAlert, NButton, NInput, NModal, NSelect } from 'naive-ui'

const props = defineProps({
  // StickerImageOut[]（编辑已有集合）或 AI 构思结果 [{caption,framePrompt,slotType}]
  images: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['confirm'])

const SLOT_OPTIONS = [
  { label: '信息位', value: 'info' },
  { label: '节奏位', value: 'pace' },
]

// 行模型：id 只做本地 key；后端按数组顺序重排 sort_order
let rowSeq = 0
const rows = ref(
  (props.images.length ? props.images : []).map((item) => ({
    _key: `r${rowSeq++}`,
    caption: item.caption || '',
    framePrompt: item.framePrompt || '',
    slotType: item.slotType === 'pace' ? 'pace' : 'info',
  })),
)

function addRow() {
  rows.value.push({ _key: `r${rowSeq++}`, caption: '', framePrompt: '', slotType: 'info' })
}

function removeRow(index) {
  rows.value.splice(index, 1)
}

function moveRow(index, delta) {
  const target = index + delta
  if (target < 0 || target >= rows.value.length) return
  const [row] = rows.value.splice(index, 1)
  rows.value.splice(target, 0, row)
}

const countValid = computed(() => rows.value.length >= 2 && rows.value.length <= 9)
const promptsReady = computed(() => rows.value.every((row) => row.framePrompt.trim()))
const canConfirm = computed(() => countValid.value && promptsReady.value && !props.busy)
const confirmText = computed(() => `确认方案，开始生成（将消耗 ${rows.value.length} 次生图）`)

const overLimitOpen = ref(false)

function confirm() {
  if (!canConfirm.value) return
  // 一次 7 张以上就是七次生图花费，先让人数一遍再点
  if (rows.value.length > 6) {
    overLimitOpen.value = true
    return
  }
  submit()
}

function submit() {
  overLimitOpen.value = false
  emit('confirm', rows.value.map(({ caption, framePrompt, slotType }) => ({
    caption: caption.trim(),
    framePrompt: framePrompt.trim(),
    slotType,
  })))
}
</script>

<template>
  <div class="outline-step stack">
    <p class="field-hint">
      每行一张图：画面描述会拼进生图提示词，配文用于发布时对照。顺序就是推送顺序。
    </p>

    <n-alert v-if="!countValid" type="warning" :bordered="false">
      一组贴图 2~9 张，当前 {{ rows.length }} 行。
    </n-alert>
    <n-alert v-else-if="!promptsReady" type="info" :bordered="false">
      还有行的「画面描述」是空的，填完才能开始生成。
    </n-alert>

    <div class="outline-table" role="table" aria-label="分镜表">
      <div class="outline-row outline-head" role="row">
        <span>#</span>
        <span>画面描述</span>
        <span>配文</span>
        <span>类型</span>
        <span aria-hidden="true" />
      </div>
      <div v-for="(row, index) in rows" :key="row._key" class="outline-row" role="row">
        <span class="outline-index">{{ index + 1 }}</span>
        <n-input
          v-model:value="row.framePrompt"
          type="textarea"
          size="small"
          placeholder="具体的画面：场景、主体、构图、色调"
          :autosize="{ minRows: 2, maxRows: 5 }"
        />
        <n-input v-model:value="row.caption" size="small" placeholder="一句话配文" />
        <n-select v-model:value="row.slotType" size="small" :options="SLOT_OPTIONS" />
        <span class="outline-ops">
          <n-button quaternary size="tiny" :disabled="index === 0" title="上移" @click="moveRow(index, -1)">↑</n-button>
          <n-button
            quaternary
            size="tiny"
            :disabled="index === rows.length - 1"
            title="下移"
            @click="moveRow(index, 1)"
          >
            ↓
          </n-button>
          <n-button quaternary size="tiny" title="删除这行" @click="removeRow(index)">✕</n-button>
        </span>
      </div>
    </div>

    <div class="row">
      <n-button secondary size="small" :disabled="rows.length >= 9" @click="addRow">+ 加一行</n-button>
      <span class="field-hint">{{ rows.length }} 张 · 建议 6~9 张凑满九宫格</span>
      <div class="spacer" />
      <n-button type="primary" :loading="busy" :disabled="!canConfirm" @click="confirm">{{ confirmText }}</n-button>
    </div>

    <n-modal
      v-model:show="overLimitOpen"
      preset="card"
      title="确认开始生成？"
      style="width: min(420px, calc(100vw - 32px))"
    >
      <p class="over-limit-text">
        这次将发起 <b>{{ rows.length }}</b> 次生图，会明显消耗生图额度。锚点图（第 1 张）满意后才会生成其余
        {{ rows.length - 1 }} 张。
      </p>
      <div class="row">
        <div class="spacer" />
        <n-button secondary @click="overLimitOpen = false">再想想</n-button>
        <n-button type="primary" :disabled="busy" @click="submit">确认，开始生成</n-button>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.outline-table {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.outline-row {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) minmax(0, 220px) 96px 72px;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2);
  border-bottom: 1px solid var(--color-border);
}
.outline-row:last-child {
  border-bottom: 0;
}
.outline-head {
  background: var(--color-surface-raised);
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
  font-weight: 600;
}
.outline-index {
  font-size: var(--font-sm);
  font-variant-numeric: tabular-nums;
  color: var(--color-text-muted);
  text-align: center;
}
.outline-ops {
  display: flex;
  justify-content: flex-end;
  gap: 2px;
}
.over-limit-text {
  margin: 0 0 var(--space-3);
  font-size: var(--font-body);
  line-height: 1.7;
}
@media (max-width: 767px) {
  .outline-row {
    grid-template-columns: 24px minmax(0, 1fr) 84px 64px;
  }
  .outline-row > :nth-child(3) {
    grid-column: 2 / span 2;
  }
}
</style>
