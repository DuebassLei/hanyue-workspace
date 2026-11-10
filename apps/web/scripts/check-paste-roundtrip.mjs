/**
 * 粘贴场景回归：node scripts/check-paste-roundtrip.mjs
 *
 * 模拟「从公众号 / Word / 网页粘贴文章」后点预览或源码。
 * 内容是 HTML（含 span 样式、&nbsp;、div、字体标签），不是干净 Markdown。
 */
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<!doctype html><html><body></body></html>')
global.window = dom.window
global.document = dom.window.document
Object.defineProperty(global, 'navigator', {
  value: dom.window.navigator,
  configurable: true,
  writable: true,
})
global.DOMParser = dom.window.DOMParser
global.Node = dom.window.Node
global.HTMLElement = dom.window.HTMLElement

const { Editor } = await import('@tiptap/core')
await import('@tiptap/pm/model')
await import('@tiptap/pm/state')
const StarterKit = (await import('@tiptap/starter-kit')).default
const Highlight = (await import('@tiptap/extension-highlight')).default
const Image = (await import('@tiptap/extension-image')).default
const Link = (await import('@tiptap/extension-link')).default
const { Table } = await import('@tiptap/extension-table')
const { TableRow } = await import('@tiptap/extension-table-row')
const { TableCell } = await import('@tiptap/extension-table-cell')
const { TableHeader } = await import('@tiptap/extension-table-header')
const { editorToMarkdown, setEditorFromMarkdown, markdownToHtml } = await import(
  '../src/utils/markdownSerializer.js'
)

function makeEditor() {
  const el = document.createElement('div')
  document.body.appendChild(el)
  return new Editor({
    element: el,
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3] }, codeBlock: false }),
      Highlight.configure({ multicolor: false }),
      Image.configure({ inline: false, allowBase64: true }),
      Link.configure({ openOnClick: false, autolink: false }),
      Table.configure({ resizable: false }),
      TableRow,
      TableCell,
      TableHeader,
    ],
    content: '',
  })
}

/** 公众号编辑器粘出来的典型片段：section/span 内联样式 + nbsp + 全角空格 */
const WECHAT_PASTE = `<section style="font-size:16px;line-height:1.8;color:#333">
  <p style="margin:0 0 1em"><span style="font-weight:700">如何建立稳定的内容节奏</span></p>
  <p>稳定更新，不一定意味着<strong>每天都要赶稿</strong>。关键是<mark>把选题攒成池子</mark>。</p>
  <ul><li>降低启动成本</li><li>减少临时找题</li></ul>
  <blockquote>好的内容节奏，是让读者形成期待。</blockquote>
  <p>他说道：“这是　一个测试——真的。”&nbsp;然后继续。</p>
</section>`

const WORD_PASTE = `<div class=WordSection1>
  <p class=MsoNormal><b>标题一</b></p>
  <p class=MsoNormal>正文里有<b>加粗</b>、<i>斜体</i>和<a href="https://example.com">一个链接</a>。</p>
  <table border=1><tr><td>维度</td><td>数值</td></tr><tr><td>成本</td><td>低</td></tr></table>
</div>`

const CASES = [
  { name: '公众号 HTML 粘贴', input: WECHAT_PASTE, kind: 'html' },
  { name: 'Word HTML 粘贴（带表格）', input: WORD_PASTE, kind: 'html' },
  {
    name: '网页 Markdown 粘贴（含代码块与链接）',
    kind: 'md',
    input: `## 小标题

普通段落，含 \`code\` 与 [链接](https://example.com/a(b))。

\`\`\`js
const a = 1 < 2
\`\`\`
`,
  },
  {
    name: '长段落（3000 字无标点）',
    kind: 'md',
    input: '一'.repeat(3000),
  },
]

const editor = makeEditor()
let failed = 0

const textOf = (s) => s.replace(/[\s*#>`_~=|:\-\[\]()<>/"'.,;!?&]/g, '')

for (const c of CASES) {
  editor.commands.setContent('', false)
  if (c.kind === 'html') {
    editor.commands.setContent(c.input, false)
  } else {
    setEditorFromMarkdown(editor, c.input)
  }

  const once = editorToMarkdown(editor)
  // 关键回归：切到源码 → 切回预览，内容必须还在
  setEditorFromMarkdown(editor, once)
  const twice = editorToMarkdown(editor)

  const stable = once === twice
  const nonEmpty = textOf(twice).length > 0
  const ok = stable && nonEmpty
  if (!ok) failed += 1

  console.log(`${ok ? 'PASS' : 'FAIL'}  ${c.name}  (${twice.length} 字符)`)
  if (!ok) {
    console.log('  --- 第一次序列化 ---\n' + once.slice(0, 600))
    console.log('  --- 第二次 ---\n' + twice.slice(0, 600))
  }
}

// 表格必须真的变成 GFM 表格，而不是被拍平
editor.commands.setContent('', false)
editor.commands.setContent(WORD_PASTE, false)
const md = editorToMarkdown(editor)
const hasTable = /^\|.*\|$/m.test(md)
console.log(`${hasTable ? 'PASS' : 'FAIL'}  Word 表格转成 GFM 表格`)
if (!hasTable) {
  failed += 1
  console.log(md)
}

// 空文档 / 只有空格不能报错
editor.commands.setContent('', false)
setEditorFromMarkdown(editor, '')
const emptyMd = editorToMarkdown(editor)
console.log(`${emptyMd === '' ? 'PASS' : 'FAIL'}  空文档序列化为空串`)
if (emptyMd !== '') failed += 1

// markdownToHtml 对特殊字符不炸
const html = markdownToHtml('<script>alert(1)</script> 正常文本')
console.log(html.includes('正常文本') ? 'PASS  特殊字符不丢文本' : 'FAIL  特殊字符丢文本')
if (!html.includes('正常文本')) failed += 1

editor.destroy()
console.log(failed ? `\n${failed} 个用例失败` : '\n全部通过')
process.exit(failed ? 1 : 0)
