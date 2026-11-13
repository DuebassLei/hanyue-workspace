import { defineStore } from 'pinia'
import { api, setToken } from '../api/client'

export const THEME_KEY = 'hanyue-theme'
const SUBJECT_KEY = 'hanyue-subject'
const LOGOUT_KEY = 'hanyue-logged-out'

function applyTheme(mode) {
  const dark =
    mode === 'dark' ||
    (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

export const useSessionStore = defineStore('session', {
  state: () => ({
    user: null,
    workspaceId: null,
    token: localStorage.getItem('hanyue-token') || '',
    ready: false,
    error: '',
    theme: localStorage.getItem(THEME_KEY) || 'system',
    schemeListenerBound: false,
    authListenerBound: false,
    themeSyncError: '',
    needsLogin: false,
  }),
  actions: {
    async boot() {
      applyTheme(this.theme)
      if (!this.schemeListenerBound) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
          if (this.theme === 'system') applyTheme('system')
        })
        this.schemeListenerBound = true
      }
      if (!this.authListenerBound) {
        window.addEventListener('hanyue:unauthorized', () => this.onUnauthorized())
        this.authListenerBound = true
      }
      // 显式退出后不自动重登，必须由用户点「重新进入」
      if (localStorage.getItem(LOGOUT_KEY)) {
        this.needsLogin = true
        this.ready = true
        return
      }
      if (this.token) {
        try {
          this.user = await api.get('/auth/me')
        } catch (err) {
          if (err.status === 401) {
            if (!(await this.relogin())) {
              this.needsLogin = true
              this.error = '登录已失效，请重新进入'
            }
          } else {
            // 网络故障不切换身份，避免把用户悄悄变成默认账号
            this.error = `暂时无法连接服务：${err.message}`
          }
          this.ready = true
          return
        }
      } else {
        try {
          await this.login('local-creator', '创作者')
        } catch (err) {
          this.needsLogin = true
          this.error = `无法建立本地会话：${err.message}`
          this.ready = true
          return
        }
      }
      await this.syncThemeFromServer()
      this.ready = true
    },
    /** 服务端偏好为准；失败时保留 localStorage 值，不阻塞启动 */
    async syncThemeFromServer() {
      try {
        const pref = await api.get('/preferences')
        if (pref && ['system', 'light', 'dark'].includes(pref.theme)) {
          this.theme = pref.theme
          localStorage.setItem(THEME_KEY, pref.theme)
          applyTheme(pref.theme)
        }
      } catch (err) {
        this.themeSyncError = `主题偏好未能从服务端读取：${err.message}`
      }
    },
    async relogin() {
      const subject = localStorage.getItem(SUBJECT_KEY) || 'local-creator'
      try {
        await this.login(subject, subject)
        this.error = ''
        this.needsLogin = false
        return true
      } catch {
        return false
      }
    },
    async onUnauthorized() {
      this.token = ''
      this.user = null
      this.error = '登录已过期，正在尝试重新进入…'
      if (!(await this.relogin())) {
        this.needsLogin = true
        this.error = '登录已失效，请重新进入'
      }
    },
    async login(subject, displayName) {
      const result = await api.post('/auth/login', { subject, displayName })
      setToken(result.token)
      this.token = result.token
      this.user = result.user
      this.workspaceId = result.workspaceId
      this.error = ''
      this.needsLogin = false
      localStorage.setItem(SUBJECT_KEY, subject)
      localStorage.removeItem(LOGOUT_KEY)
      return result
    },
    setTheme(mode) {
      this.theme = mode
      localStorage.setItem(THEME_KEY, mode)
      applyTheme(mode)
      this.themeSyncError = ''
      // 本地立即生效，但同步失败要让用户看见，不能假装已跨设备保存
      api.patch('/preferences', { theme: mode })
        .then(() => { this.themeSyncError = '' })
        .catch((err) => {
          this.themeSyncError = `主题已在本机生效，但未能同步到服务端：${err.message}`
        })
    },
    /** 资料保存后同步到全局：顶栏头像、署名、设置页都读同一份 user。 */
    applyUser(user) {
      if (user) this.user = user
    },
    /** 顶栏提示条已把详情收进账号面板：点掉只清展示，接口失败仍然会重新出现。 */
    dismissAlerts() {
      this.error = ''
      this.themeSyncError = ''
    },
    async logout() {
      try {
        await api.post('/auth/logout')
      } catch {
        // 后端不可达时仍完成本地退出与令牌清理
      }
      setToken('')
      this.token = ''
      this.user = null
      this.workspaceId = null
      localStorage.setItem(LOGOUT_KEY, '1')
      this.needsLogin = true
      this.error = ''
    },
    async reenter() {
      localStorage.removeItem(LOGOUT_KEY)
      this.error = ''
      const ok = await this.relogin()
      if (!ok) {
        this.error = '重新进入失败，请检查后端与身份配置'
        this.needsLogin = true
      }
      await this.syncThemeFromServer()
      this.ready = true
      return ok
    },
  },
})
