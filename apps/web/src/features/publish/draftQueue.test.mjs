import test from 'node:test'
import assert from 'node:assert/strict'
import { draftPlatformOptions, draftSubmission, actualPushTime, isManualRecord } from './queue/draftQueue.js'
import { canRetry, canCancel, canRecord } from './queue/jobActions.js'

test('only WeChat is enabled and immediate pushes still create queue payloads', () => {
  assert.deepEqual(draftPlatformOptions.filter((item) => !item.disabled).map((item) => item.value), ['wechat_official'])
  assert.deepEqual(draftSubmission({ platform: 'wechat_official', accountId: 'a', mode: 'immediate', scheduleAt: 1 }), {
    platforms: ['wechat_official'], accountId: 'a', scheduledAt: null,
  })
  assert.throws(() => draftSubmission({ platform: 'douyin', accountId: 'a' }), /尚未接入/)
  assert.throws(() => draftSubmission({ platform: 'wechat_official', accountId: '' }), /请选择/)
})

test('scheduled queue payload requires a future timestamp and serializes UTC', () => {
  const now = Date.parse('2026-11-24T12:00:00Z')
  const form = { platform: 'wechat_official', accountId: 'a', mode: 'scheduled' }
  for (const scheduleAt of [null, NaN, now, now - 1]) assert.throws(() => draftSubmission({ ...form, scheduleAt }, now), /晚于当前时间/)
  assert.equal(draftSubmission({ ...form, scheduleAt: now + 60000 }, now).scheduledAt, '2026-11-24T12:01:00.000Z')
})

test('manual records and remote success never expose blind retry', () => {
  assert.equal(canRetry({ target: 'draft', mode: 'auto', status: 'failed' }), true)
  assert.equal(canRetry({ target: 'draft', mode: 'manual', status: 'failed' }), false)
  assert.equal(canRetry({ target: 'draft', status: 'failed', remoteContentId: 'remote' }), false)
  assert.equal(isManualRecord({ mode: 'manual' }), true)
  assert.equal(actualPushTime({ pushedAt: 'actual', completedAt: 'completion' }), 'actual')
})

test('failed or cancelled jobs never report their completion as push time', () => {
  assert.equal(actualPushTime({ status: 'failed', completedAt: 'done' }), null)
  assert.equal(actualPushTime({ status: 'cancelled', completedAt: 'done' }), null)
  assert.equal(actualPushTime({ status: 'drafted', completedAt: 'done' }), 'done')
  assert.equal(actualPushTime(null), null)
})

test('backend retry verdict wins and draft tasks are never manually recorded', () => {
  // draft_result_unknown 等场景后端会明确 canRetry=false，前端不能自行放行
  assert.equal(canRetry({ target: 'draft', status: 'failed', canRetry: false }), false)
  assert.equal(canRetry({ target: 'draft', status: 'needs_attention', canRetry: false }), false)
  assert.equal(canRetry({ target: 'draft', status: 'failed', canRetry: true }), true)
  assert.equal(canCancel({ target: 'draft', status: 'failed' }), true)
  assert.equal(canCancel({ target: 'publish', status: 'failed' }), false)
  assert.equal(canRecord({ target: 'draft', status: 'failed' }), false)
  assert.equal(canRecord({ target: 'publish', status: 'failed' }), true)
})
