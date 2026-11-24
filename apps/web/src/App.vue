<script setup>
import { computed, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NBreadcrumb,
  NBreadcrumbItem,
  NButton,
  NConfigProvider,
  NDialogProvider,
  NIcon,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NLayoutSider,
  NMenu,
  NGlobalStyle,
  NMessageProvider,
  dateZhCN,
  zhCN,
} from 'naive-ui'
import { useSessionStore } from './stores/session'
import { useRbacStore } from './stores/rbac'
import { accentKey, bindShellAppearance, fontKey, setAccent, setFontSize, useNaiveTheme } from './ui/theme'
import { syncFeedbackTheme } from './ui/feedback'
import { NAV_GROUPS, breadcrumbsFor, findNav, flattenWorkspaceMenus, navEntries } from './ui/nav'
import { usePageTabs } from './ui/shell'
import PageTabs from './features/shell/PageTabs.vue'
import CommandPalette from './features/shell/CommandPalette.vue'
import AccentMenu from './features/shell/AccentMenu.vue'
import FontMenu from './features/shell/FontMenu.vue'
import AccountMenu from './features/shell/AccountMenu.vue'
import ModelStatusBar from './features/shell/ModelStatusBar.vue'
import AccountDrawer from './features/shell/AccountDrawer.vue'
import LoginView from './views/LoginView.vue'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const rbac = useRbacStore()
const COLLAPSE_KEY = 'hanyue-sider-collapsed'

const collapsed = ref(localStorage.getItem(COLLAPSE_KEY) === '1')
// 展开态只在本次会话内记忆：首次进入只展开当前页面所在的分类，
// 其余收起（工作区下有四个分类，全展开和原来的平铺没区别）。
const expandedKeys = ref([])
const { theme, themeOverrides, isDark } = useNaiveTheme(session)
bindShellAppearance(isDark)
const {
  tabs,
  activeKey: tabActiveKey,
  open: openTab,
  close: closeTab,
  closeOthers: closeOtherTabs,
  closeAll: closeAllTabs,
} = usePageTabs(route, router)
const paletteOpen = ref(false)
const accountOpen = ref(false)
const isFullscreen = ref(false)
const canFullscreen = typeof document !== 'undefined' && document.documentElement.requestFullscreen

watch(collapsed, (value) => {
  localStorage.setItem(COLLAPSE_KEY, value ? '1' : '0')
})

watch(
  () => session.theme,
  (value) => syncFeedbackTheme(value),
  { immediate: true },
)

function strokeIcon(children) {
  return () =>
    h(NIcon, null, {
      default: () =>
        h(
          'svg',
          {
            viewBox: '0 0 20 20',
            width: '1em',
            height: '1em',
            fill: 'none',
            stroke: 'currentColor',
            'stroke-width': '1.6',
            'stroke-linecap': 'round',
            'stroke-linejoin': 'round',
          },
          children,
        ),
    })
}

const groupIcons = {
  create: strokeIcon([
    h('path', { d: 'M4 15.5 14.2 5.3a1.4 1.4 0 0 1 2 2L6 17.5H4v-2Z' }),
    h('path', { d: 'm12.2 7.3 2 2' }),
  ]),
  workspace: strokeIcon([
    h('rect', { x: '3.5', y: '4', width: '13', height: '12', rx: '2' }),
    h('path', { d: 'M3.5 8h13M8 8v8' }),
  ]),
  ops: strokeIcon([
    h('path', { d: 'M4 14.5 8.2 6h1.1l4.2 8.5' }),
    h('path', { d: 'M5.6 11.2h6.3' }),
    h('path', { d: 'M14 9.5v5M11.8 12h4.4' }),
  ]),
  rbac: strokeIcon([
    h('path', { d: 'M10 2.8 16 5.5v4.4c0 3.3-2.4 6.2-6 7.3-3.6-1.1-6-4-6-7.3V5.5L10 2.8Z' }),
    h('path', { d: 'm7.6 10 1.7 1.7 3.3-3.4' }),
  ]),
  system: strokeIcon([
    h('circle', { cx: '10', cy: '10', r: '2.2' }),
    h('path', {
      d: 'M10 3.6v1.5M10 14.9v1.5M3.6 10h1.5M14.9 10h1.5M5.5 5.5l1 1M13.5 13.5l1 1M14.5 5.5l-1 1M6.5 13.5l-1 1',
    }),
  ]),
}

// 二级菜单图标：与一级分组同一套 stroke 画法，按 NAV_GROUPS 的 item.name 取用
const itemIcons = {
  overview: strokeIcon([
    h('rect', { x: '3', y: '3', width: '6', height: '6', rx: '1.2' }),
    h('rect', { x: '11', y: '3', width: '6', height: '6', rx: '1.2' }),
    h('rect', { x: '3', y: '11', width: '6', height: '6', rx: '1.2' }),
    h('rect', { x: '11', y: '11', width: '6', height: '6', rx: '1.2' }),
  ]),
  'hot-topics': strokeIcon([
    h('circle', { cx: '10', cy: '10', r: '2.6' }),
    h('path', {
      d: 'M10 2.5v2.8M10 14.7v2.8M2.5 10h2.8M14.7 10h2.8M4.7 4.7l2 2M13.3 13.3l2 2M15.3 4.7l-2 2M6.7 13.3l-2 2',
    }),
  ]),
  calendar: strokeIcon([
    h('rect', { x: '3', y: '4.5', width: '14', height: '12', rx: '2' }),
    h('path', { d: 'M6.5 3v3M13.5 3v3M3 9h14' }),
  ]),
  studio: strokeIcon([
    h('path', {
      d: 'M5 3.5h6l4 4v9a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 4 16.5v-11A2 2 0 0 1 5 3.5Z',
    }),
    h('path', { d: 'M11 3.5v4h4M7 11h6M7 14h4' }),
  ]),
  cards: strokeIcon([
    h('rect', { x: '3', y: '4', width: '14', height: '12', rx: '2' }),
    h('circle', { cx: '7', cy: '8', r: '1.1' }),
    h('path', { d: 'M3.5 14.5 8 10l2.5 2.5L13 9l3.5 5' }),
  ]),
  images: strokeIcon([
    h('rect', { x: '3', y: '5', width: '12', height: '11', rx: '2' }),
    h('circle', { cx: '6.5', cy: '8.5', r: '1' }),
    h('path', { d: 'M3.5 14 7 10.5l2.5 2.5L12 9.5l2.5 3' }),
    h('path', { d: 'M16 2.5v3M14.5 4h3' }),
  ]),
  stickers: strokeIcon([
    h('path', {
      d: 'M4 3.5h9l4 4v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-12a1 1 0 0 1 1-1Z',
    }),
    h('path', { d: 'M13 3.5v4h4M7 10h6M7 13h4' }),
  ]),
  storyboards: strokeIcon([
    h('rect', { x: '3', y: '3.5', width: '14', height: '13', rx: '1.5' }),
    h('path', { d: 'M3 10h14M10 3.5v13' }),
  ]),
  characters: strokeIcon([
    h('circle', { cx: '10', cy: '7', r: '3' }),
    h('path', { d: 'M4.5 17a5.5 5.5 0 0 1 11 0' }),
  ]),
  novels: strokeIcon([
    h('path', {
      d: 'M10 5.5C8 4.2 5.5 4 3 4.2v11c2.5-.2 5 0 7 1.3 2-1.3 4.5-1.5 7-1.3v-11c-2.5-.2-5 0-7 1.3Z',
    }),
    h('path', { d: 'M10 5.5v11' }),
  ]),
  prompts: strokeIcon([
    h('path', { d: 'm4.5 15.5 8-8 2 2-8 8-3 1 1-3Z' }),
    h('path', { d: 'M15 3v3M13.5 4.5h3M17 8v2M16 9h2' }),
  ]),
  assets: strokeIcon([
    h('path', {
      d: 'M3 6a2 2 0 0 1 2-2h3l2 2h5a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6Z',
    }),
  ]),
  publishing: strokeIcon([
    h('path', { d: 'M10 13V4m0 0L7 7m3-3 3 3' }),
    h('path', { d: 'M4 13v2.5A1.5 1.5 0 0 0 5.5 17h9a1.5 1.5 0 0 0 1.5-1.5V13' }),
  ]),
  'wechat-layout': strokeIcon([
    h('rect', { x: '3', y: '4', width: '14', height: '12', rx: '2' }),
    h('path', { d: 'M6 8h8M6 11h8M6 14h5' }),
  ]),
  models: strokeIcon([
    h('rect', { x: '5', y: '5', width: '10', height: '10', rx: '1.5' }),
    h('path', {
      d: 'M8 2.5v2.5M12 2.5v2.5M8 15v2.5M12 15v2.5M2.5 8H5M2.5 12H5M15 8h2.5M15 12h2.5',
    }),
  ]),
  logs: strokeIcon([
    h('rect', { x: '3', y: '4', width: '14', height: '12', rx: '2' }),
    h('path', { d: 'm6.5 8 2 2-2 2M11 12.5h3' }),
  ]),
  settings: strokeIcon([
    h('path', { d: 'M4 6h12M4 10h12M4 14h12' }),
    h('circle', { cx: '8', cy: '6', r: '2' }),
    h('circle', { cx: '13', cy: '10', r: '2' }),
    h('circle', { cx: '7', cy: '14', r: '2' }),
  ]),
  schedules: strokeIcon([
    h('circle', { cx: '10', cy: '10', r: '6.5' }),
    h('path', { d: 'M10 6.5V10l2.6 1.6' }),
  ]),
  users: strokeIcon([
    h('circle', { cx: '7.5', cy: '7.5', r: '2.6' }),
    h('path', { d: 'M3 16.5a4.5 4.5 0 0 1 9 0' }),
    h('path', { d: 'M13 5.2a2.4 2.4 0 0 1 0 4.6M14.5 16.5a4 4 0 0 0-2-3.4' }),
  ]),
  roles: strokeIcon([
    h('path', { d: 'M10 2.8 16 5.5v4.4c0 3.3-2.4 6.2-6 7.3-3.6-1.1-6-4-6-7.3V5.5L10 2.8Z' }),
    h('path', { d: 'm7.6 10 1.7 1.7 3.3-3.4' }),
  ]),
  menus: strokeIcon([
    h('path', { d: 'M3.5 5.5h13M3.5 10h13M3.5 14.5h13' }),
    h('circle', { cx: '6' , cy: '5.5', r: '1.1' }),
    h('circle', { cx: '6', cy: '10', r: '1.1' }),
    h('circle', { cx: '6', cy: '14.5', r: '1.1' }),
  ]),
}

// 菜单以服务端为准（管理员可在「菜单管理」调整）；拉取失败时回退静态表，
// 保证后端不可达也能看到骨架，而不是空白侧栏。
// 工作区仅改变展示层级，不改服务端菜单管理与权限配置；兼容已有的分类菜单。
const navGroups = computed(() => flattenWorkspaceMenus(rbac.menus.length ? rbac.menus : NAV_GROUPS))

/** 叶子页面项（带祖先链），选中态、移动端底栏和展开定位共用 */
const navPages = computed(() => navEntries(navGroups.value))

function itemIcon(item) {
  return itemIcons[item.name] || itemIcons[item.icon] || itemIcons.overview
}

function toMenuOption(item) {
  const option = { label: item.label, key: item.name, icon: itemIcon(item) }
  if (item.children?.length) option.children = item.children.map(toMenuOption)
  return option
}

const menuOptions = computed(() =>
  navGroups.value.map((group) => ({
    label: group.label,
    key: group.key,
    icon: groupIcons[group.key] || itemIcon(group),
    children: (group.items || []).map(toMenuOption),
  })),
)

const mobileNav = computed(() => navPages.value)

const activeKey = computed(() => {
  const hit = navPages.value.find(
    (item) => item.name === route.name || item.aliases?.includes(route.name),
  )
  return hit?.name || null
})

/** 只补开当前页面的祖先分类，不关掉用户手动展开的其它分组。 */
function expandCurrentPath() {
  const hit = navPages.value.find(
    (item) => item.name === route.name || item.aliases?.includes(route.name),
  )
  if (!hit?.ancestors?.length) return
  expandedKeys.value = [...new Set([...expandedKeys.value, ...hit.ancestors])]
}

watch([() => route.name, navPages], expandCurrentPath, { immediate: true })

const crumbs = computed(() => breadcrumbsFor(route.name, route.meta.title, navGroups.value))

const themeLabel = computed(() =>
  session.theme === 'system' ? '跟随系统' : session.theme === 'dark' ? '暗色' : '浅色',
)

function cycleTheme() {
  const order = ['system', 'light', 'dark']
  session.setTheme(order[(order.indexOf(session.theme) + 1) % order.length])
}

/** 提示条有了处理入口后，点它就等于确认看过：只清本地展示，不替用户重试。 */
function onSessionAlert() {
  session.dismissAlerts()
}

function go(name) {
  if (name) router.push({ name })
}

function goTarget(target) {
  if (!target?.name) return
  router.push({ name: target.name, query: target.query || {} })
}

function onGlobalKey(event) {
  // 带 Shift 的组合留给具体编辑器（成稿台用 Ctrl/Cmd+Shift+K 删段），
  // 否则命令面板会抢走焦点，快捷键变成「打开面板」。
  if (event.shiftKey) return
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    paletteOpen.value = true
  }
}

function syncFullscreen() {
  isFullscreen.value = document.fullscreenElement === document.documentElement
}

async function toggleFullscreen() {
  if (!canFullscreen) return
  try {
    if (document.fullscreenElement) await document.exitFullscreen()
    else await document.documentElement.requestFullscreen()
  } catch {
    /* 浏览器拒绝全屏时保持原状，按钮仍可再试 */
  }
}

// 登录态变化时同步权限：登出即清空，登入即拉取。
// 拉取失败保留旧值（network 抖动不该让侧栏突然空掉）。
watch(
  () => session.user?.id || null,
  async (userId) => {
    if (!userId) {
      rbac.reset()
      return
    }
    await rbac.load()
  },
)

// 未登录时统一使用根路径展示公开首页；登录后仍由根路径进入创作总览。
watch(
  () => [session.needsLogin, route.path],
  ([needsLogin, path]) => {
    if (needsLogin && path !== '/') router.replace({ path: '/' })
  },
  { immediate: true },
)

// 首屏直达一个没权限的页面时（旧书签/群里的链接），落到明确的 403 页。
// 守卫在登录前无法判断，只能等权限加载完在这里补一次。
watch(
  () => [rbac.loaded, route.name],
  () => {
    const need = route.meta?.permission
    if (!rbac.loaded || !need || rbac.can(need)) return
    router.replace({ name: 'forbidden', query: { from: route.fullPath, need } })
  },
)

onMounted(() => {
  session.boot()
  window.addEventListener('keydown', onGlobalKey)
  document.addEventListener('fullscreenchange', syncFullscreen)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKey)
  document.removeEventListener('fullscreenchange', syncFullscreen)
})
</script>

<template>
  <n-config-provider :theme="theme" :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-global-style />
    <n-message-provider>
      <n-dialog-provider>
        <LoginView v-if="session.needsLogin" />
        <n-layout v-else has-sider class="shell" position="absolute">
          <n-layout-sider
            class="sider"
            bordered
            collapse-mode="width"
            :collapsed="collapsed"
            :collapsed-width="64"
            :width="232"
            :native-scrollbar="false"
            show-trigger="bar"
            @collapse="collapsed = true"
            @expand="collapsed = false"
          >
            <button class="brand" type="button" aria-label="AI工作台首页" @click="go('overview')">
              <img class="brand-mark" src="/mascot-plane.png" alt="" />
              <span v-show="!collapsed" class="brand-name">AI工作台</span>
            </button>
            <n-menu
              :value="activeKey"
              v-model:expanded-keys="expandedKeys"
              :collapsed="collapsed"
              :collapsed-width="64"
              :collapsed-icon-size="18"
              :options="menuOptions"
              :indent="18"
              @update:value="go"
            />
            <div v-show="!collapsed" class="sider-foot">
              {{ session.user?.displayName || '未登录' }}
            </div>
          </n-layout-sider>

          <!-- flex-height + native：main 高度固定不滚动，滚动全部交给 .main-content，
               避免出现第二个原生滚动容器抢滚轮（wheel 链被 overflow:hidden 截断的根因） -->
          <n-layout class="main" :native-scrollbar="true" flex-height>
            <n-layout-header bordered class="topbar">
              <div class="topbar-start">
                <button
                  type="button"
                  class="tool-btn sider-toggle"
                  :aria-label="collapsed ? '展开菜单' : '收起菜单'"
                  :aria-pressed="collapsed"
                  @click="collapsed = !collapsed"
                >
                  <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
                    <path d="M4 5.5h12M4 10h12M4 14.5h12" stroke-linecap="round" />
                  </svg>
                </button>
                <span class="current-page">{{ crumbs[crumbs.length - 1]?.label }}</span>
              </div>
              <div class="topbar-actions">
                <ModelStatusBar @manage-models="go('models')" />
                <button type="button" class="tool-btn" aria-label="搜索菜单" title="搜索 Ctrl K" @click="paletteOpen = true">
                  <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
                    <circle cx="9" cy="9" r="4.5" />
                    <path d="M12.5 12.5 16 16" stroke-linecap="round" />
                  </svg>
                </button>
                <FontMenu :value="fontKey" @select="setFontSize" />
                <AccentMenu :value="accentKey" @select="setAccent" />
                <button
                  type="button"
                  class="tool-btn"
                  :disabled="!canFullscreen"
                  :aria-label="isFullscreen ? '退出全屏' : '全屏'"
                  @click="toggleFullscreen"
                >
                  <svg v-if="!isFullscreen" viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
                    <path d="M4 8V4.8A.8.8 0 0 1 4.8 4H8M12 4h3.2a.8.8 0 0 1 .8.8V8M16 12v3.2a.8.8 0 0 1-.8.8H12M8 16H4.8a.8.8 0 0 1-.8-.8V12" stroke-linecap="round" />
                  </svg>
                  <svg v-else viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
                    <path d="M8 4.5V8H4.5M12 4.5V8h3.5M8 15.5V12H4.5M12 15.5V12h3.5" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="tool-btn"
                  :aria-label="`切换主题，当前${themeLabel}`"
                  :title="themeLabel"
                  @click="cycleTheme"
                >
                  <svg v-if="session.theme === 'dark'" viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
                    <path d="M14.5 12.2A5.2 5.2 0 0 1 7.8 5.5 5.4 5.4 0 1 0 14.5 12.2Z" stroke-linejoin="round" />
                  </svg>
                  <svg v-else-if="session.theme === 'light'" viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
                    <circle cx="10" cy="10" r="3.2" />
                    <path d="M10 3.2v1.6M10 15.2v1.6M3.2 10h1.6M15.2 10h1.6M5.2 5.2l1.1 1.1M13.7 13.7l1.1 1.1M14.8 5.2l-1.1 1.1M6.3 13.7l-1.1 1.1" stroke-linecap="round" />
                  </svg>
                  <svg v-else viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
                    <circle cx="10" cy="10" r="6.2" />
                    <path d="M10 3.8a6.2 6.2 0 0 0 0 12.4V3.8Z" fill="currentColor" stroke="none" />
                  </svg>
                </button>
                <span class="tool-gap" aria-hidden="true" />
                <AccountMenu
                  v-if="session.token"
                  @profile="accountOpen = true"
                  @settings="go('settings')"
                />
              </div>
            </n-layout-header>

            <PageTabs
              class="desktop-tabs"
              :tabs="tabs"
              :active-key="tabActiveKey"
              @open="openTab"
              @close="closeTab"
              @close-others="closeOtherTabs"
              @close-all="closeAllTabs"
            />

            <div
              v-if="session.error || session.themeSyncError"
              class="session-alert"
              role="alert"
              title="点击关闭提示"
              @click="onSessionAlert"
            >
              <span>{{ session.error || session.themeSyncError }}</span>
              <n-button v-if="session.needsLogin" size="small" type="primary" @click.stop="session.reenter()">
                去登录
              </n-button>
            </div>

            <!-- 不加 has-sider 时 n-layout-content 默认 overflow:hidden，会把页面撑破 main
                 （main 同为 hidden）→ 滚轮链截断、整页滚不动。这里让它成为唯一原生滚动器。 -->
            <n-layout-content
              class="main-content"
              :class="{ 'brand-sensitive': ['paper', 'cards', 'novels', 'storyboards', 'wechat-layout'].includes(route.name) }"
            >
              <RouterView v-if="session.ready && !session.needsLogin" />
              <LoginView v-else-if="session.needsLogin" />
              <div v-else class="page" aria-busy="true">
                <div class="skeleton" style="width: 28%; height: 28px" />
                <div class="skeleton" style="width: 100%; height: 120px" />
              </div>
            </n-layout-content>
          </n-layout>

          <nav class="mobile-nav" aria-label="移动端导航">
            <button
              v-for="item in mobileNav"
              :key="item.name"
              type="button"
              class="mobile-nav-item"
              :class="{ 'is-active': activeKey === item.name }"
              @click="go(item.name)"
            >
              {{ item.short }}
            </button>
          </nav>
        </n-layout>
        <CommandPalette v-model:show="paletteOpen" :tabs="tabs" @go="goTarget" />
        <AccountDrawer :open="accountOpen" @close="accountOpen = false" />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<style scoped>
.shell {
  min-height: 100vh;
}

.sider {
  background: var(--color-surface);
}

.sider :deep(.n-layout-sider-scroll-container) {
  display: flex;
  flex-direction: column;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 56px;
  padding: 0 18px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.brand-mark {
  width: 28px;
  height: 28px;
  display: block;
  object-fit: cover;
  border-radius: 6px;
  flex: 0 0 auto;
}

.brand-name {
  font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif;
  font-size: 18px;
  letter-spacing: 0.12em;
}

.sider-foot {
  margin-top: auto;
  padding: 14px 18px 18px;
  color: var(--color-text-muted);
  font-size: 12px;
}

/* Naive Layout 自带 scroll-container。只让内容区的这一层负责滚动，
   外层不能再设 height:100% + auto，否则页头高度会额外撑出第二根滚动条。 */
.main {
  height: 100%;
  min-height: 0;
  overflow: hidden;
}
.main > :deep(.n-layout-scroll-container) {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}
:global(html[data-theme='light']) .sider,
:global(html[data-theme='light']) .topbar {
  background: var(--brand-shell);
}

:global(html[data-theme='light']) .main-content {
  background: var(--brand-canvas);
}

:global(html[data-theme='light']) .brand-name {
  color: var(--brand-ink);
}

.main-content {
  background: var(--color-bg);
  flex: 1 1 0;
  min-height: 0;
  height: auto;
  overflow: hidden;
}
.main-content > :deep(.n-layout-scroll-container) {
  height: 100%;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior-y: contain;
}
.desktop-tabs,
.session-alert {
  flex-shrink: 0;
}

.topbar {
  background: var(--color-surface);
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 16px 0 12px;
  flex: none;
}

.topbar-start {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.current-page {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}

.tool-btn {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.tool-btn:hover:not(:disabled) {
  background: var(--color-surface-raised);
  color: var(--color-text-primary);
}

.tool-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex: 0 0 auto;
}

.tool-gap {
  width: 1px;
  height: 16px;
  margin: 0 6px;
  background: var(--color-border-strong);
}

.session-alert {
  margin: 12px 24px 0;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid color-mix(in srgb, var(--color-warning) 40%, transparent);
  background: color-mix(in srgb, var(--color-warning) 12%, transparent);
  border-radius: 8px;
  font-size: 13px;
}

.mobile-nav {
  display: none;
}

@media (max-width: 767px) {
  .desktop-tabs {
    display: none;
  }

  .sider {
    display: none !important;
  }

  .shell {
    padding-bottom: calc(56px + env(safe-area-inset-bottom));
  }

  .topbar {
    padding: 0 8px;
  }

  .mobile-nav {
    display: flex;
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 40;
    height: calc(52px + env(safe-area-inset-bottom));
    padding: 0 4px env(safe-area-inset-bottom);
    background: var(--color-surface);
    border-top: 1px solid var(--color-border);
    overflow-x: auto;
  }

  .mobile-nav-item {
    flex: 1 0 auto;
    min-width: 48px;
    min-height: 44px;
    border: 0;
    background: transparent;
    color: var(--color-text-muted);
    font: inherit;
    font-size: 12px;
  }

  .mobile-nav-item.is-active {
    color: var(--color-accent);
    font-weight: 650;
  }
}
</style>
