import { defineStore } from 'pinia'
import { api } from '../api/client'

/**
 * 权限与菜单。
 *
 * 服务端是唯一真相：菜单树、权限点、是否超管全部来自 `GET /rbac/me`。
 * 前端这份状态只做两件事——渲染侧栏、给按钮做 `can()` 判断。
 * **它不是安全边界**：接口一律由后端 require_permission 校验，绕过前端也拿不到数据。
 */
export const useRbacStore = defineStore('rbac', {
  state: () => ({
    loaded: false,
    loading: false,
    error: '',
    isSuperadmin: false,
    roleKeys: [],
    permissions: [],
    menus: [],
    // 内部标记：事件只绑一次 + 刷新去抖
    _denialBound: false,
    _refreshTimer: null,
  }),
  getters: {
    /** 侧栏项（带分组信息），命令面板与面包屑共用 */
    navItems: (state) =>
      state.menus.flatMap((group) =>
        group.items.map((item) => ({
          ...item,
          group: group.label,
          groupKey: group.key,
        })),
      ),
    /** 第一个可访问页面：无权限时不至于停在空白页 */
    firstNavName() {
      return this.navItems[0]?.name || null
    },
  },
  actions: {
    /** 只绑一次：权限被收回时后端会返回 permission_denied，前端据此重新拉权限。
     *  否则用户会停留在「菜单还在、点一下就报错」的过期状态里。 */
    _bindDenialListener() {
      if (this._denialBound) return
      this._denialBound = true
      window.addEventListener('hanyue:permission-denied', () => {
        // 冷却期内不重复拉取：一个页面并发 3 个请求会同时失败
        if (this._refreshTimer) return
        this._refreshTimer = setTimeout(() => {
          this._refreshTimer = null
          if (this.loaded) this.load()
        }, 300)
      })
    },
    async load() {
      this._bindDenialListener()
      if (this.loading) return
      this.loading = true
      try {
        const data = await api.get('/rbac/me')
        this.isSuperadmin = Boolean(data.isSuperadmin)
        this.roleKeys = data.roleKeys || []
        this.permissions = data.permissions || []
        this.menus = data.menus || []
        this.loaded = true
        this.error = ''
      } catch (err) {
        // 拿不到权限时不清空已有状态：网络抖动不该让侧栏突然变空
        this.error = `权限信息读取失败：${err.message}`
      } finally {
        this.loading = false
      }
    },
    can(code) {
      if (!code) return true
      return this.isSuperadmin || this.permissions.includes(code)
    },
    canAny(...codes) {
      if (this.isSuperadmin) return true
      return codes.some((code) => this.permissions.includes(code))
    },
    reset() {
      this.loaded = false
      this.loading = false
      this.error = ''
      this.isSuperadmin = false
      this.roleKeys = []
      this.permissions = []
      this.menus = []
    },
  },
})
