<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NButton, NModal } from 'naive-ui'
import { copyText, downloadImage, shotFramePrompt, shotPromptFor, shotStagePrompt } from './shotImages'

const props = defineProps({
  shot: { type: Object, default: null },
  images: { type: Array, default: () => [] },
  index: { type: Number, default: 0 },
})
const emit = defineEmits(['close', 'update:index'])

const open = computed(() => Boolean(props.shot && props.images.length))
const currentIndex = computed(() => Math.min(Math.max(props.index, 0), props.images.length - 1))
const current = computed(() => props.images[currentIndex.value] || null)
const generatedPrompt = computed(() => shotStagePrompt(props.shot, current.value?.stage))
const fullPrompt = computed(() => shotPromptFor(props.shot, current.value?.stage))
const framePrompt = computed(() => shotFramePrompt(props.shot))
const shotNumber = computed(() => String((props.shot?.sortOrder ?? 0) + 1).padStart(2, '0'))
// 后端原图响应默认带 Content-Disposition: attachment，直接 target=_blank 会变成下载；
// 带 inline=1 时才在新标签里正常显示图片。
const inlineUrl = computed(() => {
  const url = current.value?.url
  if (!url) return ''
  return `${url}${url.includes('?') ? '&' : '?'}inline=1`
})
// 后端 raw 链不带扩展名，统一存成 .png（浏览器对 blob 下载的文件名以 download 为准）
const downloadName = computed(() => `镜头-${shotNumber.value}-${(current.value?.label || '画面').replace(/\s+/g, '')}.png`)
const downloading = ref(false)
const downloadError = ref('')
let downloadTimer

async function download() {
  if (!current.value || downloading.value) return
  downloading.value = true
  downloadError.value = ''
  try {
    await downloadImage(current.value.url, downloadName.value)
  } catch (err) {
    downloadError.value = err?.message || '下载失败，请改用「打开原图」后手动保存'
    clearTimeout(downloadTimer)
    downloadTimer = setTimeout(() => { downloadError.value = '' }, 4000)
  } finally {
    downloading.value = false
  }
}
const copyState = ref('')
let copyTimer

function select(index) {
  const size = props.images.length
  if (!size) return
  emit('update:index', (index + size) % size)
}

function close() {
  emit('close')
}

async function copy(kind) {
  const text = kind === 'full' ? fullPrompt.value : framePrompt.value
  const ok = await copyText(text)
  copyState.value = ok ? kind : 'failed'
  clearTimeout(copyTimer)
  copyTimer = setTimeout(() => { copyState.value = '' }, 1800)
}

function onKeydown(event) {
  if (!open.value) return
  if (event.key === 'Escape') close()
  else if (event.key === 'ArrowLeft') { event.preventDefault(); select(currentIndex.value - 1) }
  else if (event.key === 'ArrowRight') { event.preventDefault(); select(currentIndex.value + 1) }
}

watch([() => props.shot?.id, () => props.index], () => { copyState.value = '' })
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  clearTimeout(copyTimer)
  clearTimeout(downloadTimer)
})
</script>

<template>
  <n-modal
    :show="open"
    preset="card"
    :title="`镜头 ${shotNumber} · ${current?.label || '画面预览'}`"
    style="width: min(1120px, calc(100vw - 32px))"
    @update:show="(value) => { if (!value) close() }"
  >
    <div class="viewer">
      <div class="viewer-stage">
        <div class="viewer-figure">
          <img v-if="current" :src="current.url" :alt="`镜头 ${shotNumber} ${current.label}`" />
          <template v-if="images.length > 1">
            <button class="viewer-nav prev" type="button" title="上一张（←）" @click="select(currentIndex - 1)">‹</button>
            <button class="viewer-nav next" type="button" title="下一张（→）" @click="select(currentIndex + 1)">›</button>
          </template>
        </div>
        <div v-if="images.length > 1" class="viewer-thumbs">
          <button
            v-for="(item, itemIndex) in images"
            :key="`${item.url}-${itemIndex}`"
            type="button"
            :class="{ active: itemIndex === currentIndex }"
            :title="item.label"
            @click="select(itemIndex)"
          ><img :src="item.url" :alt="item.label" /></button>
        </div>
        <div class="viewer-actions">
          <span class="viewer-counter">{{ currentIndex + 1 }} / {{ images.length }} · {{ current?.label }}</span>
          <span v-if="images.length > 1" class="viewer-tip">← → 切换图片</span>
          <div v-if="current" class="viewer-links">
            <a class="viewer-link" :href="inlineUrl" target="_blank" rel="noopener" title="在新标签显示原图（不会下载）">打开原图 ↗</a>
            <button class="viewer-link" type="button" :disabled="downloading" :title="`保存为 ${downloadName}`" @click="download">{{ downloading ? '下载中…' : '下载图片 ↓' }}</button>
          </div>
        </div>
        <p v-if="downloadError" class="copy-error">{{ downloadError }}</p>
      </div>

      <aside class="viewer-side">
        <div class="prompt-block">
          <div class="prompt-head">
            <strong>实际出图提示词</strong>
            <n-button size="tiny" secondary :disabled="!fullPrompt" @click="copy('full')">
              {{ copyState === 'full' ? '已复制 ✓' : '复制完整提示词' }}
            </n-button>
          </div>
          <pre class="prompt-text">{{ fullPrompt || '这一镜还没有提示词' }}</pre>
          <p v-if="generatedPrompt" class="prompt-hint">当前为{{ current?.stage === 'final' ? '正式图' : '草图' }}阶段的入队原文，含参考图标注与角色一致性文案；重新生成后会同步更新。</p>
          <p v-else class="prompt-hint">这一镜还没有生成记录，暂时显示画面描述。生成图片后这里会变成实际出图的完整提示词。</p>
        </div>
        <div class="prompt-block">
          <div class="prompt-head">
            <strong>镜头画面提示词</strong>
            <n-button size="tiny" quaternary :disabled="!framePrompt" @click="copy('frame')">
              {{ copyState === 'frame' ? '已复制 ✓' : '复制画面提示词' }}
            </n-button>
          </div>
          <pre class="prompt-text short">{{ framePrompt || '未填写' }}</pre>
          <p class="prompt-hint">在右侧「镜头设定」里可以直接编辑这段文本。</p>
        </div>
        <p v-if="copyState === 'failed'" class="copy-error">复制失败，请选中上方文本手动复制</p>
      </aside>
    </div>
  </n-modal>
</template>

<style scoped>
.viewer { display:grid; grid-template-columns:minmax(0,1fr) 330px; gap:18px; }
.viewer-stage { min-width:0; display:flex; flex-direction:column; gap:10px; }
.viewer-figure { position:relative; display:grid; place-items:center; height:min(66vh,620px); min-height:280px; background:#eae6de; border:1px solid #d5cfc5; overflow:hidden; }
.viewer-figure img { max-width:100%; max-height:100%; width:auto; height:auto; object-fit:contain; display:block; }
.viewer-nav { position:absolute; top:50%; transform:translateY(-50%); width:36px; height:52px; border:1px solid #d5cfc5; background:rgba(255,254,250,.9); color:#3a3731; font-size:30px; line-height:1; cursor:pointer; }
.viewer-nav:hover { background:#fff; border-color:#315e68; color:#315e68; }
.viewer-nav.prev { left:10px; }.viewer-nav.next { right:10px; }
.viewer-thumbs { display:flex; gap:6px; overflow-x:auto; padding-bottom:2px; }
.viewer-thumbs button { flex:0 0 auto; width:62px; height:48px; padding:0; border:1px solid #d5cfc5; background:#f4f1ea; cursor:pointer; }
.viewer-thumbs button.active { border:2px solid #315e68; }
.viewer-thumbs img { width:100%; height:100%; object-fit:cover; display:block; }
.viewer-actions { display:flex; align-items:center; flex-wrap:wrap; gap:10px; font-size:12px; color:#7b7268; }
.viewer-counter { color:#302d29; font-weight:650; margin-right:auto; }.viewer-tip { color:#948b81; }
.viewer-links { display:flex; gap:8px; }
.viewer-link { display:inline-flex; align-items:center; padding:5px 10px; border:1px solid #d5cfc5; background:#fffefa; color:#315e68; font-size:12px; white-space:nowrap; text-decoration:none; }
.viewer-link:hover { border-color:#315e68; background:#f1f6f5; }
button.viewer-link { font-family:inherit; cursor:pointer; }
button.viewer-link:disabled { opacity:.6; cursor:default; }
.viewer-side { min-width:0; border-left:1px solid #e1ddd5; padding-left:16px; display:flex; flex-direction:column; gap:16px; max-height:min(72vh,680px); overflow:auto; }
.prompt-block { min-width:0; }
.prompt-head { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:8px; }
.prompt-head strong { color:#2f2b27; font-size:12px; }
.prompt-text { margin:0; padding:10px; max-height:230px; overflow:auto; white-space:pre-wrap; word-break:break-word; background:#f6f3ed; border:1px solid #e1ddd5; color:#3a352f; font:12px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace; user-select:text; }
.prompt-text.short { max-height:130px; }
.prompt-hint { margin:6px 0 0; color:#948b81; font-size:11px; line-height:1.55; }
.copy-error { margin:0; color:#a7463d; font-size:12px; }
@media(max-width:860px) {
  .viewer { grid-template-columns:1fr; }
  .viewer-side { border-left:0; border-top:1px solid #e1ddd5; padding:14px 0 0; max-height:none; }
  .viewer-figure { height:min(48vh,420px); }
}
</style>
