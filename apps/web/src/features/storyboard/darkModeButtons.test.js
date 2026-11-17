import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (file) => readFileSync(resolve(here, file), 'utf8')

test('storyboard project rows inherit theme text color', () => {
  assert.match(read('../../views/StoryboardsView.vue'), /\.project-row\s*\{[^}]*color:\s*inherit/)
})

test('asset board tabs inherit theme text color', () => {
  assert.match(read('AssetBoard.vue'), /\.tabs button\s*\{[^}]*color:\s*inherit/)
})

test('scene selection buttons inherit theme text color', () => {
  assert.match(read('SceneTree.vue'), /\.scene-select\s*\{[^}]*color:\s*inherit/)
})
