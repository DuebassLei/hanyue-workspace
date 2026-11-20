import test from 'node:test'
import assert from 'node:assert/strict'
import { hasSceneStyleOverride, sceneStyleNumber, sceneStylePayload } from '../src/features/storyboard/sceneStyle.js'

test('sceneStylePayload 设置覆盖时携带 style_number', () => {
  assert.deepEqual(sceneStylePayload('S-01'), { style_number: 'S-01' })
  assert.deepEqual(sceneStylePayload('  S-02  '), { style_number: 'S-02' })
})

test('sceneStylePayload 清除覆盖时返回空对象（回落项目级）', () => {
  assert.deepEqual(sceneStylePayload(''), {})
  assert.deepEqual(sceneStylePayload('   '), {})
  assert.deepEqual(sceneStylePayload(null), {})
  assert.deepEqual(sceneStylePayload(undefined), {})
  assert.deepEqual(sceneStylePayload(42), {})
})

test('hasSceneStyleOverride 判定空对象 / null / 非对象为未覆盖', () => {
  assert.equal(hasSceneStyleOverride({ style_number: 'S-01' }), true)
  assert.equal(hasSceneStyleOverride({ foo: 1 }), true)
  assert.equal(hasSceneStyleOverride({}), false)
  assert.equal(hasSceneStyleOverride(null), false)
  assert.equal(hasSceneStyleOverride(undefined), false)
  assert.equal(hasSceneStyleOverride('S-01'), false)
  assert.equal(hasSceneStyleOverride(42), false)
  assert.equal(hasSceneStyleOverride([]), false)
  assert.equal(hasSceneStyleOverride(['S-01']), false)
})

test('sceneStyleNumber 只在覆盖且编号为字符串时返回编号', () => {
  assert.equal(sceneStyleNumber({ style_number: 'S-01' }), 'S-01')
  assert.equal(sceneStyleNumber({ other: 1 }), '')
  assert.equal(sceneStyleNumber({ style_number: 7 }), '')
  assert.equal(sceneStyleNumber(null), '')
  assert.equal(sceneStyleNumber({}), '')
})
