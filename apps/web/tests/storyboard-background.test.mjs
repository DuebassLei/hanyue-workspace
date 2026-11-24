import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/StoryboardsView.vue', import.meta.url), 'utf8')

test('storyboard neutral surfaces are scoped to the light page, not exports or dark tokens', () => {
  assert.match(source, /:global\(html\[data-theme='light'\]\) \.storyboard-page\s*\{[^}]*--paper-surface:\s*#ffffff;[^}]*--paper-sunken:\s*#f1f2f3;/s)
  assert.match(source, /\.storyboard-page\s*\{\s*min-height:\s*100%;\s*background:\s*var\(--paper-bg\);\s*\}/)
  assert.doesNotMatch(source, /:global\(html\[data-theme='dark'\]\) \.storyboard-page/)
})
