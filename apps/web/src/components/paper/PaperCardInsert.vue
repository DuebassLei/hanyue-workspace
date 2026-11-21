<script setup>
/**
 * 成稿台 · 插入卡片弹窗。
 *
 * 图片 / 长图 / 长提示词这三类不能像引用卡那样点一下出占位：
 * 图片要先拿到地址，提示词要先写好正文，所以单独用一个弹窗收内容。
 *
 * 与参考实现（React 原生 `<dialog>`）对齐的点：字段顺序、校验顺序、文案。
 * 错误只落在表单底部一行红字里，不弹 toast —— 用户还停在弹窗里改，不需要额外的浮层。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NInput, NModal } from 'naive-ui'
import { api, apiUrl } from '../../api/client'
import { cardMarkdown, formatLabel } from '../../utils/editorFormat'

const props = defineProps({
  show: { type: Boolean, default: false },
  /** { id, selection, file? }：id 只会是 image / long / prompt / promptbox */
  card: { type: Object, default: null },
})
const emit = defineEmits(['update:show', 'insert', 'closed'])

// 后端按 settings.max_upload_bytes 拦，这里先用 10 MB 挡一次，省掉一次注定失败的请求
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024
const ACCEPT = 'image/png,image/jpeg,image/gif,image/webp,image/bmp,.png,.jpg,.jpeg,.gif,.webp,.bmp'

const title = ref('')
const value = ref('')
const file = ref(null)
const preview = ref('')
const uploading = ref(false)
const error = ref('')
const fileInput = ref(null)

const isImage = computed(() => props.card?.id === 'image' || props.card?.id === 'long')
const heading = computed(() => (props.card?.id ? `插入${formatLabel(props.card.id)}` : '插入卡片'))
const titleLabel = computed(() =>
  isImage.value ? '图注' : props.card?.id === 'prompt' ? '文件名' : '标题',
)
const titlePlaceholder = computed(() =>
  isImage.value ? '描述这张图' : props.card?.id === 'prompt' ? '提示词.txt' : '提示词',
)

function revokePreview() {
  if (!preview.value) return
  URL.revokeObjectURL(preview.value)
  preview.value = ''
}

/** 按打开时的选区推导初值，规则与参考实现一致 */
function reset() {
  const card = props.card
  if (!card) {
    title.value = ''
    value.value = ''
    file.value = null
    error.value = ''
    return
  }
  const image = card.id === 'image' || card.id === 'long'
  const selection = String(card.selection || '')
  const selectedUrl = image && /^https?:\/\/\S+$/i.test(selection)
  // 选区本身就是地址：地址进地址框、图注留空；
  // 否则整段选区当图注用（压成一行，图注不能带换行）
  title.value = image && !selectedUrl ? selection.replace(/\s+/g, ' ').trim() : ''
  // 提示词类的选区就是正文；图片类只有选区是地址时才预填，免得把一段普通文字当地址
  value.value = !image || selectedUrl ? selection : ''
  file.value = card.file || null
  error.value = ''
}

// 选完图立刻出预览，不必等上传；objectURL 不会自动回收，换图 / 卸载时要手动 revoke
watch(file, (next) => {
  revokePreview()
  if (next) preview.value = URL.createObjectURL(next)
})

watch(
  () => props.show,
  (visible) => {
    if (visible) {
      reset()
    } else {
      // 关闭时清空，避免下次打开闪出上次的内容；
      // 先清 file 才能让上面的 watch 收回 objectURL
      file.value = null
      title.value = ''
      value.value = ''
      error.value = ''
    }
  },
  { immediate: true },
)

function chooseFile(next) {
  if (!next) return
  if (next.size > MAX_UPLOAD_BYTES) {
    error.value = '本地图片不能超过 10 MB。'
    return
  }
  file.value = next
  error.value = ''
}

function onFileChange(event) {
  chooseFile(event.target.files?.[0])
  // 清空 input：不清的话再选同一个文件不会触发 change
  event.target.value = ''
}

function removeFile() {
  file.value = null
  error.value = ''
}

async function submit() {
  if (uploading.value || !props.card) return
  error.value = ''
  // 图片既没地址也没文件：给比「请输入有效的图片地址」更具体的提示
  if (isImage.value && !file.value && !value.value.trim()) {
    error.value = '请填写图片地址，或选择一张本地图片。'
    return
  }
  let next = value.value
  try {
    if (isImage.value && file.value) {
      if (file.value.size > MAX_UPLOAD_BYTES) throw new Error('本地图片不能超过 10 MB。')
      uploading.value = true
      const form = new FormData()
      form.append('file', file.value)
      form.append('source_type', 'upload')
      const asset = await api.upload('/assets', form)
      // asset.url 是带签名的相对路径，原样写进正文后复制到别处（或发布端）就取不到图，
      // 必须转成绝对地址；顺手回填到输入框，让用户看到最终用的是哪个链接
      next = apiUrl(asset.url)
      value.value = next
      file.value = null
    }
    emit('insert', cardMarkdown(props.card.id, title.value, next))
    emit('update:show', false)
  } catch (err) {
    // cardMarkdown 抛的是给用户看的中文提示，直接展示
    error.value = err?.message || '插入失败，请稍后重试。'
  } finally {
    uploading.value = false
  }
}

/** 上传中不允许关闭：请求还在飞，关了之后结果无处安放 */
function requestClose() {
  if (uploading.value) return
  emit('update:show', false)
}

function onShowChange(visible) {
  if (!visible && uploading.value) return
  emit('update:show', visible)
}

onBeforeUnmount(revokePreview)
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="heading"
    style="width: 520px; max-width: 94vw"
    :closable="!uploading"
    :mask-closable="!uploading"
    :close-on-esc="!uploading"
    @update:show="onShowChange"
    @after-leave="emit('closed')"
  >
    <div v-if="card" class="card-insert">
      <template v-if="isImage">
        <div class="local-image">
          <div v-if="preview" class="thumb">
            <img :src="preview" alt="待上传图片预览" />
          </div>
          <div class="local-image-body">
            <div class="local-image-actions">
              <NButton size="small" :disabled="uploading" @click="fileInput?.click()">
                {{ file ? '更换本地图片' : '选择本地图片' }}
              </NButton>
              <button v-if="file" type="button" class="link" :disabled="uploading" @click="removeFile">
                移除
              </button>
            </div>
            <p class="hint">
              {{ file ? file.name : 'PNG、JPEG、GIF、WebP 或 BMP，不超过 10 MB；也可在正文里直接粘贴。' }}
            </p>
            <!-- 素材链接带签名，7 天后失效；公众号草稿发布后会被抓取，过期就裂图，必须说清楚 -->
            <p class="hint">上传的是素材库临时链接（7 天有效），正式发布前请换成公众号图床地址。</p>
          </div>
          <input ref="fileInput" type="file" :accept="ACCEPT" hidden @change="onFileChange" />
        </div>

        <label class="field">
          <span class="label">图片地址<span class="req">必填</span></span>
          <NInput
            v-model:value="value"
            :placeholder="file ? '上传后自动填入图床地址' : 'https://…'"
            :readonly="Boolean(file) || uploading"
          />
        </label>
      </template>

      <label class="field">
        <span class="label">{{ titleLabel }}<span class="req">选填</span></span>
        <NInput
          v-model:value="title"
          maxlength="120"
          :placeholder="titlePlaceholder"
          @keyup.enter="submit"
        />
      </label>

      <label v-if="!isImage" class="field">
        <span class="label">提示词内容<span class="req">必填</span></span>
        <NInput v-model:value="value" type="textarea" :rows="6" placeholder="粘贴或输入提示词正文" />
      </label>

      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </div>

    <template #footer>
      <div class="foot">
        <NButton :disabled="uploading" @click="requestClose">取消</NButton>
        <NButton type="primary" :disabled="uploading || !card" @click="submit">
          {{ uploading ? '正在上传…' : '插入卡片' }}
        </NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.card-insert {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.local-image {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 10px;
  border: 1px dashed var(--paper-line-strong, #d9d6cf);
  border-radius: 10px;
  background: var(--paper-bg, #fbfaf6);
}
.thumb {
  flex: 0 0 auto;
}
.thumb img {
  display: block;
  width: 88px;
  height: 88px;
  object-fit: cover;
  border: 1px solid var(--paper-line, #e1ddd6);
  border-radius: 8px;
  background: var(--color-surface, #fff);
}
.local-image-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.local-image-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.hint {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--paper-ink-muted, #827a71);
  /* 文件名可能很长，必须能断行，否则会把弹窗撑宽 */
  word-break: break-all;
}
.link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--paper-accent, #315e68);
  font-size: 12px;
  cursor: pointer;
}
.link:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.label {
  font-size: 12.5px;
  color: var(--paper-ink-soft, #5c564e);
}
.req {
  margin-left: 6px;
  font-size: 11px;
  color: var(--paper-ink-muted, #827a71);
}

.error {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--paper-danger, #9b3e37);
}

.foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
