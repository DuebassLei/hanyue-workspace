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
    // 登录能力开关（GET /auth/config）：决定登录页显示注册入口、开发态快捷进入
    authConfig: null,
  }),
  getters: {
    devLoginAvailable: (state) => Boolean(state.authConfig?.allowDevLogin),
    allowRegistration: (state) => Boolean(state.authConfig?.allowRegistration),
    bootstrapAvailable: (state) => Boolean(state.authConfig?.bootstrapAvailable),
  },
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
      // 登录页需要知道后端支持哪些登录方式；拿不到就按「仅密码登录」渲染，
      // 不因此把用户挡在外面（这时提交会拿到真实错误）。
      await this.loadAuthConfig()

      // 显式退出后不自动重登，必须由用户在登录页输入凭据
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
            // 仅开发态可以用上次的 subject 静默重建会话；
            // 密码账号拿不回密码，只能让用户重新登录。
            if (!(await this.relogin())) {
              this.needsLogin = true
              this.error = '登录已失效，请重新登录'
            }
          } else {
            // 网络故障不切换身份，避免把用户悄悄变成默认账号
            this.error = `暂时无法连接服务：${err.message}`
          }
          this.ready = true
          return
        }
      } else {
        // 没有任何凭据：先尝试开发态免密进入（若后端开放且本机用过），
        // 否则停在登录页——不再像以前那样自动建一个 local-creator 账号。
        if (!(await this.relogin())) {
          this.needsLogin = true
        }
      }
      if (this.user) {
        await this.syncThemeFromServer()
      }
      this.ready = true
    },
    async loadAuthConfig() {
      try {
        this.authConfig = await api.get('/auth/config')
      } catch {
        // 后端不可达时保留 null：登录页仍可渲染，提交时才报错
        this.authConfig = null
      }
      return this.authConfig
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
    /** 开发态静默重登：只有后端开放且本机存过 subject 时才尝试 */
    async relogin() {
      if (!this.authConfig) await this.loadAuthConfig()
      if (!this.devLoginAvailable) return false
      const subject = localStorage.getItem(SUBJECT_KEY)
      if (!subject) return false
      try {
        await this.loginWithSubject(subject, subject)
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
      this.error = '登录已过期，请重新登录'
      if (!(await this.relogin())) {
        this.needsLogin = true
      }
    },
    _applySession(result, { subject } = {}) {
      setToken(result.token)
      this.token = result.token
      this.user = result.user
      this.workspaceId = result.workspaceId
      this.error = ''
      this.needsLogin = false
      localStorage.removeItem(LOGOUT_KEY)
      if (subject) localStorage.setItem(SUBJECT_KEY, subject)
    },
    /** 正式登录：邮箱 + 密码 */
    async loginWithPassword(identifier, password) {
      const result = await api.post('/auth/login', { identifier, password })
      this._applySession(result)
      // 密码账号不再沿用开发态的 subject，避免下次启动走免密路径
      localStorage.removeItem(SUBJECT_KEY)
      return result
    },
    /** 注册并直接进入（后端返回会话） */
    async register({ email, password, displayName }) {
      const result = await api.post('/auth/register', {
        email,
        password,
        displayName: displayName || undefined,
      })
      this._applySession(result)
      localStorage.removeItem(SUBJECT_KEY)
      return result
    },
    /** 开发态便利入口：按 subject 换 token（后端 ALLOW_DEV_LOGIN=false 时会 403） */
    async loginWithSubject(subject, displayName) {
      const result = await api.post('/auth/login', { subject, displayName })
      this._applySession(result, { subject })
      return result
    },
    /** 兼容旧调用点：原来的 login(subject, displayName) */
    async login(subject, displayName) {
      return this.loginWithSubject(subject, displayName)
    },
    async changePassword(currentPassword, newPassword) {
      const result = await api.post('/auth/password', {
        currentPassword: currentPassword || undefined,
        newPassword,
      })
      // 改密后旧令牌立即失效，必须换用服务端新签发的那张
      setToken(result.token)
      this.token = result.token
      if (result.user) this.user = result.user
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
    /** 顶栏「重新进入」：清掉退出标记并回登录页（不再尝试免密重登） */
    async reenter() {
      localStorage.removeItem(LOGOUT_KEY)
      this.error = ''
      const ok = await this.relogin()
      if (!ok) {
        this.needsLogin = true
      } else {
        await this.syncThemeFromServer()
        this.needsLogin = false
      }
      this.ready = true
      return ok
    },
  },
})
