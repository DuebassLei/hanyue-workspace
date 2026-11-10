/** Markdown → 固定画幅卡片的分页器（与后端 services/card_paginator.py 完全同构）。 */

const IMAGE_LINE = /^!\[([^\]]*)\]\(([^)]+)\)$/
const HEADING = /^(#{1,3})\s+(.+)$/
const UL_ITEM = /^[-*+]\s+/
const OL_ITEM = /^\d+\.\s+/
const TASK_ITEM = /^\[([ xX])\]\s+(.+)$/

function estimateBlockHeight(block, contentWidth, fontSizeScale) {
  const charsPerLine = Math.max(12, Math.floor(contentWidth / (14.5 * fontSizeScale)))

  switch (block.type) {
    case 'h1': {
      const lines = Math.max(1, Math.ceil(block.content.length / Math.floor(charsPerLine * 0.62)))
      return Math.round((lines * 34 + 22) * fontSizeScale)
    }
    case 'h2': {
      const lines = Math.max(1, Math.ceil(block.content.length / Math.floor(charsPerLine * 0.75)))
      return Math.round((lines * 26 + 18) * fontSizeScale)
    }
    case 'h3': {
      const lines = Math.max(1, Math.ceil(block.content.length / Math.floor(charsPerLine * 0.85)))
      return Math.round((lines * 22 + 14) * fontSizeScale)
    }
    case 'paragraph': {
      const plainLength = block.content.replace(/(\*\*|\*|==|`)/g, '').length
      const lines = Math.max(1, Math.ceil(plainLength / charsPerLine))
      return Math.round((lines * 22 + 12) * fontSizeScale)
    }
    case 'blockquote': {
      const plainLength = block.content.replace(/(\*\*|\*|==|`)/g, '').length
      const lines = Math.max(1, Math.ceil(plainLength / Math.max(10, charsPerLine - 3)))
      return Math.round((lines * 21 + 26) * fontSizeScale)
    }
    case 'image':
      return Math.round(148 + (block.imageAlt ? 22 : 6))
    case 'table': {
      if (!block.table) return 80
      const rowCount = block.table.rows.length + 1
      return Math.round(rowCount * 31 + 20)
    }
    case 'ul':
    case 'ol': {
      const items = block.items || []
      let totalLines = 0
      for (const item of items) {
        const plain = item.text.replace(/(\*\*|\*|==|`)/g, '').length
        totalLines += Math.max(1, Math.ceil(plain / Math.max(10, charsPerLine - 2)))
      }
      return Math.round((totalLines * 21 + items.length * 6 + 12) * fontSizeScale)
    }
    case 'code': {
      const codeLines = block.content.split('\n').length
      return Math.round(codeLines * 18 + 28)
    }
    default:
      return 40
  }
}

function parseSegmentBlocks(rawSegment, contentWidth, fontSizeScale, segmentIdx) {
  const lines = rawSegment.replace(/\r\n/g, '\n').split('\n')
  const blocks = []
  let i = 0
  let blockCounter = 0

  const nextId = () => `blk-${segmentIdx}-${blockCounter++}`
  const push = (partial) => {
    blocks.push({ ...partial, estimatedHeight: estimateBlockHeight(partial, contentWidth, fontSizeScale) })
  }

  while (i < lines.length) {
    const trimmed = lines[i].trim()

    if (!trimmed) {
      i++
      continue
    }

    // 1. 代码块
    if (trimmed.startsWith('```')) {
      const language = trimmed.slice(3).trim() || 'text'
      const codeLines = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i])
        i++
      }
      if (i < lines.length) i++
      push({ id: nextId(), type: 'code', content: codeLines.join('\n'), language })
      continue
    }

    // 2. 标题
    const headingMatch = trimmed.match(HEADING)
    if (headingMatch) {
      const level = headingMatch[1].length
      push({
        id: nextId(),
        type: level === 1 ? 'h1' : level === 2 ? 'h2' : 'h3',
        content: headingMatch[2].trim(),
      })
      i++
      continue
    }

    // 3. 独立图片
    const imageMatch = trimmed.match(IMAGE_LINE)
    if (imageMatch) {
      push({
        id: nextId(),
        type: 'image',
        content: imageMatch[1],
        imageAlt: imageMatch[1],
        imageUrl: imageMatch[2].trim(),
      })
      i++
      continue
    }

    // 4. 表格
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      const tableLines = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tableLines.push(lines[i].trim())
        i++
      }
      if (tableLines.length >= 2) {
        const parseRow = (rowStr) => rowStr.slice(1, -1).split('|').map((cell) => cell.trim())
        const headers = parseRow(tableLines[0])
        const isSeparator = tableLines[1].replace(/[\s|:-]/g, '').length === 0
        const dataStartIndex = isSeparator ? 2 : 1
        const rows = tableLines.slice(dataStartIndex).map(parseRow)
        push({ id: nextId(), type: 'table', content: '', table: { headers, rows } })
        continue
      }
    }

    // 5. 引用
    if (trimmed.startsWith('>')) {
      const quoteLines = []
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ''))
        i++
      }
      push({ id: nextId(), type: 'blockquote', content: quoteLines.join(' ') })
      continue
    }

    // 6. 无序列表
    if (UL_ITEM.test(trimmed)) {
      const items = []
      while (i < lines.length && UL_ITEM.test(lines[i].trim())) {
        const rawItem = lines[i].trim().replace(UL_ITEM, '')
        const taskMatch = rawItem.match(TASK_ITEM)
        if (taskMatch) {
          items.push({ checked: taskMatch[1].toLowerCase() === 'x', text: taskMatch[2].trim() })
        } else {
          items.push({ text: rawItem })
        }
        i++
      }
      push({ id: nextId(), type: 'ul', content: '', items })
      continue
    }

    // 7. 有序列表
    if (OL_ITEM.test(trimmed)) {
      const items = []
      while (i < lines.length && OL_ITEM.test(lines[i].trim())) {
        items.push({ text: lines[i].trim().replace(OL_ITEM, '') })
        i++
      }
      push({ id: nextId(), type: 'ol', content: '', items })
      continue
    }

    // 8. 普通段落
    const paraLines = []
    while (i < lines.length) {
      const current = lines[i].trim()
      if (!current) break
      if (
        current.startsWith('#') ||
        current.startsWith('```') ||
        current.startsWith('>') ||
        current.startsWith('|') ||
        IMAGE_LINE.test(current) ||
        UL_ITEM.test(current) ||
        OL_ITEM.test(current)
      ) {
        break
      }
      paraLines.push(current)
      i++
    }
    const content = paraLines.join(' ')
    if (content) push({ id: nextId(), type: 'paragraph', content })
  }

  return blocks
}

export function paginateMarkdownToCards(markdown, preset, template, meta, mode) {
  const headerReserve = meta.showHeader ? 32 : 0
  const footerReserve = meta.showFooter || meta.showPageNumber ? 34 : 0
  const maxAvailableHeight = Math.max(
    160,
    preset.height - template.paddingY * 2 - headerReserve - footerReserve,
  )
  const contentWidth = Math.max(180, preset.width - template.paddingX * 2)

  const segments = markdown
    .replace(/\r\n/g, '\n')
    .split(/\n\s*---+\s*\n/g)
    .map((segment) => segment.trim())
    .filter(Boolean)
    .map((segment, idx) => parseSegmentBlocks(segment, contentWidth, meta.fontSizeScale, idx))

  const rawPages = []

  if (mode === 'manual-hr') {
    for (const blocks of segments) {
      if (blocks.length === 0) continue
      rawPages.push({ blocks, usedHeight: blocks.reduce((acc, b) => acc + b.estimatedHeight, 0) })
    }
  } else if (mode === 'by-h2') {
    for (const blocks of segments) {
      let currentBlocks = []
      let currentHeight = 0
      for (const block of blocks) {
        if ((block.type === 'h1' || block.type === 'h2') && currentBlocks.length > 0) {
          rawPages.push({ blocks: currentBlocks, usedHeight: currentHeight })
          currentBlocks = [block]
          currentHeight = block.estimatedHeight
        } else {
          currentBlocks.push(block)
          currentHeight += block.estimatedHeight
        }
      }
      if (currentBlocks.length > 0) rawPages.push({ blocks: currentBlocks, usedHeight: currentHeight })
    }
  } else {
    for (const blocks of segments) {
      let currentBlocks = []
      let currentHeight = 0
      for (let idx = 0; idx < blocks.length; idx++) {
        const block = blocks[idx]
        const nextBlock = blocks[idx + 1]
        const headingKeepWithNext =
          (block.type === 'h2' || block.type === 'h3') && nextBlock
            ? Math.min(60, nextBlock.estimatedHeight * 0.5)
            : 0

        if (
          currentBlocks.length > 0 &&
          currentHeight + block.estimatedHeight + headingKeepWithNext > maxAvailableHeight
        ) {
          rawPages.push({ blocks: currentBlocks, usedHeight: currentHeight })
          currentBlocks = [block]
          currentHeight = block.estimatedHeight
        } else {
          currentBlocks.push(block)
          currentHeight += block.estimatedHeight
        }
      }
      if (currentBlocks.length > 0) rawPages.push({ blocks: currentBlocks, usedHeight: currentHeight })
    }
  }

  if (rawPages.length === 0) {
    rawPages.push({
      blocks: [
        {
          id: 'empty-placeholder',
          type: 'paragraph',
          content: '在左侧输入 Markdown 内容，这里将实时生成符合平台规范的固定尺寸卡片...',
          estimatedHeight: 48,
        },
      ],
      usedHeight: 48,
    })
  }

  const total = rawPages.length
  return rawPages.map((page, idx) => ({
    index: idx + 1,
    total,
    isCover: idx === 0 && page.blocks.some((b) => b.type === 'h1'),
    blocks: page.blocks,
    estimatedUsedHeight: page.usedHeight,
    maxAvailableHeight,
    isOverflowing: page.usedHeight > maxAvailableHeight + 16,
  }))
}
