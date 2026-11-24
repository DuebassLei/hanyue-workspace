import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const paper = readFileSync(new URL('../src/views/PaperView.vue', import.meta.url), 'utf8')
const studio = readFileSync(new URL('../src/api/studio.js', import.meta.url), 'utf8')

test('writing tools send selected writing skills for generation, not markdown import', () => {
  assert.match(paper, /skill\.skillType === 'writing' && skill\.status === 'ready'/)
  assert.match(paper, /skillIds: createForm\.markdown\.trim\(\) \? \[\] : selectedSkillIds\.value/)
  assert.match(paper, /studioApi\.draft\(currentArticle\.id, writerStyle\.value, selectedSkillIds\.value\)/)
  assert.match(paper, /最多 5 个/)
  assert.match(studio, /draft: \(id, style, skillIds = \[\]\)/)
})
