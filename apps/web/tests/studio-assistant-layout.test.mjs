import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const studio = readFileSync(new URL('../src/views/StudioView.vue', import.meta.url), 'utf8')

test('medium-width studio keeps AI assistant beside the editor', () => {
  assert.match(studio, /\.studio-tools\s*\{[^}]*position: sticky;[^}]*grid-column: 2;[^}]*grid-row: 1;/s)
  assert.match(studio, /\.studio-tools\s*\{[^}]*max-height: calc\(100vh - 124px\);[^}]*overflow-y: auto;/s)
})
