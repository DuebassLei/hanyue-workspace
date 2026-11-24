import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/CharactersView.vue', import.meta.url), 'utf8')

test('reference image angle keeps its grid column even without a clear button', () => {
  assert.match(source, /v-if="image\.assetId"[\s\S]*?class="image-clear"/)
  assert.match(source, /class="angle-select" aria-label="参考图视角"/)
  assert.match(source, /\.angle-select\s*\{\s*grid-column:\s*3;/)
  assert.match(source, /\.image-clear, \.angle-select, \.image-label,[^\n]*grid-column:\s*auto;/)
})
