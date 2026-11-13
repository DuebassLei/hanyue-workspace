import { computed, ref, watch } from 'vue'
import { darkTheme } from 'naive-ui'

/**
 * 亮暗仍按 Naive 文档：theme 为 null 用默认亮色，darkTheme 用默认暗色。
 * 主色单独走 accent：默认绿不写 themeOverrides；其余五色只覆盖 primary 四件。
 */

export const ACCENT_KEY = 'hanyue-accent'
export const FONT_KEY = 'hanyue-font-size'

export const FONT_SIZES = [
  { key: 'sm', label: '小', px: 13 },
  { key: 'md', label: '标准', px: 14 },
  { key: 'lg', label: '大', px: 16 },
]

export const ACCENTS = [
  { key: 'green', label: '默认绿' },
  { key: 'blue', label: '蓝' },
  { key: 'purple', label: '紫' },
  { key: 'cyan', label: '青' },
  { key: 'orange', label: '橙' },
  { key: 'red', label: '红' },
]

/** primary / hover / pressed / suppl。暗色用更亮的同色相，pressed 略深于 primary。 */
const PALETTES = {
  blue: {
    light: ['#2080f0', '#4098fc', '#1060c9', '#4098fc'],
    dark: ['#70c0e8', '#8acbec', '#5aaed6', '#8acbec'],
  },
  purple: {
    light: ['#7c3aed', '#8b5cf6', '#6d28d9', '#8b5cf6'],
    dark: ['#c4b5fd', '#ddd6fe', '#a78bfa', '#ddd6fe'],
  },
  cyan: {
    light: ['#0891b2', '#06b6d4', '#0e7490', '#06b6d4'],
    dark: ['#67e8f9', '#a5f3fc', '#22d3ee', '#a5f3fc'],
  },
  orange: {
    light: ['#f0a020', '#fcb040', '#d48806', '#fcb040'],
    dark: ['#f2c97d', '#f7d7a1', '#e6b65c', '#f7d7a1'],
  },
  red: {
    light: ['#d03050', '#de576d', '#ab1f3c', '#de576d'],
    dark: ['#e88080', '#f0a0a0', '#d06060', '#f0a0a0'],
  },
}

function readStored(key, allowed, fallback) {
  try {
    const value = localStorage.getItem(key)
    if (allowed.includes(value)) return value
  } catch {
    /* 隐私模式当没存过 */
  }
  return fallback
}

export const accentKey = ref(readStored(ACCENT_KEY, ACCENTS.map((item) => item.key), 'green'))
export const fontKey = ref(readStored(FONT_KEY, FONT_SIZES.map((item) => item.key), 'md'))

export function setAccent(key) {
  if (ACCENTS.some((item) => item.key === key)) accentKey.value = key
}

export function setFontSize(key) {
  if (FONT_SIZES.some((item) => item.key === key)) fontKey.value = key
}

export function fontPx(key = fontKey.value) {
  return FONT_SIZES.find((item) => item.key === key)?.px || 14
}

/** 默认绿返回 null，让 Naive 用内置主色。 */
export function primaryOverrides(key, isDark) {
  const palette = PALETTES[key]
  if (!palette) return null
  const [primaryColor, primaryColorHover, primaryColorPressed, primaryColorSuppl] =
    palette[isDark ? 'dark' : 'light']
  return { common: { primaryColor, primaryColorHover, primaryColorPressed, primaryColorSuppl } }
}

/** 字号三档始终写入 common.fontSize；默认绿不附带主色。 */
export function shellOverrides(key, isDark, px) {
  const common = { fontSize: `${px}px`, fontSizeMedium: `${px}px` }
  const primary = primaryOverrides(key, isDark)
  return { common: primary ? { ...primary.common, ...common } : common }
}

export function useNaiveTheme(session) {
  const isDark = computed(() => {
    if (session.theme === 'dark') return true
    if (session.theme === 'light') return false
    return typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
  })
  const theme = computed(() => (isDark.value ? darkTheme : null))
  const themeOverrides = computed(() => shellOverrides(accentKey.value, isDark.value, fontPx()))
  return { theme, themeOverrides, isDark }
}

function paintAccent(key, isDark) {
  const root = document.documentElement
  root.dataset.accent = key
  if (key === 'green' || !PALETTES[key]) {
    root.style.removeProperty('--color-accent')
    root.style.removeProperty('--color-accent-hover')
    root.style.removeProperty('--color-accent-soft')
    root.style.removeProperty('--color-on-accent')
    return
  }
  const [primary, hover] = PALETTES[key][isDark ? 'dark' : 'light']
  root.style.setProperty('--color-accent', primary)
  root.style.setProperty('--color-accent-hover', hover)
  root.style.setProperty(
    '--color-accent-soft',
    `color-mix(in srgb, ${primary} ${isDark ? 18 : 14}%, transparent)`,
  )
  root.style.setProperty('--color-on-accent', isDark ? '#101014' : '#fff')
}

export function bindShellAppearance(isDark) {
  watch(
    [accentKey, isDark],
    ([accent, dark]) => {
      paintAccent(accent, dark)
      try {
        localStorage.setItem(ACCENT_KEY, accent)
      } catch {
        /* 写不进去不影响当前页 */
      }
    },
    { immediate: true },
  )
  watch(
    fontKey,
    (key) => {
      document.documentElement.style.fontSize = `${fontPx(key)}px`
      try {
        localStorage.setItem(FONT_KEY, key)
      } catch {
        /* 写不进去不影响当前页 */
      }
    },
    { immediate: true },
  )
}
