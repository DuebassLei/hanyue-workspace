import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const viewPath = resolve(dirname(fileURLToPath(import.meta.url)), 'VideosView.vue')
const source = readFileSync(viewPath, 'utf8')
const rowMainRule = source.match(/\.row-main\s*\{[^}]*\}/)?.[0] || ''

test('video project row inherits the theme text color in dark mode', () => {
  assert.match(rowMainRule, /color\s*:\s*inherit/)
})
