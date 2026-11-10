/**
 * Markdown ↔ tiptap 序列化工具
 *
 * 为什么不用 prosemirror-markdown 的 defaultMarkdownParser / defaultMarkdownSerializer：
 *   1. 默认序列化器**不认识表格**（table / tableRow / tableCell / tableHeader），
 *      文档里一旦有表格就抛 "Token type `table` not supported by Markdown renderer"。
 *   2. 默认解析器认不出 `<mark>` / `<u>`，而 tiptap 的高亮正是导出成 `<mark>`，
 *      于是切一次模式高亮就丢。
 *   旧实现把异常 catch 后直接退化成 editor.getText()，结果就是
 *   「粘一篇文章 → 点预览 / 源码 → 格式和换行全没了」，表现为内容像是被清空。
 *
 * 现在：自定义 serializer 覆盖编辑器实际能产出的一切节点（含表格），
 * 解析走 HTML（table / mark / u / br 都能被 tiptap 正确还原），
 * 并且序列化不再静默降级——失败时保留全文并给出告警。
 */
import { MarkdownSerializer } from 'prosemirror-markdown'
import MarkdownIt from 'markdown-it'

// ---------------------------------------------------------------------------
// 序列化：编辑器 doc → Markdown
// ---------------------------------------------------------------------------

/** 单元格内容：段落用 <br> 连接，竖线转义，整体压成一行 */
function cellToText(cell) {
  const parts = []
  cell.forEach((child) => {
    if (child.type.name === 'paragraph' || child.isTextblock) {
      parts.push(cellInlineText(child))
    } else if (child.type.name === 'hardBreak') {
      parts.push('')
    } else if (child.type.name === 'code_block') {
      parts.push(child.textContent.trim())
    }
  })
  return parts
    .join('<br>')
    .replace(/\s*\n+\s*/g, '<br>')
    .replace(/\|/g, '\\|')
    .trim()
}

/**
 * 取一个文本块的内联 Markdown。
 * MarkdownSerializerState 没有「只渲染内联」的公开方法，这里用一个独立的
 * 一次性 writer 收集输出，避免碰原型链上已有的 renderInline（会递归）。
 */
function cellInlineText(textblock) {
  const chunks = []
  textblock.forEach((node) => {
    if (!node.isText) {
      if (node.type.name === 'hardBreak') chunks.push('<br>')
      if (node.type.name === 'image') chunks.push(`![${node.attrs.alt || ''}](${node.attrs.src || ''})`)
      return
    }
    let text = node.text
    const marks = node.marks.map((m) => m.type.name)
    if (marks.includes('code')) text = `\`${text}\``
    if (marks.includes('highlight')) text = `==${text}==`
    if (marks.includes('underline')) text = `<u>${text}</u>`
    if (marks.includes('strike')) text = `~~${text}~~`
    if (marks.includes('em')) text = `*${text}*`
    if (marks.includes('strong')) text = `**${text}**`
    const link = node.marks.find((m) => m.type.name === 'link')
    if (link && link.attrs.href) text = `[${text}](${link.attrs.href})`
    chunks.push(text)
  })
  return chunks.join('')
}

/**
 * 注意：tiptap 的节点名是**驼峰**（bulletList / horizontalRule / hardBreak /
 * codeBlock），不是 prosemirror-markdown 默认 schema 的下划线形式。
 * 用错名字不会报错，只会静默走到未知节点分支——这里必须逐一对齐。
 */
const serializerNodes = {
  blockquote(state, node) {
    state.wrapBlock('> ', null, node, () => state.renderContent(node))
  },

  codeBlock(state, node) {
    const params = node.attrs.language || node.attrs.params || ''
    const backticks = node.textContent.match(/`{3,}/gm)
    const fence = backticks ? `${backticks.sort().slice(-1)[0]}\`` : '```'
    state.write(`${fence}${params}\n`)
    state.text(node.textContent, false)
    state.write('\n')
    state.write(fence)
    state.closeBlock(node)
  },
  // 兜底：万一某个扩展用了下划线命名
  code_block(state, node) {
    serializerNodes.codeBlock(state, node)
  },

  heading(state, node) {
    state.write(`${state.repeat('#', node.attrs.level)} `)
    state.renderInline(node, false)
    state.closeBlock(node)
  },

  horizontalRule(state, node) {
    state.write('---')
    state.closeBlock(node)
  },
  horizontal_rule(state, node) {
    serializerNodes.horizontalRule(state, node)
  },

  bulletList(state, node) {
    state.renderList(node, '  ', () => '- ')
  },
  bullet_list(state, node) {
    serializerNodes.bulletList(state, node)
  },

  orderedList(state, node) {
    const start = node.attrs.start ?? node.attrs.order ?? 1
    const maxW = String(start + node.childCount - 1).length
    const space = state.repeat(' ', maxW + 2)
    state.renderList(node, space, (i) => {
      const nStr = String(start + i)
      return `${state.repeat(' ', maxW - nStr.length)}${nStr}. `
    })
  },
  ordered_list(state, node) {
    serializerNodes.orderedList(state, node)
  },

  listItem(state, node) {
    state.renderContent(node)
  },
  list_item(state, node) {
    serializerNodes.listItem(state, node)
  },

  paragraph(state, node) {
    state.renderInline(node)
    state.closeBlock(node)
  },

  image(state, node) {
    const src = String(node.attrs.src || '')
    const alt = state.esc(node.attrs.alt || '')
    const title = node.attrs.title ? ` "${String(node.attrs.title).replace(/"/g, '\\"')}"` : ''
    state.write(`![${alt}](${src.replace(/[()]/g, '\\$&')}${title})`)
  },

  hardBreak(state, node, parent, index) {
    for (let i = index + 1; i < parent.childCount; i += 1) {
      if (parent.child(i).type !== node.type) {
        state.write('  \n')
        return
      }
    }
  },
  hard_break(state, node, parent, index) {
    serializerNodes.hardBreak(state, node, parent, index)
  },

  text(state, node) {
    state.text(node.text, !state.inAutoLink)
  },

  // ---- 表格：默认序列化器完全不支持，这里是关键补丁 ----
  table(state, node) {
    const rows = []
    node.forEach((row) => {
      const cells = []
      row.forEach((cell) => {
        let text = cellToText(cell)
        // 表头单元格加粗，回解析时能区分表头（GFM 表格靠位置区分，不靠 bold）
        cells.push(text)
      })
      rows.push(cells)
    })
    if (!rows.length) return

    const colCount = Math.max(...rows.map((r) => r.length))
    if (!colCount) return
    const pad = (row) => {
      const copy = row.slice()
      while (copy.length < colCount) copy.push('')
      return copy
    }

    const header = pad(rows[0])
    const body = rows.slice(1).map(pad)

    const lines = [`| ${header.join(' | ')} |`, `| ${header.map(() => '---').join(' | ')} |`]
    body.forEach((row) => lines.push(`| ${row.join(' | ')} |`))

    state.write(lines.join('\n'))
    state.closeBlock(node)
  },

  // 由 table 统一渲染，子节点不单独输出
  tableRow() {},
  tableCell() {},
  tableHeader() {},

  // 卡片台的分页符节点（CardPageBreak），序列化为 --- 分隔线
  cardPageBreak(state, node) {
    state.write('---')
    state.closeBlock(node)
  },
}

/**
 * 同理，mark 名也是驼峰：bold / italic（不是 strong / em）、strike、code、
 * highlight、link。键名对不上就是「Mark type `bold` not supported」。
 */
const serializerMarks = {
  bold: { open: '**', close: '**', mixable: true, expelEnclosingWhitespace: true },
  strong: { open: '**', close: '**', mixable: true, expelEnclosingWhitespace: true },
  italic: { open: '*', close: '*', mixable: true, expelEnclosingWhitespace: true },
  em: { open: '*', close: '*', mixable: true, expelEnclosingWhitespace: true },
  strike: { open: '~~', close: '~~', mixable: true, expelEnclosingWhitespace: true },
  code: {
    open(_state, _mark, parent, index) {
      return backticksFor(parent.child(index), -1)
    },
    close(_state, _mark, parent, index) {
      return backticksFor(parent.child(index - 1), 1)
    },
    escape: false,
  },
  highlight: { open: '==', close: '==', mixable: true, expelEnclosingWhitespace: true },
  // 下划线在 Markdown 里没有标准语法，统一用 HTML <u>；解析端也认 <u>
  underline: { open: '<u>', close: '</u>', mixable: true, escape: false },
  link: {
    open(state, mark, parent, index) {
      state.inAutoLink = isPlainURL(mark, parent, index)
      return state.inAutoLink ? '<' : '['
    },
    close(state, mark, parent, index) {
      const inAutoLink = state.inAutoLink
      state.inAutoLink = undefined
      if (inAutoLink) return '>'
      const href = String(mark.attrs.href || '').replace(/[()"]/g, '\\$&')
      const title = mark.attrs.title ? ` "${String(mark.attrs.title).replace(/"/g, '\\"')}"` : ''
      return `](${href}${title})`
    },
    mixable: true,
  },
}

function backticksFor(node, side) {
  const ticks = /`+/g
  let m
  let len = 0
  if (node.isText) {
    while ((m = ticks.exec(node.text))) len = Math.max(len, m[0].length)
  }
  let result = len > 0 && side > 0 ? ' `' : '`'
  for (let i = 0; i < len; i += 1) result += '`'
  if (len > 0 && side < 0) result += ' '
  return result
}

function isPlainURL(link, parent, index) {
  if (link.attrs.title || !/^\w+:/.test(link.attrs.href || '')) return false
  const content = parent.child(index)
  return (
    content.isText &&
    content.text === link.attrs.href &&
    content.marks[content.marks.length - 1] === link &&
    (index === parent.childCount - 1 || !link.isInSet(parent.child(index + 1).marks))
  )
}

// tightLists: true → 列表项之间不留空行，源码模式下读起来才像正常 Markdown
const markdownSerializer = new MarkdownSerializer(serializerNodes, serializerMarks, {
  tightLists: true,
})

// ---------------------------------------------------------------------------
// 解析：Markdown → HTML → 编辑器 doc
// ---------------------------------------------------------------------------

let mdIt = null
function getMarkdownIt() {
  if (mdIt) return mdIt
  // html: true 让 <mark> / <u> / <table> 这类标签原样透传，tiptap 的 DOM 解析器才能识别
  mdIt = new MarkdownIt({ html: true, breaks: false, linkify: false, typographer: false })
  mdIt.enable('table')
  return mdIt
}

/**
 * 预处理编辑器特有的语法扩展，转成 markdown-it 认识的 HTML：
 *  - `==高亮==`   → `<mark>高亮</mark>`
 *  - `===高亮加粗===` → `<mark><strong>…</strong></mark>`（公众号排版语法）
 * 代码围栏内不做替换，避免破坏代码块。
 */
export function preprocessMarkdown(markdown) {
  if (!markdown) return ''
  const lines = String(markdown).replace(/\r\n?/g, '\n').split('\n')
  let inFence = false
  return lines
    .map((line) => {
      if (/^\s*(```|~~~)/.test(line)) {
        inFence = !inFence
        return line
      }
      if (inFence) return line
      return line
        .replace(/===([^=\n]+?)===/g, '<mark><strong>$1</strong></mark>')
        .replace(/==([^=\n]+?)==/g, '<mark>$1</mark>')
    })
    .join('\n')
}

/** Markdown → HTML */
export function markdownToHtml(markdown) {
  try {
    return getMarkdownIt().render(preprocessMarkdown(markdown || ''))
  } catch (err) {
    console.warn('[markdownSerializer] 解析失败，按纯文本展示：', err)
    return escapeHtml(String(markdown || '')).replace(/\n/g, '<br>')
  }
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// ---------------------------------------------------------------------------
// 对外 API
// ---------------------------------------------------------------------------

/**
 * tiptap editor doc → Markdown string
 * @param {import('@tiptap/core').Editor} editor
 * @returns {string}
 */
export function editorToMarkdown(editor) {
  if (!editor) return ''
  try {
    return markdownSerializer.serialize(editor.state.doc)
  } catch (err) {
    // 不再退化成 getText()：保留段落结构，至少不把全文粘成一行
    console.warn('[markdownSerializer] 序列化失败，已按纯文本结构降级：', err)
    return editor.state.doc.textBetween(0, editor.state.doc.content.size, '\n\n', '\n')
  }
}

/**
 * Markdown string → tiptap content
 * emitUpdate 传 false，避免程序性写入被当成用户编辑再抛回父组件形成回环。
 * @param {import('@tiptap/core').Editor} editor
 * @param {string} markdown
 */
export function setEditorFromMarkdown(editor, markdown) {
  if (!editor) return
  editor.commands.setContent(markdownToHtml(markdown), false)
}

/**
 * Markdown → markdown-it token 列表（不依赖 editor，便于测试/离线使用）
 */
export function parseMarkdown(markdown) {
  return getMarkdownIt().parse(preprocessMarkdown(markdown || ''), {})
}

export default editorToMarkdown
