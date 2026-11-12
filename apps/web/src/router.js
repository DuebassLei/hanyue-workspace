import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'overview', component: () => import('./views/OverviewView.vue'), meta: { title: '创作总览', icon: '◈' } },
  { path: '/topics', redirect: { name: 'studio' } },
  { path: '/calendar', name: 'calendar', component: () => import('./views/CalendarView.vue'), meta: { title: '内容日历', icon: '▦' } },
  { path: '/studio', name: 'studio', component: () => import('./views/StudioView.vue'), meta: { title: '创作空间', icon: '✎' } },
  { path: '/cards', name: 'cards', component: () => import('./views/CardsView.vue'), meta: { title: '图文卡片', icon: '▤' } },
  { path: '/images', name: 'images', component: () => import('./views/ImageGenView.vue'), meta: { title: '图片生成', icon: '✦' } },
  { path: '/assets', name: 'assets', component: () => import('./views/AssetsView.vue'), meta: { title: '素材图库', icon: '▣' } },
  { path: '/prompts', name: 'prompts', component: () => import('./views/PromptsView.vue'), meta: { title: '提示词库', icon: '✧' } },
  { path: '/handdraw', name: 'handdraw', component: () => import('./views/HanddrawView.vue'), meta: { title: '提示词库', icon: '✧' } },
  { path: '/publishing', name: 'publishing', component: () => import('./views/PublishingView.vue'), meta: { title: '发布管理', icon: '▲' } },
  { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue'), meta: { title: '设置', icon: '⚙' } },
]

export default createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})
