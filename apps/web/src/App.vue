<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from './stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const sidebarCollapsed = ref(false)

const navGroups = [
  {
    label: '创作',
    items: [
      { name: 'overview', label: '创作总览', icon: '◈' },
      { name: 'calendar', label: '内容日历', icon: '▦' },
    ],
  },
  {
    label: '工作区',
    items: [
      { name: 'studio', label: '创作空间', icon: '✎' },
      { name: 'cards', label: '图文卡片', icon: '▤' },
      { name: 'images', label: '图片生成', icon: '✦' },
      { name: 'prompts', label: '提示词库', icon: '✧' },
      { name: 'assets', label: '素材图库', icon: '▣' },
    ],
  },
  {
    label: '运营',
    items: [{ name: 'publishing', label: '发布管理', icon: '▲' }],
  },
  { label: '系统', items: [{ name: 'settings', label: '设置', icon: '⚙' }] },
]

const mobileNav = [
  { name: 'overview', label: '总览', icon: '◈' },
  { name: 'calendar', label: '日历', icon: '▦' },
  { name: 'studio', label: '创作', icon: '✎' },
  { name: 'cards', label: '卡片', icon: '▤' },
  { name: 'images', label: '图片', icon: '✦' },
  { name: 'prompts', label: '提示词', icon: '✧' },
  { name: 'assets', label: '图库', icon: '▣' },
  { name: 'publishing', label: '发布', icon: '▲' },
  { name: 'settings', label: '我的', icon: '⚙' },
]

const isActive = (name) =>
  route.name === name || (name === 'prompts' && route.name === 'handdraw')

const themeLabel = computed(() =>
  session.theme === 'system' ? '跟随系统' : session.theme === 'dark' ? '暗色' : '浅色',
)

function cycleTheme() {
  const order = ['system', 'light', 'dark']
  const next = order[(order.indexOf(session.theme) + 1) % order.length]
  session.setTheme(next)
}

onMounted(() => session.boot())

function go(name) {
  router.push({ name })
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ 'is-collapsed': sidebarCollapsed }" aria-label="主导航">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">AI</span>
        <span class="brand-name">AI工作台</span>
        <button
          class="sidebar-toggle icon-btn"
          type="button"
          :aria-label="sidebarCollapsed ? '展开侧栏' : '折叠侧栏'"
          :title="sidebarCollapsed ? '展开侧栏' : '折叠侧栏'"
          @click="sidebarCollapsed = !sidebarCollapsed"
        >
          <span aria-hidden="true">{{ sidebarCollapsed ? '»' : '«' }}</span>
        </button>
      </div>
      <nav class="nav-group" v-for="group in navGroups" :key="group.label">
        <span class="nav-label">{{ group.label }}</span>
        <RouterLink
          v-for="item in group.items"
          :key="item.name"
          :to="{ name: item.name }"
          class="nav-item"
          :class="{ 'is-active': isActive(item.name) }"
          :title="sidebarCollapsed ? item.label : undefined"
        >
          <span class="nav-dot" aria-hidden="true">{{ item.icon }}</span>
          <span class="nav-text">{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div class="spacer" />
      <span class="nav-label">{{ session.user?.displayName || '未登录' }}</span>
    </aside>

    <div class="main-area">
      <header class="topbar">
        <div class="row row-tight">
          <strong style="font-size: var(--font-section)">{{ route.meta.title || '工作台' }}</strong>
        </div>
        <div class="row row-tight">
          <button class="icon-btn" :aria-label="`切换主题，当前${themeLabel}`" :title="`主题：${themeLabel}`" @click="cycleTheme">
            <span aria-hidden="true">{{ session.theme === 'dark' ? '☾' : session.theme === 'light' ? '☀' : '◐' }}</span>
          </button>
          <button v-if="session.token" class="btn btn-ghost btn-sm" @click="session.logout()">退出</button>
        </div>
      </header>

      <div
        v-if="session.error || session.themeSyncError"
        class="banner banner-warning"
        style="margin: var(--space-3); display: flex; align-items: center; gap: var(--space-2)"
      >
        <span>{{ session.error || session.themeSyncError }}</span>
        <button v-if="session.needsLogin" class="btn btn-sm" @click="session.reenter()">重新进入</button>
      </div>

      <RouterView v-if="session.ready && !session.needsLogin" />
      <div v-else-if="session.needsLogin" class="page">
        <div class="card">
          <h2 class="card-title">需要重新进入</h2>
          <p class="page-sub">当前会话已退出或失效；开发态会按上次的身份重新建立本地会话。</p>
          <button class="btn" @click="session.reenter()">重新进入</button>
        </div>
      </div>
      <div v-else class="page">
        <div class="skeleton" style="width: 40%; height: 24px" />
        <div class="skeleton" style="width: 100%; height: 96px" />
        <div class="skeleton" style="width: 100%; height: 96px" />
      </div>
    </div>

    <nav class="mobile-nav" aria-label="移动端导航">
      <a
        v-for="item in mobileNav"
        :key="item.name"
        class="mobile-nav-item"
        :class="{ 'is-active': isActive(item.name) }"
        href="#"
        @click.prevent="go(item.name)"
      >
        <span class="mobile-nav-icon" aria-hidden="true">{{ item.icon }}</span>
        <span>{{ item.label }}</span>
      </a>
    </nav>
  </div>
</template>
