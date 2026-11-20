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
let pushPayload
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

test('发布先选择真实平台，关闭后重新打开回到平台选择', async () => {
  const component = await loadComponent('./PublishDrawer.vue', [
    [/import WechatDraftPanel from [^\n]+/, `const WechatDraftPanel = { props: ['open', 'versionId'], setup(props) { return () => props.open ? globalThis.__publishTest.vue.h('div', { 'data-testid': 'wechat-panel', 'data-version-id': props.versionId }, '公众号发布配置') : null } }`],
  ])
  const open = vue.ref(true)
  const root = document.createElement('div')
  document.body.appendChild(root)
  const app = vue.createApp({ setup: () => () => vue.h(component, { open: open.value, versionId: 'version-7' }) })
  app.mount(root)
  assert.match(document.body.textContent, /选择发布平台/)
  document.querySelector('.platform-option').click()
  await vue.nextTick()
  assert.equal(document.querySelector('[data-testid="wechat-panel"]').dataset.versionId, 'version-7')
  assert.equal(document.querySelector('.platform-picker'), null)
  open.value = false
  await vue.nextTick()
  open.value = true
  await vue.nextTick()
  assert.ok(document.querySelector('.platform-picker'))
  app.unmount()
  root.remove()
})

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

test('发布初始化不触发排版，推送仅带正文与素材而不覆盖排版主题', async () => {
  let source = readFileSync(new URL('../../composables/useWechatPublish.js', import.meta.url), 'utf8')
  source = source.replace("from 'vue'", `from ${JSON.stringify(vueUrl)}`)
  source = source.replace(/import \{ api \} from [^\n]+/, 'const { api } = globalThis.__publishTest')
  const { useWechatPublish } = await importCode(source)
  globalThis.__publishTest.useWechatPublish = useWechatPublish
  const publish = useWechatPublish()
  for (const key of ['themes', 'loadThemes', 'wechatThemeId', 'renderLayout', 'scheduleLayout', 'insertSnippet']) {
    assert.equal(key in publish, false)
  }
  await publish.loadWechat()
  assert.equal(requests.includes('/wechat-mp/themes'), false)
  requests.length = 0
  publish.initForm({ title: '标题', body: '发布正文', contentId: 'article-1', versionId: 'version-7' })
  publish.wechatForm.coverId = 'cover-1'
  publish.wechatForm.inlineIds = ['inline-1']
  publish.wechatForm.needOpenComment = true
  await new Promise((resolve) => setTimeout(resolve, 350))
  assert.equal(requests.includes('/wechat-mp/drafts/render'), false)
  const job = await publish.pushWechatDraft()
  assert.equal(job.id, 'job-1')
  assert.equal(pushPayload.contentItemId, 'article-1')
  assert.equal(pushPayload.versionId, 'version-7')
  assert.equal(pushPayload.body, '发布正文')
  assert.deepEqual(pushPayload.assetIds, ['cover-1', 'inline-1'])
  assert.equal(pushPayload.needOpenComment, true)
  assert.equal('themeId' in pushPayload, false)
  await publish.previewWechatDraft()
  assert.equal('needOpenComment' in pushPayload, false)
  assert.equal('onlyFansCanComment' in pushPayload, false)
  assert.equal('idempotencyKey' in pushPayload, false)
  assert.equal(pushPayload.versionId, 'version-7')
  assert.equal('themeId' in pushPayload, false)
  publish.initForm({ title: '无版本稿件', body: '正文', contentId: 'article-1' })
  assert.equal(publish.wechatPayload().versionId, null)
})

test('共享发布流程不保存或提交旧主题字段，草稿正文与素材保持不变', async () => {
  let source = readFileSync(new URL('./flow/usePublishFlow.js', import.meta.url), 'utf8')
  source = source.replace("from 'vue'", `from ${JSON.stringify(vueUrl)}`)
  source = source.replace(/import \{ api \} from [^\n]+/, 'const { api } = globalThis.__publishTest')
  const { usePublishFlow } = await importCode(source)
  const flow = usePublishFlow({ lockedMode: 'draft', lockStep1: true })
  flow.accounts.value = [{ id: 'account-1', platform: 'wechat_official' }]
  flow.reset({ title: '标题', body: '正文', accountIds: ['account-1'], coverAssetId: 'cover-1', inlineAssetIds: ['inline-1'], themeId: 'ink-green' })
  assert.equal('themeId' in flow.form, false)
  await flow.preview()
  assert.equal(requests.at(-1), '/wechat-mp/drafts/preview')
  assert.equal('themeId' in pushPayload, false)
  assert.equal(pushPayload.body, '正文')
  assert.deepEqual(pushPayload.assetIds, ['cover-1', 'inline-1'])
  await flow.submit()
  assert.equal(requests.at(-1), '/wechat-mp/drafts')
  assert.equal('themeId' in pushPayload, false)
  assert.equal(pushPayload.title, '标题')
  assert.equal(flow.results.value[0].ok, true)
})

test('目标方式面板仅保留草稿素材配置，不再显示旧主题选择', async () => {
  const component = await loadComponent('./flow/StepTargetMode.vue', [
    [/import \{ NCheckbox[^\n]+/, `${buttonStubs} const NDatePicker = NButton; const NEmpty = NButton; const NRadio = NButton;`],
  ])
  const root = document.createElement('div')
  document.body.appendChild(root)
  const app = vue.createApp(component, {
    form: { mode: 'draft', accountIds: [], coverAssetId: '', inlineAssetIdsText: '' },
    accounts: [], modeAvailability: {},
  })
  app.mount(root)
  assert.match(root.textContent, /封面图片素材 ID/)
  assert.match(root.textContent, /正文图片素材 ID/)
  assert.doesNotMatch(root.textContent, /排版主题/)
  app.unmount()
  root.remove()
})

test('写作规则保留语气和执行策略，不再声明排版主题规则', async () => {
  const source = readFileSync(new URL('../../views/writing-rules/SkillRulesPanel.vue', import.meta.url), 'utf8')
  let script = source.match(/<script setup>([\s\S]*?)<\/script>/)[1]
  script = script.replace(/import[\s\S]*?from ['"][^'"]+['"]\n/g, '')
  const definitions = script.slice(script.indexOf('const GROUPS = ['), script.indexOf('const currentSection ='))
  const { groups, sections } = await importCode(`const ref = (value) => ({ value }); const computed = (fn) => fn;\n${definitions}\nexport { GROUPS as groups, EDITOR_SECTIONS as sections }`)
  assert.ok(groups.some((group) => group.type === 'tone'))
  assert.ok(groups.some((group) => group.type === 'workflow_step'))
  assert.equal(groups.some((group) => group.type === 'theme'), false)
  assert.equal(sections.some((section) => section.types.includes('theme')), false)
  const advanced = sections.find((section) => section.key === 'advanced')
  assert.deepEqual(advanced.types, ['image_policy', 'workflow_step', 'repair_policy'])
  assert.doesNotMatch(source, /case 'theme':|ink-green/)
  const view = readFileSync(new URL('../../views/WritingRulesView.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(view, /row\.ruleType === 'theme'/)
  assert.match(view, /row\.ruleType === 'tone'/)
})

test('公众号配置面板保留发布输入，不再显示或请求排版预览', async () => {
  const component = await loadComponent('./WechatDraftPanel.vue', [
    [/import \{ NAlert[^\n]+/, `${buttonStubs} const NAlert = NButton;`],
    [/import \{ apiUrl \} from [^\n]+/, 'const apiUrl = (value) => value'],
    [/import \{ useWechatPublish \} from [^\n]+/, 'const { useWechatPublish } = globalThis.__publishTest'],
    [/import \{ usePublishFlow \} from [^\n]+/, 'const usePublishFlow = () => ({})'],
    [/import \{ toast \} from [^\n]+/, 'const toast = () => {}'],
  ])
  const open = vue.ref(false)
  const root = document.createElement('div')
  document.body.appendChild(root)
  const app = vue.createApp({ setup: () => () => vue.h(component, { open: open.value, title: '测试文章', body: '正文', contentId: 'article-1', versionId: 'version-7' }) })
  app.mount(root)
  requests.length = 0
  open.value = true
  await flush()
  const panel = document.querySelector('.publish-config-pane')
  assert.ok(panel)
  assert.match(panel.textContent, /目标公众号/)
  assert.match(panel.textContent, /正文/)
  assert.match(panel.textContent, /封面图/)
  assert.doesNotMatch(panel.textContent, /排版主题|插入区块|内容预览/)
  assert.equal(document.querySelector('.publish-preview-pane'), null)
  assert.equal(requests.includes('/wechat-mp/drafts/render'), false)
  app.unmount()
  root.remove()
})
