/**
 * 成稿台段落编辑器的格式化 / 插入规则（纯函数，不碰 DOM，便于单测）。
 *
 * 迁移自参考项目 `client/editor-format.js`，三处刻意保留原样：
 * 1. 强调用公众号排版引擎认识的记号（`**` / `==` / `++`），不用 `<b>` / `<u>`：
 *    正文最终要进微信，行内 HTML 标签的样式会被清掉，只有这些记号会被引擎转成内联样式。
 * 2. 块级插入（引用、卡片、代码块、分隔线）前后自动补空行，
 *    否则 markdown-it 会把卡片并进上一段，渲染成"段落里的一个代码块"。
 * 3. 插入后把光标/选区落在"该改的那一段"上（占位文字或图注），
 *    用户插完可以直接打字，不用再框选。
 */

/** 行内强调：按钮上的符号就是用户看到的记号 */
export const emphasisFormats = [
  { id: 'bold', label: '荧光笔加粗', symbol: 'B', marker: '**' },
  { id: 'highlight', label: '色块强调', symbol: '色', marker: '==' },
  { id: 'underline', label: '主题色下划线', symbol: 'U', marker: '++' },
]

/**
 * 插入项分三组，只列排版引擎真能渲染的类型。
 *
 * 这里刻意没跟截图里的「引用 / 提示 / 金句卡」逐字对齐：
 * 引擎只认 quote、QUOTE、TIP、WARN、NOTE、TLDR 六种 callout，
 * 插一个它不认识的标记（比如 `> [!GOLDEN]`）会在正文里印出「GOLDEN」这个英文标签，
 * 比少一个菜单项更糟。菜单项因此等于引擎能力边界。
 */
export const insertFormats = [
  {
    label: '引用与提示',
    items: [
      ['quote', '引用卡'],
      ['QUOTE', '金句卡'],
      ['TIP', '小技巧'],
      ['WARN', '注意'],
      ['NOTE', '说明'],
      ['TLDR', '省流版'],
      ['heading', '小标题'],
    ],
  },
  {
    label: '代码与提示词',
    items: [
      ['bash', '终端 · bash'],
      ['text', '终端 · text'],
      ['prompt', '长提示词 · 黑标签'],
      ['promptbox', '长提示词 · 虚线框'],
    ],
  },
  {
    label: '图片与分隔',
    items: [['image', '图片与图注'], ['long', '长图卡'], ['rule', '分隔线']],
  },
]

/**
 * 需要弹窗先把内容填好再插入的项。
 * 图片/长图要先拿到地址，长提示词要先写好正文；终端与其它 callout 点一下直接出占位。
 */
export const CARD_IDS = ['image', 'long', 'prompt', 'promptbox']

/** 弹窗标题用的可读名字（找不到就退回 id） */
export function formatLabel(id) {
  const hit = insertFormats.flatMap((group) => group.items).find((item) => item[0] === id)
  return hit ? hit[1] : id
}

/**
 * 弹窗提交后拼出的 Markdown 片段。
 * 图片校验协议：只放行 http/https，且不接受带账号密码的地址——
 * 这种地址贴进公众号会让读者看到 401 或泄漏凭据。
 */
export function cardMarkdown(id, title, value) {
  const label = String(title || '').trim().replace(/[\r\n[\]]/g, ' ')
  const content = String(value || '').trim()
  if (id === 'image' || id === 'long') {
    let url
    try {
      url = new URL(content)
    } catch {
      throw new Error('请输入有效的图片地址。')
    }
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) {
      throw new Error('请输入有效的 HTTP 或 HTTPS 图片地址。')
    }
    const src = url.href.replaceAll(')', '%29').replaceAll('>', '%3E')
    return `![${label}](<${src}>${id === 'long' ? ' "long"' : ''})`
  }
  if (!content) throw new Error('请填写提示词内容。')
  // 内容里已有 ``` 围栏时改用 ~~~，否则会提前闭合外层的块
  const fence = /^\s*`{3}/m.test(content) ? '~~~' : '```'
  if (fence === '~~~' && /^\s*~{3}/m.test(content)) {
    throw new Error('提示词同时包含两种代码围栏，请移除其中一种围栏后再插入。')
  }
  const name = label || (id === 'prompt' ? '提示词.txt' : '提示词')
  return `${fence}${id} ${name}\n${content}\n${fence}`
}

/**
 * 在文本里插入片段，返回新文本与"应该选中的区间"。
 *
 * `card` 有值时表示内容来自弹窗（已成形），不再做占位替换；
 * 没有时按 id 现造一段带占位的片段，让用户直接改。
 */
export function formatSelection(text, start, end, id, card) {
  const before = text.slice(0, start)
  const after = text.slice(end)
  const selection = text.slice(start, end)
  const inline = emphasisFormats.find((item) => item.id === id)
  let body
  let from
  let to

  if (card !== undefined) {
    body = card
    from = to = body.length
  } else if (inline) {
    const content = selection || '文字'
    // 多行选区逐行包裹：整段用一个 `**` 会把换行也包进去，渲染成一个大加粗块
    const pieces = content.split('\n').map((line) =>
      line.replace(/^(\s*)(\S(?:.*\S)?)(\s*)$/, (_, left_, core, right) => left_ + inline.marker + core + inline.marker + right),
    )
    body = pieces.join('\n')
    from = selection ? 0 : inline.marker.length
    to = selection ? body.length : from + content.length
  } else if (id === 'image' || id === 'long') {
    const isUrl = /^https?:\/\/\S+$/i.test(selection)
    const caption = !isUrl && selection ? selection.replace(/[[\]\r\n]/g, ' ').trim() : '图注'
    const url = isUrl ? selection : '图片地址'
    body = `![${caption}](<${url}>${id === 'long' ? ' "long"' : ''})`
    // 选中了地址就让图注可编辑，否则让地址可编辑
    from = isUrl ? 2 : caption.length + 5
    to = from + (isUrl ? caption.length : url.length)
  } else if (id === 'rule') {
    body = '---'
    from = to = body.length
    // 有选区时把分隔线放到选区后面，原来选中的内容不能被顶掉
    if (selection) return formatSelection(text, end, end, id)
  } else if (id === 'heading') {
    const content = selection || '小标题'
    body = `## ${content}`
    // 有选区时整行选中（用户可能想整句换掉），否则选中占位文字
    from = selection ? 0 : 3
    to = selection ? body.length : from + content.length
  } else if (['bash', 'text', 'prompt', 'promptbox'].includes(id)) {
    const header = `\`\`\`${id}${({ prompt: ' 提示词.txt', promptbox: ' 标题' })[id] || ''}\n`
    const content = selection || (id.startsWith('prompt') ? '在这里填写提示词' : '在这里填写命令或输出')
    body = `${header}${content}\n\`\`\``
    from = header.length
    to = from + content.length
  } else {
    // callout：`> [!TIP] 内容`，续行也要带 `> `，否则第二行会掉到引用外面
    const prefix = `> ${id === 'quote' ? '' : `[!${id}] `}`
    const content = selection || '在这里填写内容'
    body = prefix + content.replaceAll('\n', '\n> ')
    from = prefix.length
    to = body.length
  }

  // 块级片段两侧补空行；行内强调不补，否则会把 `**粗**` 单独顶成一段
  const left = !inline && before.trim()
    ? (before.endsWith('\n\n') ? '' : before.endsWith('\n') ? '\n' : '\n\n')
    : ''
  const right = !inline && after.trim()
    ? (after.startsWith('\n\n') ? '' : after.startsWith('\n') ? '\n' : '\n\n')
    : ''
  return {
    text: before + left + body + right + after,
    start: start + left.length + from,
    end: start + left.length + to,
  }
}
