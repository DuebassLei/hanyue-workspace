import test from 'node:test'
import assert from 'node:assert/strict'
import { articleBodyFromOutput, normalizeArticleMarkdown } from '../src/utils/aiArticle.js'

const escaped = String.raw`第一段。\n\n## 实验背景\n\n第二段。\n\n- 要点一\n- 要点二\n\n> 引用`
const formatted = '第一段。\n\n## 实验背景\n\n第二段。\n\n- 要点一\n- 要点二\n\n> 引用'

test('plain AI article escaped paragraph breaks become Markdown lines', () => {
  assert.equal(articleBodyFromOutput(escaped), formatted)
})
test('double-escaped markdown inside JSON is normalized after parsing', () => {
  assert.equal(articleBodyFromOutput(JSON.stringify({ title: '标题', markdown: escaped })), formatted)
})
test('normal Markdown and literal escapes in code remain unchanged', () => {
  assert.equal(normalizeArticleMarkdown(formatted), formatted)
  const code = '```js\nconst sample = "a\\n\\nb"\n```'
  assert.equal(normalizeArticleMarkdown(code), code)
  assert.equal(normalizeArticleMarkdown(String.raw`使用 \n 表示换行`), String.raw`使用 \n 表示换行`)
})
test('normalization is idempotent and handles escaped CRLF', () => {
  assert.equal(normalizeArticleMarkdown(normalizeArticleMarkdown(escaped)), formatted)
  assert.equal(normalizeArticleMarkdown(String.raw`段一\r\n\r\n## 标题`), '段一\n\n## 标题')
})
