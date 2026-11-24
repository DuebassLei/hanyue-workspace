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
let paperError = false
const api = {
  async get(path) {
    requests.push(path)
    if (path === '/wechat-mp/status') return { configured: true, account: { id: 'account-1' } }
    if (path === '/platform-accounts') return [{ id: 'account-1', platform: 'wechat_official', status: 'connected', displayName: '测试账号' }]
    if (path.startsWith('/publishing/jobs') || path.startsWith('/assets')) return []
    throw new Error(`意外的请求：${path}`)
  },
  async getPage() { return { data: [{ id: 'article-1', title: '测试文章', versionNo: 7 }], nextCursor: '' } },
  async post(path) {
    requests.push(path)
    if (paperError) throw new Error('打不开稿件')
    return { id: 'paper-1' }
  },
}
globalThis.__publishTest = { vue, api, media: {} }
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

test('文章管理不再提供独立排版动作，标题和成稿台按钮进入同一稿件', async () => {
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
  const findButton = (text) => [...root.querySelectorAll('button')].find((item) => item.textContent.trim() === text)
  assert.equal(findButton('排版预览'), undefined)
  for (const label of ['测试文章', '进入成稿台']) {
    destination = null
    const button = findButton(label)
    assert.ok(button)
    button.click()
    await flush()
    assert.deepEqual(destination, { name: 'paper', query: { id: 'paper-1' } })
  }
  assert.equal(requests.filter((path) => path === '/studio/articles/from-content/article-1').length, 2)
  assert.ok(!requests.includes('/content/article-1'))
  destination = null
  paperError = true
  findButton('进入成稿台').click()
  await flush()
  assert.equal(destination, null)
  paperError = false
  app.unmount()
  root.remove()
})

const mediaInputStubs = `
const { h } = globalThis.__publishTest.vue;
const NButton = { props: ['disabled'], setup(props, { slots }) { return () => h('button', { disabled: props.disabled }, slots.default?.()) } };
const NInput = { props: ['value', 'disabled', 'size', 'inputProps'], emits: ['update:value'], setup(props, { emit }) { return () => h('input', { ...props.inputProps, value: props.value, disabled: props.disabled, onInput: (event) => emit('update:value', event.target.value) }) } };
const NSelect = { props: ['value', 'options', 'disabled'], emits: ['update:value'], setup(props, { emit }) { return () => h('select', { value: props.value ?? '', disabled: props.disabled, onChange: (event) => emit('update:value', event.target.value) }, [h('option', { value: '' }), ...props.options.map((item) => h('option', { value: item.value, disabled: item.disabled }, item.label))]) } };
const NModal = { render: () => null };
`

async function mountComponent(component, props) {
  const root = document.createElement('div')
  document.body.appendChild(root)
  const app = vue.createApp(component, props)
  app.mount(root)
  await flush()
  return { root, close() { app.unmount(); root.remove() } }
}

async function mountMediaPanel(capabilities, calls) {
  globalThis.__publishTest.media = {
    api: { get: async (path) => path === '/studio/image-capabilities' ? capabilities : [] },
    studioExtraApi: {
      renderOptions: async () => ({}),
      generateCover: async (_id, payload) => { calls.push(payload) },
    },
  }
  const component = await loadComponent('../../components/paper/PaperMediaPanel.vue', [
    [/import \{ NButton[^\n]+/, mediaInputStubs],
    [/import \{ api, apiUrl \} from [^\n]+/, 'const api = { get: (...args) => globalThis.__publishTest.media.api.get(...args) }; const apiUrl = (url) => url'],
    [/import \{ studioExtraApi \} from [^\n]+/, 'const studioExtraApi = { renderOptions: (...args) => globalThis.__publishTest.media.studioExtraApi.renderOptions(...args), generateCover: (...args) => globalThis.__publishTest.media.studioExtraApi.generateCover(...args) }'],
    [/import PaperSvgCardPicker from [^\n]+/, 'const PaperSvgCardPicker = NModal'],
    [/import PaperAssetPicker from [^\n]+/, 'const PaperAssetPicker = NModal'],
  ])
  return mountComponent(component, { section: 'cover', article: { id: 'paper-1' } })
}

test('封面仅展示并默认选用模型支持的预设，手输必须精确匹配，插图预设不变', async () => {
  const calls = []
  const { root, close } = await mountMediaPanel({ available: true, ratios: ['4:3', '1:1', '5:4'] }, calls)
  try {
    const cover = root.querySelector('#paper-media-cover')
    const preset = cover.querySelector('select.ratio')
    const generate = [...cover.querySelectorAll('button')].find((item) => item.textContent.trim() === '生成封面')
    assert.deepEqual([...preset.options].filter((item) => item.value).map((item) => item.value), ['4:3', '1:1'])
    assert.equal(preset.value, '4:3')
    assert.equal(generate.disabled, false)
    generate.click()
    await flush()
    assert.equal(calls[0].aspectRatio, '4:3')

    const input = cover.querySelector('input[aria-label="封面比例"]')
    async function type(value) {
      input.value = value
      input.dispatchEvent(new dom.window.Event('input', { bubbles: true }))
      await vue.nextTick()
    }
    for (const value of ['2.35:1', '10:8', '5x4', '']) {
      await type(value)
      assert.equal(generate.disabled, true)
      assert.match(cover.textContent, /不支持|请输入/)
      generate.click()
    }
    assert.equal(calls.length, 1)
    await type(' 5:4 ')
    assert.equal(generate.disabled, false)
    generate.click()
    await flush()
    assert.deepEqual(calls.map(({ aspectRatio }) => aspectRatio), ['4:3', '5:4'])

    const inline = root.querySelector('#paper-media-inline select.ratio')
    assert.equal(inline.value, '16:9')
    assert.equal(inline.querySelector('option[value="16:9"]').disabled, true)
    assert.equal(inline.querySelector('option[value="4:3"]').disabled, false)
    assert.equal(inline.querySelector('option[value="5:4"]'), null)
  } finally {
    close()
  }
})

test('没有已确认比例时不允许生图封面，仍然可以从图库选封面', async () => {
  const { root, close } = await mountMediaPanel({ available: true, ratios: [] }, [])
  try {
    const cover = root.querySelector('#paper-media-cover')
    const button = (title) => [...cover.querySelectorAll('button')].find((item) => item.textContent.trim() === title)
    assert.equal(button('生成封面').disabled, true)
    assert.equal(button('从素材图库选').disabled, false)
    assert.match(cover.textContent, /模型管理/)
  } finally {
    close()
  }
})

test('SVG 卡样例随选中卡型直接显示和切换', async () => {
  const component = await loadComponent('../../components/paper/PaperSvgCardPicker.vue', [
    [/import \{ NButton[^\n]+/, mediaInputStubs],
    [/import PaperParagraphPickControls from [^\n]+/, 'const PaperParagraphPickControls = NModal'],
  ])
  const catalog = [
    { id: 'svg-table', name: '表格', previewHtml: '<svg>表格</svg>' },
    { id: 'svg-steps', name: '步骤', previewHtml: '<svg>步骤</svg>' },
  ]
  const { root, close } = await mountComponent(component, { catalog })
  try {
    assert.equal(root.querySelector('iframe'), null)
    const select = root.querySelector('select')
    for (const card of catalog) {
      select.value = card.id
      select.dispatchEvent(new dom.window.Event('change', { bubbles: true }))
      await vue.nextTick()
      const frame = root.querySelector('iframe')
      assert.equal(frame.closest('details:not([open])'), null)
      assert.equal(frame.getAttribute('sandbox'), '')
      assert.ok(frame.srcdoc.includes(card.previewHtml))
    }
  } finally {
    close()
  }
})

