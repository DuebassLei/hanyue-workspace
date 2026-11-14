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
import { accentKey, bindShellAppearance, fontKey, setAccent, setFontSize, useNaiveTheme } from './ui/theme'
import { syncFeedbackTheme } from './ui/feedback'
import { NAV_GROUPS, breadcrumbsFor, findNav } from './ui/nav'
import { usePageTabs } from './ui/shell'
import PageTabs from './features/shell/PageTabs.vue'
import CommandPalette from './features/shell/CommandPalette.vue'
import AccentMenu from './features/shell/AccentMenu.vue'
import FontMenu from './features/shell/FontMenu.vue'
import AccountMenu from './features/shell/AccountMenu.vue'
import AccountDrawer from './features/shell/AccountDrawer.vue'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const COLLAPSE_KEY = 'hanyue-sider-collapsed'
const OPEN_KEY = 'hanyue-menu-open'

const collapsed = ref(localStorage.getItem(COLLAPSE_KEY) === '1')
const expandedKeys = ref(readOpenKeys())
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

function readOpenKeys() {
  try {
    const saved = JSON.parse(localStorage.getItem(OPEN_KEY) || 'null')
    if (Array.isArray(saved)) return saved
  } catch {
    /* 坏数据就当没存过 */
  }
  return NAV_GROUPS.map((group) => group.key)
}

watch(collapsed, (value) => {
  localStorage.setItem(COLLAPSE_KEY, value ? '1' : '0')
})

watch(
  expandedKeys,
  (value) => {
    localStorage.setItem(OPEN_KEY, JSON.stringify(value))
  },
  { deep: true },
)

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
  system: strokeIcon([
    h('circle', { cx: '10', cy: '10', r: '2.2' }),
    h('path', {
      d: 'M10 3.6v1.5M10 14.9v1.5M3.6 10h1.5M14.9 10h1.5M5.5 5.5l1 1M13.5 13.5l1 1M14.5 5.5l-1 1M6.5 13.5l-1 1',
    }),
  ]),
}

const menuOptions = NAV_GROUPS.map((group) => ({
  label: group.label,
  key: group.key,
  icon: groupIcons[group.key],
  children: group.items.map((item) => ({
    label: item.label,
    key: item.name,
  })),
}))

const mobileNav = NAV_GROUPS.flatMap((group) => group.items)

const activeKey = computed(() => findNav(route.name)?.name || null)

const crumbs = computed(() => breadcrumbsFor(route.name, route.meta.title))

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
        <n-layout has-sider class="shell" position="absolute">
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
            <button class="brand" type="button" @click="go('overview')">
              <span class="brand-mark" aria-hidden="true">文</span>
              <span v-show="!collapsed" class="brand-name">文案台</span>
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
                重新进入
              </n-button>
            </div>

            <!-- 不加 has-sider 时 n-layout-content 默认 overflow:hidden，会把页面撑破 main
                 （main 同为 hidden）→ 滚轮链截断、整页滚不动。这里让它成为唯一原生滚动器。 -->
            <n-layout-content class="main-content">
              <RouterView v-if="session.ready && !session.needsLogin" />
              <div v-else-if="session.needsLogin" class="page">
                <div class="card">
                  <h2 class="card-title">需要重新进入</h2>
                  <p class="page-sub">当前会话已退出或失效；开发态会按上次的身份重新建立本地会话。</p>
                  <n-button type="primary" @click="session.reenter()">重新进入</n-button>
                </div>
              </div>
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
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: var(--color-text-primary);
  color: var(--color-bg);
  font-family: 'Source Han Serif SC', 'Noto Serif SC', 'Songti SC', Georgia, serif;
  font-weight: 700;
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

.main-content {
  height: 100%;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.topbar {
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
