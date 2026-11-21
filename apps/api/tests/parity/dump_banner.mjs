// Node 侧 banner 差分 harness：对 specs.jsonl 里的每个 {id, spec, theme} 用参考
// vendor/layout/banner.js 的 build / checkSpec 输出 html 与 issues，供 pytest 与
// Python 侧逐字节比较。
//
// 入口语义与参考 layout.js 的 renderPage 一致：theme 非空时把 layout.js 的 15 套
// 配色映射成 banner 键名 {main, light: tint, line, border: tint, bg: soft} 传给
// build；theme 为 null 时用 banner.js 自己的 THEMES。
//
// 用法：node dump_banner.mjs --cases specs.jsonl --out node_banner.jsonl
// 环境变量 LAYOUT_REF_DIR 可覆盖参考项目路径。
import { readFile, writeFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'

const args = process.argv.slice(2)
const getArg = k => {
  const i = args.indexOf(k)
  return i >= 0 ? args[i + 1] : null
}
const casesPath = getArg('--cases') ?? 'banner_specs.jsonl'
const outPath = getArg('--out') ?? 'node_banner.jsonl'
const refDir = process.env.LAYOUT_REF_DIR ?? 'C:/Users/11301/Downloads/dsh-wechat-studio-main/dsh-wechat-studio-main'

const B = await import(pathToFileURL(refDir + '/vendor/layout/banner.js').href)
const L = await import(pathToFileURL(refDir + '/vendor/layout/layout.js').href)

const mapped = (() => {
  const themes = { ...B.THEMES }
  for (const [k, T] of Object.entries(L.THEMES)) themes[k] = { main: T.main, light: T.tint, line: T.line, border: T.tint, bg: T.soft }
  return themes
})()

const lines = (await readFile(casesPath, 'utf8')).split('\n').filter(Boolean)
const out = []
for (const line of lines) {
  const c = JSON.parse(line)
  let html, issues
  try {
    const themes = c.theme ? mapped : undefined
    html = B.build(structuredClone(c.spec), themes)
    issues = B.checkSpec(structuredClone(c.spec), [html])
  } catch (e) {
    html = '__ERROR__ ' + e.name + ': ' + e.message
    issues = []
  }
  out.push(JSON.stringify({ id: c.id, html, issues }))
}
await writeFile(outPath, out.join('\n') + '\n', 'utf8')
console.log(`banner_dump: ${out.length} cases -> ${outPath}`)
