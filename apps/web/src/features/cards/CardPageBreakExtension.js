/**
 * CardPageBreak — tiptap 自定义节点
 * 将卡片台的分页符 `---` 渲染为可视分页线，双击删除
 *
 * 序列化规则：输出为 Markdown `\n\n---\n\n`
 * 反序列化规则：匹配 hrule token 时创建此节点
 */
import { Node, mergeAttributes } from '@tiptap/core'

export const CardPageBreak = Node.create({
  name: 'cardPageBreak',

  group: 'block',
  atom: true,  // 不可编辑内部

  parseHTML() {
    return [{ tag: 'div[data-type="card-page-break"]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-type': 'card-page-break', class: 'card-page-break' })]
  },

  addNodeView() {
    return ({ node, getPos, editor }) => {
      const dom = document.createElement('div')
      dom.setAttribute('data-type', 'card-page-break')
      dom.className = 'card-page-break-node'
      dom.title = '双击删除此分页符'

      const label = document.createElement('span')
      label.className = 'card-page-break-label'
      label.textContent = '─── 分页 ───'
      dom.appendChild(label)

      dom.addEventListener('dblclick', () => {
        const pos = getPos()
        if (typeof pos === 'number') {
          editor.chain().focus().deleteRange({ from: pos, to: pos + node.nodeSize }).run()
        }
      })

      return { dom }
    }
  },

  // 快捷键：在空段落按 Cmd/Ctrl+Enter 插入分页符
  addKeyboardShortcuts() {
    return {
      'Mod-Enter': () => this.editor.commands.insertCardPageBreak(),
    }
  },

  addCommands() {
    return {
      insertCardPageBreak: () => ({ commands }) => {
        return commands.insertContent({ type: this.name })
      },
    }
  },
})

export default CardPageBreak
