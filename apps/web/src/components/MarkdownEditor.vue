<script setup>
/**
 * MarkdownEditor — 共用富文本编辑器
 *
 * Props:
 *   modelValue        String  — Markdown 字符串（v-model）
 *   extensions        Array   — 额外 tiptap 扩展（如卡片台专属 CardPageBreak）
 *   toolbar           Object  — 控制工具栏显示项（见 DEFAULT_TOOLBAR）
 *   enableAiBubble    Boolean — 是否启用选中文字 AI 浮动菜单
 *   placeholder       String  — 空内容占位文本
 *   readonly          Boolean — 只读模式（发布预览用）
 *
 * Emits:
 *   update:modelValue — Markdown 字符串变化时
 *   editor-ready      — editor 实例就绪时，传出 editor 对象
 */
import { ref, watch, onBeforeUnmount, computed, nextTick } from 'vue'
import { useEditor, EditorContent, BubbleMenu } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import Highlight from '@tiptap/extension-highlight'
import Image from '@tiptap/extension-image'
import Link from '@tiptap/extension-link'
import { Table } from '@tiptap/extension-table'
import { TableRow } from '@tiptap/extension-table-row'
import { TableCell } from '@tiptap/extension-table-cell'
import { TableHeader } from '@tiptap/extension-table-header'
import Placeholder from '@tiptap/extension-placeholder'
import { editorToMarkdown, setEditorFromMarkdown } from '../utils/markdownSerializer'
import { articleBodyFromOutput } from '../utils/aiArticle'
import { api, apiUrl, streamGeneration } from '../api/client'

// ---------- props / emits ----------
const props = defineProps({
  modelValue: { type: String, default: '' },
  extensions: { type: Array, default: () => [] },
  toolbar: { type: Object, default: () => ({}) },
  enableAiBubble: { type: Boolean, default: false },
  placeholder: { type: String, default: '在这里写正文...' },
  readonly: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'editor-ready'])

// ---------- 模式切换 ----------
// 'wysiwyg' 用 tiptap 渲染，'source' 用纯文本 textarea 编辑
const mode = ref('wysiwyg')
// source 模式下的工作副本：切到 source 时从编辑器序列化而来，
// 切回 wysiwyg 时再解析回去。两边以这份字符串为唯一真相。
const sourceValue = ref(props.modelValue || '')

// ---------- AI bubble 状态 ----------
const aiBubbleTask = ref(null)  // null = 未激活
const aiRunning = ref(false)
const aiError = ref('')
let abortAiStream = null

const AI_INLINE_TASKS = [
  { value: 'polish',   label: '润色' },
  { value: 'rewrite',  label: '改写' },
  { value: 'summarize',label: '摘要' },
  { value: 'shorten',  label: '缩短' },
  { value: 'expand',   label: '扩写' },
]

// ---------- 本地图片上传 ----------
const localImageInput = ref(null)
const imageUploading = ref(false)
const imageError = ref('')
// 限 15MB：与素材页一致，后端还会再校验一次
const IMAGE_MAX_BYTES = 15 * 1024 * 1024

// ---------- 工具栏合并配置 ----------
const DEFAULT_TOOLBAR = {
  heading: true, bold: true, italic: true, strike: true,
  bulletList: true, orderedList: true, blockquote: true,
  highlight: true, image: true, table: true, horizontalRule: true,
  undo: true, redo: true,
}
const tb = computed(() => ({ ...DEFAULT_TOOLBAR, ...props.toolbar }))

// ---------- tiptap 扩展列表 ----------
const builtinExtensions = [
  StarterKit.configure({
    heading: { levels: [1, 2, 3] },
    codeBlock: false,
    horizontalRule: true,
  }),
  Highlight.configure({ multicolor: false }),
  Image.configure({ inline: false, allowBase64: true }),
  // 文章里的链接要能存住，否则序列化时整段 <a> 会退化成纯文字
  Link.configure({ openOnClick: false, autolink: false, HTMLAttributes: { rel: 'noopener noreferrer' } }),
  Table.configure({ resizable: false }),
  TableRow,
  TableCell,
  TableHeader,
  Placeholder.configure({ placeholder: props.placeholder }),
]

// ---------- 编辑器实例 ----------
// 最近一次与 prop 对齐过的 Markdown：由编辑器自身产生，或已确认一致。
// 与 prop 相同就说明只是回环，不必重建文档；不同才是真正的外部修改。
let lastEmitted = props.modelValue || ''

const editor = useEditor({
  // 关键：onUpdate 里算出来的 Markdown 要按「自己产生的值」记账（见下方 lastEmitted），
  // 否则一次格式往返（序列化 → 解析）就会吃掉表格/高亮。
  extensions: [...builtinExtensions, ...props.extensions],
  editable: !props.readonly,
  content: '',
  onUpdate: ({ editor: ed }) => {
    // source 模式下编辑器已被 v-if 卸下，不该再往外抛
    if (mode.value !== 'wysiwyg') return
    const md = editorToMarkdown(ed)
    lastEmitted = md
    emit('update:modelValue', md)
  },
  onCreate: ({ editor: ed }) => {
    if (props.modelValue) setEditorFromMarkdown(ed, props.modelValue)
    sourceValue.value = props.modelValue || ''
    emit('editor-ready', ed)
  },
})

// 父组件从外部改值（切换稿件 / 载入版本 / AI 插入）时才重建文档
watch(() => props.modelValue, (val) => {
  const next = val ?? ''
  if (mode.value === 'source') {
    // source 模式下编辑器可能已销毁，仅同步工作副本
    if (next !== sourceValue.value) {
      sourceValue.value = next
      // 显示值必须一起同步：textarea 绑的是 sourceView，
      // 落后于实际内容时任何一次重渲染都会把 DOM 回滚、光标甩到末尾
      if (sourceExpanded.value) sourceView.value = next
      else refreshSourceView()
    }
    lastEmitted = next
    return
  }
  if (!editor.value) return
  if (next === lastEmitted) return
  lastEmitted = next
  sourceValue.value = next
  setEditorFromMarkdown(editor.value, next)
})

watch(() => props.readonly, (val) => {
  editor.value?.setEditable(!val)
})

// ---------- 模式切换逻辑 ----------
function switchToSource() {
  if (!editor.value) return
  // 以编辑器当前内容为准刷新工作副本，保证用户刚敲的字不丢
  const md = editorToMarkdown(editor.value)
  sourceValue.value = md
  lastEmitted = md
  // 进入源码模式默认展示原文：折叠文本只能看，直接编辑会把省略号写进正文
  sourceExpanded.value = true
  refreshSourceView()
  mode.value = 'source'
  // 光标直接落进 textarea，否则回车/输入会落到隐藏的富文本编辑器里
  nextTick(() => sourceEl.value?.focus())
}

function switchToWysiwyg() {
  mode.value = 'wysiwyg'
  nextTick(() => {
    if (!editor.value) return
    setEditorFromMarkdown(editor.value, sourceValue.value)
    lastEmitted = sourceValue.value
    emit('update:modelValue', sourceValue.value)
    // 解析可能规整了 Markdown（例如列表符号、表格对齐），
    // 把规整后的结果回写到 textarea 数据，下次切换才一致
    sourceValue.value = editorToMarkdown(editor.value)
    lastEmitted = sourceValue.value
  })
}

function onSourceInput(e) {
  const value = e.target.value
  sourceValue.value = value
  // 同步显示值：textarea 是受控的，sourceView 落后时父组件的重渲染
  // （字数、自动保存状态、折叠提示条）会覆盖 DOM 内容并把光标顶到末尾
  sourceView.value = value
  lastEmitted = value
  emit('update:modelValue', value)
}

// ---------- 源码模式：超长图片地址折叠 ----------
// 图片一旦是 base64 或带签名的长直链，源码里就是一屏乱码。
// 这里只折叠**显示**，sourceValue 永远是完整原文；
// 用户点「展开」才会把真实内容放回 textarea 供编辑。
const SOURCE_URL_LIMIT = 72
const sourceView = ref('')
const sourceExpanded = ref(false)

function condenseUrl(url) {
  if (url.length <= SOURCE_URL_LIMIT) return url
  const name = url.startsWith('data:')
    ? `${(url.match(/^data:([^;,]+)/)?.[1] || 'image')} 内嵌图`
    : url.slice(-10)
  return `⟪${name} · ${url.length} 字符⟫`
}

function shortenUrls(text) {
  if (!text) return ''
  return text
    .replace(/\]\(([^()\s]{80,})\)/g, (_m, url) => `](${condenseUrl(url)})`)
    .replace(/\b(src|href)=("|')([^"'\s]{80,})\2/g, (m, attr, quote, url) => `${attr}=${quote}${condenseUrl(url)}${quote}`)
}

function refreshSourceView() {
  sourceView.value = sourceExpanded.value ? sourceValue.value : shortenUrls(sourceValue.value)
}

function expandSource() {
  sourceExpanded.value = true
  sourceView.value = sourceValue.value
}

function collapseSource() {
  sourceExpanded.value = false
  refreshSourceView()
}

const sourceEl = ref(null)

/** 源码模式下把 Markdown 片段插到光标处（图片等），不经过隐藏的富文本编辑器 */
function insertSourceMarkdown(markdown) {
  const el = sourceEl.value
  const current = sourceValue.value
  const start = el?.selectionStart ?? current.length
  const end = el?.selectionEnd ?? start
  const next = `${current.slice(0, start)}${markdown}${current.slice(end)}`
  sourceExpanded.value = true
  sourceValue.value = next
  sourceView.value = next
  lastEmitted = next
  emit('update:modelValue', next)
  nextTick(() => {
    if (!el) return
    el.focus()
    const caret = start + markdown.length
    el.setSelectionRange(caret, caret)
  })
}

/** 源码模式下统计被折叠的长地址数量，用于决定是否显示「展开」按钮 */
const foldedCount = computed(() => {
  const all = sourceValue.value || ''
  if (!all) return 0
  const urls = [
    ...all.matchAll(/\]\(([^()\s]{80,})\)/g),
    ...all.matchAll(/\b(?:src|href)=("|')([^"'\s]{80,})\1/g).map((m) => [m[0], m[2]]),
  ]
  return urls.length
})

/** 正文里还残留多少张 base64 内嵌图（只有历史稿件才会出现） */
const base64Count = computed(() => {
  const all = String(props.modelValue || '')
  return (all.match(/data:image\/[a-z+.-]+;base64,/g) || []).length
})
const convertingBase64 = ref(false)

/**
 * 把正文里历史遗留的 base64 内嵌图逐张上传成素材，替换成素材直链。
 * 不自动做：一次可能几十张、每张几百 KB，静默上传会让用户莫名其妙地等。
 */
async function convertBase64Images() {
  if (convertingBase64.value || !editor.value) return
  const md = editorToMarkdown(editor.value)
  const pattern = /!\[([^\]]*)\]\((data:image\/[^;]+;base64,[^)]+)\)/g
  const matches = [...md.matchAll(pattern)]
  if (!matches.length) return

  convertingBase64.value = true
  imageError.value = ''
  let replaced = 0
  let next = md
  try {
    for (const [whole, alt, dataUrl] of matches) {
      const blob = await (await fetch(dataUrl)).blob()
      const ext = (blob.type.split('/')[1] || 'png').replace('jpeg', 'jpg')
      const session = await api.post('/assets/upload-sessions?maxBytes=15728640')
      const form = new FormData()
      form.append('file', blob, `内嵌图-${replaced + 1}.${ext}`)
      const asset = await api.upload(`/assets?sessionId=${encodeURIComponent(session.sessionId)}`, form)
      const url = apiUrl(`/assets/${asset.id}/raw?${asset.url.split('?')[1] || ''}`)
      next = next.replace(whole, `![${alt || '配图'}](${url})`)
      replaced += 1
    }
    setEditorFromMarkdown(editor.value, next)
    sourceValue.value = next
    lastEmitted = next
    emit('update:modelValue', next)
    imageError.value = `已转换 ${replaced} 张内嵌图`
  } catch (err) {
    imageError.value = `转换中断（已完成 ${replaced} 张）：${err.message || '上传失败'}`
  } finally {
    convertingBase64.value = false
  }
}

// ---------- 工具栏操作 ----------
function insertTable() {
  editor.value?.chain().focus()
    .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
    .run()
}

/**
 * 插入本地图片：先上传成素材，正文里只写素材直链。
 * 以前用 FileReader 读成 data URL 直接塞进 <img>，那条 base64 会原样进 Markdown，
 * 源码模式下一整屏都是乱码，文档体积也会爆掉。
 */
async function onLocalImagePicked(e) {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file) return
  if (!file.type.startsWith('image/')) {
    imageError.value = '只能插入图片文件'
    return
  }
  if (file.size > IMAGE_MAX_BYTES) {
    imageError.value = `图片超过 ${IMAGE_MAX_BYTES / (1024 * 1024)} MB，请先压缩`
    return
  }
  imageError.value = ''
  imageUploading.value = true
  try {
    // 一次性上传会话把限额绑到当前用户，与素材页同一套流程
    const session = await api.post('/assets/upload-sessions?maxBytes=15728640')
    const form = new FormData()
    form.append('file', file)
    const asset = await api.upload(`/assets?sessionId=${encodeURIComponent(session.sessionId)}`, form)
    // alt 里的方括号会截断 ![]() 语法，先剔掉
    const alt = file.name.replace(/\.[^.]+$/, '').replace(/[[\]]/g, '')
    // /assets/{id}/raw 需要签名才能读（<img> 带不了 Authorization 头），
    // 漏掉 asset.url 里的 exp/sig 会直接 401，正文里只剩一张裂图
    const query = String(asset.url || '').split('?')[1] || ''
    const src = apiUrl(`/assets/${asset.id}/raw${query ? `?${query}` : ''}`)
    if (mode.value === 'source') {
      insertSourceMarkdown(`![${alt}](${src})`)
    } else {
      editor.value?.chain().focus().setImage({ src, alt }).run()
    }
  } catch (err) {
    imageError.value = err.message || '图片上传失败'
  } finally {
    imageUploading.value = false
  }
}

// ---------- AI Bubble Menu ----------
function runAiBubble(taskValue) {
  if (!editor.value || aiRunning.value) return
  const { from, to } = editor.value.state.selection
  if (from === to) return
  const selectedText = editor.value.state.doc.textBetween(from, to, '\n')
  if (!selectedText.trim()) return

  aiBubbleTask.value = taskValue
  aiRunning.value = true
  aiError.value = ''
  let result = ''

  abortAiStream = streamGeneration(
    {
      taskType: taskValue,
      prompt: selectedText,
      constraints: { platform: 'wechat_official' },
    },
    {
      onDelta: (data) => { result += data.text || '' },
      onError: (data) => {
        aiError.value = data.message || '生成失败'
        aiRunning.value = false
        aiBubbleTask.value = null
      },
      onEnd: () => {
        // 润色/改写同样走文章 JSON 契约，选区里只能落 markdown 正文
        const insertText = result ? articleBodyFromOutput(result) : ''
        if (insertText && editor.value) {
          const { from: f, to: t } = editor.value.state.selection
          editor.value.chain().focus()
            .deleteRange({ from: f, to: t })
            .insertContentAt(f, insertText)
            .run()
        }
        aiRunning.value = false
        aiBubbleTask.value = null
      },
    },
  )
}

function stopAiBubble() {
  abortAiStream?.()
  aiRunning.value = false
  aiBubbleTask.value = null
}

const shouldShowBubble = computed(() => props.enableAiBubble && !props.readonly)

onBeforeUnmount(() => {
  abortAiStream?.()
  editor.value?.destroy()
})
</script>

<template>
  <div class="md-editor" :class="{ 'md-editor--readonly': readonly }">
    <input ref="localImageInput" type="file" accept="image/*" hidden @change="onLocalImagePicked" />

    <!-- 工具栏 -->
    <div v-if="!readonly" class="md-editor-toolbar">
      <!-- 富文本命令只在预览模式可用：源码模式下它们会 focus 到隐藏的编辑器，
           抢走 textarea 焦点，导致回车/输入失效 -->
      <template v-if="mode === 'wysiwyg'">
      <!-- 格式 -->
      <button
        v-if="tb.heading" class="tbar-btn" title="H1"
        :class="{ 'is-active': editor?.isActive('heading', { level: 1 }) }"
        @click="editor?.chain().focus().toggleHeading({ level: 1 }).run()"
      >H1</button>
      <button
        v-if="tb.heading" class="tbar-btn" title="H2"
        :class="{ 'is-active': editor?.isActive('heading', { level: 2 }) }"
        @click="editor?.chain().focus().toggleHeading({ level: 2 }).run()"
      >H2</button>
      <button
        v-if="tb.heading" class="tbar-btn" title="H3"
        :class="{ 'is-active': editor?.isActive('heading', { level: 3 }) }"
        @click="editor?.chain().focus().toggleHeading({ level: 3 }).run()"
      >H3</button>

      <span class="tbar-sep" />

      <button
        v-if="tb.bold" class="tbar-btn tbar-bold" title="加粗"
        :class="{ 'is-active': editor?.isActive('bold') }"
        @click="editor?.chain().focus().toggleBold().run()"
      >B</button>
      <button
        v-if="tb.italic" class="tbar-btn tbar-italic" title="斜体"
        :class="{ 'is-active': editor?.isActive('italic') }"
        @click="editor?.chain().focus().toggleItalic().run()"
      >I</button>
      <button
        v-if="tb.strike" class="tbar-btn tbar-strike" title="删除线"
        :class="{ 'is-active': editor?.isActive('strike') }"
        @click="editor?.chain().focus().toggleStrike().run()"
      >S</button>
      <button
        v-if="tb.highlight" class="tbar-btn" title="高亮"
        :class="{ 'is-active': editor?.isActive('highlight') }"
        @click="editor?.chain().focus().toggleHighlight().run()"
      >高亮</button>

      <span class="tbar-sep" />

      <button
        v-if="tb.bulletList" class="tbar-btn" title="无序列表"
        :class="{ 'is-active': editor?.isActive('bulletList') }"
        @click="editor?.chain().focus().toggleBulletList().run()"
      >• 列表</button>
      <button
        v-if="tb.orderedList" class="tbar-btn" title="有序列表"
        :class="{ 'is-active': editor?.isActive('orderedList') }"
        @click="editor?.chain().focus().toggleOrderedList().run()"
      >1. 列表</button>
      <button
        v-if="tb.blockquote" class="tbar-btn" title="引用"
        :class="{ 'is-active': editor?.isActive('blockquote') }"
        @click="editor?.chain().focus().toggleBlockquote().run()"
      >引用</button>

      <span class="tbar-sep" />

      <button
        v-if="tb.table" class="tbar-btn" title="插入表格"
        @click="insertTable"
      >表格</button>
      <button
        v-if="base64Count"
        class="tbar-btn tbar-convert"
        :disabled="convertingBase64"
        title="正文里有内嵌 base64 图片，上传成素材后源码更清爽"
        @click="convertBase64Images"
      >{{ convertingBase64 ? '转换中…' : `转存内嵌图 (${base64Count})` }}</button>
      <button
        v-if="tb.horizontalRule" class="tbar-btn" title="分割线"
        @click="editor?.chain().focus().setHorizontalRule().run()"
      >—</button>

      <span class="tbar-sep" />

      <button
        v-if="tb.undo" class="tbar-btn" title="撤销"
        :disabled="!editor?.can().undo()"
        @click="editor?.chain().focus().undo().run()"
      >↩</button>
      <button
        v-if="tb.redo" class="tbar-btn" title="重做"
        :disabled="!editor?.can().redo()"
        @click="editor?.chain().focus().redo().run()"
      >↪</button>
      </template>

      <!-- 插图两种模式都支持：源码模式直接写进 textarea -->
      <button
        v-if="tb.image" class="tbar-btn" title="插入图片（自动上传为素材）"
        :disabled="imageUploading"
        @click="localImageInput?.click()"
      >{{ imageUploading ? '上传中…' : '图片' }}</button>
      <span v-if="imageError" class="tbar-error" :title="imageError">{{ imageError }}</span>

      <!-- 模式切换 -->
      <span class="tbar-spacer" />
      <div class="tbar-mode" role="group" aria-label="编辑模式">
        <button
          type="button"
          class="tbar-mode-btn" :class="{ 'is-active': mode === 'wysiwyg' }"
          :aria-pressed="mode === 'wysiwyg'"
          @click="switchToWysiwyg"
        >预览</button>
        <button
          type="button"
          class="tbar-mode-btn" :class="{ 'is-active': mode === 'source' }"
          :aria-pressed="mode === 'source'"
          @click="switchToSource"
        >源码</button>
      </div>
    </div>

    <!-- AI Bubble Menu（选中文字时浮出） -->    <BubbleMenu
      v-if="shouldShowBubble && editor"
      :editor="editor"
      :tippy-options="{ duration: 100, placement: 'top-start' }"
      class="ai-bubble"
    >
      <template v-if="!aiRunning">
        <button
          v-for="task in AI_INLINE_TASKS"
          :key="task.value"
          class="ai-bubble-btn"
          @click="runAiBubble(task.value)"
        >{{ task.label }}</button>
      </template>
      <template v-else>
        <span class="ai-bubble-running">{{ aiBubbleTask ? AI_INLINE_TASKS.find(t=>t.value===aiBubbleTask)?.label : 'AI' }} 生成中…</span>
        <button class="ai-bubble-btn ai-bubble-stop" @click="stopAiBubble">中断</button>
      </template>
      <span v-if="aiError" class="ai-bubble-error">{{ aiError }}</span>
    </BubbleMenu>

    <!-- WYSIWYG 编辑区：用 v-show 而不是 v-if，
         切换模式时只隐藏、不销毁 tiptap 实例，避免重新挂载时把内容丢掉 -->
    <EditorContent
      v-show="mode === 'wysiwyg'"
      :editor="editor"
      class="md-editor-content"
    />

    <!-- Source 编辑区 -->
    <textarea
      v-show="mode === 'source'"
      ref="sourceEl"
      :value="sourceView"
      :readonly="!sourceExpanded && foldedCount > 0"
      class="md-editor-source"
      spellcheck="false"
      @input="onSourceInput"
    />

    <!-- 折叠提示条：只在真的折叠了长地址时出现 -->
    <div v-if="mode === 'source' && foldedCount" class="md-source-fold-note">
      <span>{{ sourceExpanded ? '已展开' : '已折叠' }} {{ foldedCount }} 个超长图片地址（真实内容不变）</span>
      <button type="button" class="md-source-fold-btn" @click="sourceExpanded ? collapseSource() : expandSource()">
        {{ sourceExpanded ? '折叠' : '展开原文' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.md-editor {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--color-surface);
}

/* 工具栏 */
.md-editor-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
  padding: 4px 8px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface-raised);
}

.tbar-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 26px;
  padding: 0 6px;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  white-space: nowrap;
}

.tbar-btn:hover:not(:disabled) {
  background: var(--color-border);
  color: var(--color-text-primary);
}

.tbar-btn.is-active {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.tbar-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.tbar-bold { font-weight: 700; }
.tbar-italic { font-style: italic; }
.tbar-strike { text-decoration: line-through; }

/* 转换内嵌图：弱化处理，别抢主操作的视觉 */
.tbar-convert {
  border: 1px dashed var(--color-border);
  color: var(--color-text-muted);
  font-size: 11px;
}

/* 上传/转换的错误与结果提示 */
.tbar-error {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: #d9534f;
  padding: 0 4px;
}

/* 源码模式折叠提示条 */
.md-source-fold-note {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: 4px 30px;
  font-size: 11px;
  color: var(--color-text-muted);
  background: var(--color-surface-raised);
  border-top: 1px solid var(--color-border);
}

.md-source-fold-btn {
  padding: 2px 8px;
  font-size: 11px;
  font-family: inherit;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-surface);
  color: var(--color-text-secondary);
  cursor: pointer;
}

.md-source-fold-btn:hover {
  color: var(--color-text-primary);
  border-color: var(--color-accent);
}

.tbar-sep {
  width: 1px;
  height: 18px;
  background: var(--color-border);
  margin: 0 4px;
  flex-shrink: 0;
}

.tbar-spacer { flex: 1; }

.tbar-mode {
  display: flex;
  gap: 2px;
  padding: 2px;
  background: var(--color-border);
  border-radius: 6px;
}

.tbar-mode-btn {
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  font-family: inherit;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.tbar-mode-btn.is-active {
  background: var(--color-surface);
  color: var(--color-text-primary);
  box-shadow: 0 1px 3px rgba(0,0,0,.08);
}

/* AI 浮动菜单 */
.ai-bubble {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  background: var(--color-text-primary);
  border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0,0,0,.18);
}

.ai-bubble-btn {
  padding: 3px 8px;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--color-surface);
  cursor: pointer;
  white-space: nowrap;
}

.ai-bubble-btn:hover {
  background: rgba(255,255,255,.15);
}

.ai-bubble-stop {
  color: #fca5a5;
}

.ai-bubble-running {
  font-size: 12px;
  color: var(--color-surface);
  padding: 0 4px;
  opacity: .8;
}

.ai-bubble-error {
  font-size: 11px;
  color: #fca5a5;
  margin-left: 4px;
}

/* WYSIWYG 编辑区 */
.md-editor-content {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-4) 30px;
  min-height: 0;
}

/* v-show 隐藏时不要参与 flex 布局，否则会把 textarea 挤走 */
.md-editor-content[style*="display: none"] {
  flex: 0;
}

/* ProseMirror 内部样式 */
:deep(.ProseMirror) {
  outline: none;
  min-height: 480px;
  font-size: 14px;
  line-height: 1.8;
  color: var(--color-text-primary);
}

:deep(.ProseMirror p) { margin: 0 0 0.6em; }
:deep(.ProseMirror h1) { font-size: 1.5em; font-weight: 700; margin: 1em 0 0.4em; }
:deep(.ProseMirror h2) { font-size: 1.25em; font-weight: 700; margin: .9em 0 0.35em; }
:deep(.ProseMirror h3) { font-size: 1.1em; font-weight: 600; margin: .8em 0 0.3em; }
:deep(.ProseMirror ul, .ProseMirror ol) { padding-left: 1.4em; margin: 0.4em 0; }
:deep(.ProseMirror li) { margin: 0.15em 0; }
:deep(.ProseMirror blockquote) {
  border-left: 3px solid var(--color-accent);
  padding-left: 1em;
  margin: 0.6em 0;
  color: var(--color-text-secondary);
}
:deep(.ProseMirror strong) { font-weight: 700; }
:deep(.ProseMirror em) { font-style: italic; }
:deep(.ProseMirror s) { text-decoration: line-through; opacity: .7; }
:deep(.ProseMirror mark) {
  background: var(--color-accent-soft, #fff3b0);
  color: inherit;
  padding: 0 2px;
  border-radius: 2px;
}
:deep(.ProseMirror hr) {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: 1.2em 0;
}
:deep(.ProseMirror img) {
  max-width: 100%;
  border-radius: 4px;
  margin: 4px 0;
}
:deep(.ProseMirror table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  margin: 0.6em 0;
}
:deep(.ProseMirror th, .ProseMirror td) {
  border: 1px solid var(--color-border);
  padding: 6px 10px;
  text-align: left;
}
:deep(.ProseMirror th) {
  background: var(--color-surface-raised);
  font-weight: 600;
}
:deep(.ProseMirror p.is-editor-empty:first-child::before) {
  content: attr(data-placeholder);
  color: var(--color-text-muted);
  pointer-events: none;
  float: left;
  height: 0;
}

/* Source 编辑区 */
.md-editor-source {
  flex: 1;
  width: 100%;
  min-height: 480px;
  padding: var(--space-4) 30px;
  border: none;
  outline: none;
  resize: none;
  background: var(--color-surface);
  color: var(--color-text-primary);
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 13px;
  line-height: 1.7;
}

/* tiptap 的表格在源码里应是 GFM 表格，这里保证空单元格也有高度 */
:deep(.ProseMirror td:empty::after),
:deep(.ProseMirror th:empty::after) {
  content: '';
  display: inline-block;
}

:deep(.ProseMirror u) {
  text-decoration: underline;
  text-underline-offset: 2px;
}
</style>

