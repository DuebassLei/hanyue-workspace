import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { JSDOM } from 'jsdom'
import { parse, compileScript } from '@vue/compiler-sfc'

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' })
for (const key of ['window', 'document', 'navigator', 'Element', 'HTMLElement', 'SVGElement', 'Node']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true })
const vue = await import('vue')
const vueUrl = import.meta.resolve('vue')
const queueUrl = new URL('./queue/draftQueue.js', import.meta.url).href
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0)) }

async function mountPanel(extraProps = {}) {
  const calls = [], notices = []
  let fail = false
  globalThis.__draftPanel = { vue, api: {
    wechatAccounts: async () => [{ id: 'account-1', displayName: '公众号一' }],
    review: async () => ({ versionId: 'version-1', platforms: [] }),
    pushDraft: async (id, payload) => { calls.push({ id, payload }); if (fail) throw new Error('账号校验失败'); return { status: 'queued' } },
  } }
  const { descriptor } = parse(readFileSync(new URL('../../components/paper/PaperPublishPanel.vue', import.meta.url), 'utf8'))
  let { content } = compileScript(descriptor, { id: 'draft-panel', inlineTemplate: true })
  content = content.replaceAll(/from ["']vue["']/g, `from ${JSON.stringify(vueUrl)}`)
    .replace(/import \{ NButton[^\n]+/, `const { h } = globalThis.__draftPanel.vue;
const NButton = { props:['disabled'], setup(p,{attrs,slots}) { return () => h('button',{...attrs,disabled:p.disabled},slots.default?.()) } };
const NSelect = { props:['value','options','disabled'], emits:['update:value'], setup(p,{attrs,emit}) { return () => h('select',{...attrs,value:p.value,disabled:p.disabled,onChange:e=>emit('update:value',e.target.value)},p.options.map(o=>h('option',{value:o.value,disabled:o.disabled},o.label))) } };
const NDatePicker = { props:['value','size'], emits:['update:value'], setup(p,{emit}) { return () => h('input',{type:'number',value:p.value,onInput:e=>emit('update:value',Number(e.target.value))}) } };`)
    .replace(/import \{ studioExtraApi \} from [^\n]+/, 'const studioExtraApi = globalThis.__draftPanel.api')
    .replace(/from ['"][^'"]*queue\/draftQueue['"]/, `from ${JSON.stringify(queueUrl)}`)
  const component = (await import(`data:text/javascript;base64,${Buffer.from(content).toString('base64')}#${Math.random()}`)).default
  const root = document.createElement('div'); document.body.append(root)
  const app = vue.createApp(component, { article: { id:'paper-1' }, onNotify: message => notices.push(message), ...extraProps })
  app.mount(root); await flush()
  return { root, calls, notices, setFail: () => { fail = true }, close() { app.unmount(); root.remove() } }
}

test('成稿台立即推送发送真实队列参数且不提示假成功', async () => {
  const panel = await mountPanel()
  try {
    const button = [...panel.root.querySelectorAll('button')].find(b => b.textContent === '立即加入推送队列')
    assert.equal(button.disabled, false)
    button.click(); await flush()
    assert.deepEqual(panel.calls[0], { id:'paper-1', payload:{ platforms:['wechat_official'], accountId:'account-1', scheduledAt:null } })
    assert.match(panel.notices[0], /已入队，尚未推送成功/)
    assert.ok(panel.root.querySelector('option[value="douyin"]').disabled)
  } finally { panel.close() }
})

test('提交前复核父级守卫：有未保存改动时不再发请求', async () => {
  const panel = await mountPanel({ beforeSubmit: () => false })
  try {
    const button = [...panel.root.querySelectorAll('button')].find(b => b.textContent === '立即加入推送队列')
    button.click(); await flush()
    assert.equal(panel.calls.length, 0)
    assert.match(panel.root.textContent, /未保存的修改/)
    assert.equal(panel.notices.length, 0)
  } finally { panel.close() }
})

test('定时推送需未来时间；后端失败展示错误不提示入队成功', async () => {
  const panel = await mountPanel()
  try {
    const select = panel.root.querySelector('select[aria-label="推送方式"]')
    select.value = 'scheduled'; select.dispatchEvent(new dom.window.Event('change')); await flush()
    const button = [...panel.root.querySelectorAll('button')].find(b => b.textContent === '创建定时推送任务')
    button.click(); await flush()
    assert.equal(panel.calls.length, 0)
    assert.match(panel.root.textContent, /晚于当前时间/)
    const input = panel.root.querySelector('input'); const future = Date.now() + 3600000
    input.value = String(future); input.dispatchEvent(new dom.window.Event('input')); await flush()
    panel.setFail(); [...panel.root.querySelectorAll('button')].find(b => b.textContent === '创建定时推送任务').click(); await flush()
    assert.equal(panel.calls.length, 1, panel.root.textContent)
    assert.equal(panel.calls[0].payload.scheduledAt, new Date(future).toISOString())
    assert.match(panel.root.textContent, /账号校验失败/)
    assert.equal(panel.notices.length, 0)
  } finally { panel.close() }
})
