/**
 * 成稿台接口客户端。
 *
 * 与后端 `modules/studio.py` 一一对应；所有返回都是整篇稿件快照，
 * 页面按快照整体替换，不做局部合并——避免后台任务改稿时前端拼出中间态。
 */
import { api } from './client'

const base = (id) => `/studio/articles/${id}`

export const studioApi = {
  state: (archived = false) => api.get(`/studio/state?archived=${archived ? 'true' : 'false'}`),
  settings: () => api.get('/studio/settings'),
  saveSettings: (body) => api.post('/studio/settings', body),

  create: (body) => api.post('/studio/articles/new', body),
  get: (id) => api.get(base(id)),
  archive: (id, archived = true) => api.post(`${base(id)}/archive?archived=${archived}`, {}),
  remove: (id) => api.del(base(id)),
  duplicate: (id) => api.post(`${base(id)}/duplicate`, {}),
  markdown: (id) => api.get(`${base(id)}/markdown`),
  exportToContent: (id) => api.post(`${base(id)}/export`, {}),

  chat: (id, message) => api.post(`${base(id)}/chat`, { message }),
  titles: (id) => api.post(`${base(id)}/titles`, {}),
  pickTitle: (id, text) => api.post(`${base(id)}/title/pick`, { text }),
  updateBlock: (id, blockId, body) => api.patch(`${base(id)}/blocks/${blockId}`, body),
  keepBlock: (id, blockId) => api.post(`${base(id)}/blocks/${blockId}/keep`, {}),
  revertBlock: (id, blockId) => api.post(`${base(id)}/blocks/${blockId}/revert`, {}),
  fixBlock: (id, blockId) => api.post(`${base(id)}/blocks/${blockId}/fix`, {}),
  undo: (id) => api.post(`${base(id)}/undo`, {}),
  revisions: (id) => api.get(`${base(id)}/revisions`),

  // style 为空用稿件当前风格；带了就是换风格重写
  draft: (id, style) => api.post(`${base(id)}/draft`, style ? { style } : {}),
  setWriterStyle: (id, style) => api.post(`${base(id)}/writer-style`, { style }),
  fixAll: (id) => api.post(`${base(id)}/fix-all`, {}),
  stop: (id) => api.post(`${base(id)}/job/stop`, {}),
  banner: (id, style) => api.post(`${base(id)}/banner`, { style }),

  // body: { blockIds?: string[], source?: 'auto'|'zhuque'|'local' }，缺省=全文+自动
  detect: (id, body) => api.post(`${base(id)}/zhuque/detect`, body || {}),
  schedule: (id, at) => api.post(`${base(id)}/schedule`, { at }),
  saveLayout: (id, options) => api.post(`${base(id)}/layout/preferences`, options),
  clearNotice: (id) => api.post(`${base(id)}/notice/clear`, {}),
}

/** 朱雀报告 PDF 走原始字节上传（后端按 content-type 分支解析）。 */
export async function uploadReport(articleId, file) {
  const token = localStorage.getItem('hanyue-token') || ''
  const response = await fetch(`/api/v1/studio/articles/${articleId}/zhuque/report`, {
    method: 'POST',
    headers: {
      'Content-Type': file.type || 'application/pdf',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: file,
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    // 后端错误体是扁平的 {code, message}（ApiError），detail.message 只用于兜住 FastAPI 原生校验错误
    throw new Error(payload?.message || payload?.detail?.message || `报告解析失败（HTTP ${response.status}）`)
  }
  return payload
}
