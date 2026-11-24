import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'

for (const name of ['ModelsView', 'SchedulesView', 'SkillCenterView', 'PaperView', 'LayoutView']) {
  test(`${name} uses asynchronous application confirmation`, () => {
    const source = readFileSync(new URL(`../src/views/${name}.vue`, import.meta.url), 'utf8')
    assert.doesNotMatch(source, /window\.(?:alert|confirm|prompt)\s*\(/)
    assert.match(source, /import\s*\{[^}]*\bconfirm\b[^}]*\}\s*from\s*['"]\.\.\/ui\/feedback['"]/)
    const { descriptor, errors } = parse(source)
    assert.deepEqual(errors, [])
    assert.doesNotThrow(() => compileScript(descriptor, { id: name, inlineTemplate: true }))
  })
}
