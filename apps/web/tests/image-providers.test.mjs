import test from 'node:test'
import assert from 'node:assert/strict'
import { providerOptions, resolveSelectedModelId } from '../src/composables/imageProviders.js'

const providers = [
  { modelId: null, label: '环境变量 · flux' },
  { modelId: 'cat-1', label: '自建 · gpt-image' },
]

test('image providers become select options, env provider uses an empty id', () => {
  assert.deepEqual(providerOptions(providers), [
    { label: '环境变量 · flux', value: '' },
    { label: '自建 · gpt-image', value: 'cat-1' },
  ])
  assert.deepEqual(providerOptions(null), [])
})

test('a missing selection falls back to the first provider', () => {
  assert.equal(resolveSelectedModelId(providers, 'gone'), '')
  assert.equal(resolveSelectedModelId(providers, 'cat-1'), 'cat-1')
  assert.equal(resolveSelectedModelId([], 'cat-1'), '')
})
