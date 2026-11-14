import test from 'node:test'
import assert from 'node:assert/strict'
import { shotFramePrompt, shotImages, shotPromptFor, shotStagePrompt } from '../src/features/storyboard/shotImages.js'

const shot = {
  id: 'shot-1',
  frameDescription: '雨夜站台',
  prompt: '',
  sketchUrl: '/api/v1/assets/sketch/raw?sig=1',
  sketchPrompt: '雨夜站台, wide shot\nreference image 1 is character 林夏',
  finalUrls: ['/api/v1/assets/final-1/raw?sig=2', '/api/v1/assets/final-2/raw?sig=3'],
  finalPrompt: '雨夜站台, wide shot\nreference image 1 is the approved sketch.',
}

test('shotImages lists sketch first then every final candidate with its stage', () => {
  assert.deepEqual(shotImages(shot), [
    { url: shot.sketchUrl, label: '草图', stage: 'sketch' },
    { url: shot.finalUrls[0], label: '正式候选 1', stage: 'final' },
    { url: shot.finalUrls[1], label: '正式候选 2', stage: 'final' },
  ])
  assert.deepEqual(shotImages(null), [])
  assert.deepEqual(shotImages({ finalUrls: [null, '/api/v1/assets/x/raw'] }), [
    { url: '/api/v1/assets/x/raw', label: '正式候选 2', stage: 'final' },
  ])
})

test('prompt shown for an image uses that stage real generation prompt', () => {
  assert.equal(shotStagePrompt(shot, 'sketch'), shot.sketchPrompt)
  assert.equal(shotStagePrompt(shot, 'final'), shot.finalPrompt)
  assert.equal(shotPromptFor(shot, 'sketch'), shot.sketchPrompt)
  assert.equal(shotPromptFor(shot, 'final'), shot.finalPrompt)
})

test('prompt falls back to the written frame text before any generation', () => {
  const pending = { frameDescription: '雨夜站台', prompt: '', sketchUrl: null, finalUrls: [] }
  assert.equal(shotStagePrompt(pending, 'sketch'), '')
  assert.equal(shotFramePrompt(pending), '雨夜站台')
  assert.equal(shotPromptFor(pending, 'sketch'), '雨夜站台')
  assert.equal(shotFramePrompt({ frameDescription: '雨夜站台', prompt: '水墨风' }), '水墨风')
})
