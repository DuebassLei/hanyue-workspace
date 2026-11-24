/**
 * 成稿台「排版 · 媒体 · 发布」接口客户端。
 *
 * 与 `api/studio.js` 分开维护：那份是主控统一改的成稿台主流程接口，
 * 这份只放本轮新增的三个能力（见 docs/paper-studio-contract.md 第 3 节）。
 * 返回同样遵守「整篇快照」约定——除 render / review 这类只读派生数据外，
 * 所有写接口都回完整 article，调用方按快照整体替换，不做局部合并。
 */
import { api } from './client'

const base = (id) => `/studio/articles/${id}`

export const studioExtraApi = {
  // ---- 排版 ----
  /** 渲染公众号正文：中间栏预览唯一的数据来源，避免多处各渲染一次。options 缺省时后端用稿件已存的排版选项。 */
  render: (id, options) => api.post(`${base(id)}/render`, options ? { options } : {}),
  /** 排版面板的初始化：拿稿件当前选项 + 风格/主题/SVG 卡目录（目录决定面板里能选什么）。 */
  renderOptions: (id) => api.get(`${base(id)}/render/options`),
  /** 「保存为默认」：把当前选项写回稿件的 layout_options，下次打开/发布自动沿用。 */
  saveLayout: (id, options) => api.post(`${base(id)}/layout/preferences`, options || {}),

  // ---- 封面 ----
  /** 提交生图任务；后端只入队并回 running 快照，产物要靠 sync 拉，所以前端必须轮询。 */
  generateCover: (id, body) => api.post(`${base(id)}/cover/generate`, body || {}),
  /** 轮询查询封面任务结果：成功写 assetId/url，失败写 error（不会静默成功）。 */
  syncCover: (id) => api.post(`${base(id)}/cover/sync`, {}),
  /** 选定/清空封面素材：换图走同一接口，传 null 表示移除封面。 */
  pickCover: (id, assetId) => api.post(`${base(id)}/cover/pick`, { assetId: assetId || null }),

  // ---- 正文插图 ----
  /** 针对某个段落提交插图任务；同样只入队，结果靠 sync 拉。 */
  generateInline: (id, body) => api.post(`${base(id)}/inline/generate`, body || {}),
  /** 从素材图库挑一张图插到指定段落之后：不触发生图，图直接进正文。 */
  pickInline: (id, assetId, blockId) => api.post(`${base(id)}/inline/pick`, { assetId, blockId }),
  /** 轮询所有 running 的插图：成功后后端会在段落之后插入 media 块，所以必须用返回的快照刷新整篇。 */
  syncInline: (id) => api.post(`${base(id)}/inline/sync`, {}),
  /** 移除一张插图：同时删掉它插入正文的那个块，避免留下空图段。 */
  removeInline: (id, diagramId) => api.del(`${base(id)}/inline/${diagramId}`),

  // ---- 提示词建议 ----
  /** 按当前稿件现拟提示词：scope=cover 出封面候选，scope=inline 出插图候选（给 blockId 就只拟那一段）。 */
  suggestPrompts: (id, body) => api.post(`${base(id)}/media/prompt-suggestions`, body || {}),

  // ---- SVG 动画卡 ----
  /** 按段落 + 卡片类型生成 SVG 卡；传 blockIds（≥2 段）时合并所选段落生成一张卡，插在最后一处之后。 */
  svgCard: (id, blockId, kind, blockIds) =>
    api.post(`${base(id)}/blocks/${blockId}/svg-card`, {
      kind,
      ...(Array.isArray(blockIds) && blockIds.length > 1 ? { blockIds } : {}),
    }),

  // ---- 发布与复盘 ----
  /** 推送已存档版本到指定公众号的草稿箱，不正式发布。 */
  pushDraft: (id, payload) => api.post(`${base(id)}/publish`, payload),
  retryDraft: (id, jobId) => api.post(`${base(id)}/publish/${jobId}/retry`),
  wechatAccounts: async () => (await api.get('/platform-accounts')).filter(
    (account) => account.platform === 'wechat_official' && account.status === 'connected' && account.authSource === 'app_secret',
  ),
  /** 复盘：按 contentItemId 反查各平台任务状态，用于发布结果表格。 */
  review: (id) => api.get(`${base(id)}/review`),
  /** 排期：只记「计划什么时候发」，与 studio.js 的 schedule 同一接口，这里复用于发布面板。 */
  schedule: (id, at) => api.post(`${base(id)}/schedule`, { at: at || null }),
}
