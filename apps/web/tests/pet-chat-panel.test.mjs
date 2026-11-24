import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<body></body>')
for (const key of ['window', 'document', 'Document', 'ShadowRoot', 'Element', 'SVGElement']) globalThis[key] = dom.window[key]
const vue = await import('vue')
const { descriptor } = parse(readFileSync(new URL('../src/components/PetChatPanel.vue', import.meta.url), 'utf8'))
const code = compileScript(descriptor, { id: 'pet-chat-panel-test', inlineTemplate: true }).content
  .replace(/from ['"]vue['"]/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replace(/^import \{ (api|studioApi) \} from [^\n]+/gm, 'const $1 = new Proxy({}, { get: (_, key) => globalThis.petChatPanelTest.$1[key] })')
const { default: PetChatPanel } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)

const settle = () => new Promise((resolve) => setImmediate(resolve))
const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
const markdown = '# 初稿标题\n\n初稿正文'
const reply = (action, result, content = '回复') => ({ role: 'assistant', content, metadata: { action, result } })
const conversation = (id, ...messages) => {
  const content = messages.findLast((msg) => msg.role === 'assistant' && msg.metadata.action === 'create_article' && msg.metadata.result?.trim())?.metadata.result
  return { id, messages, activeSkillIds: [], lastGeneration: content ? { action: 'create_article', content, title: '初稿标题' } : {} }
}
const history = (...args) => [conversation(...args)]

async function mount({ props = {}, getHistory = () => [], post, create } = {}) {
  const host = document.createElement('div')
  const calls = { posts: [], creates: [], created: [], chats: [], clears: 0 }
  globalThis.petChatPanelTest = {
    api: {
      get: async (path) => path === '/skill-center/skills'
        ? [{ id: 'style', name: '叙事风格', skillType: 'writing' }]
        : getHistory(new URL(path, 'https://test.invalid').searchParams.get('studio_article_id')),
      post: async (path, payload) => {
        calls.posts.push({ path, payload })
        return post?.(path, payload) ?? conversation('sent', reply('create_article', markdown, markdown))
      },
    },
    studioApi: { create: async (payload) => { calls.creates.push(payload); return create ? create(payload) : { id: 'saved' } } },
  }
  const state = vue.reactive({ studioArticleId: 'a', articleTitle: '当前稿件', mode: 'create', ...props })
  const app = vue.createApp({ render: () => vue.h(PetChatPanel, {
    ...state,
    onCreated: (id) => calls.created.push(id),
    onChat: (text) => calls.chats.push(text),
    onClearSelection: () => calls.clears++,
  }) })
  app.mount(host)
  await settle()
  const button = (label) => [...host.querySelectorAll('button')].find((el) => el.textContent.trim() === label)
  const send = () => host.querySelector('form').dispatchEvent(new dom.window.Event('submit', { cancelable: true }))
  return { host, state, calls, button, send, unmount: () => app.unmount() }
}

function enter(host, value) {
  const textarea = host.querySelector('textarea')
  textarea.value = value
  textarea.dispatchEvent(new dom.window.Event('input'))
}

test('父层控制模式；热点和快捷指令只预填，显式发送后才触发请求或改稿', async () => {
  const { host, state, calls, button, send, unmount } = await mount({ props: {
    hotspot: { title: '热点一', material: '热点来源：原始报道' },
    selectedBlock: { number: 2, text: '原段落摘要' },
  } })
  try {
    assert.match(host.textContent, /当前稿件/)
    assert.equal(host.querySelector('[role="tablist"]'), null)
    assert.match(host.querySelector('textarea').value, /热点一/)
    assert.match(host.textContent, /原始报道/)
    assert.equal(calls.posts.length, 0)
    button('叙事风格').click()
    enter(host, '根据热点写一篇\n保留来源')
    send()
    await settle()
    assert.deepEqual(calls.posts[0].payload, { studioArticleId: 'a', message: '根据热点写一篇\n保留来源', activeSkillIds: ['style'] })
    state.mode = 'edit'
    await settle()
    assert.match(host.textContent, /只改第 2 段 · 原段落摘要/)
    button('压短').click()
    await settle()
    assert.match(host.querySelector('textarea').value, /压到三句以内/)
    assert.deepEqual(calls.chats, [])
    send()
    assert.match(calls.chats[0], /压到三句以内/)
    host.querySelector('[aria-label="取消指定段落，改成整篇指令"]').click()
    assert.equal(calls.clears, 1)
  } finally { unmount() }
})

test('只有合法助手初稿可写入或另存，并展示取消、失败和成功状态', async () => {
  const pending = deferred()
  const applied = []
  const { host, state, calls, button, unmount } = await mount({
    props: { applyBusy: true, applyMarkdown: async (text) => {
      applied.push(text)
      if (applied.length === 1) return pending.promise
      if (applied.length === 2) throw new Error('稿件已变化')
    } },
    getHistory: () => history('draft', reply('create_article', markdown), reply('refine_paragraph', markdown), reply('generate_svg_card', markdown), reply('create_article', '  \n '), reply('query_help', markdown), { ...reply('create_article', markdown), role: 'user' }),
  })
  try {
    for (const label of ['写入当前稿', '另存为新稿']) {
      assert.equal([...host.querySelectorAll('button')].filter((el) => el.textContent.trim() === label).length, 1)
      assert.equal(button(label).disabled, true)
    }
    state.applyBusy = false
    await settle()
    button('写入当前稿').click()
    await settle()
    assert.equal(button('另存为新稿').disabled, true)
    pending.resolve(false)
    await settle()
    assert.match(host.querySelector('[role="status"]').textContent, /取消/)
    assert.deepEqual(calls.created, [])
    button('写入当前稿').click()
    await settle()
    assert.match(host.querySelector('[role="status"]').textContent, /稿件已变化/)
    button('写入当前稿').click()
    await settle()
    assert.match(host.querySelector('[role="status"]').textContent, /已写入当前稿/)
    assert.deepEqual(applied, [markdown, markdown, markdown])
    button('另存为新稿').click()
    await settle()
    assert.equal(calls.creates[0].markdown, markdown)
    assert.equal(calls.creates[0].topic, '初稿标题')
    assert.deepEqual(calls.created, ['saved'])
  } finally { unmount() }
})

test('切稿立即清理旧会话，丢弃乱序上下文和 A→B→A 的迟到回复', async () => {
  const staleSend = deferred()
  const staleHistory = deferred()
  let readsA = 0
  const { host, state, calls, send, unmount } = await mount({
    getHistory: (id) => id === 'b' ? staleHistory.promise : (++readsA === 1 ? history('old-a', reply('create_article', markdown, '旧稿回复')) : history('fresh-a', reply('query_help', null, '最新稿回复'))),
    post: () => staleSend.promise,
  })
  try {
    enter(host, '旧请求')
    send()
    state.studioArticleId = 'b'
    await settle()
    assert.doesNotMatch(host.textContent, /旧稿回复|另存为新稿/)
    state.studioArticleId = 'a'
    await settle()
    staleHistory.resolve(history('b', reply('create_article', markdown, 'B 的迟到历史')))
    staleSend.resolve(conversation('old-a', reply('create_article', markdown, 'A 的迟到回复')))
    await settle()
    assert.match(host.textContent, /最新稿回复/)
    assert.doesNotMatch(host.textContent, /迟到|另存为新稿/)
    enter(host, '新请求')
    send()
    assert.equal(calls.posts[1].path, '/pet/conversations/fresh-a/message')
  } finally { unmount() }
})

test('切稿后另存完成不触发旧会话的 created 事件', async () => {
  const saved = deferred()
  const { host, state, calls, button, unmount } = await mount({
    getHistory: (id) => id === 'a' ? history('a-conv', reply('create_article', markdown)) : [],
    create: () => saved.promise,
  })
  try {
    button('另存为新稿').click()
    state.studioArticleId = 'b'
    await settle()
    saved.resolve({ id: 'old-draft' })
    await settle()
    assert.deepEqual(calls.created, [])
    assert.doesNotMatch(host.textContent, /已另存/)
  } finally { unmount() }
})
