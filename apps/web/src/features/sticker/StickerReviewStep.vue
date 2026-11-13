<script setup>
import { computed, ref } from 'vue'
import { NAlert, NButton, NInput, NModal } from 'naive-ui'
import { apiUrl } from '../../api/client'

const props = defineProps({
  detail: { type: Object, required: true }, // StickerSetOut（含 images，容器负责 2s 轮询刷新）
  busy: { type: Boolean, default: false },
})

const emit = defineEmits([
  'retry-image',
  'generate-anchor',
  'generate-rest',
  'finalize',
  'append',
  'update-caption',
])

// 生图错误码 → 人话。映射不到的原样显示，方便按 errorCode 去查供应商日志。
const ERROR_TEXT = {
  provider_unavailable: '生图供应商未配置或已失效，请到「设置」检查模型连接',
  provider_timeout: '生图超时，上游太忙，稍后重试这张',
  provider_error: '生图服务返回错误，可先重试这张',
  content_policy_violation: '画面描述被安全策略拒绝，改描得更中性些再重试',
  safety_refusal: '画面描述触发了内容审核，换个说法再重试',
  invalid_prompt: '画面描述无法用于生图，检查这一行的描述',
  rate_limited: '生图请求过于频繁，等一会儿再重试',
  quota_exceeded: '生图额度用完，联系管理员或明天再来',
  asset_missing: '生成结果丢失，重试这张即可',
  job_failed: '生成任务失败，重试这张',
}

function errorText(code) {
  if (!code) return '生成失败'
  return ERROR_TEXT[code] || `生成失败（${code}），可重试这张`
}

const images = computed(() =>
  [...(props.detail.images || [])].sort((a, b) => a.sortOrder - b.sortOrder),
)
const anchor = computed(() => images.value[0] || null)
const anchorReady = computed(() => anchor.value?.status === 'succeeded')
const allDone = computed(
  () => images.value.length > 0 && images.value.every((item) => item.status === 'succeeded'),
)
const restCount = computed(() => Math.max(0, images.value.length - 1))
const generating = computed(() =>
  images.value.some((item) => ['queued', 'generating'].includes(item.status)),
)

/** 锚点之外是否仍在跑：决定按钮区显示什么 */
const restPending = computed(
  () => anchorReady.value && images.value.slice(1).some((item) => item.status !== 'succeeded'),
)

function imageSrc(image) {
  return image.url ? apiUrl(image.url) : ''
}

// —— hover 编辑配文 ——
const editingId = ref('')
const editCaption = ref('')

function startEdit(image) {
  editingId.value = image.id
  editCaption.value = image.caption || ''
}

function saveCaption(image) {
  emit('update-caption', { id: image.id, caption: editCaption.value.trim() })
  editingId.value = ''
}

// —— 再补一张 ——
const appendOpen = ref(false)
const appendRow = ref({ caption: '', framePrompt: '', slotType: 'info' })

function openAppend() {
  appendRow.value = { caption: '', framePrompt: '', slotType: 'info' }
  appendOpen.value = true
}

function submitAppend() {
  if (!appendRow.value.framePrompt.trim()) return
  emit('append', {
    caption: appendRow.value.caption.trim(),
    framePrompt: appendRow.value.framePrompt.trim(),
    slotType: appendRow.value.slotType,
  })
  appendOpen.value = false
}

function archiveUrl() {
  return apiUrl(`/stickers/${props.detail.id}/archive.zip`)
}
</script>

<template>
  <div class="review-step stack">
    <n-alert v-if="detail.failed" type="warning" :bordered="false">
      {{ detail.failed }} 张生成失败，逐张看下方提示，可单独重试。
    </n-alert>

    <div class="review-grid">
      <figure v-for="(image, index) in images" :key="image.id" class="review-cell">
        <div class="review-media">
          <!-- 成功：图 + hover 配文编辑 -->
          <template v-if="image.status === 'succeeded'">
            <img :src="imageSrc(image)" :alt="image.caption || `第 ${index + 1} 张贴图`" loading="lazy" />
            <div class="review-hover">
              <n-input
                v-if="editingId === image.id"
                v-model:value="editCaption"
                size="small"
                placeholder="配文"
                @keyup.enter="saveCaption(image)"
              />
              <n-button v-else size="tiny" secondary @click.stop="startEdit(image)">
                {{ image.caption || '加配文' }}
              </n-button>
              <n-button v-if="editingId === image.id" size="tiny" type="primary" @click.stop="saveCaption(image)">
                存
              </n-button>
            </div>
          </template>
          <!-- 排队/生成中：骨架屏呼吸 -->
          <div v-else-if="image.status === 'queued' || image.status === 'generating'" class="review-skeleton">
            <span class="review-breathe" aria-hidden="true" />
            <span>{{ image.status === 'queued' ? '排队中' : '生成中' }}</span>
          </div>
          <!-- 失败：中文错误说明 + 单张重试 -->
          <div v-else-if="image.status === 'failed'" class="review-failed">
            <span class="review-failed-text">{{ errorText(image.errorCode) }}</span>
            <n-button size="tiny" secondary :disabled="busy" @click="emit('retry-image', image)">重试这张</n-button>
          </div>
          <!-- pending：锚点没出图时其余格子是锁着的 -->
          <div v-else class="review-pending">
            <span v-if="index > 0 && !anchorReady">等锚点图确认</span>
            <span v-else>待生成</span>
          </div>
        </div>
        <figcaption class="review-caption">
          <b>{{ index + 1 }}</b>
          <span :title="image.framePrompt">{{ image.caption || image.framePrompt }}</span>
          <span class="tag" :class="image.slotType === 'pace' ? 'tag-warning' : 'tag-info'">
            {{ image.slotType === 'pace' ? '节奏' : '信息' }}
          </span>
        </figcaption>
      </figure>
    </div>

    <div class="row review-foot">
      <span class="field-hint">进度 {{ detail.succeeded }}/{{ detail.total }}{{ generating ? ' · 正在生成…' : '' }}</span>
      <div class="spacer" />

      <!-- 锚点关卡：第 1 张没成功前只做一件事——把锚点抽出来 -->
      <template v-if="!anchorReady">
        <n-button
          type="primary"
          :loading="anchor && ['queued', 'generating'].includes(anchor.status)"
          :disabled="busy || (anchor && ['queued', 'generating'].includes(anchor.status))"
          @click="emit('generate-anchor')"
        >
          {{ anchor?.status === 'failed' ? '重抽锚点' : '先生成锚点图' }}
        </n-button>
      </template>
      <template v-else-if="restPending">
        <n-button quaternary :disabled="busy" @click="emit('generate-anchor')">重抽锚点</n-button>
        <n-button
          type="primary"
          :disabled="busy || images.slice(1).some((item) => ['queued', 'generating'].includes(item.status))"
          @click="emit('generate-rest')"
        >
          满意，生成其余 {{ restCount }} 张
        </n-button>
      </template>
      <template v-else-if="allDone">
        <n-button secondary tag="a" :href="archiveUrl()">下载全部</n-button>
        <n-button secondary :disabled="busy" @click="openAppend">再补一张</n-button>
        <n-button type="primary" :disabled="busy" @click="emit('finalize')">完成归档</n-button>
      </template>
      <template v-else>
        <n-button secondary :disabled="busy" @click="openAppend">再补一张</n-button>
        <n-button type="primary" :disabled="busy" @click="emit('finalize')">完成归档</n-button>
      </template>
    </div>

    <n-modal
      v-model:show="appendOpen"
      preset="card"
      title="再补一张"
      style="width: min(480px, calc(100vw - 32px))"
    >
      <div class="stack-2">
        <label class="field">
          <span class="field-label">画面描述</span>
          <n-input
            v-model:value="appendRow.framePrompt"
            type="textarea"
            placeholder="这多出来的一张画什么"
            :autosize="{ minRows: 2, maxRows: 5 }"
          />
        </label>
        <label class="field">
          <span class="field-label">配文</span>
          <n-input v-model:value="appendRow.caption" placeholder="一句话配文（可选）" />
        </label>
      </div>
      <div class="row" style="margin-top: var(--space-3)">
        <span class="field-hint">追加后回到分镜表确认，再生成这一张。</span>
        <div class="spacer" />
        <n-button secondary @click="appendOpen = false">取消</n-button>
        <n-button type="primary" :disabled="!appendRow.framePrompt.trim() || busy" @click="submitAppend">
          追加
        </n-button>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.review-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--space-3);
}
.review-cell {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.review-media {
  position: relative;
  aspect-ratio: 1;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg);
}
.review-media > img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.review-hover {
  position: absolute;
  left: var(--space-1);
  right: var(--space-1);
  bottom: var(--space-1);
  display: flex;
  gap: var(--space-1);
  opacity: 0;
  transition: opacity 0.15s ease;
}
.review-media:hover .review-hover,
.review-hover:focus-within {
  opacity: 1;
}
.review-skeleton {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  background: linear-gradient(
    90deg,
    var(--color-surface-raised) 25%,
    var(--color-border) 37%,
    var(--color-surface-raised) 63%
  );
  background-size: 400% 100%;
  animation: review-shimmer 1.4s ease infinite;
}
.review-breathe {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--color-accent) 22%, transparent);
  animation: review-breathe 1.8s ease-in-out infinite;
}
@keyframes review-shimmer {
  0% { background-position: 100% 50%; }
  100% { background-position: 0 50%; }
}
@keyframes review-breathe {
  0%, 100% { transform: scale(0.8); opacity: 0.5; }
  50% { transform: scale(1.1); opacity: 1; }
}
.review-failed {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-3);
  text-align: center;
  background: color-mix(in srgb, var(--color-danger) 6%, var(--color-bg));
}
.review-failed-text {
  font-size: var(--font-xs);
  line-height: 1.6;
  color: var(--color-danger);
}
.review-pending {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  font-size: var(--font-xs);
  color: var(--color-text-muted);
  background: var(--color-surface-raised);
}
.review-caption {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
  min-width: 0;
}
.review-caption > span:nth-child(2) {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.review-caption b {
  font-variant-numeric: tabular-nums;
  color: var(--color-text-muted);
}
.review-foot {
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
}
</style>
