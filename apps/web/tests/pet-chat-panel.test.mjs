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

async function mount({ props = {}, getHistory = () => [], getSkills = () => [{ id: 'style', name: '叙事风格', skillType: 'writing' }], post, create } = {}) {
  const host = document.createElement('div')
  const calls = { posts: [], creates: [], created: [], chats: [], ensures: [], clears: 0 }
  globalThis.petChatPanelTest = {
    api: {
      get: async (path) => path === '/skill-center/skills'
        ? getSkills()
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
    onEnsureArticle: (text) => calls.ensures.push(text),
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

test('助手表单和对话记录直接渲染为可交互 DOM，而不是原生 template 内容', async () => {
  const { host, unmount } = await mount()
  try {
    assert.equal(host.querySelector('template'), null)
    assert.ok(host.querySelector('form'))
    assert.ok(host.querySelector('textarea'))
    assert.ok(host.querySelector('[role="log"]'))
  } finally { unmount() }
})

test('热点和快捷指令只预填，单输入按选段路由', async () => {
  const edits = []
  const { host, state, calls, button, send, unmount } = await mount({ props: {
    hotspot: { title: '热点一', material: '热点来源：原始报道' },
    sendEditRequest: async (text) => edits.push(text),
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
    state.selectedBlock = { number: 2, text: '原段落摘要' }
    await settle()
    assert.match(host.textContent, /已选第 2 段 · 原段落摘要/)
    host.querySelector('.anchor input[type="checkbox"]').checked = true
    host.querySelector('.anchor input[type="checkbox"]').dispatchEvent(new dom.window.Event('change'))
    await settle()
    button('压短').click()
    await settle()
    assert.match(host.querySelector('textarea').value, /压到三句以内/)
    assert.deepEqual(calls.chats, [])
    send()
    await settle()
    assert.match(edits[0], /压到三句以内/)
    host.querySelector('[aria-label="取消指定段落，返回普通对话"]').click()
    assert.equal(calls.clears, 1)
  } finally { unmount() }
})

test('选段默认仍是普通对话，改写须显式勾选且缺少安全回调不降级', async () => {
  const panel = await mount({ props: { mode: 'unified', selectedBlock: { id: 'one', number: 1, text: '正文' }, chatTurns: [{ role: 'user', text: '旧改稿' }] }, getHistory: () => history('conv', reply('query_help', '帮助', '旧对话')) })
  try {
    assert.match(panel.host.textContent, /跨记录顺序不代表时间先后/)
    enter(panel.host, '普通问题'); panel.send(); await settle()
    assert.equal(panel.calls.posts.length, 1)
    const checkbox = panel.host.querySelector('.anchor input[type="checkbox"]')
    checkbox.checked = true; checkbox.dispatchEvent(new dom.window.Event('change')); await settle()
    enter(panel.host, '改写'); panel.send(); await settle()
    assert.equal(panel.calls.posts.length, 1)
    assert.deepEqual(panel.calls.chats, [])
    assert.match(panel.host.textContent, /无法安全改稿/)
    assert.equal(panel.host.querySelector('textarea').value, '改写')
  } finally { panel.unmount() }
})

test('模型选择用于初稿和改稿，自动模式不传模型', async () => {
  const edits = []
  const { host, state, calls, send, unmount } = await mount({ props: {
    modelId: 'platform-1', modelOptions: [{ value: '', label: '自动' }, { value: 'platform-1', label: '平台模型' }],
    sendEditRequest: async (...args) => edits.push(args),
  } })
  try {
    enter(host, '写一篇文章')
    send()
    await settle()
    assert.equal(calls.posts[0].payload.modelId, 'platform-1')
    state.selectedBlock = { number: 1, text: '正文' }
    await settle()
    host.querySelector('.anchor input[type="checkbox"]').checked = true
    host.querySelector('.anchor input[type="checkbox"]').dispatchEvent(new dom.window.Event('change'))
    await settle()
    enter(host, '精简内容')
    send()
    await settle()
    assert.deepEqual(edits[0], ['精简内容', 'platform-1'])
    state.selectedBlock = null
    state.modelId = ''
    await settle()
    enter(host, '再写一篇')
    send()
    await settle()
    assert.equal('modelId' in calls.posts[1].payload, false)
  } finally { unmount() }
})

test('初稿和改稿展示实际模型及降级状态', async () => {
  const msg = reply('create_article', markdown)
  msg.metadata.model = { modelId: 'id-1', modelCode: 'model-one', fallbackUsed: true }
  const { host, state, unmount } = await mount({ getHistory: () => history('conv', msg), props: {
    chatTurns: [{ role: 'assistant', text: '已完成', model: { modelCode: 'model-two', fallbackUsed: true } }],
  } })
  try {
    assert.match(host.textContent, /实际模型：model-one（已降级）/)
    state.mode = 'edit'
    await settle()
    assert.match(host.textContent, /实际模型：model-two（已降级）/)
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

test('改稿失败保留输入，成功后清空；无稿请求父层创建并带回输入', async () => {
  let attempts = 0
  const panel = await mount({ props: { mode: 'unified', selectedBlock: { number: 1, text: '原文' }, sendEditRequest: async () => { if (!attempts++) throw new Error('改稿出错') } } })
  try {
    panel.host.querySelector('.anchor input[type="checkbox"]').checked = true
    panel.host.querySelector('.anchor input[type="checkbox"]').dispatchEvent(new dom.window.Event('change')); await settle()
    enter(panel.host, '请修改'); panel.send(); await settle()
    assert.equal(panel.host.querySelector('textarea').value, '请修改')
    assert.match(panel.host.textContent, /改稿出错/)
    panel.send(); await settle()
    assert.equal(panel.host.querySelector('textarea').value, '')
  } finally { panel.unmount() }
  const empty = await mount({ props: { studioArticleId: '', initialPrompt: '自定义需求' } })
  try { empty.send(); assert.deepEqual(empty.calls.ensures, ['自定义需求']) } finally { empty.unmount() }
})

test('历史和技能独立恢复，历史失败阻止发送，技能最多五个', async () => {
  let historyFailed = true
  let skillsFailed = true
  const panel = await mount({
    getHistory: () => { if (historyFailed) throw new Error('历史错误'); return history('old') },
    getSkills: () => { if (skillsFailed) throw new Error('技能错误'); return Array.from({ length: 6 }, (_, i) => ({ id: String(i), name: `技能${i}` })) },
  })
  try {
    assert.match(panel.host.textContent, /历史错误/); assert.match(panel.host.textContent, /技能错误/)
    enter(panel.host, '需求'); panel.send(); assert.equal(panel.calls.posts.length, 0)
    historyFailed = false; skillsFailed = false
    panel.button('重试历史').click(); panel.button('重试技能').click(); await settle()
    for (let i = 0; i < 5; i++) { panel.button(`技能${i}`).click(); await vue.nextTick() }
    assert.equal(panel.button('技能5').disabled, true)
    panel.send(); await settle()
    assert.equal(panel.calls.posts[0].path, '/pet/conversations/old/message')
    assert.equal(panel.calls.posts[0].payload.activeSkillIds.length, 5)
  } finally { panel.unmount() }
})

test('改稿消息可以重新编辑但不会自动发送', async () => {
  const panel = await mount({ props: { mode: 'edit', chatTurns: [{ role: 'user', text: '原始改稿要求' }], sendEditRequest: async () => { throw new Error('不应发送') } } })
  try {
    panel.button('重新编辑').click(); await vue.nextTick()
    assert.equal(panel.host.querySelector('textarea').value, '原始改稿要求')
    assert.doesNotMatch(panel.host.textContent, /不应发送/)
  } finally { panel.unmount() }
})

test('展示来源和修改预览，接受失败可重试', async () => {
  let attempts = 0
  const proposal = { id: 'p', status: 'pending', changes: [{ blockId: 'b', before: '修改前', after: '修改后' }], receipts: ['一处修改'] }
  const panel = await mount({ props: { mode: 'edit', chatTurns: [{ role: 'assistant', text: '预览', proposal }], onAcceptProposal: async () => { if (!attempts++) throw new Error('接受失败') }, onDiscardProposal: async () => {} } })
  try {
    assert.match(panel.host.textContent, /修改前/); assert.match(panel.host.textContent, /修改后/)
    panel.button('接受修改').click(); await settle(); assert.match(panel.host.textContent, /接受失败/)
    panel.button('接受修改').click(); await settle(); assert.equal(attempts, 2)
  } finally { panel.unmount() }
  const source = await mount({ getHistory: () => history('a', { role: 'assistant', content: '回答', metadata: { action: 'query_article', source: { url: 'https://example.test', status: 'ok', characters: 123, possibly_truncated: true }, context: '正文', result_type: 'answer' } }) })
  try { assert.match(source.host.textContent, /字符数：123/); assert.match(source.host.textContent, /可能已截断/); assert.match(source.host.textContent, /上下文：正文/) } finally { source.unmount() }
  const multi = await mount({ getHistory: () => history('a', { role: 'assistant', content: '回答', metadata: { sources: [{ url: 'first', used_characters: 12 }, { url: 'second', context_truncated: true }] } }) })
  try { assert.match(multi.host.textContent, /first/); assert.match(multi.host.textContent, /second/); assert.match(multi.host.textContent, /使用字符数：12/); assert.match(multi.host.textContent, /上下文已截断/) } finally { multi.unmount() }
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
