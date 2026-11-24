import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'

const source = readFileSync(new URL('../src/views/ModelsView.vue', import.meta.url), 'utf8')
const { descriptor, errors } = parse(source)

test('model management view compiles and exposes role-specific controls', () => {
  assert.deepEqual(errors, [])
  assert.doesNotThrow(() => compileScript(descriptor, { id: 'models-management', inlineTemplate: true }))
  assert.match(source, /v-if="isSuperadmin && isOwnConnection\(conn\)"/)
  assert.match(source, /所有者：\{\{ conn\.ownerLabel/)
  assert.match(source, /设为平台兜底/)
  assert.match(source, /取消平台兜底/)
  assert.match(source, /设为默认/)
  assert.match(source, /取消默认/)
  assert.match(source, /<template v-if="isOwnConnection\(conn\)">[\s\S]*?@click="setDefault\(section\.value/)
  assert.match(source, /class="card"/)
  assert.doesNotMatch(source, /class="model-list-head"/)
  assert.doesNotMatch(source, /<h2 class="card-title">平台兜底模型<\/h2>/)
})

test('platform selection uses only own enabled capable models and null clears', () => {
  assert.match(source, /connections\.value\.filter\(isOwnConnection\)/)
  assert.match(source, /if \(!model\.enabled\) continue/)
  assert.match(source, /model\.isTextModel === true/)
  assert.match(source, /includes\('image\.generate'\)/)
  assert.match(source, /api\.patch\('\/models\/platform-defaults'/)
  assert.match(source, /defaultImageModelId'\]: value \|\| null/)
})

test('cross-user mutations and probes require confirmation and send the confirmation flag', () => {
  for (const action of ['拉取模型列表', '测试配置', '修改连接及模型，并测试连接', '测试连接', '删除连接及其模型']) {
    assert.ok(source.includes(`await confirmOtherConnection(${action === '修改连接及模型，并测试连接' ? 'editing' : 'conn'}, '${action}')`), action)
  }
  assert.match(source, /\?confirmCrossUser=true/)
  for (const endpoint of ['/models/probe/models', '/models/probe/test', '/models/connections/', '/models/catalog']) {
    assert.ok(source.includes(endpoint), endpoint)
  }
})
