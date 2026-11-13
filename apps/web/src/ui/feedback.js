import { createDiscreteApi, darkTheme } from 'naive-ui'
import { computed, ref } from 'vue'
import { accentKey, fontKey, fontPx, shellOverrides } from './theme'

const mode = ref('light')

const configProviderProps = computed(() => ({
  theme: mode.value === 'dark' ? darkTheme : null,
  themeOverrides: shellOverrides(accentKey.value, mode.value === 'dark', fontPx(fontKey.value)),
}))

const { message, dialog } = createDiscreteApi(['message', 'dialog'], {
  configProviderProps,
})

export function syncFeedbackTheme(themeName) {
  if (themeName === 'dark') mode.value = 'dark'
  else if (themeName === 'light') mode.value = 'light'
  else {
    mode.value =
      typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
  }
}

export function toast(content, type = 'success') {
  const fn = message[type] || message.info
  fn(content, { duration: 2800, keepAliveOnHover: true })
}

export function confirm(content, title = '确认') {
  return new Promise((resolve) => {
    dialog.warning({
      title,
      content,
      positiveText: '确定',
      negativeText: '取消',
      onPositiveClick: () => resolve(true),
      onNegativeClick: () => resolve(false),
      onClose: () => resolve(false),
    })
  })
}
