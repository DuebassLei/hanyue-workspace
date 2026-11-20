import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const studio = readFileSync(new URL('../src/views/StudioView.vue', import.meta.url), 'utf8')

test('shell wrapper is fixed and content scroll container is the single page scroller', () => {
  assert.match(app, /\.main > :deep\(\.n-layout-scroll-container\)\s*\{[^}]*display: flex;[^}]*overflow: hidden;/s)
  assert.match(app, /\.main-content\s*\{[^}]*flex: 1 1 0;[^}]*min-height: 0;[^}]*height: auto;[^}]*overflow: hidden;/s)
  assert.match(app, /\.main-content > :deep\(\.n-layout-scroll-container\)\s*\{[^}]*overflow-y: auto;/s)
})
test('studio navigation and tools do not create nested page scrollers', () => {
  assert.match(studio, /\.studio-drafts\s*\{[^}]*overflow: visible;/s)
  assert.match(studio, /\.studio-tab-panel\s*\{[^}]*overflow: visible;/s)
  assert.match(studio, /\.studio-drafts,\s*\.studio-tools\s*\{[^}]*max-height: none;/s)
})
