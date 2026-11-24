import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compileScript, parse } from '@vue/compiler-sfc'
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<body></body>', { url: 'http://localhost' })
for (const key of ['window', 'document', 'Element', 'SVGElement', 'localStorage']) globalThis[key] = dom.window[key]
const vue = await import('vue')
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise((resolve) => setTimeout(resolve, 0)) }
const { descriptor } = parse(readFileSync(new URL('../src/views/PaperView.vue', import.meta.url), 'utf8'))
const code = compileScript(descriptor, { id: 'hotspot-test' }).content
  .replace(/from ['"]vue['"]/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replace(/import \{ onBeforeRouteLeave, useRoute \} from [^\n]+/, 'const onBeforeRouteLeave = (guard) => { globalThis.paperHotspot.guard = guard }; const useRoute = () => globalThis.paperHotspot.route')
  .replace(/import \{ NButton[^\n]+/, '')
  .replace(/^import \w+ from [^\n]+\.vue['"]\s*$/gm, (line) => `const ${line.split(' ')[1]} = {};`)
  .replace(/import \{ studioApi, uploadReport \} from [^\n]+/, 'const { studioApi } = globalThis.paperHotspot; const uploadReport = () => {}')
  .replace(/import \{ (api, apiUrl|apiUrl|markdownToHtml|confirm, toast|useParagraphDrafts|usePaperParagraphSelection|useModelSelectionStore) \} from [^\n]+/g, 'const { $1 } = globalThis.paperHotspot')

async function mount({ material, blocks = [], notice = null, pet = '1', markdown, confirm = async () => true, saveMarkdown } = {}) {
  let calls = 0
  let snapshot = { id: 'one', title: '来源测试', material, blocks, notice }
  const route = vue.reactive({ query: { id: 'one', pet } })
  const activity = []
  const drafts = vue.ref([])
  const baseline = ['# 来源测试', ...blocks.map((block) => block.text)].join('\n\n') + '\n'
  globalThis.paperHotspot = {
    route, api: { get: async () => [] }, toast() {}, async confirm(...args) { activity.push(['confirm']); return confirm(...args) }, apiUrl() {}, markdownToHtml() {},
    useModelSelectionStore: () => ({ selectedModelId: '', options: [], error: '', load() {} }),
    useParagraphDrafts: () => ({ draftVersion: vue.ref(0), storageFailed: vue.ref(false), draftsFor: () => new Map(), listUnsaved: () => drafts.value, lastParagraph: () => '' }),
    usePaperParagraphSelection: () => ({ active: vue.ref(false), ids: vue.ref([]), reset() {}, stop() {} }),
    studioApi: {
      state: async () => ({ articles: [], writerStyles: [] }),
      get: async (id) => id === 'one' ? snapshot : { id, title: '另一稿', blocks: [] },
      chat: async (...args) => { calls++; activity.push(['chat', ...args]); return snapshot },
      async markdown(id) { activity.push(['get', id]); return { markdown: markdown ?? baseline } },
      async saveMarkdown(...args) {
        activity.push(['patch', ...args])
        return saveMarkdown ? saveMarkdown(...args) : { ...snapshot, blocks: [{ id: 'new', text: args[1] }] }
      },
      draftIfEmpty: async () => {
        if (++calls === 1) snapshot = { ...snapshot, notice: { error: true, text: '失败' } }
        else snapshot = { ...snapshot, notice: null, blocks: [{ id: 'block', text: '生成正文' }] }
        return snapshot
      },
    },
  }
  const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(code + `\n// ${Math.random()}`).toString('base64')}`)
  component.render = () => null
  const app = vue.createApp(component)
  const instance = app.mount(document.createElement('div'))
  await flush()
  return { state: instance.$.setupState, app, route, activity, drafts, baseline, calls: () => calls }
}

test('取消源码丢弃确认时保留编辑内容、稿件选择和路由', async () => {
  const { state, app, activity } = await mount({ confirm: async () => false })
  try {
    state.sourceOriginal = '原文'
    state.sourceText = '未保存'
    state.showArticles = true
    await state.reloadSource()
    assert.equal(state.sourceText, '未保存')
    assert.equal(state.sourceOriginal, '原文')
    assert.equal(await state.openArticle('other'), false)
    assert.equal(state.article.id, 'one')
    assert.equal(state.showArticles, true)
    await state.createArticle()
    assert.equal(await globalThis.paperHotspot.guard(), false)
    assert.equal(state.sourceText, '未保存')
    assert.equal(activity.filter(([kind]) => kind === 'confirm').length, 4)
  } finally { app.unmount() }
})

test('成稿台草稿推送入口在任务执行时禁用，并复用未保存保护', () => {
  assert.match(descriptor.template.content, /:disabled="!article \|\| editingLocked" @click="canUseSavedDraft\(\) && \(showPublish = true\)">推送草稿/)
  assert.match(descriptor.template.content, /title="创建草稿推送任务"/)
  assert.match(descriptor.template.content, /:before-submit="\(\) => !editingLocked && canUseSavedDraft\(\)"/)
})

test('写作工具独立占用侧栏，返回对话不卸载聊天或清空新稿输入', async () => {
  const { state, app } = await mount()
  try {
    state.createForm.topic = '保留的选题'
    state.openCreateTools()
    assert.equal(state.showAssistantTools, true)
    assert.equal(state.showCreate, true)
    assert.equal(state.assistantMounted, true)
    state.openPet()
    assert.equal(state.showAssistantTools, false)
    assert.equal(state.createForm.topic, '保留的选题')
    assert.equal(state.assistantMounted, true)
    assert.match(descriptor.template.content, /v-if="assistantMounted" v-show="!loading && !showAssistantTools"/)
    assert.match(descriptor.template.content, /← 返回对话/)
    assert.doesNotMatch(descriptor.styles[0].content, /max-height:\s*40%/)
  } finally { app.unmount() }
})

const material = '【热点选题】来源测试\n出现平台：微博'

test('热点入口把选题和来源交给创作小助手，不在稿纸自动生成', async () => {
  const { state, app, calls } = await mount({ material })
  try {
    assert.equal(state.tab, 'assistant')
    assert.equal(state.assistantMounted, true)
    assert.deepEqual(state.assistantHotspot, { title: '来源测试', material })
    assert.equal(calls(), 0)
    assert.equal(state.article.blocks.length, 0)
    assert.match(descriptor.template.content, /<PetChatPanel[\s\S]*?:hotspot="assistantHotspot"/)
  } finally { app.unmount() }
})

test('普通助手入口、已有正文以及载入失败的热点均不自动生成', async () => {
  for (const input of [{ material: '普通素材' }, { material, blocks: [{ id: 'kept', text: '已有正文' }] }, { material, notice: { error: true } }, { material, pet: '' }]) {
    const { app, state, calls } = await mount(input)
    try {
      assert.equal(calls(), 0)
      assert.equal(state.tab, input.pet === '' ? 'layout' : 'assistant')
      assert.equal(state.assistantMounted, input.pet !== '')
    } finally { app.unmount() }
  }
})

const existing = [{ id: 'kept', text: '已有正文' }]
const generated = '# 新标题\n\n新正文'

test('助手改稿发送稳定段落 ID 和预览标记，未保存时禁止发送', async () => {
  const { state, app, activity, drafts } = await mount({ blocks: existing })
  try {
    state.selectedBlockId = 'kept'
    await state.sendPetEdit('调整措辞')
    assert.deepEqual(activity.find((item) => item[0] === 'chat'), ['chat', 'one', '调整措辞', { targetBlockId: 'kept', preview: true }])
    drafts.value = [{ blockId: 'kept', text: '未保存' }]
    await assert.rejects(state.sendPetEdit('再改'), /先保存/)
    assert.equal(activity.filter((item) => item[0] === 'chat').length, 1)
  } finally { app.unmount() }
})

test('整稿写回先读取服务端基线，再确认，最后携带基线保存并刷新源码', async () => {
  const { state, app, activity, baseline } = await mount({ blocks: existing })
  try {
    state.sheetMode = 'source'
    assert.equal(await state.applyAssistantMarkdown(generated), true)
    assert.deepEqual(activity, [['get', 'one'], ['confirm'], ['patch', 'one', generated, baseline]])
    assert.equal(state.article.blocks[0].text, generated)
    assert.equal(state.sourceOriginal, state.sourceText)
  } finally { app.unmount() }
})

test('取消覆盖时不保存，空结果不能写回整稿', async () => {
  const { state, app, activity } = await mount({ blocks: existing, confirm: async () => false })
  try {
    assert.equal(await state.applyAssistantMarkdown(generated), false)
    assert.deepEqual(activity, [['get', 'one'], ['confirm']])
    activity.length = 0
    await assert.rejects(() => state.applyAssistantMarkdown('  '), /正文/)
    await assert.rejects(() => state.applyAssistantMarkdown('# 只有标题\n'), /正文/)
    assert.deepEqual(activity, [])
  } finally { app.unmount() }
})

test('有未保存段落、未保存源码或后台任务时，拒绝读取和保存', async () => {
  const { state, app, activity, drafts } = await mount({ blocks: existing })
  try {
    drafts.value = [{}]
    await assert.rejects(() => state.applyAssistantMarkdown(generated), /未保存/)
    drafts.value = []
    state.sourceOriginal = '原文'
    state.sourceText = '未保存'
    await assert.rejects(() => state.applyAssistantMarkdown(generated), /未保存/)
    state.sourceOriginal = state.sourceText
    state.article.job = { kind: 'draft' }
    await assert.rejects(() => state.applyAssistantMarkdown(generated), /任务/)
    assert.deepEqual(activity, [])
  } finally { app.unmount() }
})

test('空稿不用确认也会携带服务端基线保存', async () => {
  const { state, app, activity, baseline } = await mount()
  try {
    assert.equal(await state.applyAssistantMarkdown(generated), true)
    assert.deepEqual(activity, [['get', 'one'], ['patch', 'one', generated, baseline]])
  } finally { app.unmount() }
})

test('确认期间新增未保存的段落，不会被写回覆盖', async () => {
  let finishConfirm
  const { state, app, activity, drafts } = await mount({ blocks: existing, confirm: () => new Promise((resolve) => { finishConfirm = resolve }) })
  try {
    const applying = state.applyAssistantMarkdown(generated)
    await flush()
    drafts.value = [{}]
    finishConfirm(true)
    await assert.rejects(applying, /未保存/)
    assert.deepEqual(activity, [['get', 'one'], ['confirm']])
  } finally { app.unmount() }
})

test('另存完成事件打开新稿，不写回原稿', async () => {
  const { state, app, activity } = await mount({ blocks: existing })
  try {
    const original = state.article
    await state.onPetCreated('two')
    assert.equal(state.article.id, 'two')
    assert.deepEqual(original.blocks, existing)
    assert.deepEqual(activity, [])
  } finally { app.unmount() }
})

test('正文比页面更新时不确认也不写回', async () => {
  const { state, app, activity } = await mount({ blocks: existing, markdown: '# 来源测试\n\n外部更新\n' })
  try {
    await assert.rejects(() => state.applyAssistantMarkdown(generated), /重新核对/)
    assert.deepEqual(activity, [['get', 'one']])
  } finally { app.unmount() }
})

test('确认后服务端发现冲突时不重试，不替换现有快照', async () => {
  const { state, app, activity } = await mount({ blocks: existing, saveMarkdown: async () => { throw Object.assign(new Error('冲突'), { status: 409 }) } })
  try {
    await assert.rejects(() => state.applyAssistantMarkdown(generated), /重新核对/)
    assert.deepEqual(activity.map(([event]) => event), ['get', 'confirm', 'patch'])
    assert.equal(state.article.blocks[0].text, '已有正文')
  } finally { app.unmount() }
})

test('确认期间路由切稿时，不写回旧稿或新稿', async () => {
  let finishConfirm
  const { state, app, route, activity } = await mount({ material, blocks: existing, confirm: () => new Promise((resolve) => { finishConfirm = resolve }) })
  try {
    const applying = state.applyAssistantMarkdown(generated)
    await flush()
    route.query = { id: 'two' }
    await flush()
    finishConfirm(true)
    await assert.rejects(applying, /稿件/)
    await flush()
    assert.equal(state.article.id, 'two')
    assert.equal(state.assistantHotspot, null)
    assert.deepEqual(activity, [['get', 'one'], ['confirm']])
  } finally { app.unmount() }
})
