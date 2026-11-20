import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/views/StudioView.vue', import.meta.url), 'utf8')
const apply = source.slice(source.indexOf('async function useAiOutput()'), source.indexOf('/** 把已生成'))

test('workspace separates draft and result without unmounting the editor', () => {
  assert.match(source, /id="workspace-draft" v-show="workspaceView === 'draft'"/)
  assert.match(source, /id="workspace-result" v-show="workspaceView === 'result'"/)
  assert.equal((source.match(/ref="aiResultRef"/g) || []).length, 1)
})

test('adoption retains the result and prevents duplicate or cross-document writes', () => {
  assert.doesNotMatch(apply, /aiOutput\.value\s*=\s*''/)
  assert.match(apply, /aiRunning\.value \|\| aiApplying\.value \|\| aiApplied\.value/)
  assert.match(apply, /aiSourceItemId\.value !== detail\.value\.item\.id/)
  assert.match(apply, /aiApplied\.value = true/)
  assert.match(apply, /await flushAutoSave\(\)/)
})

test('replacing a draft requires confirmation and a second identity check', () => {
  const confirmation = apply.indexOf('await confirm(')
  const recheck = apply.indexOf('aiSourceItemId.value !== detail.value?.item.id', confirmation)
  const write = apply.indexOf('draftBody.value = output')
  assert.ok(confirmation > 0 && recheck > confirmation && write > recheck)
})
