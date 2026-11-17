import { createRouter, createWebHistory } from 'vue-router'
import { useSessionStore } from './stores/session'
import { useRbacStore } from './stores/rbac'

/**
 * 路由表。
 *
 * `meta.permission` 是页面级门禁的第二道闸：侧栏已经不显示没有权限的页面，
 * 但用户仍可能从旧链接、书签、命令面板直达，所以守卫必须再挡一次。
 */
const routes = [
  { path: '/', name: 'overview', component: () => import('./views/OverviewView.vue'), meta: { title: '创作总览', icon: '◈', permission: 'overview.view' } },
  { path: '/topics', redirect: { name: 'studio' } },
  { path: '/hot-topics', name: 'hot-topics', component: () => import('./views/HotTopicsView.vue'), meta: { title: '热点选题', icon: '◉', permission: 'hot_topics.view' } },
  { path: '/schedules', name: 'schedules', component: () => import('./views/SchedulesView.vue'), meta: { title: '定时任务', icon: '⏱', permission: 'schedules.use' } },
  { path: '/calendar', name: 'calendar', component: () => import('./views/CalendarView.vue'), meta: { title: '内容日历', icon: '▦', permission: 'calendar.view' } },
  { path: '/studio', name: 'studio', component: () => import('./views/StudioView.vue'), meta: { title: '创作空间', icon: '✎', permission: 'studio.use' } },
  { path: '/cards', name: 'cards', component: () => import('./views/CardsView.vue'), meta: { title: '图文卡片', icon: '▤', permission: 'cards.use' } },
  { path: '/images', name: 'images', component: () => import('./views/ImageGenView.vue'), meta: { title: '图片生成', icon: '✦', permission: 'images.use' } },
  { path: '/stickers', name: 'stickers', component: () => import('./views/StickersView.vue'), meta: { title: '贴图创作', icon: '❖', permission: 'stickers.use' } },
  { path: '/storyboards', name: 'storyboards', component: () => import('./views/StoryboardsView.vue'), meta: { title: '漫画分镜', icon: '▧', permission: 'storyboards.use' } },
  { path: '/videos', name: 'videos', component: () => import('./views/VideosView.vue'), meta: { title: '视频创作', icon: '▶', permission: 'videos.use' } },
  { path: '/novels', name: 'novels', component: () => import('./views/NovelsView.vue'), meta: { title: '小说创作', icon: '✎', permission: 'novels.use' } },
  { path: '/characters', name: 'characters', component: () => import('./views/CharactersView.vue'), meta: { title: '角色库', icon: '☻', permission: 'characters.use' } },
  { path: '/assets', name: 'assets', component: () => import('./views/AssetsView.vue'), meta: { title: '素材图库', icon: '▣', permission: 'assets.use' } },
  { path: '/prompts', name: 'prompts', component: () => import('./views/PromptsView.vue'), meta: { title: '提示词库', icon: '✧', permission: 'prompts.use' } },
  { path: '/handdraw', name: 'handdraw', component: () => import('./views/HanddrawView.vue'), meta: { title: '提示词库', icon: '✧', permission: 'prompts.use' } },
  { path: '/publishing', name: 'publishing', component: () => import('./views/PublishingView.vue'), meta: { title: '发布管理', icon: '▲', permission: 'publishing.view' } },
  { path: '/wechat-layout', name: 'wechat-layout', component: () => import('./views/LayoutView.vue'), meta: { title: '微信排版', icon: '❐', permission: 'wechat_layout.use' } },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('./views/SettingsView.vue'),
    meta: { title: '设置', icon: '⚙', permission: 'settings.view' },
    // 旧链接 / 旧页签里还留着 ?tab=models|logs，两块都已独立成页面，别静默落回第一个分栏
    beforeEnter: (to) => (['models', 'logs'].includes(to.query.tab) ? { name: to.query.tab } : true),
  },
  { path: '/models', name: 'models', component: () => import('./views/ModelsView.vue'), meta: { title: '模型管理', icon: '◎', permission: 'models.view' } },
  { path: '/logs', name: 'logs', component: () => import('./views/LogsView.vue'), meta: { title: '系统日志', icon: '≡', permission: 'logs.view' } },
  {
    path: '/users',
    name: 'users',
    component: () => import('./views/UsersView.vue'),
    meta: { title: '用户管理', icon: '☺', permission: 'rbac.user.manage' },
  },
  {
    path: '/roles',
    name: 'roles',
    component: () => import('./views/RolesView.vue'),
    meta: { title: '角色管理', icon: '⚿', permission: 'rbac.role.manage' },
  },
  {
    path: '/menus',
    name: 'menus',
    component: () => import('./views/MenusView.vue'),
    meta: { title: '菜单管理', icon: '☰', permission: 'rbac.menu.manage' },
  },
  // 权限被人调整但旧链接/书签还在时，落到明确的 403 页，而不是白屏或悄悄跳首页
  { path: '/forbidden', name: 'forbidden', component: () => import('./views/ForbiddenView.vue'), meta: { title: '无权访问', public: true } },
  { path: '/:pathMatch(.*)*', redirect: { name: 'overview' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const session = useSessionStore()
  const rbac = useRbacStore()

  if (to.meta?.public) return true
  // 未登录不在这里跳转：App 会渲染登录页，守卫抢着跳会和 boot 打架
  if (!session.user) return true

  if (!rbac.loaded) await rbac.load()

  const need = to.meta?.permission
  if (need && !rbac.can(need)) {
    return { name: 'forbidden', query: { from: to.fullPath, need } }
  }
  return true
})

export default router
