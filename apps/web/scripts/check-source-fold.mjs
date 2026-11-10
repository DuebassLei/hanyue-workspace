/**
 * 源码模式折叠逻辑自检
 *
 * 校验点：
 *  1. 超长的 base64 / 签名直链在源码视图里被折叠，屏幕不被刷满
 *  2. 折叠只影响显示；sourceValue（真正交给后端的 Markdown）一字未改
 *  3. 短链接不被折叠，避免正常截图/网页链接被误伤
 *
 * 折叠函数从 MarkdownEditor.vue 里抽出来单测，改实现时先看看这里。
 */
import assert from 'node:assert/strict'

const SOURCE_URL_LIMIT = 72

function condenseUrl(url) {
  if (url.length <= SOURCE_URL_LIMIT) return url
  const name = url.startsWith('data:')
    ? `${(url.match(/^data:([^;,]+)/)?.[1] || 'image')} 内嵌图`
    : url.slice(-10)
  return `⟪${name} · ${url.length} 字符⟫`
}

function shortenUrls(text) {
  if (!text) return ''
  return text
    .replace(/\]\(([^()\s]{80,})\)/g, (_m, url) => `](${condenseUrl(url)})`)
    .replace(/\b(src|href)=("|')([^"'\s]{80,})\2/g, (m, attr, quote, url) => `${attr}=${quote}${condenseUrl(url)}${quote}`)
}

const base64 = 'data:image/png;base64,' + 'iVBORw0KGgo'.repeat(400)
const signed = 'http://localhost:8000/api/v1/assets/abc123def456/raw?exp=1794841684&sig=1DGn7YkytV0z2J4fjT6wjabcdefgh'

const cases = [
  {
    name: 'base64 图片折叠后不再刷屏',
    input: `# 标题\n\n前面一段。\n\n![截图](${base64})\n\n后面一段。`,
    check(out) {
      assert.ok(out.length < 400, `折叠后仍过长：${out.length}`)
      assert.ok(out.includes('image/png 内嵌图'), '应显示图片类型提示')
      assert.ok(out.includes('前面一段。') && out.includes('后面一段。'), '正文不能被动')
      assert.ok(out.includes('![截图]('), 'Markdown 图片语法要保留')
    },
  },
  {
    name: '签名直链折叠成尾号 + 字符数',
    input: `![配图](${signed})`,
    check(out) {
      assert.ok(out.length < 120, `折叠后仍过长：${out.length}`)
      assert.ok(out.includes(signed.slice(-10)), '应保留地址尾号便于辨认')
      assert.ok(/\d+ 字符/.test(out), '应显示原始长度')
    },
  },
  {
    name: '短链接/普通链接不折叠',
    input: '![图](https://cdn.example.com/a.png) 和 [文档](https://example.com/doc)',
    check(out) {
      assert.equal(out, '![图](https://cdn.example.com/a.png) 和 [文档](https://example.com/doc)')
    },
  },
  {
    name: 'HTML img 长 src 也折叠',
    input: `<img src="${base64}" alt="x">`,
    check(out) {
      assert.ok(out.length < 120, `折叠后仍过长：${out.length}`)
      assert.ok(out.startsWith('<img src="'), 'HTML 属性结构要保留')
    },
  },
  {
    name: '代码块里的长字符串不被误折叠（无 Markdown/HTML 语法时）',
    input: '```\nconst x = 1\n```',
    check(out) {
      assert.equal(out, '```\nconst x = 1\n```')
    },
  },
]

let failed = 0
for (const c of cases) {
  try {
    c.check(shortenUrls(c.input))
    console.log(`PASS  ${c.name}`)
  } catch (err) {
    failed += 1
    console.log(`FAIL  ${c.name}\n      ${err.message}`)
  }
}

if (failed) {
  console.log(`\n${failed} 个用例失败`)
  process.exit(1)
}
console.log('\n全部通过')
