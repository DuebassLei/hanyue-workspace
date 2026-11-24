import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<body></body>', { url: 'http://localhost' })
for (const key of ['window', 'document', 'Element', 'SVGElement']) globalThis[key] = dom.window[key]
const vue = await import('vue')
const { descriptor } = parse(readFileSync(new URL('../src/views/LayoutView.vue', import.meta.url), 'utf8'))
const code = compileScript(descriptor, { id: 'layout-confirm-test' }).content
  .replace(/from ['"]vue['"]/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replace(/import \{ useRoute, useRouter, onBeforeRouteLeave, onBeforeRouteUpdate \} from [^\n]+/, 'const { useRoute, useRouter, onBeforeRouteLeave, onBeforeRouteUpdate } = globalThis.layoutTest')
  .replace(/import \{ useLayoutStudio \} from [^\n]+/, 'const { useLayoutStudio } = globalThis.layoutTest')
  .replace(/import \{ confirm \} from [^\n]+/, 'const { confirm } = globalThis.layoutTest')
  .replace(/import \{ STYLE_PRESETS[^\n]+/, 'const STYLE_PRESETS = [], COLOR_PRESETS = [], BANNER_MODES = [], SVG_CARD_PRESETS = []')

test('排版路由守卫等待确认结果并保留取消导航', async () => {
  const dirty = vue.ref(true), articleId = vue.ref('one'), saving = vue.ref(false)
  let leave, update, calls = 0
  globalThis.layoutTest = {
    useRoute: () => ({}), useRouter: () => ({}),
    onBeforeRouteLeave: (fn) => { leave = fn }, onBeforeRouteUpdate: (fn) => { update = fn },
    useLayoutStudio: () => ({ dirty, articleId, saving, options: {}, body: vue.ref('正文'), cardPanel: {} }),
    confirm: async () => { calls++; return false },
  }
  const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)
  component.render = () => null
  const app = vue.createApp(component)
  app.mount(document.createElement('div'))
  try {
    assert.equal(await leave(), false)
    assert.equal(await update({ query: { articleId: 'two' } }, { query: { articleId: 'one' } }), false)
    assert.equal(calls, 2)
    assert.equal(await update({ query: { articleId: 'one' } }, { query: { articleId: 'one' } }), true)
    saving.value = true
    assert.equal(await update({ query: { articleId: 'two' } }, { query: { articleId: 'one' } }), true)
    dirty.value = false
    assert.equal(await leave(), true)
    assert.equal(calls, 2)
  } finally { app.unmount() }
})
