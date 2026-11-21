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

  draft: (id) => api.post(`${base(id)}/draft`, {}),
  fixAll: (id) => api.post(`${base(id)}/fix-all`, {}),
  stop: (id) => api.post(`${base(id)}/job/stop`, {}),
  banner: (id, style) => api.post(`${base(id)}/banner`, { style }),

  detect: (id) => api.post(`${base(id)}/zhuque/detect`, {}),
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
    throw new Error(payload?.detail?.message || `报告解析失败（HTTP ${response.status}）`)
  }
  return payload
}
