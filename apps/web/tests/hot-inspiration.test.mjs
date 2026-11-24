import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { parse, compileTemplate } from '@vue/compiler-sfc'

const view = readFileSync(new URL('../src/views/HotTopicsView.vue', import.meta.url), 'utf8')
const card = readFileSync(new URL('../src/features/hot/HotInspirationCard.vue', import.meta.url), 'utf8')

test('灵感默认显示，可切换列表，两个视图使用同一筛选结果与空态', () => {
  assert.match(view, /viewMode = ref\('inspiration'\)/)
  assert.match(view, /viewMode = 'list'/)
  assert.match(view, /v-for="\(cluster, index\) in visible"/)
  assert.match(view, /v-else-if="!visible\.length"/)
  assert.match(view, /columns: 300px/)
  assert.match(view, /max-width: 560px[\s\S]*columns: 1/)
})

test('灵感卡片透传原有操作及权限、详情状态', () => {
  for (const event of ['write', 'ignore', 'toggle', 'sticker']) {
    assert.ok(card.includes(`emit('${event}', cluster)`))
    assert.ok(view.includes(`@${event}=`))
  }
  for (const prop of ['detail', 'degraded', 'busy', 'detail-loading', 'expanded', 'write-denied', 'sticker-denied']) {
    assert.ok(view.includes(`:${prop}=`))
  }
  assert.match(card, /v-if="expanded"/)
  assert.match(card, /detail\.items/)
  assert.match(card, /Boolean\(writeDenied\)/)
  assert.match(card, /Boolean\(stickerDenied\)/)
})

test('两个组件的模板均可编译', () => {
  for (const source of [view, card]) {
    const parsed = parse(source)
    assert.deepEqual(parsed.errors, [])
    assert.deepEqual(compileTemplate({ source: parsed.descriptor.template.content, filename: 'test.vue', id: 'test' }).errors, [])
  }
})
