import { computed, ref, watch } from 'vue'
import { findNav } from './nav'

const TABS_KEY = 'hanyue-tabs'

function stableQuery(query) {
  const entries = Object.entries(query || {})
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => [key, Array.isArray(value) ? value.map(String).join(',') : String(value)])
    .sort(([a], [b]) => a.localeCompare(b))
  return Object.fromEntries(entries)
}

/**
 * 标签按「导航页」归档，不按完整 URL 归档。
 * 平台筛选、草稿 id 这类参数只是同一页里的状态，跟着标签更新即可；
 * 早先按 `name?query` 做 key，切一次平台就多一个「热点选题」，所以这里收成页面级。
 */
export function tabKeyOf(route) {
  const name = String(route.name || '')
  return findNav(name)?.name || name
}

export function tabTitle(route) {
  const nav = findNav(route.name)
  return nav?.label || route.meta?.title || String(route.name || '页面')
}

function loadTabs() {
  try {
    const saved = JSON.parse(localStorage.getItem(TABS_KEY) || 'null')
    if (!Array.isArray(saved)) return []
    // 旧版本把同一页面的不同参数存成多条，这里按页面去重，只留最后访问的那次
    const deduped = new Map()
    for (const tab of saved) {
      if (!tab || typeof tab.name !== 'string' || !findNav(tab.name)) continue
      if (tab.query !== undefined && (typeof tab.query !== 'object' || !tab.query)) continue
      const key = findNav(tab.name).name
      if (key === 'overview') continue
      deduped.set(key, {
        key,
        name: tab.name,
        query: tab.query || {},
        title: tabTitle({ name: tab.name }),
      })
    }
    return [...deduped.values()]
  } catch {
    return []
  }
}

export function usePageTabs(route, router) {
  const opened = ref(loadTabs())

  const tabs = computed(() => [
    { key: 'overview', name: 'overview', query: {}, title: '创作总览', pinned: true },
    ...opened.value.map((tab) => ({ ...tab, pinned: false })),
  ])

  const activeKey = computed(() => tabKeyOf(route))

  watch(
    opened,
    (value) => {
      try {
        localStorage.setItem(TABS_KEY, JSON.stringify(value))
      } catch {
        /* 隐私模式或配额满时，这一次不记住 */
      }
    },
    { deep: true },
  )

  watch(
    () => [route.name, route.fullPath],
    () => {
      if (!route.name || !findNav(route.name)) return
      const key = tabKeyOf(route)
      if (key === 'overview') return
      const query = stableQuery(route.query)
      const existing = opened.value.find((tab) => tab.key === key)
      if (existing) {
        // 同一页再进来，只刷新它记着的下钻位置（提示词库/手绘、草稿 id），不新开标签
        existing.name = String(route.name)
        existing.query = query
        existing.title = tabTitle(route)
        return
      }
      opened.value.push({ key, name: String(route.name), query, title: tabTitle(route) })
    },
    { immediate: true },
  )

  function open(tab) {
    router.push({ name: tab.name, query: tab.query || {} })
  }

  function close(key) {
    const index = opened.value.findIndex((tab) => tab.key === key)
    if (index < 0) return
    const wasActive = activeKey.value === key
    opened.value.splice(index, 1)
    if (!wasActive) return
    const next = opened.value[index] || opened.value[index - 1]
    if (next) open(next)
    else router.push({ name: 'overview' })
  }

  /** 只留这一个。从总览发起就等于全关，但人留在总览。 */
  function closeOthers(key) {
    if (key === 'overview' || !opened.value.some((tab) => tab.key === key)) {
      opened.value = []
      if (activeKey.value !== 'overview') router.push({ name: 'overview' })
      return
    }
    const keep = opened.value.find((tab) => tab.key === key)
    opened.value = [keep]
    if (activeKey.value !== key) open(keep)
  }

  /** 总览是常驻的，不会被关掉，所以这里只清空打开的页面。 */
  function closeAll() {
    if (!opened.value.length) return
    opened.value = []
    router.push({ name: 'overview' })
  }

  return { tabs, activeKey, open, close, closeOthers, closeAll }
}
