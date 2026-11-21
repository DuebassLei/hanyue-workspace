<script setup>
/**
 * 成稿台的单段编辑器。
 *
 * 几个看起来矛盾、其实是有意的设计：
 * 1. 输入即写 localStorage（草稿），只有保存才落库——切稿、刷新都不丢半成品，
 *    但半成品也不会被后台质检 / 改写当成正文。
 * 2. `original` 取打开编辑器时的快照正文，而不是草稿里存的旧版本：
 *    expectedText 必须对齐后端才会生效，草稿过期时改用 conflict 拦住保存，
 *    而不是让后端把别人的改动无声覆盖掉。
 * 3. 快捷键挂在容器上而不是 textarea 上：预览态没有 textarea，但保存要照样能按。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { studioApi } from '../../api/studio'
import { useParagraphDrafts } from '../../composables/useParagraphDrafts'
import { toast } from '../../ui/feedback'
import { markdownToHtml } from '../../utils/markdownSerializer'
import { CARD_IDS, emphasisFormats, formatSelection, insertFormats } from '../../utils/editorFormat'
import PaperCardInsert from './PaperCardInsert.vue'

const MAX_CHARS = 20000
// 与后端「一段最多 20000 字」对齐；textarea 的 maxlength 只挡输入，格式化 / 插入还要自己判

const props = defineProps({
  articleId: { type: String, required: true },
  block: { type: Object, required: true },
  number: { type: Number, required: true },
  nextBlock: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['saved', 'done', 'error'])

const { draftsFor, setDraft, storageFailed } = useParagraphDrafts()

// 初值只在 setup 里读一次：之后再输入都由本地 draft 驱动，
// 父级快照刷新不能把用户正在敲的字冲掉。
const stored = draftsFor(props.articleId).get(props.block.id)
// 草稿内容与当前正文完全一致时没有暂存的必要，顺手清掉，免得角标挂着
const resume = stored && stored.text.trim() !== props.block.text ? stored : null
if (stored && !resume) setDraft(props.articleId, props.block.id, null)

const draft = ref({
  text: resume ? resume.text : props.block.text,
  original: props.block.text,
})
// 草稿是基于哪一版正文写的。它和当前快照不一致，就说明别处已经改过这一段。
// 单独存一个 ref 是为了让「changed」永远拿最新正文做基准，冲突判断另算。
const draftBase = ref(resume ? resume.original : props.block.text)

const preview = ref(false)
const saving = ref(false)
// 保存被后端 409 拒绝时置真：本地快照没变，但服务端已经有新版本了
const serverConflict = ref(false)
const textarea = ref(null)
const menu = ref(null)
// 非 null 时表示「插入卡片」弹窗开着：{ id, start, end, selection, file? }
// start/end 要在开弹窗时记下来，弹窗一开 textarea 就失焦，之后读不到选区了
const card = ref(null)

const changed = computed(() => draft.value.text.trim() !== draft.value.original)
const conflict = computed(() => serverConflict.value || props.block.text !== draftBase.value)
const empty = computed(() => !draft.value.text.trim())
const locked = computed(() => saving.value || props.busy)
const canSave = computed(() => !locked.value && !conflict.value && changed.value)
const canRemove = computed(() => !locked.value && !conflict.value)

const CALLOUT_LABELS = { TIP: '小技巧', NOTE: '说明', WARN: '注意', TLDR: '省流版', QUOTE: '金句' }

/**
 * 预览前的轻量改写：只为了让 markdown-it 显示得像最终效果。
 * - `> [!TIP]` → `> **小技巧**｜`，别让用户看到英文标记；
 * - `++下划线++` → `<u>`，markdown-it 不认这个记号；
 * 围栏里的内容一律不动，否则会把代码改坏。
 */
function previewText(markdown) {
  let inFence = false
  return String(markdown || '')
    .split('\n')
    .map((line) => {
      if (/^\s*(```|~~~)/.test(line)) {
        inFence = !inFence
        return line
      }
      if (inFence) return line
      return line
        .replace(/^(\s*)>\s*\[!([A-Z]+)\]\s?/, (_, pad, kind) => `${pad}> **${CALLOUT_LABELS[kind] || kind}**｜`)
        .replace(/\+\+(?=\S)(.+?)(?<=\S)\+\+/g, '<u>$1</u>')
    })
    .join('\n')
}

// 预览只在切到预览态时才算；编辑态每敲一个字重算一遍 markdown-it 是白烧 CPU
const previewHtml = computed(() => (preview.value ? markdownToHtml(previewText(draft.value.text)) : ''))
const showCounter = computed(() => draft.value.text.length > MAX_CHARS - 200)
// 弹窗开关跟 card 一起管：关弹窗就丢掉待插入项，避免下次开别的卡片带出旧选区
const cardShow = computed({
  get: () => Boolean(card.value),
  set: (value) => {
    if (!value) card.value = null
  },
})

// 文案与顺序严格对齐参考实现：越靠前的状态越需要用户立刻知道
const statusText = computed(() => {
  if (saving.value) return '正在保存…'
  if (storageFailed.value) return '本地暂存失败，退出前请保存正文'
  if (conflict.value) return '正文已有新版本，当前修改仍保留。'
  if (empty.value) return '清空后可手动删除这段'
  if (changed.value) return '已在本机暂存，尚未写入正文'
  return '⌘ / Ctrl + Enter 保存'
})

function edit(text) {
  draft.value = { ...draft.value, text }
  // 改回原样就不留草稿；但冲突态下即使内容等于基线也要留着，否则用户唯一的副本没了
  const unchanged = text.trim() === draftBase.value
  setDraft(
    props.articleId,
    props.block.id,
    unchanged && !conflict.value ? null : { text, original: draftBase.value },
  )
}

function onInput(event) {
  edit(event.target.value)
}

// ---- 自动高度 ----
// textarea 不出现内部滚动条，整段多长就多高；宽度变化会重排换行，也要重算。
let observer = null
let lastWidth = 0

function fit() {
  const el = textarea.value
  if (!el) return
  el.style.height = '0px'
  el.style.height = `${el.scrollHeight}px`
}

watch(
  () => draft.value.text,
  async () => {
    if (preview.value) return
    await nextTick()
    fit()
  },
)

watch(preview, async (open) => {
  if (open) return
  await nextTick()
  fit()
  // 从预览切回来时焦点要回到正文，否则接着敲字会落到别处
  textarea.value?.focus({ preventScroll: true })
})

function closeMenu() {
  if (menu.value?.open) menu.value.open = false
}

function togglePreview() {
  if (saving.value) return
  closeMenu()
  preview.value = !preview.value
}

// ---- 保存 / 删除 / 取消 ----
async function save({ remove = false, advance = false } = {}) {
  // busy 时直接发请求只会拿到 409，白白让用户以为撞了冲突
  if (saving.value || props.busy) return
  if (conflict.value) return
  // 清空后按快捷键不删除：删除必须是「点删除按钮」这个明确动作，
  // 否则一个 Ctrl+Enter 就把整段删了，用户只会以为是保存失败
  if (empty.value && !remove) return
  if (!remove && !changed.value) return
  if (advance && (remove || empty.value || !props.nextBlock)) return
  saving.value = true
  try {
    const snapshot = await studioApi.updateBlock(props.articleId, props.block.id, {
      text: remove ? '' : draft.value.text,
      // 用打开编辑器时看到的正文做乐观锁：别处改过就让后端拒掉
      expectedText: draft.value.original,
    })
    setDraft(props.articleId, props.block.id, null)
    emit('saved', snapshot)
    emit('done', { advance: Boolean(advance) })
  } catch (err) {
    const message = err?.message || '保存失败'
    if (err?.status === 409 && message.includes('任务在跑')) {
      // 不是内容冲突，任务结束后还能存，所以只提示、不解锁编辑
      toast(message, 'error')
    } else if (err?.status === 409) {
      // 服务端已有新版本：锁住保存，把未保存的稿子留给用户复制
      serverConflict.value = true
      toast('这一段已有新版本，未保存的编辑已保留。', 'error')
    } else {
      toast(message, 'error')
    }
    emit('error', message)
  } finally {
    saving.value = false
  }
}

function cancel() {
  if (saving.value) return
  setDraft(props.articleId, props.block.id, null)
  emit('done', { advance: false })
}

async function copyModification() {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(draft.value.text)
    toast('已复制未保存的修改', 'success')
  } catch {
    // 非 https 或权限被拒时只能让用户自己框选，比静默失败好
    toast('复制失败，请手动选中文本复制', 'error')
  }
}

// ---- 格式化与插入：只改文本，不引富文本依赖 ----
function applyFormat(next) {
  if (next.text.length > MAX_CHARS) {
    toast(`这一段最多 ${MAX_CHARS} 个字符。`, 'error')
    return
  }
  closeMenu()
  edit(next.text)
  // DOM 里的 value 要等下一次渲染才更新，立刻 setSelectionRange 会落在旧文本上
  nextTick(() => {
    const el = textarea.value
    if (!el) return
    el.focus({ preventScroll: true })
    el.setSelectionRange(next.start, next.end)
  })
}

/**
 * 插入项统一入口。
 * 图片 / 长图 / 两种长提示词要先填内容，开弹窗；
 * 其余（强调、callout、终端、分隔线）直接按占位生成文本，插完就能改。
 */
function runFormat(id) {
  const el = textarea.value
  if (!el || saving.value || props.busy) return
  const start = el.selectionStart
  const end = el.selectionEnd
  if (CARD_IDS.includes(id)) {
    closeMenu()
    card.value = { id, start, end, selection: draft.value.text.slice(start, end) }
    return
  }
  applyFormat(formatSelection(draft.value.text, start, end, id))
}

// 弹窗提交：markdown 已成形，交给 formatSelection 的 card 分支落位
function onCardInsert(markdown) {
  const current = card.value
  card.value = null
  if (!current) return
  applyFormat(formatSelection(draft.value.text, current.start, current.end, current.id, markdown))
  // 素材直链带 7 天签名，插入时说一次，免得发布当天才发现裂图
  if (markdown.includes('/assets/')) {
    toast('已插入素材库图片，链接 7 天内有效，正式发布前请换成公众号图床地址。')
  }
}

/**
 * 弹窗关闭动画结束后把焦点还给正文。
 * naive-ui 关闭时会把焦点还原到打开前那个按钮（插入），
 * 时机还晚于 applyFormat 的 nextTick，所以只能等它的 after-leave。
 */
function focusEditor() {
  textarea.value?.focus({ preventScroll: true })
}

// 截屏 / 复制图片直接进正文：比「先存文件再选路径」少两步
function onPaste(event) {
  if (saving.value || props.busy) return
  const data = event.clipboardData
  const fromItems = [...(data?.items ?? [])].find(
    (item) => item.kind === 'file' && item.type.startsWith('image/'),
  )
  const file = fromItems?.getAsFile() ?? [...(data?.files ?? [])].find((item) => item.type.startsWith('image/'))
  if (!file) return
  // 拦掉默认粘贴，否则浏览器会把图片再插一次（变成重复的手填占位）
  event.preventDefault()
  closeMenu()
  const el = textarea.value
  card.value = { id: 'image', start: el?.selectionStart ?? 0, end: el?.selectionEnd ?? 0, selection: '', file }
}

// ---- 键盘 ----
function onKeydown(event) {
  // 输入法合成期（拼音还没上屏）不能抢 Enter / Esc
  if (event.isComposing || event.keyCode === 229) return
  // 卡片弹窗开着时键盘归弹窗管：这里再抢 Esc 会把「关弹窗」变成「退出编辑」
  if (card.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    // 顺序：预览 → 插入菜单 → 取消编辑，一层一层退
    if (preview.value) {
      preview.value = false
      return
    }
    if (menu.value?.open) {
      closeMenu()
      textarea.value?.focus({ preventScroll: true })
      return
    }
    cancel()
    return
  }
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    event.preventDefault()
    // 先判 shift：Ctrl+Shift+Enter 是「保存并改下一段」，不能退化成普通保存
    if (event.shiftKey) {
      if (props.nextBlock && !empty.value) save({ advance: true })
      return
    }
    save()
  }
}

// 点编辑器外面收起插入菜单（原生 details 不会自己关）
function onDocumentPointerDown(event) {
  if (!menu.value?.open) return
  if (!menu.value.contains(event.target)) closeMenu()
}

/**
 * 菜单默认向下展开。段落越靠文末，按钮越贴视口底，向下展开会被切掉半截——
 * 这时候翻到按钮上方展开。只在打开时量一次，之后滚动页面也不重算，
 * 避免面板跟着视口抖动。
 */
function onMenuToggle(event) {
  const details = event.target
  const panel = details.querySelector?.('.insert-panel')
  if (!panel) return
  panel.classList.remove('up')
  if (!details.open) return
  nextTick(() => {
    const box = panel.getBoundingClientRect()
    if (box.bottom > window.innerHeight && box.height < details.getBoundingClientRect().top) {
      panel.classList.add('up')
    }
  })
}

onMounted(() => {
  fit()
  // 点开就想改：光标直接落进正文，不然用户还得再点一次输入框
  textarea.value?.focus({ preventScroll: true })
  document.addEventListener('pointerdown', onDocumentPointerDown)
  if (typeof ResizeObserver !== 'undefined' && textarea.value) {
    observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect?.width || 0
      // 只对宽度变化重算：高度是我们自己写的，跟着它重算会绕成死循环
      if (width === lastWidth) return
      lastWidth = width
      fit()
    })
    observer.observe(textarea.value)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  observer?.disconnect()
  observer = null
})
</script>

<template>
  <div class="paper-block-editor" @keydown="onKeydown">
    <div class="editor-tools">
      <span class="editor-title">编辑第 {{ number }} 段</span>
      <div class="editor-tools-right">
        <template v-if="!preview && !saving">
          <div class="format-group" role="group" aria-label="文字强调">
            <button
              v-for="item in emphasisFormats"
              :key="item.id"
              type="button"
              class="tool"
              :class="`format-${item.id}`"
              :title="item.label"
              :aria-label="item.label"
              @mousedown.prevent
              @click="runFormat(item.id)"
            >{{ item.symbol }}</button>
          </div>
          <details ref="menu" class="insert-menu" @toggle="onMenuToggle">
            <summary class="tool">插入</summary>
            <div class="insert-panel" role="group" aria-label="插入 Markdown 片段">
              <div v-for="group in insertFormats" :key="group.label" class="insert-group" role="group" :aria-label="group.label">
                <span class="insert-group-label">{{ group.label }}</span>
                <div class="insert-grid">
                  <button
                    v-for="item in group.items"
                    :key="item[0]"
                    type="button"
                    @mousedown.prevent
                    @click="runFormat(item[0])"
                  >{{ item[1] }}</button>
                </div>
              </div>
            </div>
          </details>
        </template>
        <span v-if="showCounter" class="counter">{{ draft.text.length }} / {{ MAX_CHARS }}</span>
        <button type="button" class="tool" :aria-pressed="preview" :disabled="saving" @click="togglePreview">
          {{ preview ? '继续编辑' : '预览' }}
        </button>
      </div>
    </div>

    <textarea
      v-show="!preview"
      ref="textarea"
      class="editor-input"
      :value="draft.text"
      :maxlength="MAX_CHARS"
      :readonly="saving"
      spellcheck="false"
      :aria-label="`编辑第 ${number} 段`"
      @input="onInput"
      @paste="onPaste"
    />
    <div v-show="preview" class="editor-preview" v-html="previewHtml" />

    <div class="editor-footer">
      <span class="editor-status" :class="{ danger: conflict, warn: storageFailed }" role="status">
        {{ statusText }}
      </span>
      <div class="editor-actions">
        <button v-if="conflict" type="button" class="tool" @click="copyModification">复制修改</button>
        <button
          v-if="!empty"
          type="button"
          class="tool danger-text"
          title="删除当前段落，可通过顶部「撤销一步」恢复"
          :disabled="!canRemove"
          @click="save({ remove: true })"
        >删除</button>
        <button type="button" class="tool" :disabled="saving" @click="cancel">取消</button>
        <button
          v-if="!preview && nextBlock && !empty"
          type="button"
          class="tool"
          title="⌘ / Ctrl + Shift + Enter"
          :disabled="!canSave"
          @click="save({ advance: true })"
        >保存并改下一段</button>
        <button type="button" class="tool primary" :disabled="!canSave" @click="save({ remove: empty })">
          {{ empty ? '删除段落' : '保存' }}
        </button>
      </div>
    </div>

    <PaperCardInsert v-model:show="cardShow" :card="card" @insert="onCardInsert" @closed="focusEditor" />
  </div>
</template>

<style scoped>
.paper-block-editor {
  position: relative;
  border: 1px solid var(--paper-line-strong, #d9d6cf);
  border-radius: 10px;
  background: var(--color-surface, #fff);
}
/* textarea 自己的 outline 被关掉了（容器已经有一圈边框），焦点提示改由容器承担 */
.paper-block-editor:focus-within {
  border-color: var(--paper-accent, #315e68);
  box-shadow: 0 0 0 2px var(--paper-accent-soft, #e1ebea);
}

.editor-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 10px;
  border-bottom: 1px dashed var(--paper-line, #e1ddd6);
  font-size: 11.5px;
  color: var(--paper-ink-soft, #5c564e);
  flex-wrap: wrap;
}
.editor-tools-right { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }

.tool {
  padding: 2px 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: none;
  color: var(--paper-ink-soft, #5c564e);
  font: inherit;
  line-height: 1.6;
  cursor: pointer;
}
.tool:hover:not(:disabled) {
  background: var(--paper-hover, #f6f3ed);
  border-color: var(--paper-line, #e1ddd6);
}
.tool:focus-visible { outline: 2px solid var(--paper-accent, #315e68); outline-offset: 1px; }
.tool:disabled { color: var(--paper-ink-muted, #827a71); opacity: 0.55; cursor: not-allowed; }
.tool.primary {
  background: var(--paper-accent, #315e68);
  border-color: var(--paper-accent, #315e68);
  color: var(--paper-on-accent, #fff);
}
.tool.primary:hover:not(:disabled) { background: var(--paper-accent, #315e68); opacity: 0.88; }
.tool.primary:disabled {
  background: var(--paper-line, #e1ddd6);
  border-color: var(--paper-line, #e1ddd6);
  color: var(--paper-ink-muted, #827a71);
}
.danger-text { color: var(--paper-danger, #9b3e37); }

.format-group {
  display: flex;
  gap: 2px;
  margin-right: 2px;
  padding-right: 6px;
  border-right: 1px solid var(--paper-line, #e1ddd6);
}
.format-group .tool { width: 26px; padding: 2px 0; text-align: center; }
/* 按钮符号本身就是记号：B 加粗、色 带高亮底、U 带下划线，点之前就能看出效果 */
.format-bold { font-weight: 700; }
.format-highlight {
  background: var(--paper-warning-soft, #fff8e8);
  color: var(--paper-ink, #302d29);
  font-size: 11px;
}
.format-highlight:hover:not(:disabled) { background: var(--paper-warning-soft, #ffefc9); }
.format-underline { text-decoration: underline; text-underline-offset: 2px; }

.insert-menu { position: relative; }
/* 原生 details 的三角标在 Chrome 里是 ::marker，列表样式和 webkit 伪元素都要关 */
.insert-menu > summary { display: block; list-style: none; user-select: none; }
.insert-menu > summary::-webkit-details-marker { display: none; }
.insert-panel {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 12;
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 304px;
  max-height: min(70vh, 420px);
  overflow-y: auto;
  padding: 8px;
  border: 1px solid var(--paper-line-strong, #d9d6cf);
  border-radius: 10px;
  background: var(--color-surface, #fff);
  box-shadow: var(--shadow-md, 0 8px 24px rgba(0, 0, 0, 0.08));
}
.insert-group { display: flex; flex-direction: column; gap: 2px; }
/* 文末段落下方放不下时由脚本翻到按钮上方（见 onMenuToggle） */
.insert-panel.up { top: auto; bottom: calc(100% + 4px); }
.insert-group-label {
  padding: 2px 6px 4px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--paper-ink-soft, #5c564e);
}
/* 两列：12 个项目竖排会顶出屏幕，横排又要滚动找 */
.insert-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; }
.insert-grid button {
  padding: 6px 8px;
  border: none;
  border-radius: 5px;
  background: none;
  color: var(--paper-ink, #302d29);
  font: inherit;
  font-size: 12px;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
.insert-grid button:hover { background: var(--paper-hover, #f6f3ed); }
.insert-grid button:focus-visible { outline: 2px solid var(--paper-accent, #315e68); outline-offset: -2px; }

.counter {
  font-variant-numeric: tabular-nums;
  color: var(--paper-warning, #9a6230);
}

.editor-input {
  display: block;
  width: 100%;
  box-sizing: border-box;
  min-height: 64px;
  padding: 10px 12px;
  border: none;
  outline: none;
  /* 高度由脚本按 scrollHeight 算，用户可以拖拽的话两者会打架 */
  resize: none;
  overflow: hidden;
  background: transparent;
  color: var(--paper-ink, #302d29);
  font-family: var(--font-body, inherit);
  font-size: 15px;
  line-height: 1.85;
  white-space: pre-wrap;
}

.editor-preview {
  padding: 10px 12px;
  color: var(--paper-ink, #302d29);
  font-size: 15px;
  line-height: 1.85;
  word-break: break-word;
}
.editor-preview :deep(p:first-child) { margin-top: 0; }
.editor-preview :deep(p:last-child) { margin-bottom: 0; }
.editor-preview :deep(p) { margin: 0 0 0.6em; }
.editor-preview :deep(h1),
.editor-preview :deep(h2),
.editor-preview :deep(h3) {
  margin: 0.8em 0 0.4em;
  font-size: 1.05em;
  line-height: 1.5;
}
.editor-preview :deep(blockquote) {
  margin: 0.6em 0;
  padding: 2px 0 2px 12px;
  border-left: 3px solid var(--paper-accent, #315e68);
  color: var(--paper-ink-soft, #5c564e);
}
.editor-preview :deep(hr) {
  margin: 1em 0;
  border: none;
  border-top: 1px solid var(--paper-line, #e1ddd6);
}
.editor-preview :deep(code) {
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--paper-sunken, #f7f4ee);
  font-size: 0.92em;
}
.editor-preview :deep(mark) { background: var(--paper-accent-soft, #e1ebea); }

.editor-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 10px;
  border-top: 1px dashed var(--paper-line, #e1ddd6);
  flex-wrap: wrap;
}
.editor-status { font-size: 11.5px; color: var(--paper-ink-muted, #827a71); }
.editor-status.danger { color: var(--paper-danger, #9b3e37); }
.editor-status.warn { color: var(--paper-warning, #9a6230); }
.editor-actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
</style>
