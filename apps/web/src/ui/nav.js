/**
 * 主导航**兜底表**。
 *
 * 真相在服务端 `menus` 表（管理员可在「菜单管理」里增删改、调顺序、隐藏）。
 * 这里保留一份静态副本只是为了：后端不可达、或 /rbac/me 失败时外壳仍有骨架，
 * 不至于整站只剩一个空白侧栏。**改动服务端菜单不需要改这个文件**，
 * 但新增页面时两边都要加，否则后端不可达时该页面在侧栏里会暂时消失。
 *
 * `permission` 与后端权限点一一对应，前端只用它做可见性判断。
 */

export const NAV_GROUPS = [
  {
    key: 'create',
    label: '创作',
    items: [
      { name: 'overview', label: '创作总览', short: '总览', permission: 'overview.view' },
      { name: 'hot-topics', label: '热点选题', short: '热点', permission: 'hot_topics.view' },
      { name: 'calendar', label: '内容日历', short: '日历', permission: 'calendar.view' },
    ],
  },
  {
    key: 'workspace',
    label: '工作区',
    items: [
      // 工作区功能直接平铺，不再按内容类型折叠。
      // 成稿台是「逐段改」的主入口；兜底导航漏了它，菜单接口失败时用户就找不到入口
      { name: 'paper', label: '成稿台', short: '成稿', permission: 'studio.use' },
      { name: 'originality', label: '原创检测', short: '检测', permission: 'studio.use' },
      { name: 'prompts', label: '提示词库', short: '提示词', permission: 'prompts.use', aliases: ['handdraw'] },
      { name: 'articles', label: '文章管理', short: '文章', permission: 'studio.use' },
      { name: 'cards', label: '图文卡片', short: '卡片', permission: 'cards.use' },
      { name: 'images', label: '图片生成', short: '图片', permission: 'images.use' },
      { name: 'stickers', label: '贴图创作', short: '贴图', permission: 'stickers.use' },
      { name: 'storyboards', label: '漫画分镜', short: '分镜', permission: 'storyboards.use' },
      { name: 'assets', label: '素材图库', short: '图库', permission: 'assets.use' },
      { name: 'novels', label: '小说创作', short: '小说', permission: 'novels.use' },
      { name: 'characters', label: '角色库', short: '角色', permission: 'characters.use' },
    ],
  },
  {
    key: 'ops',
    label: '运营',
    items: [
      { name: 'publishing', label: '发布管理', short: '发布', permission: 'publishing.view' },
      { name: 'wechat-layout', label: '微信排版', short: '排版', permission: 'wechat_layout.use' },
      // 定时任务是「到点自动干活」，和发布/排版同属运营流程，不放进系统设置里
      { name: 'schedules', label: '定时任务', short: '定时', permission: 'schedules.use' },
    ],
  },
  {
    key: 'rbac',
    label: '权限',
    items: [
      { name: 'users', label: '用户管理', short: '用户', permission: 'rbac.user.manage' },
      { name: 'roles', label: '角色管理', short: '角色', permission: 'rbac.role.manage' },
      { name: 'menus', label: '菜单管理', short: '菜单', permission: 'rbac.menu.manage' },
    ],
  },
  {
    key: 'system',
    label: '系统',
    items: [
      { name: 'models', label: '模型管理', short: '模型', permission: 'models.view' },
      { name: 'logs', label: '系统日志', short: '日志', permission: 'logs.view' },
      { name: 'settings', label: '设置', short: '我的', permission: 'settings.view' },
    ],
  },
]

/** 仅将工作区的分类展开为页面；保留服务端已过滤的可见项、顺序和权限信息。 */
export function flattenWorkspaceMenus(groups) {
  const leaves = (items) => (items || []).flatMap((item) =>
    item.children?.length ? leaves(item.children) : [item],
  )
  return groups.map((group) => group.key === 'workspace'
    ? { ...group, items: leaves(group.items) }
    : group,
  )
}

/**
 * 把「分组 → 分类 → 页面」摊平成带所属分组与祖先链的页面列表。
 * 服务端菜单树（`/rbac/me`）与静态兜底表形状一致，同一个函数处理两者。
 */
export function navEntries(groups) {
  const rows = []
  for (const group of groups) {
    const walk = (items, ancestors) => {
      for (const item of items || []) {
        const path = [...ancestors, item.name]
        if (item.children?.length) {
          walk(item.children, path)
        } else {
          rows.push({
            ...item,
            group: group.label,
            groupKey: group.key,
            ancestors: path.slice(0, -1),
          })
        }
      }
    }
    walk(group.items, [group.key])
  }
  return rows
}

export const NAV_ITEMS = navEntries(NAV_GROUPS)

export function findNav(routeName) {
  return (
    NAV_ITEMS.find((item) => item.name === routeName || item.aliases?.includes(routeName)) || null
  )
}

function findTrail(items, routeName) {
  for (const item of items || []) {
    if (item.children?.length) {
      const nested = findTrail(item.children, routeName)
      if (nested) return [item, ...nested]
    } else if (item.name === routeName || item.aliases?.includes(routeName)) {
      return [item]
    }
  }
  return null
}

/** 面包屑跟随菜单层级：工作区 / 文章 / 文章管理。 */
export function breadcrumbsFor(routeName, pageTitle, groups = NAV_GROUPS) {
  for (const group of groups) {
    const trail = findTrail(group.items, routeName)
    if (!trail) continue
    const crumbs = [{ label: group.label }]
    trail.forEach((entry, index) => {
      const isLeaf = index === trail.length - 1
      crumbs.push({ label: entry.label, ...(isLeaf ? { name: entry.name } : {}) })
    })
    const leaf = trail[trail.length - 1]
    if (routeName && routeName !== leaf.name) {
      crumbs.push({ label: pageTitle || routeName })
    }
    return crumbs
  }
  return [{ label: pageTitle || '工作台' }]
}
