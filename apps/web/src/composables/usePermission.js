import { computed } from 'vue'
import { useRbacStore } from '../stores/rbac'

/**
 * 按钮级的权限判断。
 *
 * **为什么需要它**：菜单和路由守卫管的是「能不能进这个页面」，但页面内部经常有
 * 跨权限点的入口——创作空间里的「转为视频」需要 `videos.use`、「发布」需要
 * `publishing.use`，而创作空间本身只要 `studio.use`。没有按钮级判断时，普通用户
 * 点下去会跳到 403 页，看起来像 bug。
 *
 * **只用于「引导」而不是「安全」**：真正的拦截在后端 `require_permission`，
 * 这里做的是不让人点到一个注定失败的动作。
 *
 * 约定两种表现：
 * - `disabledReason(code)` 返回空串表示有权限；返回文案表示该置灰并提示。
 *   置灰优于隐藏：用户知道「有这功能，但我的角色没有」，管理员也知道该给什么权限。
 * - 纯导航链接（点了只会跳页面）用 `can()` 直接隐藏，避免侧栏之外再出现死链。
 */
export function usePermission() {
  const rbac = useRbacStore()

  const can = (code) => rbac.can(code)
  const canAny = (...codes) => rbac.canAny(...codes)
  const isSuperadmin = computed(() => rbac.isSuperadmin)

  /** 缺少权限时返回提示文案，用于 `:disabled` + `:title` 成对使用 */
  const disabledReason = (code, label) => {
    if (rbac.can(code)) return ''
    return label ? `需要「${label}」权限` : '当前角色无此权限'
  }

  return { can, canAny, disabledReason, isSuperadmin }
}
