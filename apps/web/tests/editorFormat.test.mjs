import test from 'node:test'
import assert from 'node:assert/strict'
import {
  CARD_IDS,
  cardMarkdown,
  emphasisFormats,
  formatLabel,
  formatSelection,
  insertFormats,
} from '../src/utils/editorFormat.js'

test('emphasis markers match what the wechat layout engine renders', () => {
  // 引擎只认这三种记号（** / == / ++），换成 <b> / <u> 会被微信清掉样式
  assert.deepEqual(emphasisFormats.map((item) => item.marker), ['**', '==', '++'])
})

test('every insert item maps to a syntax the engine understands', () => {
  const ids = insertFormats.flatMap((group) => group.items.map((item) => item[0]))
  for (const id of ids) {
    assert.ok(typeof formatLabel(id) === 'string' && formatLabel(id) !== id, `${id} 没有可读名字`)
  }
  // GOLDEN 之类引擎不认识的 callout 不能进菜单：插进去会在正文印出英文标签
  assert.ok(!ids.includes('GOLDEN'))
  assert.deepEqual(CARD_IDS, ['image', 'long', 'prompt', 'promptbox'])
})

test('inline emphasis wraps each line separately', () => {
  const result = formatSelection('第一行\n第二行', 0, 7, 'bold')
  assert.equal(result.text, '**第一行**\n**第二行**')
  // 有选区时整段选中，方便用户直接覆盖成自己的字
  assert.equal(result.start, 0)
  assert.equal(result.end, result.text.length)
})

test('inline emphasis on empty selection inserts a placeholder and selects it', () => {
  const result = formatSelection('正文', 1, 1, 'highlight')
  assert.equal(result.text, '正==文字==文')
  assert.equal(result.text.slice(result.start, result.end), '文字')
})

test('block insert pads blank lines around the fragment', () => {
  const result = formatSelection('上一段\n下一段', 4, 4, 'TIP')
  assert.equal(result.text, '上一段\n\n> [!TIP] 在这里填写内容\n\n下一段')
  assert.equal(result.text.slice(result.start, result.end), '在这里填写内容')
})

test('block insert does not add a third newline when one is already there', () => {
  const result = formatSelection('上一段\n', 4, 4, 'NOTE')
  assert.equal(result.text, '上一段\n\n> [!NOTE] 在这里填写内容')
})

test('callout keeps every line inside the quote', () => {
  const result = formatSelection('', 0, 0, 'WARN')
  assert.equal(result.text, '> [!WARN] 在这里填写内容')
  const multi = formatSelection('第一行\n第二行', 0, 7, 'TLDR')
  assert.equal(multi.text, '> [!TLDR] 第一行\n> 第二行')
})

test('terminal and prompt fences carry the language tag', () => {
  assert.ok(formatSelection('', 0, 0, 'bash').text.startsWith('```bash\n'))
  assert.ok(formatSelection('', 0, 0, 'text').text.startsWith('```text\n'))
  assert.ok(formatSelection('', 0, 0, 'prompt').text.startsWith('```prompt 提示词.txt\n'))
  assert.ok(formatSelection('', 0, 0, 'promptbox').text.startsWith('```promptbox 标题\n'))
})

test('rule goes after the selection instead of replacing it', () => {
  const result = formatSelection('保留我\n后面', 0, 3, 'rule')
  assert.ok(result.text.startsWith('保留我'))
  assert.ok(result.text.includes('---'))
})

test('image insert selects the address when nothing was selected', () => {
  const result = formatSelection('', 0, 0, 'image')
  assert.equal(result.text, '![图注](<图片地址>)')
  assert.equal(result.text.slice(result.start, result.end), '图片地址')
})

test('long image carries the long title so the engine renders the tall card', () => {
  const result = formatSelection('', 0, 0, 'long')
  assert.equal(result.text, '![图注](<图片地址> "long")')
})

test('selecting a url puts the caption in focus instead', () => {
  const result = formatSelection('https://example.com/a.png', 0, 27, 'image')
  assert.equal(result.text, '![图注](<https://example.com/a.png>)')
  assert.equal(result.text.slice(result.start, result.end), '图注')
})

test('cardMarkdown builds a data image and rejects bad urls', () => {
  assert.equal(
    cardMarkdown('image', ' 图注 ', 'https://example.com/a.png'),
    '![图注](<https://example.com/a.png>)',
  )
  assert.throws(() => cardMarkdown('image', '', 'ftp://example.com/a.png'), /HTTP 或 HTTPS/)
  assert.throws(() => cardMarkdown('image', '', 'https://user:pass@example.com/a.png'), /HTTP 或 HTTPS/)
  assert.throws(() => cardMarkdown('image', '', '不是地址'), /有效的图片地址/)
})

test('cardMarkdown escapes parentheses that would break the link', () => {
  // 右括号必须转义，否则 `](<...>)` 会提前闭合；左括号无害，保持原样更好读
  assert.equal(
    cardMarkdown('long', '图', 'https://example.com/a(1).png'),
    '![图](<https://example.com/a(1%29.png> "long")',
  )
})

test('cardMarkdown fences prompt content and dodges fence conflicts', () => {
  assert.equal(cardMarkdown('prompt', '', '内容'), '```prompt 提示词.txt\n内容\n```')
  assert.equal(cardMarkdown('promptbox', '标题', '内容'), '```promptbox 标题\n内容\n```')
  // 内容自带 ``` 时外层换成 ~~~，否则会把块提前闭合
  assert.ok(cardMarkdown('prompt', '', '```js\nx\n```').startsWith('~~~prompt'))
  assert.throws(() => cardMarkdown('prompt', '', '```\n~~~'), /代码围栏/)
  assert.throws(() => cardMarkdown('prompt', '', '   '), /请填写提示词内容/)
})

test('card branch inserts an already built fragment without re-templating', () => {
  const result = formatSelection('前文', 2, 2, 'image', '![图](https://x/a.png)')
  assert.equal(result.text, '前文\n\n![图](https://x/a.png)')
  assert.equal(result.start, result.text.length)
})

test('heading keeps the two hashes and selects the text the user meant', () => {
  const empty = formatSelection('', 0, 0, 'heading')
  assert.equal(empty.text, '## 小标题')
  // 没选区时只选中占位文字，`## ` 要留着
  assert.equal(empty.text.slice(empty.start, empty.end), '小标题')
  const picked = formatSelection('原来的标题', 0, 5, 'heading')
  assert.equal(picked.text, '## 原来的标题')
  // 有选区时整行选中，方便直接覆盖
  assert.equal(picked.text.slice(picked.start, picked.end), '## 原来的标题')
})
