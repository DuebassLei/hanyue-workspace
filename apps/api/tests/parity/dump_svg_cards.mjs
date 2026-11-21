// Node 侧 SVG 动画卡差分 harness：对 cases.jsonl 里每个
// {id, kind, title, lines, style, theme} 调用参考 vendor/layout/layout.js 的
// renderSvgCard，输出 {id, html, warnings}，供 pytest 与 Python 侧逐字节比较。
//
// 参考模块的 WARN 是模块级状态，renderSvgCard 内部会先重置，逐个 case 调用即可。
//
// 用法：node dump_svg_cards.mjs --cases svg_cases.jsonl --out node_svg.jsonl
// 环境变量 LAYOUT_REF_DIR 可覆盖参考项目路径。
import { readFile, writeFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'

const args = process.argv.slice(2)
const getArg = k => {
  const i = args.indexOf(k)
  return i >= 0 ? args[i + 1] : null
}
const casesPath = getArg('--cases') ?? 'svg_cases.jsonl'
const outPath = getArg('--out') ?? 'node_svg.jsonl'
const refDir = process.env.LAYOUT_REF_DIR ?? 'C:/Users/11301/Downloads/dsh-wechat-studio-main/dsh-wechat-studio-main'

const { renderSvgCard } = await import(pathToFileURL(refDir + '/vendor/layout/layout.js').href)

const lines = (await readFile(casesPath, 'utf8')).split('\n').filter(Boolean)
const out = []
for (const line of lines) {
  const c = JSON.parse(line)
  const opts = {}
  if (c.style) opts.style = c.style
  if (c.theme) opts.theme = c.theme
  let html, warnings
  try {
    const r = renderSvgCard(c.kind, c.title ?? null, c.lines ?? [], opts)
    html = r.html
    warnings = r.warnings
  } catch (e) {
    html = '__ERROR__ ' + (e && e.name ? e.name : 'Error') + ': ' + (e && e.message ? e.message : String(e))
    warnings = []
  }
  out.push(JSON.stringify({ id: c.id, html, warnings }))
}
await writeFile(outPath, out.join('\n') + '\n', 'utf8')
console.log(`svg_cards_dump: ${out.length} cases -> ${outPath}`)
