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
      { name: 'videos', label: '视频创作', short: '视频' },
      { name: 'characters', label: '角色库', short: '角色' },
      { name: 'novels', label: '小说创作', short: '小说' },
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
      // 定时任务是「到点自动干活」，和发布/排版同属运营流程，不放进系统设置里
      { name: 'schedules', label: '定时任务', short: '定时' },
    ],
  },
  {
    key: 'system',
    label: '系统',
    items: [
      { name: 'models', label: '模型管理', short: '模型' },
      { name: 'logs', label: '系统日志', short: '日志' },
      { name: 'settings', label: '设置', short: '我的' },
    ],
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
