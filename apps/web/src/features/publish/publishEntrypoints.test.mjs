import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { JSDOM } from 'jsdom'
import { compileScript, parse } from '@vue/compiler-sfc'

const dom = new JSDOM('<!doctype html><html><body></body></html>')
for (const key of ['window', 'document', 'HTMLElement', 'Element', 'SVGElement', 'Node']) globalThis[key] = dom.window[key]
const vue = await import('vue')
const vueUrl = import.meta.resolve('vue')
const requests = []
let articleVersions = [{ id: 'version-7' }]
let detailError = false
const api = {
  async get(path) {
    requests.push(path)
    if (path === '/wechat-mp/status') return { configured: true, account: { id: 'account-1' } }
    if (path === '/platform-accounts') return [{ id: 'account-1', platform: 'wechat_official', status: 'connected', displayName: '测试账号' }]
    if (path.startsWith('/publishing/jobs') || path.startsWith('/assets')) return []
    if (detailError) throw new Error('文章读取失败')
    return { item: { id: 'article-1' }, versions: articleVersions }
  },
  async getPage() { return { data: [{ id: 'article-1', title: '测试文章', versionNo: 7 }], nextCursor: '' } },
  async post(path, payload) { requests.push(path); pushPayload = payload; return { id: 'job-1' } },
}
globalThis.__publishTest = { vue, api }
const importCode = (source) => import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0)) }

async function loadComponent(relativePath, replacements) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  let { content } = compileScript(descriptor, { id: 'publish-test', inlineTemplate: true })
  content = content.replaceAll(/from ["']vue["']/g, `from ${JSON.stringify(vueUrl)}`)
  for (const [pattern, replacement] of replacements) content = content.replace(pattern, replacement)
  return (await importCode(content)).default
}

const buttonStubs = `const { h } = globalThis.__publishTest.vue; const NButton = { setup(_, { slots, attrs }) { return () => h('button', attrs, slots.default?.()) } }; const NCheckbox = NButton; const NInput = NButton; const NSelect = NButton; const NSwitch = NButton;`

test('文章排版入口获取真实版本后跳转独立工作台', async () => {
  let destination
  globalThis.__publishTest.router = { push(value) { destination = value }, replace() {} }
  const component = await loadComponent('../../views/ArticlesView.vue', [
    [/import \{ useRoute, useRouter \} from [^\n]+/, `const useRoute = () => ({ query: {} }); const useRouter = () => globalThis.__publishTest.router`],
    [/import \{ NButton[^\n]+/, buttonStubs],
    [/import \{ api \} from [^\n]+/, 'const { api } = globalThis.__publishTest'],
    [/import \{ confirm, toast \} from [^\n]+/, 'const confirm = async () => true; const toast = () => {}'],
    [/import \{ usePermission \} from [^\n]+/, "const usePermission = () => ({ disabledReason: () => '' })"],
    [/import StatusTag from [^\n]+/, 'const StatusTag = NButton'],
  ])
  const root = document.createElement('div')
  document.body.appendChild(root)
  const app = vue.createApp(component)
  app.mount(root)
  await flush()
  const button = [...root.querySelectorAll('button')].find((item) => item.textContent.trim() === '排版预览')
  assert.ok(button)
  button.click()
  await flush()
  assert.deepEqual(destination, { path: '/wechat-layout', query: { articleId: 'article-1', versionId: 'version-7' } })
  assert.ok(requests.includes('/content/article-1'))
  articleVersions = []
  button.click()
  await flush()
  assert.deepEqual(destination, { path: '/wechat-layout', query: { articleId: 'article-1' } })
  destination = null
  detailError = true
  button.click()
  await flush()
  assert.equal(destination, null)
  detailError = false
  app.unmount()
  root.remove()
})

