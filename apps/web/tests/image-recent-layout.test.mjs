import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/ImageGenView.vue', import.meta.url), 'utf8')

test('recent generated images keep consistent thumbnails and prompt space', () => {
  assert.match(source, /<span class="recent-prompt">\{\{ asset\.prompt \|\| '' \}\}<\/span>/)
  assert.match(source, /\.recent-item img\s*\{[^}]*height:\s*120px;[^}]*object-fit:\s*cover;/s)
  assert.match(source, /\.recent-prompt\s*\{[^}]*-webkit-line-clamp:\s*2;[^}]*height:\s*calc\(3em \+ 2 \* var\(--space-2\)\);/s)
})
