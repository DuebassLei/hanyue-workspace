// Node 侧差分 harness：调用参考项目 vendor/layout/layout.js 的 renderPage，
// 从生成页面中提取 const D=<JSON> 里的 data.styles（正文 HTML，颜色为 TOK 占位符），
// 按主题 paint 成最终配色后逐 case 输出 JSONL，供 pytest 与 Python 输出逐字节比较。
//
// 用法：node ref_dump.mjs --cases cases.jsonl --out node_dump.jsonl
// 环境变量 LAYOUT_REF_DIR 可覆盖参考项目路径。
import { readFile, writeFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'

const args = process.argv.slice(2)
const getArg = k => {
  const i = args.indexOf(k)
  return i >= 0 ? args[i + 1] : null
}
const casesPath = getArg('--cases') ?? 'cases.jsonl'
const outPath = getArg('--out') ?? 'node_dump.jsonl'
const refDir = process.env.LAYOUT_REF_DIR ?? 'C:/Users/11301/Downloads/dsh-wechat-studio-main/dsh-wechat-studio-main'

const { renderPage } = await import(pathToFileURL(refDir + '/vendor/layout/layout.js').href)

const lines = (await readFile(casesPath, 'utf8')).split('\n').filter(Boolean)
const out = []
for (const line of lines) {
  const c = JSON.parse(line)
  const opts = { outro: 'true' }
  if (c.style) opts.style = c.style
  if (c.theme) opts.theme = c.theme
  if (c.brandAuthor) opts.brandAuthor = c.brandAuthor
  if (c.brandSlogan) opts.brandSlogan = c.brandSlogan
  let html
  try {
    const page = renderPage(c.md, opts)
    const idx = page.html.indexOf('const D=')
    const end = page.html.indexOf('\n', idx)
    const D = JSON.parse(page.html.slice(idx + 8, end).replace(/;$/, ''))
    const style = c.style ?? D.default_style
    const theme = c.theme ?? D.default_theme
    let body = D.styles[style]
    for (const k in D.tokens) body = body.split(D.tokens[k]).join(D.palette[theme][k])
    html = body
  } catch (e) {
    html = '__ERROR__ ' + (e && e.message ? e.message : String(e))
  }
  out.push(JSON.stringify({ id: c.id, html }))
}
await writeFile(outPath, out.join('\n') + '\n', 'utf8')
console.log(`ref_dump: ${out.length} cases -> ${outPath}`)
