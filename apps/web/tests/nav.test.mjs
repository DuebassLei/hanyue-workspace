import test from 'node:test'
import assert from 'node:assert/strict'
import { NAV_GROUPS, breadcrumbsFor, flattenWorkspaceMenus, navEntries } from '../src/ui/nav.js'

test('workspace fallback pages are flat and retain every feature', () => {
  const workspace = NAV_GROUPS.find((group) => group.key === 'workspace')
  assert.deepEqual(workspace.items.map((item) => item.name), [
    'studio', 'paper', 'originality', 'prompts', 'articles', 'cards', 'images',
    'stickers', 'storyboards', 'assets', 'novels', 'characters',
  ])
  assert.ok(workspace.items.every((item) => !item.children))
})

test('server workspace categories flatten recursively without changing other groups or source data', () => {
  const page = { name: 'articles', label: '文章管理', permission: 'studio.use' }
  const groups = [
    { key: 'workspace', label: '工作区', items: [
      { name: 'studio', label: '创作空间' },
      { name: 'group-articles', children: [{ name: 'nested', children: [page] }] },
      { name: 'prompts', label: '提示词库', aliases: ['handdraw'] },
    ] },
    { key: 'system', items: [{ name: 'custom', children: [{ name: 'settings' }] }] },
  ]
  const before = structuredClone(groups)
  const flattened = flattenWorkspaceMenus(groups)
  assert.deepEqual(flattened[0].items.map((item) => item.name), ['studio', 'articles', 'prompts'])
  assert.equal(flattened[0].items[1], page)
  assert.equal(flattened[1], groups[1])
  assert.deepEqual(groups, before)
  assert.deepEqual(flattenWorkspaceMenus(flattened), flattened)
  assert.deepEqual(navEntries(flattened).find((item) => item.name === 'articles').ancestors, ['workspace'])
  assert.deepEqual(breadcrumbsFor('articles', '', flattened), [
    { label: '工作区' }, { label: '文章管理', name: 'articles' },
  ])
  assert.deepEqual(breadcrumbsFor('handdraw', '手绘风格', flattened), [
    { label: '工作区' }, { label: '提示词库', name: 'prompts' }, { label: '手绘风格' },
  ])
})

test('empty workspace and missing workspace are supported', () => {
  assert.deepEqual(flattenWorkspaceMenus([]), [])
  assert.deepEqual(flattenWorkspaceMenus([{ key: 'workspace', items: [] }]), [
    { key: 'workspace', items: [] },
  ])
})
