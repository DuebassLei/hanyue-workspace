import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/OverviewView.vue', import.meta.url), 'utf8')

test('overview brand styling is scoped to light theme and the pilot page', () => {
  assert.match(source, /:global\(html\[data-theme=['"]light['"]\]\) \.overview-page/)
  assert.match(source, /--overview-lime:\s*#eaff70/i)
  assert.match(source, /:global\(html\[data-theme=['"]light['"]\]\) \.metric-featured/)
  assert.match(source, /:focus-visible/)
})
