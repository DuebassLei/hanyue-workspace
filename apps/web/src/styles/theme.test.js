import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const themePath = resolve(dirname(fileURLToPath(import.meta.url)), 'theme.css')
const source = readFileSync(themePath, 'utf8')

test('theme declares matching browser color schemes', () => {
  assert.match(source, /html\[data-theme='light'\]\s*\{\s*color-scheme:\s*light;/)
  assert.match(source, /html\[data-theme='dark'\]\s*\{\s*color-scheme:\s*dark;/)
})
