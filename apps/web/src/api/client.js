const BASE = '/api/v1'

let token = localStorage.getItem('hanyue-token') || ''

export function setToken(value) {
  token = value || ''
  if (value) localStorage.setItem('hanyue-token', value)
  else localStorage.removeItem('hanyue-token')
}

export function getToken() {
  return token
}

export class ApiError extends Error {
  constructor(message, code, requestId, status) {
    super(message)
    this.code = code
    this.requestId = requestId
    this.status = status
  }
}

function notifyUnauthorized(path) {
  if (path.startsWith('/auth/login')) return
  setToken('')
  // session store 监听该事件：区分「令牌过期」与「网络故障」，不再静默切回默认账号
  window.dispatchEvent(new CustomEvent('hanyue:unauthorized'))
}

function errorMessage(detail, fallback) {
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || JSON.stringify(item)).join('；') || fallback
  }
  if (detail && typeof detail === 'object') return detail.message || fallback
  if (typeof detail === 'string' && detail) return detail
  return fallback
}

async function request(path, options = {}) {
  const { meta = false, ...fetchOptions } = options
  const headers = { ...(fetchOptions.headers || {}) }
  if (token) headers.Authorization = `Bearer ${token}`
  if (fetchOptions.body && !(fetchOptions.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  const response = await fetch(`${BASE}${path}`, { ...fetchOptions, headers })
  const nextCursor = response.headers.get('X-Next-Cursor')
  if (response.status === 204) return meta ? { data: null, nextCursor } : null
  const contentType = response.headers.get('content-type') || ''
  const payload = contentType.includes('application/json') ? await response.json() : null
  if (!response.ok) {
    if (response.status === 401) notifyUnauthorized(path)
    const detail = payload?.detail || payload || {}
    const structured = detail && !Array.isArray(detail) && typeof detail === 'object'
    const code = structured ? detail.code || 'error' : 'validation_error'
    // 权限被管理员收回（改角色/删权限点）时，前端手上的菜单和按钮已经过期。
    // 抛给调用方提示的同时广播一次，让 rbac store 重新拉取，页面自己会收敛。
    if (response.status === 403 && code === 'permission_denied') {
      window.dispatchEvent(new CustomEvent('hanyue:permission-denied'))
    }
    throw new ApiError(
      errorMessage(detail, `请求失败（HTTP ${response.status}）`),
      code,
      structured ? detail.requestId : undefined,
      response.status,
    )
  }
  return meta ? { data: payload, nextCursor } : payload
}

export const api = {
  get: (path, options) => request(path, options),
  /** 读取真 keyset 分页：返回 { data, nextCursor }，令牌来自 X-Next-Cursor 响应头 */
  getPage: (path, options) => request(path, { ...(options || {}), method: 'GET', meta: true }),
  post: (path, body) => request(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
  patch: (path, body) => request(path, { method: 'PATCH', body: JSON.stringify(body ?? {}) }),
  put: (path, body) => request(path, { method: 'PUT', body: JSON.stringify(body ?? {}) }),
  del: (path) => request(path, { method: 'DELETE' }),
  upload: (path, formData) => request(path, { method: 'POST', body: formData }),
}

/** 把接口相对路径拼成绝对地址（正文里的图片链接要能被外部发布端取用） */
export function apiUrl(path) {
  const raw = String(path || '')
  // 已是绝对地址直接返回
  if (/^https?:\/\//i.test(raw)) return raw
  // 后端下发的直链（如 /api/v1/assets/xx/raw?exp=..&sig=..）已带前缀，不能重复拼接
  const withPrefix = raw.startsWith(`${BASE}/`) ? raw : `${BASE}${raw.startsWith('/') ? '' : '/'}${raw}`
  return new URL(withPrefix, window.location.origin).href
}

/** 读取需要鉴权的二进制资源（素材原图），返回可放进 <img src> 的 object URL */
export async function fetchBlobUrl(path) {
  const response = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (response.status === 401) notifyUnauthorized(path)
  if (!response.ok) throw new Error(`素材读取失败（HTTP ${response.status}）`)
  return URL.createObjectURL(await response.blob())
}

/**
 * 下载需要鉴权的二进制文件（如打包 zip）。
 *
 * `<a href>` 直接跳转带不了 Authorization 头，后端只会回 401「缺少登录会话」；
 * 所以必须先 fetch 成 blob 再触发下载。filename 缺省时从 Content-Disposition 取。
 * 非 2xx 会把后端的中文错误信息抛出来（而不是静默下一个坏文件）。
 */
export async function downloadAuthenticated(path, fallbackName = 'download') {
  const response = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (response.status === 401) notifyUnauthorized(path)
  if (!response.ok) {
    let message = `下载失败（HTTP ${response.status}）`
    try {
      const payload = await response.json()
      message = errorMessage(payload?.detail || payload, message)
    } catch {
      /* 非 JSON 错误体：沿用状态码文案 */
    }
    throw new ApiError(message, 'download_failed', undefined, response.status)
  }
  const disposition = response.headers.get('content-disposition') || ''
  const matched = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)
  const filename = matched ? decodeURIComponent(matched[1]) : fallbackName
  const url = URL.createObjectURL(await response.blob())
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
  } finally {
    // 立刻 revoke 会让部分浏览器来不及取数据，延后一拍
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}

/** SSE：文本生成流；返回中止函数 */
export function streamGeneration(payload, handlers) {
  const controller = new AbortController()
  const run = async () => {
    const response = await fetch(`${BASE}/ai/text-generations`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    })
    if (!response.ok || !response.body) {
      if (response.status === 401) notifyUnauthorized('/ai/text-generations')
      handlers.onError?.({ message: `生成请求失败（HTTP ${response.status}）` })
      handlers.onEnd?.()
      return
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() || ''
      for (const block of blocks) {
        const lines = block.split('\n')
        const event = lines.find((l) => l.startsWith('event:'))?.slice(6).trim()
        const dataLine = lines.find((l) => l.startsWith('data:'))?.slice(5).trim()
        if (!event || !dataLine) continue
        let data = {}
        try {
          data = JSON.parse(dataLine)
        } catch {
          data = { raw: dataLine }
        }
        if (event === 'generation.delta') handlers.onDelta?.(data)
        else if (event === 'generation.done') handlers.onDone?.(data)
        else if (event === 'generation.error') handlers.onError?.(data)
        else if (event === 'generation.started') handlers.onStart?.(data)
        // 真正产出正文的那个候选：降级后也能如实显示实际使用的模型
        else if (event === 'generation.model') handlers.onModel?.(data)
        else if (event === 'generation.saved') handlers.onSaved?.(data)
      }
    }
    handlers.onEnd?.()
  }
  run().catch((err) => {
    if (err.name !== 'AbortError') handlers.onError?.({ message: err.message })
    handlers.onEnd?.()
  })
  return () => controller.abort()
}
