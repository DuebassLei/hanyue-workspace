/**
 * 序列化往返自检：node scripts/check-markdown-roundtrip.mjs
 *
 * 校验「富文本 → Markdown → 富文本」不丢内容，覆盖旧版会炸掉表格/高亮的场景。
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
const { editorToMarkdown, setEditorFromMarkdown } = await import(
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

const CASES = [
  {
    name: '粘贴的公众号文章（标题+加粗+高亮+列表+引用）',
    md: `# 一周三篇图文：如何建立稳定的内容节奏

稳定更新，不一定意味着**每天都要赶稿**。关键是==把选题攒成池子==。

## 为什么要攒选题

- 降低启动成本
- 减少临时找题
- 保持风格一致

> 好的内容节奏，是让读者形成期待。

1. 收集灵感
2. 每周整理
3. 提前一天写完
`,
  },
  {
    name: '表格（旧版会抛 table not supported）',
    md: `## 对比

| 维度 | 方案 A | 方案 B |
| --- | --- | --- |
| 成本 | 低 | 高 |
| 效果 | 一般 | 好 |
`,
  },
  {
    name: '分割线 + 代码 + 链接',
    md: `---

\`inline code\` 与 [链接](https://example.com)。

正文结束。
`,
  },
  {
    name: '中文排版常见字符（全角空格、弯引号、破折号）',
    md: `他说道：“这是　一个测试——真的。”然后继续写下去。`,
  },
  {
    name: '空内容',
    md: '',
  },
]

let failed = 0
const editor = makeEditor()

for (const c of CASES) {
  editor.commands.setContent('', false)
  setEditorFromMarkdown(editor, c.md)
  const once = editorToMarkdown(editor)

  editor.commands.setContent('', false)
  setEditorFromMarkdown(editor, once)
  const twice = editorToMarkdown(editor)

  const stable = once === twice
  // 内容不丢：纯文字（去掉空白/标记符）应完整保留
  const strip = (s) => s.replace(/[\s*#>`_~=|:\-\[\]()]/g, '')
  const keepAll = strip(c.md) === strip(twice)

  if (!stable || !keepAll) failed += 1
  console.log(`${stable && keepAll ? 'PASS' : 'FAIL'}  ${c.name}`)
  if (!stable || !keepAll) {
    console.log('  --- 原始 ---\n' + c.md)
    console.log('  --- 往返后 ---\n' + twice)
  }
}

editor.destroy()
console.log(failed ? `\n${failed} 个用例失败` : '\n全部通过')
process.exit(failed ? 1 : 0)
