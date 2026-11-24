import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const css = readFileSync(new URL('../src/styles/theme.css', import.meta.url), 'utf8')
const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')

test('workspace brand surfaces are light-only and exclude creative canvases', () => {
  assert.match(css, /html\[data-theme='light'\] \.main-content:not\(\.brand-sensitive\) \.card/)
  assert.match(css, /--brand-lime:\s*#eaff70/i)
  for (const route of ['paper', 'cards', 'novels', 'storyboards', 'wechat-layout']) {
    assert.ok(app.includes(`'${route}'`), `${route} should be protected`)
  }
  assert.match(app, /html\[data-theme='light'\]\) \.main-content/)
})
