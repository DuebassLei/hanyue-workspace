/** 主导航。外壳、面包屑、移动底栏都读这一份，不在 App.vue 再写一套。 */

export const NAV_GROUPS = [
  {
    key: 'create',
    label: '创作',
    items: [
      { name: 'overview', label: '创作总览', short: '总览' },
      { name: 'hot-topics', label: '热点选题', short: '热点' },
      { name: 'calendar', label: '内容日历', short: '日历' },
    ],
  },
  {
    key: 'workspace',
    label: '工作区',
    items: [
      { name: 'studio', label: '创作空间', short: '创作' },
      { name: 'cards', label: '图文卡片', short: '卡片' },
      { name: 'images', label: '图片生成', short: '图片' },
      { name: 'stickers', label: '贴图创作', short: '贴图' },
      { name: 'storyboards', label: '漫画分镜', short: '分镜' },
      { name: 'prompts', label: '提示词库', short: '提示词', aliases: ['handdraw'] },
      { name: 'assets', label: '素材图库', short: '图库' },
    ],
  },
  {
    key: 'ops',
    label: '运营',
    items: [
      { name: 'publishing', label: '发布管理', short: '发布' },
      { name: 'wechat-layout', label: '微信排版', short: '排版' },
    ],
  },
  {
    key: 'system',
    label: '系统',
    items: [{ name: 'settings', label: '设置', short: '我的' }],
  },
]

export const NAV_ITEMS = NAV_GROUPS.flatMap((group) =>
  group.items.map((item) => ({ ...item, group: group.label, groupKey: group.key })),
)

export function findNav(routeName) {
  return (
    NAV_ITEMS.find((item) => item.name === routeName || item.aliases?.includes(routeName)) || null
  )
}

/** 两级：分组 / 页面。手绘是提示词库的子页，多一级。 */
export function breadcrumbsFor(routeName, pageTitle) {
  const item = findNav(routeName)
  if (!item) return [{ label: pageTitle || '工作台' }]
  const crumbs = [
    { label: item.group },
    { label: item.label, name: item.name },
  ]
  if (routeName && routeName !== item.name) {
    crumbs.push({ label: pageTitle || routeName })
  }
  return crumbs
}
