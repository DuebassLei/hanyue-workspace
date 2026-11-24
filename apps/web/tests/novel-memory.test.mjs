import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<body></body>', { url: 'http://localhost' })
for (const key of ['window', 'document', 'Element', 'SVGElement']) globalThis[key] = dom.window[key]
const vue = await import('vue')
const source = readFileSync(new URL('../src/composables/useNovel.js', import.meta.url), 'utf8')
  .replace(/from ['"]vue['"]/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replace(/import \{ api, getToken \} from [^\n]+/, 'const api = new Proxy({}, { get: (_, key) => globalThis.novelTest.api[key] }); const getToken = () => globalThis.novelTest.getToken()')
  .replace(/import \{ useModelSelectionStore \} from [^\n]+/, 'const useModelSelectionStore = () => globalThis.novelTest.useModelSelectionStore()')
globalThis.novelTest = { api: {}, getToken: () => '', useModelSelectionStore: () => ({}) }
const { useNovel } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r }); return { promise, resolve } }

function setup(api) {
  globalThis.novelTest = { api, getToken: () => '', useModelSelectionStore: () => ({}) }
  let store
  const app = vue.createApp({ setup() { store = useNovel(); return () => null } })
  app.mount(document.createElement('div'))
  return { store, app }
}

test('续写等待设定保存并选中新章节', async () => {
  const save = deferred(), calls = []
  const project = { id: 'a', chapters: [], characters: [], aliases: [] }
  const api = {
    get: async () => ({ ...project, chapters: [{ id: 'new', status: 'pending' }] }),
    patch: async () => { calls.push('save'); return save.promise },
    post: async () => { calls.push('continue'); return { jobId: 'job', chapterId: 'new' } },
  }
  const { store, app } = setup(api)
  try {
    store.project.value = project
    const saving = store.patchProject({ rules: {} })
    const starting = store.continueNext()
    await Promise.resolve()
    assert.deepEqual(calls, ['save'])
    save.resolve({ id: 'a' })
    await saving; await starting
    assert.deepEqual(calls, ['save', 'continue'])
    assert.equal(store.selectedChapterId.value, 'new')
  } finally { app.unmount() }
})

test('较早项目请求不会覆盖后来打开的项目', async () => {
  const a = deferred(), b = deferred()
  const { store, app } = setup({ get: (url) => url.endsWith('/a') ? a.promise : b.promise })
  try {
    const first = store.loadProject('a'), second = store.loadProject('b')
    b.resolve({ id: 'b', chapters: [] }); await second
    a.resolve({ id: 'a', chapters: [] }); await first
    assert.equal(store.project.value.id, 'b')
  } finally { app.unmount() }
})
