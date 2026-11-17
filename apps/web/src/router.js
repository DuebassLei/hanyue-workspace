import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'overview', component: () => import('./views/OverviewView.vue'), meta: { title: '创作总览', icon: '◈' } },
  { path: '/topics', redirect: { name: 'studio' } },
  { path: '/hot-topics', name: 'hot-topics', component: () => import('./views/HotTopicsView.vue'), meta: { title: '热点选题', icon: '◉' } },
  { path: '/schedules', name: 'schedules', component: () => import('./views/SchedulesView.vue'), meta: { title: '定时任务', icon: '⏱' } },
  { path: '/calendar', name: 'calendar', component: () => import('./views/CalendarView.vue'), meta: { title: '内容日历', icon: '▦' } },
  { path: '/studio', name: 'studio', component: () => import('./views/StudioView.vue'), meta: { title: '创作空间', icon: '✎' } },
  { path: '/cards', name: 'cards', component: () => import('./views/CardsView.vue'), meta: { title: '图文卡片', icon: '▤' } },
  { path: '/images', name: 'images', component: () => import('./views/ImageGenView.vue'), meta: { title: '图片生成', icon: '✦' } },
  { path: '/stickers', name: 'stickers', component: () => import('./views/StickersView.vue'), meta: { title: '贴图创作', icon: '❖' } },
  { path: '/storyboards', name: 'storyboards', component: () => import('./views/StoryboardsView.vue'), meta: { title: '漫画分镜', icon: '▧' } },
  { path: '/videos', name: 'videos', component: () => import('./views/VideosView.vue'), meta: { title: '视频创作', icon: '▶' } },
  { path: '/novels', name: 'novels', component: () => import('./views/NovelsView.vue'), meta: { title: '小说创作', icon: '✎' } },
  { path: '/characters', name: 'characters', component: () => import('./views/CharactersView.vue'), meta: { title: '角色库', icon: '☻' } },
  { path: '/assets', name: 'assets', component: () => import('./views/AssetsView.vue'), meta: { title: '素材图库', icon: '▣' } },
  { path: '/prompts', name: 'prompts', component: () => import('./views/PromptsView.vue'), meta: { title: '提示词库', icon: '✧' } },
  { path: '/handdraw', name: 'handdraw', component: () => import('./views/HanddrawView.vue'), meta: { title: '提示词库', icon: '✧' } },
  { path: '/publishing', name: 'publishing', component: () => import('./views/PublishingView.vue'), meta: { title: '发布管理', icon: '▲' } },
  { path: '/wechat-layout', name: 'wechat-layout', component: () => import('./views/LayoutView.vue'), meta: { title: '微信排版', icon: '❐' } },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('./views/SettingsView.vue'),
    meta: { title: '设置', icon: '⚙' },
    // 旧链接 / 旧页签里还留着 ?tab=models|logs，两块都已独立成页面，别静默落回第一个分栏
    beforeEnter: (to) => (['models', 'logs'].includes(to.query.tab) ? { name: to.query.tab } : true),
  },
  { path: '/models', name: 'models', component: () => import('./views/ModelsView.vue'), meta: { title: '模型管理', icon: '◎' } },
  { path: '/logs', name: 'logs', component: () => import('./views/LogsView.vue'), meta: { title: '系统日志', icon: '≡' } },
]

export default createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})
