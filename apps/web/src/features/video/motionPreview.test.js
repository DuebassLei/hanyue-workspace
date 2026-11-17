import test from 'node:test'
import assert from 'node:assert/strict'
import { motionPreviewStyle } from './motionPreview.js'

test('still motion uses a subtle breathing preview animation', () => {
  const style = motionPreviewStyle({ motion: 'still', intensity: 0.12 })

  assert.equal(style['--motion-animation'], 'motion-still')
  assert.equal(style['--kb-scale-from'], '0.99')
  assert.equal(style['--kb-scale-to'], '1.01')
})

test('pan diagonal drift is zero at centered vertical focus', () => {
  const style = motionPreviewStyle({ motion: 'pan_left', intensity: 0.2, focus: { x: 0.5, y: 0.5 } })

  assert.equal(style['--kb-dy'], '0.00%')
})

test('pan diagonal drift follows vertical focus direction and intensity', () => {
  const high = motionPreviewStyle({ motion: 'pan_right', intensity: 0.2, focus: { x: 0.5, y: 0.8 } })
  const low = motionPreviewStyle({ motion: 'pan_right', intensity: 0.1, focus: { x: 0.5, y: 0.2 } })

  assert.equal(high['--kb-dy'], '1.80%')
  assert.equal(low['--kb-dy'], '-0.90%')
})
