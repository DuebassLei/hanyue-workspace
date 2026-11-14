import test from 'node:test'
import assert from 'node:assert/strict'
import { applyGenerationQueued, hasActiveGeneration, isGenerationActive } from '../src/features/storyboard/generationState.js'

test('queued storyboard generation is immediately visible and blocks duplicate starts', () => {
  const shot = {
    sketchStatus: 'pending',
    sketchJobId: null,
    finalStatus: 'pending',
    finalJobId: null,
  }

  const next = applyGenerationQueued(shot, 'sketch', { jobId: 'job-1', status: 'queued' })

  assert.equal(next.sketchStatus, 'queued')
  assert.equal(next.sketchJobId, 'job-1')
  assert.equal(isGenerationActive(next, 'sketch'), true)
  assert.equal(isGenerationActive(next, 'final'), false)
  assert.equal(hasActiveGeneration(next), true)
  assert.equal(isGenerationActive(next, 'other'), false)
})
