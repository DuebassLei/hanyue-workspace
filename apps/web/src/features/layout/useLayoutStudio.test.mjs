import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import * as vue from 'vue'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { JSDOM } from 'jsdom'
import { DEFAULT_OPTIONS, SAMPLE_BODY } from './studioPresets.js'

// 隔离HTTP与组件生命周期；仍执行实际组合函数和Vue响应式，不复制业务实现。
const context = { api: null, cleanups: [] }
const api = Object.fromEntries(['get', 'post', 'put'].map(method => [method, (...args) => context.api[method](...args)]))
globalThis.__layoutStudioTest = {
  ...vue, api, DEFAULT_OPTIONS, SAMPLE_BODY,
  onBeforeUnmount: cleanup => context.cleanups.push(cleanup),
}
const source = await readFile(new URL('./useLayoutStudio.js', import.meta.url), 'utf8')
const executable = `const {computed,onBeforeUnmount,reactive,ref,watch,api,DEFAULT_OPTIONS,SAMPLE_BODY}=globalThis.__layoutStudioTest;\n${source.replace(/^import .*\n/gm, '')}`
const { useLayoutStudio } = await import(`data:text/javascript;base64,${Buffer.from(executable).toString('base64')}`)

function harness(query = { articleId: 'a1', versionId: 'v1' }) {
  const requests = []
  const state = { failConfig: false, versionCount: 0, failRender: false }
  context.api = {
    async get(path) {
      requests.push({ method: 'GET', path })
      return path.startsWith('/content/')
        ? { item: { currentVersionId: 'v1' }, versions: [{ id: 'v1', title: '真实文章标题', body: '没有一级标题的真实正文' }] }
        : { options: { ...DEFAULT_OPTIONS, title: '旧配置标题', style: 'mag', outro: false }, saved: true }
    },
    async post(path, payload) {
      requests.push({ method: 'POST', path, payload })
      if (path.endsWith('/versions')) { state.versionCount++; return { id: 'v2' } }
      if (state.failRender) throw new Error('渲染服务不可用')
      return { html: '<section><p>真实排版正文</p></section>', warnings: [{ field: 'body', level: 'warning', message: '横幅标题已缩略' }], stats: { sourceChars: 10, images: 0, readingMinutes: 1, htmlChars: 40 } }
    },
    async put(path, payload) {
      requests.push({ method: 'PUT', path, payload })
      if (state.failConfig) throw new Error('配置网络中断')
      return { saved: true }
    },
  }
  context.cleanups = []
  const route = vue.reactive({ query })
  const router = { replace: async value => { route.query = value.query; await vue.nextTick() } }
  const scope = vue.effectScope()
  const store = scope.run(() => useLayoutStudio(route, router))
  return { store, state, requests, route, dispose() { context.cleanups.forEach(cleanup => cleanup()); scope.stop() } }
}

const dom = new JSDOM('')
const previousWindow = globalThis.window
const previousDOMParser = globalThis.DOMParser
globalThis.window = dom.window
globalThis.DOMParser = dom.window.DOMParser

await test('独立排版工作台自动化回归', async t => {
  await t.test('真实组件脚本和模板编译通过，保留无脚本沙箱及减弱动画', async () => {
    const filename = new URL('../../views/LayoutView.vue', import.meta.url).pathname
    const contents = await readFile(new URL('../../views/LayoutView.vue', import.meta.url), 'utf8')
    const { descriptor, errors } = parse(contents, { filename })
    assert.deepEqual(errors, [])
    const script = compileScript(descriptor, { id: 'layout-regression' })
    const template = compileTemplate({ source: descriptor.template.content, filename, id: 'layout-regression', compilerOptions: { bindingMetadata: script.bindings } })
    assert.deepEqual(template.errors, [])
    assert.match(contents, /sandbox=""/)
    assert.match(contents, /prefers-reduced-motion/)
    assert.match(contents, /v-model="options.avatarUrl"/)
    assert.match(contents, /html && !rendering && !previewError/)
  })

  await t.test('加载真实版本，文章标题优先旧配置标题，布尔关闭关注可回读', async () => {
    const h = harness()
    try {
      await h.store.load()
      assert.equal(h.store.title.value, '真实文章标题')
      assert.equal(h.store.options.outro, false)
      assert.equal(h.store.dirty.value, false)
      assert.equal(h.store.renderWarnings.value[0], '横幅标题已缩略')
      assert.equal(h.store.renderStats.value.images, 0)
      assert.ok(h.requests.some(item => item.path === '/wechat-mp/layout-studio/articles/a1?versionId=v1'))
      assert.match(h.store.previewDocument.value, /Content-Security-Policy/)
    } finally { h.dispose() }
  })

  await t.test('八字段发包含options.title与avatarUrl，不依赖顶层title', async () => {
    const h = harness()
    try {
      await h.store.load()
      h.store.title.value = '编辑后的标题'
      h.store.options.avatarUrl = 'https://mmbiz.qpic.cn/avatar.png'
      h.store.options.banner = 'neo'
      await h.store.renderPreview()
      const render = h.requests.at(-1).payload
      assert.equal(render.title, undefined)
      assert.equal(render.options.title, '编辑后的标题')
      assert.equal(render.options.avatarUrl, 'https://mmbiz.qpic.cn/avatar.png')
      assert.equal(render.options.outro, false)
      assert.deepEqual(Object.keys(render.options).sort(), ['style', 'theme', 'banner', 'outro', 'brandAuthor', 'brandSlogan', 'avatarUrl', 'title'].sort())
      await h.store.save()
      assert.equal(h.state.versionCount, 1)
      assert.equal(h.store.versionId.value, 'v2')
      assert.equal(h.route.query.versionId, 'v2')
      const saved = h.requests.findLast(item => item.method === 'PUT')
      assert.equal(saved.path, '/wechat-mp/layout-studio/articles/a1?versionId=v2')
      assert.deepEqual(saved.payload.options, render.options)
      assert.equal(h.store.dirty.value, false)
    } finally { h.dispose() }
  })

  await t.test('只修改排版不新建正文版本，正文部分保存失败可安全重试', async () => {
    const h = harness()
    try {
      await h.store.load()
      h.store.options.theme = 'blue'
      await h.store.save()
      assert.equal(h.state.versionCount, 0)
      h.store.body.value = '更新的正文'
      h.state.failConfig = true
      await h.store.save()
      assert.equal(h.state.versionCount, 1)
      assert.equal(h.store.dirty.value, true)
      assert.match(h.store.error.value, /正文新版本已保存/)
      h.state.failConfig = false
      await h.store.save()
      assert.equal(h.state.versionCount, 1)
      assert.equal(h.store.dirty.value, false)
    } finally { h.dispose() }
  })

  await t.test('渲染失败清除旧HTML和自查统计，明确报错', async () => {
    const h = harness()
    try {
      await h.store.load()
      h.state.failRender = true
      await h.store.renderPreview()
      assert.equal(h.store.html.value, '')
      assert.equal(h.store.renderStats.value, null)
      assert.deepEqual(h.store.renderWarnings.value, [])
      assert.match(h.store.previewError.value, /渲染服务不可用/)
    } finally { h.dispose() }
  })

  await t.test('未关联文章仅样文体验，缺失指定版本不静默降级', async () => {
    let h = harness({})
    try {
      await h.store.load()
      assert.equal(h.store.body.value, SAMPLE_BODY)
      await h.store.save()
      assert.equal(h.requests.some(item => item.method === 'PUT'), false)
    } finally { h.dispose() }
    h = harness({ articleId: 'a1', versionId: 'missing' })
    try {
      await h.store.load()
      assert.equal(h.store.ready.value, false)
      assert.match(h.store.error.value, /找不到指定版本/)
    } finally { h.dispose() }
  })

  await t.test('公众号复制写入真实text/html富文本，源码复制另走writeText', async () => {
    const h = harness()
    const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
    const previousClipboardItem = globalThis.ClipboardItem
    let richItems, rawText
    Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { write: async items => { richItems = items }, writeText: async text => { rawText = text } } } })
    globalThis.ClipboardItem = class { constructor(data) { this.data = data } }
    try {
      await h.store.load()
      await h.store.copyRichText()
      assert.equal(await richItems[0].data['text/html'].text(), h.store.html.value)
      assert.equal(await richItems[0].data['text/plain'].text(), '真实排版正文')
      assert.equal(rawText, undefined)
      await h.store.copyHtml()
      assert.equal(rawText, h.store.html.value)
    } finally {
      h.dispose()
      if (navigatorDescriptor) Object.defineProperty(globalThis, 'navigator', navigatorDescriptor)
      else delete globalThis.navigator
      globalThis.ClipboardItem = previousClipboardItem
    }
  })
})

globalThis.window = previousWindow
globalThis.DOMParser = previousDOMParser
delete globalThis.__layoutStudioTest
dom.window.close()
