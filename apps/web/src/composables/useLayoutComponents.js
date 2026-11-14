import { ref } from 'vue'
import { api } from '../api/client'

/**
 * 小组件注册表（后端 LAYOUT_COMPONENTS 驱动）。
 * 模块级缓存：组件库页与发布抽屉共用一次请求；后端新增组件类型前端自动可见。
 */
const components = ref([])
const loaded = ref(false)
let inflight = null

export function useLayoutComponents() {
  async function load(force = false) {
    if (loaded.value && !force) return components.value
    if (!inflight) {
      inflight = api
        .get('/wechat-mp/components')
        .then((data) => {
          components.value = data.components || []
          loaded.value = true
          return components.value
        })
        .finally(() => {
          inflight = null
        })
    }
    return inflight
  }

  return { components, load }
}
